"""Keyless, sequential PubMed E-utilities ingestion with XML parsing."""

from dataclasses import dataclass, field
from datetime import date, timedelta
import logging
import re
import time
import xml.etree.ElementTree as ET

import requests

from src.models import Article, ArticleMetadata
from src.journal_metrics import clinical_trial_clause, is_clinical_trial, load_policy
from src.headache_scope import HEADACHE_QUERY, is_headache_topic
from src.time_windows import DEFAULT_LOOKBACK_DAYS, DEFAULT_TRIAL_LOOKBACK_YEARS, trial_date_eligible, utc_today, years_before

LOGGER = logging.getLogger(__name__)
EUTILS = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/"
SEARCH_PAGE_SIZE = 500

TARGET_JOURNALS = (
    "Cephalalgia", "Cephalalgia Reports", "The Journal of Headache and Pain", "J Headache Pain",
    "Annals of Neurology", "Neurology", "Headache", "Japanese Journal of Headache",
    "Neurology and Clinical Neuroscience", "Internal Medicine", "Intern Med", "Rinsho Shinkeigaku",
    "Acta Neurologica Taiwanica", "Acta Neurol Taiwan",
    "Journal of the Chinese Medical Association", "J Chin Med Assoc",
)
TARGET_SOCIETIES = ("Japanese Headache Society", "Taiwan Headache Society")
JAPAN_AFFILIATION_QUERY = ('("Japan"[Affiliation] AND ("Cephalalgia"[Journal] '
                           'OR "The Journal of Headache and Pain"[Journal] '
                           'OR "J Headache Pain"[Journal] OR "Headache"[Journal]))')
SOCIETY_QUERY = "((" + " OR ".join(
    [f'"{name}"[Journal]' for name in TARGET_JOURNALS]
    + [f'"{name}"[{field}]' for name in TARGET_SOCIETIES
       for field in ("Affiliation", "Title/Abstract", "Corporate Author")]
    + [JAPAN_AFFILIATION_QUERY]
) + ") AND " + HEADACHE_QUERY + " AND hasabstract[text])"
# The strict JIF requirement supersedes the old journal-only source restriction.
# Society grouping is retained; lower-JIF sources still require a clinical trial.
DEFAULT_QUERY = "(" + load_policy().pubmed_clause() + " AND " + HEADACHE_QUERY + " AND hasabstract[text])"

# These are requested source groups, not claims that every associated journal
# is an official society publication or that its authors are society members.
JOURNAL_GROUPS = {
    "International Headache Society": {"cephalalgia", "cephalalgia reports"},
    "European Headache Federation": {"the journal of headache and pain", "j headache pain"},
    "American Neurological Association": {"annals of neurology", "ann neurol"},
    "American Academy of Neurology": {"neurology"},
    "American Headache Society": {"headache"},
    "Japanese Headache Society": {"japanese journal of headache", "neurology and clinical neuroscience",
                                  "neurol clin neurosci", "internal medicine", "intern med", "rinsho shinkeigaku"},
    "Taiwan Headache Society": {"acta neurologica taiwanica", "acta neurol taiwan",
                                "journal of the chinese medical association", "j chin med assoc"},
}
JAPAN_SHARED_JOURNALS = {"cephalalgia", "the journal of headache and pain", "j headache pain", "headache"}


def journal_key(journal: str) -> str:
    return re.split(r"[|:(=]", journal, maxsplit=1)[0].strip().casefold()


def classify_societies(journal: str, title="", abstract="", affiliations=(), authors=()) -> list[str]:
    key = journal_key(journal)
    groups = {name for name, journals in JOURNAL_GROUPS.items() if key in journals}
    evidence = " ".join([title, abstract, *affiliations, *authors]).casefold()
    for name in TARGET_SOCIETIES:
        if name.casefold() in evidence:
            groups.add(name)
    if key in JAPAN_SHARED_JOURNALS and any(re.search(r"\bJapan\b", value, re.I) for value in affiliations):
        groups.add("Japanese Headache Society")
    # Country words such as Chinese/China never establish Taiwan affiliation.
    return [name for name in JOURNAL_GROUPS if name in groups]


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
                label = section.get("Label") or section.get("NlmCategory")
                sections.append(f"{label}: {body}" if label and label != "UNASSIGNED" else body)
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
        authors = [_text(x) for x in content.findall("./AuthorList/Author/CollectiveName")]
        abstract = "\n".join(sections)
        # Preserve overlapping source groups within the existing six-field schema.
        societies = classify_societies(journal, title, abstract, affiliations, authors)
        articles.append(Article(
            metadata=ArticleMetadata(
                pmid_or_id=pmid, doi=normalize_doi(doi), title=title,
                source_society_or_journal=" | ".join([journal, *societies]),
                publication_date=_publication_date(node), study_design=design,
            ),
            url=f"https://pubmed.ncbi.nlm.nih.gov/{pmid}/", abstract=abstract,
            affiliations=affiliations,
        ))
    return articles


@dataclass
class CrawlResult:
    articles: list[Article] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    successful_sources: int = 0


