"""Keyless, sequential PubMed E-utilities ingestion with XML parsing."""

from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
import logging
import re
import time
import xml.etree.ElementTree as ET

import requests

from src.models import Article, ArticleMetadata
from src.journal_metrics import load_policy

LOGGER = logging.getLogger(__name__)
EUTILS = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/"
DEFAULT_LOOKBACK_DAYS = 30
HEADACHE_QUERY = ('(migraine[Title/Abstract] OR headache[Title/Abstract] '
                  'OR cephalalgia[Title/Abstract] OR hemicrania[Title/Abstract] '
                  'OR "trigeminal neuralgia"[Title/Abstract] '
                  'OR "trigeminal autonomic cephalalgias"[Title/Abstract] '
                  'OR SUNCT[Title/Abstract] OR SUNA[Title/Abstract])')
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
# Society grouping is retained, but cannot grant admission to a lower-JIF source.
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


TOPIC = re.compile(r"\b(?:migrain(?:e|es|ous)|headaches?|cephalalgias?|hemicrania|"
                   r"trigeminal[-\s]+neuralgia|trigeminal autonomic cephalalgias?|SUNCT|SUNA)\b", re.I)
INCIDENTAL = re.compile(r"(?:symptoms?|side effects?|adverse (?:events?|effects?|reactions?))\s+"
                        r"(?:\w+\s+){0,3}(?:included?|including|such as|were|are|:)", re.I)
GOAL = re.compile(r"\b(?:we (?:aim(?:ed)?|sought)|(?:this|the) study (?:aim(?:s|ed)?|sought)|"
                  r"(?:our |the )?(?:objectives?|aims?|purposes?) (?:of [^.!?]{0,100}? )?(?:was|were|is|are)|"
                  r"we (?:investigated|evaluated|examined|assessed|compared|studied))\b", re.I)
PRIMARY_OUTCOME = re.compile(r"\bprimary (?:outcomes?|endpoints?)\b", re.I)
MIGRAINE_THERAPY = re.compile(r"\b(?:erenumab|fremanezumab|galcanezumab|eptinezumab|"
                              r"rimegepant|ubrogepant|atogepant|zavegepant|lasmiditan|"
                              r"anti[- ]CGRP|CGRP[- ](?:monoclonal antibodies|mAbs))\b", re.I)
MIGRAINE_CONTEXT = re.compile(r"(?:treatment|prevention) of (?:migraine|cluster headache)|"
                              r"migraine (?:prevention|treatment)|"
                              r"patients? with (?:(?:episodic|chronic) )?migraine", re.I)


def is_headache_topic(title: str, abstract: str) -> bool:
    """Conservative, keyless topic screen; journal and affiliations are not evidence.

    Accept a title topic, an explicit study objective/primary outcome, or a
    sustained unstructured focus. Named migraine therapies need migraine context.
    Generic trigeminal anatomy and isolated symptom/adverse-event mentions fail.
    This transparent lexical screen favors precision over exhaustive retrieval.
    """
    # Clinical-reasoning titles often enumerate symptoms of an unrelated disease.
    symptom_list_title = bool(re.search(r"\b(?:man|woman|boy|girl|patient|child) with\b", title, re.I)
                              and ("," in title or re.search(r"\band\b", title, re.I)))
    if TOPIC.search(title) and not INCIDENTAL.search(title) and not symptom_list_title:
        return True
    therapy = MIGRAINE_THERAPY.search(title)
    if therapy and MIGRAINE_CONTEXT.search(abstract):
        return True
    sections = abstract.splitlines()
    structured = any(re.match(r"^[A-Z][A-Z /&-]+:", section) for section in sections)
    for section in sections:
        label, separator, body = section.partition(":")
        if separator and re.fullmatch(r"(?:OBJECTIVES?|AIMS?|PURPOSE)", label, re.I):
            if any(TOPIC.search(sentence) and not INCIDENTAL.search(sentence)
                   for sentence in re.split(r"(?<=[.!?])\s+", body)):
                return True
        for sentence in re.split(r"(?<=[.!?])\s+", section):
            goal = GOAL.search(sentence) or PRIMARY_OUTCOME.search(sentence)
            if goal and TOPIC.search(sentence[goal.start():]) and not INCIDENTAL.search(sentence):
                return True
    if not structured:
        sentences = re.split(r"(?<=[.!?])\s+", abstract)
        focused = [sentence for sentence in sentences if TOPIC.search(sentence) and not INCIDENTAL.search(sentence)]
        # A single symptom in a case report is insufficient. The opening topic
        # must be developed again, e.g. migraine aura and its pathophysiology.
        return bool(sentences and TOPIC.search(sentences[0])
                    and not INCIDENTAL.search(sentences[0]) and len(focused) >= 2)
    return False


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
        # Apply to custom queries too, then independently check fetched metadata.
        restricted_query = f"({query}) AND {self.policy.pubmed_clause()}"
        ids = parse_search(self._get("esearch.fcgi", params={
            "db": "pubmed", "term": restricted_query, "retmode": "xml", "retmax": limit,
            "sort": "pub_date", "datetype": "pdat", "mindate": since.strftime("%Y/%m/%d"),
            "maxdate": today.strftime("%Y/%m/%d"), "tool": "headache_digest",
        }).content)
        candidates = self.fetch_by_ids(ids)
        articles = [article for article in candidates
                    if self.policy.eligible(article.metadata.source_society_or_journal)
                    and is_headache_topic(article.metadata.title, article.abstract)]
        LOGGER.info("JIF > 15 and topic screens retained %s/%s PubMed candidates", len(articles), len(candidates))
        return articles

    def fetch_by_ids(self, ids: list[str]) -> list[Article]:
        """Fetch source evidence without filtering, also useful for archive audits."""
        articles = []
        for offset in range(0, len(ids), 100):
            payload = self._get("efetch.fcgi", params={
                "db": "pubmed", "id": ",".join(ids[offset:offset + 100]),
                "retmode": "xml", "tool": "headache_digest",
            }).content
            articles.extend(parse_pubmed(payload))
        return articles

    def collect(self, lookback_days=DEFAULT_LOOKBACK_DAYS, pubmed_limit=1000, query=DEFAULT_QUERY, today=None):
        today = today or datetime.now(timezone.utc).date()
        since = today - timedelta(days=lookback_days - 1)
        try:
            articles = self.fetch_pubmed(since, today, pubmed_limit, query)
            return CrawlResult(articles=articles, successful_sources=1)
        except (SourceError, ValueError, ET.ParseError) as exc:
            detail = str(exc) if isinstance(exc, SourceError) else type(exc).__name__
            LOGGER.error("PubMed failed: %s", detail)
            return CrawlResult(errors=[detail])
