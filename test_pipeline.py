"""Offline tests: pytest. Network and real OpenAI clients are blocked by default."""

from datetime import date
import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, call, patch

import pytest
import requests
from pydantic import ValidationError

from src.crawler import (
    Crawler, CrawlResult, DEFAULT_QUERY, SourceError, TARGET_JOURNALS,
    TARGET_SOCIETIES, parse_pubmed, parse_search,
)
from src.main import load_records, main, run_pipeline, save_records
from src.models import Article, ArticleMetadata, PaperRecord, StructuredSummary
from src.summarizer import DEFAULT_MODEL, FALLBACK_NOTICE, Summarizer, SummaryError, is_fallback

FIELDS = {"methods", "inclusion_criteria", "exclusion_criteria", "number_of_inclusion",
          "mechanism_of_action_moa", "conclusion", "pitfalls_and_limitations"}
LIST_FIELDS = {"inclusion_criteria", "exclusion_criteria", "pitfalls_and_limitations"}
PUBMED_XML = b'''<PubmedArticleSet><PubmedArticle><MedlineCitation><PMID>12345</PMID><Article>
<Journal><JournalIssue><PubDate><Year>2026</Year><Month>Oct</Month></PubDate></JournalIssue><Title>Cephalalgia</Title></Journal>
<ArticleTitle>A <i>migraine</i> trial</ArticleTitle>
<Abstract><AbstractText Label="METHODS">Randomized <b>controlled</b> trial.</AbstractText><AbstractText Label="RESULTS">Results were reported.</AbstractText></Abstract>
<AuthorList><Author><AffiliationInfo><Affiliation>Taiwan Headache Society, Taipei</Affiliation></AffiliationInfo></Author></AuthorList>
<PublicationTypeList><PublicationType>Journal Article</PublicationType><PublicationType>Randomized Controlled Trial</PublicationType></PublicationTypeList>
</Article></MedlineCitation><PubmedData><ArticleIdList><ArticleId IdType="doi">10.1000/TEST</ArticleId></ArticleIdList>
<ReferenceList><Reference><ArticleIdList><ArticleId IdType="doi">10.9999/REFERENCE</ArticleId></ArticleIdList></Reference></ReferenceList>
</PubmedData></PubmedArticle></PubmedArticleSet>'''


