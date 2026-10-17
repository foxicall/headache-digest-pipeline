"""Keyless, sequential PubMed E-utilities ingestion with XML parsing."""

from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
import logging
import re
import time
import xml.etree.ElementTree as ET

import requests

from src.models import Article, ArticleMetadata

LOGGER = logging.getLogger(__name__)
EUTILS = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/"
HEADACHE_QUERY = '(migraine[Title/Abstract] OR headache[Title/Abstract] OR trigeminal[Title/Abstract])'
TARGET_JOURNALS = (
    "Cephalalgia", "The Journal of Headache and Pain", "Headache",
    "Neurology", "Annals of Neurology", "Acta Neurologica Taiwanica", "Intern Med",
)
TARGET_SOCIETIES = ("Japanese Headache Society", "Taiwan Headache Society")
DEFAULT_QUERY = "((" + " OR ".join(
    [f'"{name}"[Journal]' for name in TARGET_JOURNALS]
    + [f'"{name}"[Affiliation]' for name in TARGET_SOCIETIES]
) + ") AND " + HEADACHE_QUERY + " AND hasabstract[text])"


class SourceError(RuntimeError):
    pass


def _text(element: ET.Element | None) -> str:
    return "" if element is None else " ".join("".join(element.itertext()).split())


def normalize_doi(value: str | None) -> str:
    if not value:
        return "Not reported"
    return re.sub(r"^(?:https?://(?:dx\.)?doi\.org/|doi:\s*)", "", value.strip(), flags=re.I).lower() or "Not reported"


def _publication_date(article: ET.Element) -> str:
    node = article.find("./MedlineCitation/Article/ArticleDate")
    if node is None:
        node = article.find("./MedlineCitation/Article/Journal/JournalIssue/PubDate")
    if node is None:
        return "Not reported"
    year = _text(node.find("Year"))
    if not year:
        match = re.search(r"\b(\d{4})\b", _text(node.find("MedlineDate")))
        return match.group(1) if match else "Not reported"
    month = _text(node.find("Month"))
    if not month:
        return year
    if not month.isdigit():
        months = {name: index for index, name in enumerate(
            ("Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"), 1)}
        month = str(months.get(month[:3].title(), ""))
    if not month:
        return year
    result = f"{year}-{int(month):02d}"
    day = _text(node.find("Day"))
    return f"{result}-{int(day):02d}" if day else result


def parse_search(payload: bytes) -> list[str]:
    root = ET.fromstring(payload)
    if root.tag != "eSearchResult" or root.find("ERROR") is not None:
        raise SourceError("PubMed search returned an error or invalid XML result")
    if root.find("IdList") is None:
        raise SourceError("PubMed search omitted IdList")
    # Unmapped affiliation terms can coexist with valid journal OR branches.
    for error in root.findall("./ErrorList/*") + root.findall("./WarningList/*"):
        LOGGER.warning("PubMed search notice: %s", error.tag)
    return [_text(node) for node in root.findall("./IdList/Id") if _text(node)]


