"""Fetch, deduplicate by PMID, summarize and prepend records to repository JSON."""

import argparse
from dataclasses import asdict, dataclass
import json
import logging
import os
from pathlib import Path
import sys
import tempfile

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from openai import OpenAIError
from pydantic import TypeAdapter, ValidationError

from src.crawler import Crawler, DEFAULT_LOOKBACK_DAYS, DEFAULT_QUERY, is_headache_topic
from src.models import Article, PaperRecord
from src.journal_metrics import load_policy
from src.summarizer import DEFAULT_MODEL, Summarizer, SummaryError, is_fallback

LOGGER = logging.getLogger(__name__)
DEFAULT_DATA = Path(__file__).resolve().parents[1] / "data" / "headache_papers.json"
RECORDS = TypeAdapter(list[PaperRecord])


@dataclass
class RunStats:
    fetched: int = 0
    duplicates: int = 0
    skipped_no_text: int = 0
    skipped_off_topic: int = 0
    skipped_jif: int = 0
    removed_jif: int = 0
    deferred: int = 0
    saved: int = 0
    upgraded: int = 0
    fallback_saved: int = 0
    summary_failures: int = 0
    source_failures: int = 0


def load_records(path: Path) -> list[PaperRecord]:
    if not path.exists():
        return []
    records = RECORDS.validate_json(path.read_text(encoding="utf-8"))
    ids = [record.article_metadata.pmid_or_id for record in records]
    if len(ids) != len(set(ids)):
        raise ValueError("Existing JSON contains duplicate pmid_or_id values")
    return records


def save_records(path: Path, records: list[PaperRecord]) -> None:
    payload = json.dumps([record.model_dump(mode="json") for record in records], ensure_ascii=False, indent=2) + "\n"
    RECORDS.validate_json(payload)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=path.parent,
                                         prefix=".headache-", suffix=".tmp", delete=False) as handle:
            temporary = Path(handle.name)
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def run_pipeline(path=DEFAULT_DATA, *, crawler=None, summarizer=None, lookback_days=DEFAULT_LOOKBACK_DAYS,
                 pubmed_limit=1000, max_new=None, query=DEFAULT_QUERY, fetch_only=False,
                 model=DEFAULT_MODEL, language="Traditional Chinese") -> RunStats:
    path = Path(path)
    policy = load_policy()
    records = load_records(path)
    eligible_records = [record for record in records if policy.eligible(
        record.article_metadata.source_society_or_journal, record.article_metadata.study_design)]
    removed_jif = len(records) - len(eligible_records)
    records = eligible_records
    if removed_jif and not fetch_only:
        save_records(path, records)
        LOGGER.info("Removed %s records failing the %s JIF/clinical-trial policy", removed_jif, policy.metric_year)
    known = {record.article_metadata.pmid_or_id for record in records}
    result = (crawler or Crawler()).collect(lookback_days=lookback_days, pubmed_limit=pubmed_limit, query=query)
    stats = RunStats(fetched=len(result.articles), source_failures=len(result.errors), removed_jif=removed_jif)
    if result.successful_sources == 0:
        raise RuntimeError("PubMed is unavailable; eligible records were preserved (fetch-only leaves JSON unchanged)")
    if not fetch_only:
        summarizer = summarizer or Summarizer(model=model, language=language)

    # Upgrade saved fallback abstracts after a key is added, even outside the fetch
    # window. The JIF gate also applies to historical and seed records.
    pending = []
    if not fetch_only and summarizer.uses_ai:
        for index, record in enumerate(records):
            if is_fallback(record):
                raw = record.structured_summary.methods
                article = Article(metadata=record.article_metadata,
                                  url=f"https://pubmed.ncbi.nlm.nih.gov/{record.article_metadata.pmid_or_id}/",
                                  abstract="" if raw.startswith("Not reported: PubMed provides no abstract") else raw)
                pending.append((article, index))

    seen = set(known)
    for article in sorted(result.articles, key=lambda a: (
            "" if a.metadata.publication_date == "Not reported" else a.metadata.publication_date,
            a.metadata.pmid_or_id), reverse=True):
        identity = article.metadata.pmid_or_id
        if identity in seen:
            stats.duplicates += 1
            continue
        seen.add(identity)
        pending.append((article, None))

    additions = []
    attempts = 0
    for article, existing_index in pending:
        if not policy.eligible(article.metadata.source_society_or_journal, article.metadata.study_design):
            stats.skipped_jif += 1
            continue
        # Apply the same screen to custom queries and old fallback upgrades,
        # before spending any model calls. Society membership never bypasses it.
        if not is_headache_topic(article.metadata.title, article.abstract):
            stats.skipped_off_topic += 1
            LOGGER.info("Skipping off-topic PMID %s", article.metadata.pmid_or_id)
            continue
        if fetch_only or (max_new is not None and attempts >= max_new):
            stats.deferred += 1
            continue
        if summarizer.uses_ai and not article.abstract.strip():
            stats.skipped_no_text += 1
            continue
        attempts += 1
        try:
            record = PaperRecord.model_validate(summarizer.summarize(article))
            if record.article_metadata != article.metadata:
                raise SummaryError("Summarization must preserve source metadata")
        except (OpenAIError, SummaryError, ValidationError) as exc:
            LOGGER.error("Summary failed for %s (%s); retryable next run", article.metadata.pmid_or_id, type(exc).__name__)
            stats.summary_failures += 1
            continue
        if existing_index is None:
            additions.append(record)
            stats.saved += 1
            stats.fallback_saved += int(is_fallback(record))
        else:
            records[existing_index] = record
            stats.upgraded += 1
        # Checkpoint every success; newest additions precede the existing archive.
        save_records(path, additions + records)
    return stats


def positive_int(value):
    number = int(value)
    if number < 1:
        raise argparse.ArgumentTypeError("must be at least 1")
    return number


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=DEFAULT_DATA)
    parser.add_argument("--lookback-days", type=positive_int, default=DEFAULT_LOOKBACK_DAYS)
    parser.add_argument("--pubmed-limit", type=positive_int, default=1000)
    parser.add_argument("--max-new", type=positive_int, default=None, help="Optional cap on new-record/upgrade attempts per run")
    parser.add_argument("--query", default=DEFAULT_QUERY)
    parser.add_argument("--model", default=os.environ.get("OPENAI_MODEL") or DEFAULT_MODEL)
    parser.add_argument("--language", default=os.environ.get("SUMMARY_LANGUAGE") or "Traditional Chinese")
    parser.add_argument("--fetch-only", action="store_true", help="Validate live sources without model calls or file writes")
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    try:
        stats = run_pipeline(args.output, lookback_days=args.lookback_days, pubmed_limit=args.pubmed_limit,
                             max_new=args.max_new, query=args.query, fetch_only=args.fetch_only,
                             model=args.model, language=args.language)
    except (OSError, ValueError, RuntimeError, OpenAIError) as exc:
        LOGGER.error("%s", str(exc) if isinstance(exc, RuntimeError) else
                     f"Pipeline stopped ({type(exc).__name__}); inspect configuration and the existing JSON")
        return 1
    print(json.dumps(asdict(stats)))
    return 1 if stats.source_failures or stats.summary_failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