@pytest.fixture(autouse=True)
def no_network_or_keys(monkeypatch):
    for name in ("OPENAI_API_KEY", "OPENAI_MODEL", "SUMMARY_LANGUAGE"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setattr(requests.Session, "request", Mock(side_effect=AssertionError("Tests must not use network")))
    monkeypatch.setattr("src.summarizer.OpenAI", Mock(side_effect=AssertionError("Use a mocked OpenAI client")))


def summary(**overrides):
    values = {key: ["Not reported"] if key in LIST_FIELDS else "Not reported" for key in FIELDS}
    values.update(overrides)
    return StructuredSummary(**values)


def article(pmid="12345", **overrides):
    values = dict(
        metadata=ArticleMetadata(pmid_or_id=pmid, doi="Not reported", title="A migraine trial",
                                 source_society_or_journal="Cephalalgia", publication_date="2026-10-16",
                                 study_design="Randomized Controlled Trial"),
        url=f"https://pubmed.ncbi.nlm.nih.gov/{pmid}/",
        abstract="A randomized controlled trial abstract.", affiliations=[],
    )
    values.update(overrides)
    return Article(**values)


def record(source=None):
    return PaperRecord(article_metadata=(source or article()).metadata, structured_summary=summary())


def mock_crawler(articles=None, *, successful=1, errors=None):
    crawler = Mock()
    crawler.collect.return_value = CrawlResult(articles or [], errors or [], successful)
    return crawler


def mock_ai(*, parsed=None, refusal=None, finish_reason="stop"):
    client = Mock()
    client.beta.chat.completions.parse.return_value = SimpleNamespace(choices=[
        SimpleNamespace(message=SimpleNamespace(parsed=parsed, refusal=refusal), finish_reason=finish_reason)
    ])
    return client


def ai_summarizer():
    return Summarizer(client=mock_ai(parsed=summary()))


def response(*, content=b"", status=200, headers=None):
    item = Mock(content=content, status_code=status, headers=headers or {})
    if status >= 400:
        item.raise_for_status.side_effect = requests.HTTPError(response=item)
    return item


def test_exact_seven_fields_and_three_list_fields():
    schema = StructuredSummary.model_json_schema()
    assert set(schema["properties"]) == set(schema["required"]) == FIELDS
    assert schema["additionalProperties"] is False
    assert {name for name, field in schema["properties"].items() if field["type"] == "array"} == LIST_FIELDS


@pytest.mark.parametrize("change", [
    {"methods": ""}, {"conclusion": "   "}, {"inclusion_criteria": []},
    {"exclusion_criteria": [" "]}, {"pitfalls_and_limitations": "Not reported"},
    {"number_of_inclusion": 577}, {"placebo": "old schema"},
])
def test_schema_rejects_empty_wrong_type_and_extra_fields(change):
    with pytest.raises(ValidationError):
        summary(**change)


def test_missing_required_fields_rejected():
    with pytest.raises(ValidationError):
        StructuredSummary(methods="Incomplete")


def test_metadata_fields_and_date_precision():
    assert set(ArticleMetadata.model_fields) == {
        "pmid_or_id", "doi", "title", "source_society_or_journal", "publication_date", "study_design",
    }
    values = article().metadata.model_dump()
    for date_value in ("2026", "2026-10", "2026-10-17", "Not reported"):
        assert ArticleMetadata(**{**values, "publication_date": date_value}).publication_date == date_value
    with pytest.raises(ValidationError):
        ArticleMetadata(**{**values, "publication_date": "2026-02-31"})


def test_exact_record_contract_and_internal_source_url_validation():
    assert set(PaperRecord.model_fields) == {"article_metadata", "structured_summary"}
    assert set(record().model_dump()) == {"article_metadata", "structured_summary"}
    for field in ArticleMetadata.model_json_schema()["properties"].values():
        assert field["type"] == "string"
    with pytest.raises(ValidationError):
        PaperRecord(**{**record().model_dump(), "summary_mode": "seed"})
    for url in ("javascript:alert(1)", "https://user:secret@example.org"):
        with pytest.raises(ValidationError):
            article(url=url)


def test_xml_metadata_abstract_study_design_and_affiliation():
    paper, = parse_pubmed(PUBMED_XML)
    assert paper.metadata.pmid_or_id == "12345"
    assert paper.metadata.title == "A migraine trial"
    assert paper.metadata.source_society_or_journal == "Cephalalgia | Taiwan Headache Society"
    assert paper.metadata.publication_date == "2026-10"
    assert paper.metadata.study_design == "Randomized Controlled Trial"
    assert paper.metadata.doi == "10.1000/test"
    assert paper.abstract == "METHODS: Randomized controlled trial.\nRESULTS: Results were reported."
    assert paper.affiliations == ["Taiwan Headache Society, Taipei"]


def test_reference_doi_never_used_as_own_doi():
    xml = PUBMED_XML.replace(b'<ArticleId IdType="doi">10.1000/TEST</ArticleId>', b"")
    assert parse_pubmed(xml)[0].metadata.doi == "Not reported"


def test_funding_publication_type_is_not_a_study_design():
    xml = PUBMED_XML.replace(b"Randomized Controlled Trial", b"Research Support, Non-U.S. Gov't")
    assert parse_pubmed(xml)[0].metadata.study_design == "Not reported"


def test_electronic_date_and_doi_fallback():
    xml = PUBMED_XML.replace(b'<ArticleId IdType="doi">10.1000/TEST</ArticleId>', b"")
    xml = xml.replace(b"</Article>", b'<ArticleDate><Year>2026</Year><Month>09</Month><Day>24</Day></ArticleDate><ELocationID EIdType="doi">10.1234/OWN</ELocationID></Article>')
    paper, = parse_pubmed(xml)
    assert paper.metadata.publication_date == "2026-09-24"
    assert paper.metadata.doi == "10.1234/own"


def test_undated_no_abstract_and_unknown_study_design():
    paper, = parse_pubmed(b'<PubmedArticleSet><PubmedArticle><MedlineCitation><PMID>1</PMID><Article><ArticleTitle>No abstract</ArticleTitle></Article></MedlineCitation></PubmedArticle></PubmedArticleSet>')
    assert paper.abstract == ""
    assert paper.metadata.publication_date == "Not reported"
    assert paper.metadata.study_design == "Not reported"


def test_search_xml_empty_ids_errors_and_notices(caplog):
    assert parse_search(b"<eSearchResult><IdList/></eSearchResult>") == []
    xml = b"<eSearchResult><IdList><Id>12345</Id></IdList><WarningList><QuotedPhraseNotFound>unmapped affiliation</QuotedPhraseNotFound></WarningList></eSearchResult>"
    assert parse_search(xml) == ["12345"]
    assert "QuotedPhraseNotFound" in caplog.text
    for xml in (b"<html/>", b"<eSearchResult><ERROR>bad request</ERROR></eSearchResult>", b"<eSearchResult/>"):
        with pytest.raises(SourceError):
            parse_search(xml)


def test_fetch_xml_error_is_not_empty_success():
    with pytest.raises(SourceError):
        parse_pubmed(b"<PubmedArticleSet><ERROR>bad request</ERROR></PubmedArticleSet>")


def test_search_targets_all_requested_journals_and_societies():
    assert "migraine[Title/Abstract]" in DEFAULT_QUERY
    for journal in TARGET_JOURNALS:
        assert f'"{journal}"[Journal]' in DEFAULT_QUERY
    for society in TARGET_SOCIETIES:
        assert f'"{society}"[Affiliation]' in DEFAULT_QUERY


@patch("src.crawler.time.sleep")
def test_ncbi_requests_are_keyless_xml_and_paced(sleep):
    session = Mock()
    session.get.side_effect = [
        response(content=b"<eSearchResult><IdList><Id>12345</Id></IdList></eSearchResult>"),
        response(content=PUBMED_XML),
    ]
    result = Crawler(session=session).fetch_pubmed(date(2026, 10, 11), date(2026, 10, 17))
    assert len(result) == 1
    assert sleep.call_args_list == [call(0.4), call(0.4)]
    for request in session.get.call_args_list:
        assert request.kwargs["params"]["retmode"] == "xml"
        assert "api_key" not in request.kwargs["params"]
        assert request.kwargs["timeout"] == (10, 45)
    assert session.get.call_args_list[0].kwargs["params"]["datetype"] == "pdat"


@patch("src.crawler.time.sleep")
def test_retry_requests_also_obey_rate_limit(sleep):
    session = Mock()
    session.get.side_effect = [
        response(status=429, headers={"Retry-After": "3"}),
        response(content=b"<eSearchResult><IdList/></eSearchResult>"),
    ]
    assert Crawler(session=session).fetch_pubmed(date.today(), date.today()) == []
    assert sleep.call_args_list == [call(0.4), call(3), call(0.4)]


@patch("src.crawler.time.sleep")
def test_network_retries_bounded_and_permanent_denial_not_retried(sleep):
    session = Mock()
    session.get.side_effect = requests.Timeout("timeout")
    with pytest.raises(SourceError):
        Crawler(session=session)._get("esearch.fcgi", params={})
    assert session.get.call_count == 3
    session.get.reset_mock(side_effect=True)
    session.get.return_value = response(status=403)
    with pytest.raises(SourceError):
        Crawler(session=session)._get("esearch.fcgi", params={})
    assert session.get.call_count == 1


@pytest.mark.parametrize("key", [None, "", " ", "\n\t"])
def test_missing_or_empty_key_returns_fallback_record_without_client(key, monkeypatch, caplog):
    if key is not None:
        monkeypatch.setenv("OPENAI_API_KEY", key)
    with patch("src.summarizer.OpenAI") as constructor:
        summarizer = Summarizer()
        paper = summarizer.summarize(article())
        constructor.assert_not_called()
    assert not summarizer.uses_ai
    assert "missing or empty" in caplog.text
    assert is_fallback(paper)
    assert paper.structured_summary.methods == paper.structured_summary.conclusion == article().abstract
    for key in FIELDS - {"methods", "conclusion"}:
        assert getattr(paper.structured_summary, key) == ([FALLBACK_NOTICE] if key in LIST_FIELDS else FALLBACK_NOTICE)


def test_fallback_retains_full_abstract_and_handles_absent_abstract():
    summarizer = Summarizer()
    assert summarizer.summarize(article(abstract="x" * 25000)).structured_summary.methods == "x" * 25000
    assert summarizer.summarize(article(abstract="")).structured_summary.methods.startswith("Not reported")


def test_key_present_uses_mini_and_strict_structured_outputs(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "test-placeholder-not-a-real-key")
    client = mock_ai(parsed=summary())
    with patch("src.summarizer.OpenAI", return_value=client) as constructor:
        paper = Summarizer().summarize(article())
    constructor.assert_called_once()
    assert not is_fallback(paper)
    kwargs = client.beta.chat.completions.parse.call_args.kwargs
    assert kwargs["response_format"] is StructuredSummary
    assert kwargs["model"] == "gpt-4o-mini"
    prompt = kwargs["messages"][0]["content"]
    assert "Not reported" in prompt and "Not applicable" in prompt
    assert "Never add arm counts" in prompt
    assert json.loads(kwargs["messages"][1]["content"])["source_text"] == article().abstract


@pytest.mark.parametrize("kwargs", [
    {"refusal": "No"}, {"parsed": summary(), "finish_reason": "length"}, {},
])
def test_refused_or_incomplete_ai_output_is_not_silently_fallback(kwargs):
    with pytest.raises(SummaryError):
        Summarizer(client=mock_ai(**kwargs)).summarize(article())


def test_source_truncation_disclosed_for_ai_only():
    client = mock_ai(parsed=summary())
    paper = Summarizer(client=client).summarize(article(abstract="x" * 25000))
    evidence = json.loads(client.beta.chat.completions.parse.call_args.kwargs["messages"][1]["content"])
    assert evidence["text_truncated"] is True
    assert len(evidence["source_text"]) == 24000
    assert paper.article_metadata == article().metadata


def test_no_key_pipeline_prepends_and_rerun_deduplicates(tmp_path):
    path = tmp_path / "papers.json"
    save_records(path, [record(article("old"))])
    crawl = mock_crawler([article("new"), article("new"), article("old")])
    first = run_pipeline(path, crawler=crawl)
    before = path.read_bytes()
    second = run_pipeline(path, crawler=crawl)
    assert (first.saved, first.fallback_saved, first.duplicates) == (1, 1, 2)
    assert second.saved == 0
    assert path.read_bytes() == before
    assert [paper.article_metadata.pmid_or_id for paper in load_records(path)] == ["new", "old"]


def test_new_records_order_is_preserved_at_front(tmp_path):
    path = tmp_path / "papers.json"
    save_records(path, [record(article("old"))])
    run_pipeline(path, crawler=mock_crawler([article("1"), article("2")]))
    assert [item.article_metadata.pmid_or_id for item in load_records(path)] == ["2", "1", "old"]


def test_added_key_upgrades_saved_fallback_without_duplicate_even_outside_window(tmp_path):
    path = tmp_path / "papers.json"
    seed = record(article("seed"))
    raw = Summarizer().summarize(article("old-fallback"))
    save_records(path, [raw, seed])
    stats = run_pipeline(path, crawler=mock_crawler(), summarizer=ai_summarizer())
    papers = load_records(path)
    assert (stats.saved, stats.upgraded) == (0, 1)
    assert [paper.article_metadata.pmid_or_id for paper in papers] == ["old-fallback", "seed"]
    assert [is_fallback(paper) for paper in papers] == [False, False]
    assert papers[1] == seed


def test_failed_upgrade_preserves_existing_raw_record(tmp_path):
    path = tmp_path / "papers.json"
    save_records(path, [Summarizer().summarize(article())])
    before = path.read_bytes()
    stats = run_pipeline(path, crawler=mock_crawler(), summarizer=Summarizer(client=mock_ai(refusal="No")))
    assert stats.summary_failures == 1
    assert path.read_bytes() == before


def test_partial_success_and_checkpoint_survive_later_failure(tmp_path):
    path = tmp_path / "papers.json"
    summarizer = Mock(uses_ai=True)
    summarizer.summarize.side_effect = [record(article("2")), SummaryError("refusal")]
    stats = run_pipeline(path, crawler=mock_crawler([article("2"), article("1")]), summarizer=summarizer)
    assert (stats.saved, stats.summary_failures) == (1, 1)
    assert load_records(path)[0].article_metadata.pmid_or_id == "2"
    summarizer.summarize.side_effect = [record(article("4")), KeyboardInterrupt()]
    with pytest.raises(KeyboardInterrupt):
        run_pipeline(path, crawler=mock_crawler([article("4"), article("3")]), summarizer=summarizer)
    assert load_records(path)[0].article_metadata.pmid_or_id == "4"


def test_fallback_empty_abstract_saved_but_ai_empty_abstract_skipped(tmp_path):
    path = tmp_path / "papers.json"
    assert run_pipeline(path, crawler=mock_crawler([article(abstract="")])).fallback_saved == 1
    stats = run_pipeline(path, crawler=mock_crawler(), summarizer=ai_summarizer())
    assert stats.skipped_no_text == 1
    assert is_fallback(load_records(path)[0])


def test_attempt_cap_and_failed_batch_duplicates(tmp_path):
    summarizer = Mock(uses_ai=True)
    summarizer.summarize.side_effect = SummaryError("refused")
    stats = run_pipeline(tmp_path / "papers.json", crawler=mock_crawler([article("2"), article("2"), article("1")]),
                         summarizer=summarizer, max_new=1)
    assert (stats.summary_failures, stats.duplicates, stats.deferred) == (1, 1, 1)
    assert summarizer.summarize.call_count == 1


def test_fetch_only_never_constructs_summarizer_or_writes(tmp_path):
    path = tmp_path / "papers.json"
    with patch("src.main.Summarizer") as constructor:
        stats = run_pipeline(path, crawler=mock_crawler([article()]), fetch_only=True)
        constructor.assert_not_called()
    assert stats.deferred == 1
    assert not path.exists()


def test_corrupt_json_preserved_before_network(tmp_path):
    path = tmp_path / "papers.json"
    path.write_text('{"corrupt":')
    crawl = mock_crawler([article()])
    with pytest.raises(ValidationError):
        run_pipeline(path, crawler=crawl)
    assert path.read_text() == '{"corrupt":'
    crawl.collect.assert_not_called()


def test_duplicate_existing_ids_rejected(tmp_path):
    path = tmp_path / "papers.json"
    save_records(path, [record(), record()])
    with pytest.raises(ValueError, match="duplicate"):
        load_records(path)


def test_atomic_replace_failure_keeps_existing_file(tmp_path):
    path = tmp_path / "papers.json"
    save_records(path, [record()])
    before = path.read_bytes()
    with patch("src.main.os.replace", side_effect=OSError("disk failure")), pytest.raises(OSError):
        save_records(path, [record(article("2"))])
    assert path.read_bytes() == before
    assert list(tmp_path.glob(".headache-*.tmp")) == []


def test_source_failure_preserves_seeds_and_cli_returns_nonzero(tmp_path):
    path = tmp_path / "papers.json"
    save_records(path, [record()])
    before = path.read_bytes()
    crawl = mock_crawler(successful=0, errors=["PubMed unavailable"])
    with patch("src.main.Crawler", return_value=crawl):
        assert main(["--output", str(path)]) == 1
    assert path.read_bytes() == before


def test_cli_no_key_succeeds_and_writes_fallback(tmp_path):
    path = tmp_path / "papers.json"
    with patch("src.main.Crawler", return_value=mock_crawler([article()])):
        assert main(["--output", str(path)]) == 0
    assert is_fallback(load_records(path)[0])


def test_repository_has_three_source_verified_seed_records():
    root = Path(__file__).parent
    papers = load_records(root / "data/headache_papers.json")
    seeds = {paper.article_metadata.pmid_or_id: paper for paper in papers}
    assert set(seeds) == {"29471679", "24144382", "23390177"}
    source = {item.metadata.pmid_or_id: item for item in parse_pubmed(
        (root / "tests/fixtures/seed_pubmed.xml").read_bytes())}
    for pmid, seed in seeds.items():
        assert seed.article_metadata == source[pmid].metadata
        assert not is_fallback(seed)
    assert seeds["29471679"].article_metadata.doi == "10.1177/0333102418759786"
    assert "577" in source["29471679"].abstract
    assert "577" in seeds["29471679"].structured_summary.number_of_inclusion
    assert "570" in seeds["29471679"].structured_summary.number_of_inclusion
    assert seeds["24144382"].structured_summary.number_of_inclusion.startswith("Not applicable")
    assert seeds["23390177"].article_metadata.doi == "10.1212/wnl.0b013e3182825055"
    assert "67" in seeds["23390177"].structured_summary.number_of_inclusion


def test_default_query_matches_requested_coverage_exactly():
    expected = '(("Cephalalgia"[Journal] OR "The Journal of Headache and Pain"[Journal] OR "Headache"[Journal] OR "Neurology"[Journal] OR "Annals of Neurology"[Journal] OR "Acta Neurologica Taiwanica"[Journal] OR "Intern Med"[Journal] OR "Japanese Headache Society"[Affiliation] OR "Taiwan Headache Society"[Affiliation]) AND (migraine[Title/Abstract] OR headache[Title/Abstract] OR trigeminal[Title/Abstract]) AND hasabstract[text])'
    assert DEFAULT_QUERY == expected


def test_default_window_is_fourteen_calendar_days_inclusive():
    crawler = Crawler()
    with patch.object(crawler, "fetch_pubmed", return_value=[]) as fetch:
        crawler.collect(today=date(2026, 10, 17))
    assert fetch.call_args.args[:2] == (date(2026, 10, 4), date(2026, 10, 17))


def test_cli_passes_fourteen_day_default(tmp_path):
    crawler = mock_crawler()
    with patch("src.main.Crawler", return_value=crawler):
        assert main(["--output", str(tmp_path / "papers.json")]) == 0
    assert crawler.collect.call_args.kwargs["lookback_days"] == 14


def test_utf8_persistence_has_exact_wire_shape(tmp_path):
    path = tmp_path / "papers.json"
    item = record()
    item.structured_summary.conclusion = "偏頭痛研究結論"
    save_records(path, [item])
    text = path.read_text(encoding="utf-8")
    assert "偏頭痛研究結論" in text
    assert '\n  {\n    "article_metadata"' in text
    assert set(json.loads(text)[0]) == {"article_metadata", "structured_summary"}