class Crawler:
    def __init__(self, session=None, policy=None):
        self.session = session or requests.Session()
        self.policy = policy or load_policy()
        self.session.headers.update({"User-Agent": "HeadacheDigest/2.0 (keyless literature reader)"})

    def _request(self, endpoint, *, params):
        for attempt in range(3):
            # Every E-utilities request, including retries, stays below 3/sec.
            time.sleep(0.4)
            try:
                # ESearch uses form POST so the complete syndrome query is not
                # truncated by URL/proxy length limits. EFetch remains GET.
                if endpoint == "esearch.fcgi":
                    response = self.session.post(EUTILS + endpoint, data=params, timeout=(10, 45))
                else:
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

    def search_ids(self, since: date, today: date, query: str, limit=None) -> list[str]:
        """Page all IDs; split date ranges above PubMed's 10,000-result ceiling.

        An explicit limit is for bounded diagnostic runs only. Defaults never
        silently discard the older portion of the ten-year trial window.
        """
        ids = []
        offset = 0
        total = None
        while limit is None or len(ids) < limit:
            size = SEARCH_PAGE_SIZE if limit is None else min(SEARCH_PAGE_SIZE, limit - len(ids))
            payload = self._request("esearch.fcgi", params={
                "db": "pubmed", "term": query, "retmode": "xml", "retmax": size, "retstart": offset,
                "sort": "pub_date", "datetype": "pdat", "mindate": since.strftime("%Y/%m/%d"),
                "maxdate": today.strftime("%Y/%m/%d"), "tool": "headache_digest",
            }).content
            page = parse_search(payload)
            count_text = ET.fromstring(payload).findtext("Count")
            total = int(count_text) if count_text is not None else None
            if total is not None and total > 10000 and (limit is None or limit > 10000):
                if since == today:
                    raise SourceError("More than 10,000 PubMed matches on one day; refine the query")
                midpoint = since + (today - since) // 2
                newer = self.search_ids(midpoint + timedelta(days=1), today, query, limit)
                remaining = None if limit is None else limit - len(newer)
                older = self.search_ids(since, midpoint, query, remaining) if remaining != 0 else []
                return list(dict.fromkeys(newer + older))
            if not page:
                if total is not None and offset < total:
                    raise SourceError("PubMed search ended before all matching IDs were returned")
                break
            seen = set(ids)
            new = list(dict.fromkeys(identity for identity in page if identity not in seen))
            if not new:
                raise SourceError("PubMed search repeated a page without advancing")
            ids.extend(new)
            offset += len(page)
            if total is None or offset >= total:
                break
        if limit is not None and total is not None and total > len(ids):
            LOGGER.warning("Explicit PubMed limit retained %s/%s matches for this window", len(ids), total)
        return ids[:limit] if limit is not None else ids

    def fetch_pubmed(self, since: date, today: date, limit=None, query=DEFAULT_QUERY, *, trials_only=False):
        # Apply to custom queries too, then independently check fetched metadata.
        # Build the default from the current policy once: repeating a growing
        # registry can exceed the E-utilities URL limit (HTTP 414).
        if query == DEFAULT_QUERY:
            query = f"{HEADACHE_QUERY} AND hasabstract[text]"
        restricted_query = f"({query}) AND {self.policy.pubmed_clause()}"
        if trials_only:
            restricted_query += " AND " + clinical_trial_clause()
        ids = self.search_ids(since, today, restricted_query, limit)
        candidates = self.fetch_by_ids(ids)
        articles = [article for article in candidates
                    if self.policy.eligible(article.metadata.source_society_or_journal, article.metadata.study_design)
                    and (not trials_only or is_clinical_trial(article.metadata.study_design))
                    and is_headache_topic(article.metadata.title, article.abstract)]
        LOGGER.info("JIF/clinical-trial and topic screens retained %s/%s PubMed candidates", len(articles), len(candidates))
        return articles

    def fetch_by_ids(self, ids: list[str]) -> list[Article]:
        """Fetch source evidence without filtering, also useful for archive audits."""
        articles = []
        for offset in range(0, len(ids), 100):
            payload = self._request("efetch.fcgi", params={
                "db": "pubmed", "id": ",".join(ids[offset:offset + 100]),
                "retmode": "xml", "tool": "headache_digest",
            }).content
            articles.extend(parse_pubmed(payload))
        return articles

    def collect(self, lookback_days=DEFAULT_LOOKBACK_DAYS, pubmed_limit=None, query=DEFAULT_QUERY, today=None,
                trial_lookback_years=DEFAULT_TRIAL_LOOKBACK_YEARS):
        today = today or utc_today()
        since = today - timedelta(days=lookback_days - 1)
        trial_since = years_before(today, trial_lookback_years)
        windows = [("recent", since, today, False)]
        if trial_since < since:
            windows.append(("historical trials", trial_since, since - timedelta(days=1), True))
        result = CrawlResult()
        by_id = {}
        for name, start, end, trials_only in windows:
            try:
                articles = self.fetch_pubmed(start, end, pubmed_limit, query, trials_only=trials_only)
                for article in articles:
                    if is_clinical_trial(article.metadata.study_design) and not trial_date_eligible(
                            article.metadata.publication_date, today, trial_lookback_years):
                        continue
                    by_id.setdefault(article.metadata.pmid_or_id, article)
                result.successful_sources += 1
            except (SourceError, ValueError, ET.ParseError) as exc:
                detail = str(exc) if isinstance(exc, SourceError) else type(exc).__name__
                LOGGER.error("PubMed %s failed: %s", name, detail)
                result.errors.append(f"{name}: {detail}")
        result.articles = list(by_id.values())
        return result