def parse_pubmed(payload: bytes) -> list[Article]:
    root = ET.fromstring(payload)
    if root.tag != "PubmedArticleSet" or root.find(".//ERROR") is not None:
        raise SourceError("PubMed fetch returned an error or invalid article set")
    articles = []
    for node in root.findall("PubmedArticle"):
        content = node.find("./MedlineCitation/Article")
        if content is None:
            continue
        pmid = _text(node.find("./MedlineCitation/PMID"))
        title = _text(content.find("ArticleTitle"))
        if not pmid or not title:
            continue
        sections = []
        for section in content.findall("./Abstract/AbstractText"):
            body = _text(section)
            if body:
                sections.append(f"{section.attrib['Label']}: {body}" if section.attrib.get("Label") else body)
        # Restrict identifiers to this article, never its references.
        doi = next((_text(x) for x in node.findall("./PubmedData/ArticleIdList/ArticleId")
                    if x.get("IdType") == "doi"), None)
        if not doi:
            doi = next((_text(x) for x in content.findall("ELocationID") if x.get("EIdType") == "doi"), None)
        types = [_text(x) for x in content.findall("./PublicationTypeList/PublicationType")]
        design = "; ".join(x for x in types if x not in {"Journal Article", "English Abstract"}
                           and not x.startswith("Research Support")) or "Not reported"
        affiliations = list(dict.fromkeys(_text(x) for x in content.findall("./AuthorList/Author/AffiliationInfo/Affiliation") if _text(x)))
        journal = _text(content.find("./Journal/Title")) or "PubMed (journal not reported)"
        # The requested JSON has no affiliation field; retain only the relevant
        # society tags in its source field so the JHS/THS filters still work.
        societies = [name for name in TARGET_SOCIETIES
                     if any(name.casefold() in affiliation.casefold() for affiliation in affiliations)]
        articles.append(Article(
            metadata=ArticleMetadata(
                pmid_or_id=pmid, doi=normalize_doi(doi), title=title,
                source_society_or_journal=" | ".join([journal, *societies]),
                publication_date=_publication_date(node), study_design=design,
            ),
            url=f"https://pubmed.ncbi.nlm.nih.gov/{pmid}/", abstract="\n".join(sections),
            affiliations=affiliations,
        ))
    return articles


@dataclass
class CrawlResult:
    articles: list[Article] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    successful_sources: int = 0


class Crawler:
    def __init__(self, session=None):
        self.session = session or requests.Session()
        self.session.headers.update({"User-Agent": "HeadacheDigest/2.0 (keyless literature reader)"})

    def _get(self, endpoint, *, params):
        for attempt in range(3):
            # Every E-utilities request, including retries, stays below 3/sec.
            time.sleep(0.4)
            try:
                response = self.session.get(EUTILS + endpoint, params=params, timeout=(10, 45))
                response.raise_for_status()
                return response
            except requests.RequestException as exc:
                status = exc.response.status_code if exc.response is not None else None
                if attempt == 2 or (status is not None and status not in {429, 500, 502, 503, 504}):
                    raise SourceError(f"PubMed request failed (HTTP {status or 'network error'})") from exc
                delay = 2 ** attempt
                if exc.response is not None:
                    retry_after = exc.response.headers.get("Retry-After", "")
                    if retry_after.isdigit():
                        delay = min(max(delay, int(retry_after)), 60)
                time.sleep(delay)
        raise AssertionError("unreachable")

    def fetch_pubmed(self, since: date, today: date, limit=1000, query=DEFAULT_QUERY):
        ids = parse_search(self._get("esearch.fcgi", params={
            "db": "pubmed", "term": query, "retmode": "xml", "retmax": limit,
            "sort": "pub_date", "datetype": "pdat", "mindate": since.strftime("%Y/%m/%d"),
            "maxdate": today.strftime("%Y/%m/%d"), "tool": "headache_digest",
        }).content)
        articles = []
        for offset in range(0, len(ids), 100):
            payload = self._get("efetch.fcgi", params={
                "db": "pubmed", "id": ",".join(ids[offset:offset + 100]),
                "retmode": "xml", "tool": "headache_digest",
            }).content
            articles.extend(parse_pubmed(payload))
        return articles

    def collect(self, lookback_days=14, pubmed_limit=1000, query=DEFAULT_QUERY, today=None):
        today = today or datetime.now(timezone.utc).date()
        since = today - timedelta(days=lookback_days - 1)
        try:
            articles = self.fetch_pubmed(since, today, pubmed_limit, query)
            return CrawlResult(articles=articles, successful_sources=1)
        except (SourceError, ValueError, ET.ParseError) as exc:
            detail = str(exc) if isinstance(exc, SourceError) else type(exc).__name__
            LOGGER.error("PubMed failed: %s", detail)
            return CrawlResult(errors=[detail])
