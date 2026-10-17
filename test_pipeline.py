"""Offline tests: pytest. Network and real OpenAI clients are blocked by default."""

from datetime import date
import json
import re
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, call, patch

import pytest
import requests
from pydantic import ValidationError

from src.crawler import (
    Crawler, CrawlResult, DEFAULT_QUERY, SOCIETY_QUERY, SourceError, TARGET_JOURNALS,
    TARGET_SOCIETIES, classify_societies, is_headache_topic, parse_pubmed, parse_search,
)
from src.headache_scope import CATALOG, HEADACHE_QUERY, SEARCH_TERMS, SyndromeCatalog
from src.main import load_records, main, run_pipeline, save_records
from src.models import Article, ArticleMetadata, PaperRecord, StructuredSummary
from src.summarizer import DEFAULT_MODEL, FALLBACK_NOTICE, Summarizer, SummaryError, is_fallback

FIELDS = {"methods", "inclusion_criteria", "exclusion_criteria", "number_of_inclusion",
          "participants_feature", "conclusion", "pitfalls_and_limitations"}
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
    monkeypatch.setattr("src.main.utc_today", lambda: date(2026, 10, 17))
    monkeypatch.setattr("src.crawler.utc_today", lambda: date(2026, 10, 17))


def summary(**overrides):
    values = {key: ["Not reported"] if key in LIST_FIELDS else "Not reported" for key in FIELDS}
    values.update(overrides)
    return StructuredSummary(**values)


def article(pmid="12345", **overrides):
    values = dict(
        metadata=ArticleMetadata(pmid_or_id=pmid, doi="Not reported", title="A migraine trial",
                                 source_society_or_journal="JAMA Neurology", publication_date="2026-10-16",
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
    {"number_of_inclusion": 577}, {"participants_feature": ""},
    {"participants_feature": ["Adults with migraine"]}, {"placebo": "old schema"},
])
def test_schema_rejects_empty_wrong_type_and_extra_fields(change):
    with pytest.raises(ValidationError):
        summary(**change)


def test_missing_required_fields_rejected():
    with pytest.raises(ValidationError):
        StructuredSummary(methods="Incomplete")


def test_participants_feature_replaces_moa_without_accepting_old_content():
    values = summary(participants_feature="成人陣發性偏頭痛患者；平均年齡 42 歲，女性占 80%。").model_dump()
    assert len(values) == 7 and "participants_feature" in values
    assert "mechanism_of_action_moa" not in values
    old = {key: value for key, value in values.items() if key != "participants_feature"}
    old["mechanism_of_action_moa"] = "Blocks a receptor"
    with pytest.raises(ValidationError):
        StructuredSummary.model_validate(old)
    with pytest.raises(ValidationError):
        StructuredSummary.model_validate({**values, "mechanism_of_action_moa": "Blocks a receptor"})


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
    assert paper.metadata.source_society_or_journal == "Cephalalgia | International Headache Society | Taiwan Headache Society"
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
        assert f'"{journal}"[Journal]' in SOCIETY_QUERY
    for society in TARGET_SOCIETIES:
        assert f'"{society}"[Affiliation]' in SOCIETY_QUERY


@patch("src.crawler.time.sleep")
def test_ncbi_requests_are_keyless_xml_and_paced(sleep):
    session = Mock()
    session.post.return_value = response(content=b"<eSearchResult><IdList><Id>12345</Id></IdList></eSearchResult>")
    session.get.return_value = response(content=PUBMED_XML.replace(b"<Title>Cephalalgia</Title>", b"<Title>JAMA Neurology</Title>"))
    result = Crawler(session=session).fetch_pubmed(date(2026, 10, 11), date(2026, 10, 17))
    assert len(result) == 1
    assert sleep.call_args_list == [call(0.4), call(0.4)]
    for request in [session.post.call_args, session.get.call_args]:
        params = request.kwargs.get("data", request.kwargs.get("params"))
        assert params["retmode"] == "xml"
        assert "api_key" not in params
        assert request.kwargs["timeout"] == (10, 45)
    assert session.post.call_args.kwargs["data"]["datetype"] == "pdat"
    assert session.post.call_args.args[0].endswith('/esearch.fcgi')
    assert 'params' not in session.post.call_args.kwargs


@patch("src.crawler.time.sleep")
def test_retry_requests_also_obey_rate_limit(sleep):
    session = Mock()
    session.post.side_effect = [
        response(status=429, headers={"Retry-After": "3"}),
        response(content=b"<eSearchResult><IdList/></eSearchResult>"),
    ]
    assert Crawler(session=session).fetch_pubmed(date.today(), date.today()) == []
    assert sleep.call_args_list == [call(0.4), call(3), call(0.4)]


@patch("src.crawler.time.sleep")
def test_network_retries_bounded_and_permanent_denial_not_retried(sleep):
    session = Mock()
    session.post.side_effect = requests.Timeout("timeout")
    with pytest.raises(SourceError):
        Crawler(session=session)._request("esearch.fcgi", params={})
    assert session.post.call_count == 3
    session.post.reset_mock(side_effect=True)
    session.post.return_value = response(status=403)
    with pytest.raises(SourceError):
        Crawler(session=session)._request("esearch.fcgi", params={})
    assert session.post.call_count == 1


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


def test_mocked_ai_extracts_participant_features_and_preserves_missing_information_rules():
    source = article(abstract="Adults with episodic migraine had a mean age of 42 years; 80% were women.")
    features = "成人陣發性偏頭痛患者；平均年齡 42 歲，女性占 80%。"
    client = mock_ai(parsed=summary(participants_feature=features))
    result = Summarizer(client=client).summarize(source)
    assert result.structured_summary.participants_feature == features
    assert result.article_metadata == source.metadata
    call_args = client.beta.chat.completions.parse.call_args.kwargs
    schema = call_args['response_format'].model_json_schema()
    assert 'participants_feature' in schema['required']
    assert 'mechanism_of_action_moa' not in schema['properties']
    prompt = call_args['messages'][0]['content']
    assert 'eligibility-only age ranges' in prompt
    assert 'Do not infer ethnicity' in prompt
    assert 'post-treatment outcomes as baseline features' in prompt
    assert 'Not reported' in prompt and 'Not applicable' in prompt


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


def test_historical_seed_fixtures_remain_source_verified():
    root = Path(__file__).parent
    papers = load_records(root / "tests/fixtures/historical_seeds.json")
    seed_ids = {"29471679", "24144382", "23390177"}
    # Historical examples are retained only as test fixtures, never exempted
    # from the production JIF rule.
    seeds = {paper.article_metadata.pmid_or_id: paper for paper in papers
             if paper.article_metadata.pmid_or_id in seed_ids}
    assert set(seeds) == seed_ids
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


def test_default_query_requires_topic_and_abstract_across_all_sources():
    assert DEFAULT_QUERY.startswith('((')
    assert ') AND (migraine[Title/Abstract]' in DEFAULT_QUERY
    assert DEFAULT_QUERY.endswith('AND hasabstract[text])')
    # Generic trigeminal appears only as a craniofacial-location qualifier for
    # central neuropathic pain, never as an independent topic branch.
    assert 'OR trigeminal[Title/Abstract])))' in DEFAULT_QUERY
    assert '"trigeminal neuralgia"[Title/Abstract]' in DEFAULT_QUERY
    assert '"JAMA Neurology"[Journal]' in DEFAULT_QUERY
    assert '"Cephalalgia"[Journal]' in DEFAULT_QUERY
    assert '"Clinical Trial"[Publication Type]' in DEFAULT_QUERY
    assert '"Japan"[Affiliation]' not in DEFAULT_QUERY



def test_default_window_is_thirty_calendar_days_inclusive():
    crawler = Crawler()
    with patch.object(crawler, "fetch_pubmed", return_value=[]) as fetch:
        crawler.collect(today=date(2026, 10, 17))
    assert fetch.call_args_list[0].args[:2] == (date(2026, 9, 18), date(2026, 10, 17))
    assert fetch.call_args_list[1].args[:2] == (date(2016, 10, 17), date(2026, 9, 17))
    assert fetch.call_args_list[1].kwargs['trials_only'] is True


def test_cli_passes_thirty_day_default(tmp_path):
    crawler = mock_crawler()
    with patch("src.main.Crawler", return_value=crawler):
        assert main(["--output", str(tmp_path / "papers.json")]) == 0
    assert crawler.collect.call_args.kwargs["lookback_days"] == 30
    assert crawler.collect.call_args.kwargs["trial_lookback_years"] == 10
    assert crawler.collect.call_args.kwargs["pubmed_limit"] is None


def test_utf8_persistence_has_exact_wire_shape(tmp_path):
    path = tmp_path / "papers.json"
    item = record()
    item.structured_summary.conclusion = "偏頭痛研究結論"
    save_records(path, [item])
    text = path.read_text(encoding="utf-8")
    assert "偏頭痛研究結論" in text
    assert '\n  {\n    "article_metadata"' in text
    assert set(json.loads(text)[0]) == {"article_metadata", "structured_summary"}


@pytest.mark.parametrize("journal,group", [
    ("Cephalalgia Reports", "International Headache Society"),
    ("Cephalalgia : an international journal of headache", "International Headache Society"),
    ("J Headache Pain", "European Headache Federation"),
    ("The Journal of Headache and Pain", "European Headache Federation"),
    ("Headache", "American Headache Society"),
    ("Annals of Neurology", "American Neurological Association"),
    ("Neurology", "American Academy of Neurology"),
    ("Japanese Journal of Headache", "Japanese Headache Society"),
    ("Neurology and Clinical Neuroscience", "Japanese Headache Society"),
    ("Intern Med", "Japanese Headache Society"),
    ("Internal medicine (Tokyo, Japan)", "Japanese Headache Society"),
    ("Rinsho shinkeigaku = Clinical neurology", "Japanese Headache Society"),
    ("Acta Neurologica Taiwanica", "Taiwan Headache Society"),
    ("Acta Neurol Taiwan", "Taiwan Headache Society"),
    ("Journal of the Chinese Medical Association : JCMA", "Taiwan Headache Society"),
    ("J Chin Med Assoc", "Taiwan Headache Society"),
])
def test_source_group_aliases(journal, group):
    assert group in classify_societies(journal)


@pytest.mark.parametrize("journal", ["Cephalalgia", "J Headache Pain", "Headache"])
def test_japanese_affiliation_keeps_primary_and_jhs_group(journal):
    groups = classify_societies(journal, affiliations=["Department of Neurology, Tokyo, Japan"])
    assert "Japanese Headache Society" in groups
    assert len(groups) == 2


def test_society_evidence_in_title_abstract_and_collective_author():
    for evidence in ({"title": "Japanese Headache Society recommendations"},
                     {"abstract": "Prepared by the Japanese Headache Society."},
                     {"authors": ["Japanese Headache Society"]}):
        assert classify_societies("Unlisted journal", **evidence) == ["Japanese Headache Society"]
    assert classify_societies("Unlisted journal", authors=["Taiwan Headache Society"]) == ["Taiwan Headache Society"]


def test_china_or_chinese_does_not_imply_taiwan_society():
    assert classify_societies("Chinese Medical Journal", affiliations=["Beijing, China"]) == []
    assert classify_societies("Neurology", affiliations=["Japan"]) == ["American Academy of Neurology"]
    assert "Japanese Headache Society" not in classify_societies("Headache", affiliations=["Japanology Center, USA"])


@pytest.mark.parametrize("title,abstract", [
    ("Trigeminal neuralgia-associated affective behavior", "A circuit study."),
    ("Treatment of trigeminal-neuralgia", "A randomized trial."),
    ("Hemicrania continua", "A clinical report."),
    ("SUNCT management", "A review."),
    ("Headaches after cardiac stress testing", "A study."),
    ("A preventive treatment trial", "OBJECTIVE: To evaluate reduction of monthly migraine days.\nMETHODS: A trial."),
    ("A preventive treatment trial", "BACKGROUND AND OBJECTIVES: Treatment is challenging. We aimed to compare migraine prevention strategies.\nMETHODS: A trial."),
    ("Microemboli-induced spreading depolarization", "A woman developed daily episodes of migraine aura after surgery. Microemboli were detected. Spreading depolarization underlies migraine aura."),
    ("Pregnancy outcomes following galcanezumab exposure", "OBJECTIVE: Describe pregnancy outcomes.\nBACKGROUND: Galcanezumab is indicated for the preventive treatment of migraine."),
])
def test_headache_and_trigeminal_neuralgia_topics_are_included(title, abstract):
    assert is_headache_topic(title, abstract)


@pytest.mark.parametrize("title,abstract", [
    ("Trigeminal nerve anatomy", "We examined trigeminal sensory neurons."),
    ("Dental pain and trigeminal neuropathy", "The trigeminal ganglion was studied."),
    ("Cerebral involvement in NMOSD", "A woman developed acute headache, seizures, and aphasia. This case illustrates a rare cerebral manifestation of NMOSD."),
    ("Viral infection outcomes", "OBJECTIVE: To evaluate infection outcomes.\nRESULTS: Symptoms included fever, headache, and fatigue."),
    ("A cancer treatment trial", "OBJECTIVE: We evaluated adverse events including headache, nausea, and fatigue.\nCONCLUSION: Treatment was effective."),
    ("A protein study in intracranial hypertension", "OBJECTIVE: Evaluate blood-brain barrier markers.\nRESULTS: Markers correlated with headache scores."),
    ("Amyotrophy due to spinal CSF leaks", "BACKGROUND AND OBJECTIVES: CSF leaks cause headache. The objective of this study was to characterize amyotrophy.\nRESULTS: Some patients had a history of headache."),
    ("An unrelated paper", ""),
    ("Infection management", "Symptoms included fever and headache. Headache improved with treatment."),
])
def test_incidental_mentions_and_generic_trigeminal_are_excluded(title, abstract):
    assert not is_headache_topic(title, abstract)


def test_pubmed_objective_category_without_label_supports_scope_check():
    xml = PUBMED_XML.replace(b'A <i>migraine</i> trial', b'A preventive treatment trial')
    xml = xml.replace(b'Label="METHODS">Randomized <b>controlled</b> trial.',
                      b'NlmCategory="OBJECTIVE">To evaluate migraine prevention.')
    item = parse_pubmed(xml)[0]
    assert item.abstract.startswith("OBJECTIVE:")
    assert is_headache_topic(item.metadata.title, item.abstract)


@patch("src.crawler.time.sleep")
def test_custom_search_cannot_bypass_topic_screen(sleep):
    session = Mock()
    irrelevant = PUBMED_XML.replace(b'A <i>migraine</i> trial', b'Trigeminal nerve anatomy')
    session.post.return_value = response(content=b'<eSearchResult><IdList><Id>12345</Id></IdList></eSearchResult>')
    session.get.return_value = response(content=irrelevant)
    assert Crawler(session).fetch_pubmed(date(2026, 9, 18), date(2026, 10, 17), query="neurology") == []


def test_pipeline_filters_before_ai_including_fallback_upgrades(tmp_path):
    unrelated = article()
    unrelated.metadata.title = "A cancer trial"
    unrelated.abstract = "RESULTS: Adverse events included headache and nausea."
    path = tmp_path / "papers.json"
    save_records(path, [Summarizer().summarize(unrelated)])
    new = unrelated.model_copy(deep=True)
    new.metadata.pmid_or_id = "999"
    ai = Mock(uses_ai=True)
    stats = run_pipeline(path, crawler=mock_crawler([new]), summarizer=ai)
    assert stats.skipped_off_topic == 2
    assert stats.saved == stats.upgraded == 0
    ai.summarize.assert_not_called()


def test_pipeline_explicit_window_override(tmp_path):
    crawler = mock_crawler()
    with patch("src.main.Crawler", return_value=crawler):
        assert main(["--output", str(tmp_path / "papers.json"), "--lookback-days", "45"]) == 0
    assert crawler.collect.call_args.kwargs["lookback_days"] == 45


def test_scope_regressions_against_verbatim_pubmed_abstracts():
    source = parse_pubmed((Path(__file__).parent / "tests/fixtures/scope_pubmed.xml").read_bytes())
    retained = {a.metadata.pmid_or_id for a in source if is_headache_topic(a.metadata.title, a.abstract)}
    # Migraine-specific therapy safety and a migraine-aura case are in scope;
    # myelopathy/NMOSD or generic SIH/IIH studies are not automatically so.
    # Explicit primary headache outcomes qualify; a symptom-list case does not.
    assert retained == {"42856055", "42664477", "42775430", "42768895", "42723270", "42619634"}


def test_xml_collective_society_author_retains_cross_group_mapping():
    xml = PUBMED_XML.replace(b"Taiwan Headache Society, Taipei", b"Tokyo, Japan")
    xml = xml.replace(b"<Author>", b"<Author><CollectiveName>Taiwan Headache Society</CollectiveName>")
    source = parse_pubmed(xml)[0].metadata.source_society_or_journal
    assert source == "Cephalalgia | International Headache Society | Japanese Headache Society | Taiwan Headache Society"


def test_jif_policy_is_strict_year_specific_and_uses_exact_aliases():
    from src.journal_metrics import JournalPolicy, load_policy
    policy = load_policy()
    for value, expected in ((9.999, False), (10, False), (10.0001, True), (None, False)):
        data = policy.model_dump(mode='json')
        data['journals'][0]['jif'] = value
        custom = JournalPolicy.model_validate(data)
        assert custom.eligible(custom.journals[0].journal) is expected
    assert policy.eligible('The Lancet. Neurology | Taiwan Headache Society')
    assert policy.eligible('JAMA Neurol')
    assert policy.eligible('BMJ (Clinical research ed.)')
    for source in ('Neurology', 'Cephalalgia', 'JAMA Cardiology', 'Nature unknown journal',
                   'Headache | JAMA Neurology', 'Unknown | International Headache Society'):
        assert not policy.eligible(source)
    data = policy.model_dump(mode='json')
    data['journals'][0]['metric_year'] -= 1
    assert not JournalPolicy.model_validate(data).eligible(data['journals'][0]['journal'])


@pytest.mark.parametrize('change', [
    {'metric': 'CiteScore'}, {'metric': '5-year Journal Impact Factor'},
    {'threshold_exclusive': 14}, {'jcr_release_year': 2025},
])
def test_invalid_jif_policy_fails_closed(change):
    from src.journal_metrics import JournalPolicy, load_policy
    data = load_policy().model_dump(mode='json')
    data.update(change)
    with pytest.raises(ValidationError):
        JournalPolicy.model_validate(data)


def test_jif_invalid_metrics_and_ambiguous_aliases_rejected():
    from src.journal_metrics import JournalPolicy, load_policy
    for value in ('NaN', 'Infinity', -1, 'unverified'):
        data = load_policy().model_dump(mode='json')
        data['journals'][0]['jif'] = value
        with pytest.raises(ValidationError):
            JournalPolicy.model_validate(data)
    data = load_policy().model_dump(mode='json')
    data['journals'][0]['aliases'].append(data['journals'][1]['journal'])
    with pytest.raises(ValidationError, match='unambiguous'):
        JournalPolicy.model_validate(data)


def test_jif_empty_allowlist_cannot_turn_into_unrestricted_query():
    from src.journal_metrics import JournalPolicy, load_policy
    data = load_policy().model_dump(mode='json')
    data['journals'] = []
    assert JournalPolicy.model_validate(data).pubmed_clause() == '0[PMID]'


@patch('src.crawler.time.sleep')
def test_custom_query_and_society_cannot_bypass_jif_gate(sleep):
    session = Mock()
    session.post.return_value = response(content=b'<eSearchResult><IdList><Id>12345</Id></IdList></eSearchResult>')
    session.get.return_value = response(content=PUBMED_XML.replace(b"Randomized Controlled Trial", b"Observational Study"))
    assert Crawler(session).fetch_pubmed(date(2026, 9, 18), date(2026, 10, 17), query='headache') == []
    assert '(headache) AND (' in session.post.call_args.kwargs['data']['term']
    assert '"JAMA Neurology"[Journal]' in session.post.call_args.kwargs['data']['term']


def test_pipeline_removes_nonqualifying_archive_seeds_and_never_calls_ai(tmp_path):
    path = tmp_path / 'papers.json'
    low = article('29471679')
    low.metadata.source_society_or_journal = 'Cephalalgia | International Headache Society'
    low.metadata.study_design = 'Observational Study'
    save_records(path, [record(low), Summarizer().summarize(article('allowed'))])
    ai = ai_summarizer()
    stats = run_pipeline(path, crawler=mock_crawler([low]), summarizer=ai)
    assert (stats.removed_jif, stats.skipped_jif, stats.upgraded) == (1, 1, 1)
    assert [p.article_metadata.pmid_or_id for p in load_records(path)] == ['allowed']
    assert ai.client.beta.chat.completions.parse.call_count == 1


def test_jif_pruning_persists_even_without_new_papers_or_api_key(tmp_path):
    low = article()
    low.metadata.source_society_or_journal = 'Unknown journal'
    path = tmp_path / 'papers.json'
    save_records(path, [record(low)])
    stats = run_pipeline(path, crawler=mock_crawler())
    assert stats.removed_jif == 1
    assert load_records(path) == []
    assert path.read_text() == '[]\n'


def test_fetch_only_preserves_ineligible_archive_without_ai_calls(tmp_path):
    low = article()
    low.metadata.source_society_or_journal = 'Headache'
    low.metadata.study_design = 'Observational Study'
    path = tmp_path / 'papers.json'
    save_records(path, [record(low)])
    before = path.read_bytes()
    with patch('src.main.Summarizer') as constructor:
        stats = run_pipeline(path, crawler=mock_crawler([low]), fetch_only=True)
        constructor.assert_not_called()
    assert (stats.removed_jif, stats.skipped_jif) == (1, 1)
    assert path.read_bytes() == before


def test_jif_pruning_is_not_dependent_on_pubmed_availability(tmp_path):
    low = article()
    low.metadata.source_society_or_journal = 'Headache'
    low.metadata.study_design = 'Observational Study'
    path = tmp_path / 'papers.json'
    save_records(path, [record(low), record(article('keep'))])
    with pytest.raises(RuntimeError):
        run_pipeline(path, crawler=mock_crawler(successful=0, errors=['PubMed unavailable']))
    assert [p.article_metadata.pmid_or_id for p in load_records(path)] == ['keep']


def test_invalid_jif_registry_stops_before_network_or_json_changes(tmp_path):
    path = tmp_path / 'papers.json'
    save_records(path, [record()])
    before = path.read_bytes()
    crawler = mock_crawler()
    with patch('src.main.load_policy', side_effect=ValueError('Invalid metrics')), pytest.raises(ValueError):
        run_pipeline(path, crawler=crawler)
    crawler.collect.assert_not_called()
    assert path.read_bytes() == before


def test_every_published_record_passes_current_jif_policy():
    from src.journal_metrics import load_policy
    policy = load_policy()
    records = load_records(Path(__file__).parent / 'data/headache_papers.json')
    assert all(policy.eligible(p.article_metadata.source_society_or_journal, p.article_metadata.study_design) for p in records)


def test_migraine_therapy_class_safety_requires_explicit_indication():
    title = "Early pregnancy use of anti-CGRP monoclonal antibodies and spontaneous abortion risk"
    assert is_headache_topic(title, "IMPORTANCE: Anti-CGRP mAbs are effective for migraine prevention.\nOBJECTIVE: Evaluate pregnancy safety.")
    assert not is_headache_topic(title, "OBJECTIVE: Evaluate CGRP signaling in placental development.")


def test_jif_query_includes_indexed_aliases_for_full_titles():
    from src.journal_metrics import load_policy
    clause = load_policy().pubmed_clause()
    assert '"BMJ"[Journal]' in clause
    assert '"Lancet Neurol"[Journal]' in clause


@pytest.mark.parametrize('design,expected', [
    ('Clinical Trial', True), ('Clinical Trial, Phase I', True),
    ('Clinical Trial, Phase II', True), ('Clinical Trial, Phase III', True),
    ('Clinical Trial, Phase IV', True), ('Controlled Clinical Trial', True),
    ('Randomized Controlled Trial', True), ('Pragmatic Clinical Trial', True),
    ('Adaptive Clinical Trial', True), ('Equivalence Trial', True),
    ('Multicenter Study; Randomized Controlled Trial', True),
    (' clinical   trial ; Multicenter Study ', True),
    ('Not reported', False), ('Clinical Study', False), ('Multicenter Study', False),
    ('Clinical Trial Protocol', False), ('Clinical Trials as Topic', False),
    ('Observational Study', False), ('Clinical Trial, Veterinary', False),
    ('Review', False), ('Systematic Review', False), ('Meta-Analysis', False),
    ('A randomized controlled trial of migraine prevention', False),
    ('Randomized Controlled Trial; Clinical Trial Protocol', False),
    ('Clinical Trial; Observational Study', False), ('Clinical Trial; Review', False),
    ('Randomized Controlled Trial; Meta-Analysis', False),
    ('Clinical Trial; Clinical Trial, Veterinary', False),
])
def test_lower_jif_requires_exact_pubmed_clinical_trial_type(design, expected):
    from src.journal_metrics import is_clinical_trial, load_policy
    assert is_clinical_trial(design) is expected
    policy = load_policy()
    assert policy.eligible('Cephalalgia | International Headache Society', design) is expected
    # Study design restrictions apply only to lower-JIF journals.
    assert policy.eligible('JAMA Neurology', design)


@pytest.mark.parametrize('value,year,expected', [
    (0, 2025, True), (9.999, 2025, True), (10, 2025, False),
    (10.0001, 2025, True), (None, 2025, False), (5.8, 2024, False),
])
def test_trial_exception_never_bypasses_metric_verification(value, year, expected):
    from src.journal_metrics import JournalPolicy, load_policy
    data = load_policy().model_dump(mode='json')
    data['journals'][0].update(jif=value, metric_year=year)
    policy = JournalPolicy.model_validate(data)
    assert policy.eligible(data['journals'][0]['journal'], 'Clinical Trial') is expected
    assert not policy.eligible('Unknown | JAMA Neurology', 'Clinical Trial')


def test_pubmed_query_keeps_low_jif_and_trial_requirement_in_same_branch():
    from src.journal_metrics import JournalPolicy, load_policy
    data = load_policy().model_dump(mode='json')
    # All low entries must be paired with trial types; high entries have no design gate.
    high = next(entry for entry in data['journals'] if entry['journal'] == 'JAMA Neurology')
    low = next(entry for entry in data['journals'] if entry['journal'] == 'Neurology')
    data['journals'] = [high, low]
    clause = JournalPolicy.model_validate(data).pubmed_clause()
    assert clause.startswith('(("JAMA Neurology"[Journal] OR "JAMA Neurol"[Journal]) OR (("Neurology"[Journal]) AND (')
    assert '"Controlled Clinical Trial"[Publication Type]' in clause
    assert 'NOT ("Clinical Trial Protocol"[Publication Type]' in clause
    data['journals'] = [dict(low, jif=10)]
    assert JournalPolicy.model_validate(data).pubmed_clause() == '0[PMID]'


@patch('src.crawler.time.sleep')
def test_crawler_retains_verified_lower_jif_trial(sleep):
    session = Mock()
    session.post.return_value = response(content=b'<eSearchResult><IdList><Id>12345</Id></IdList></eSearchResult>')
    session.get.return_value = response(content=PUBMED_XML)
    fetched = Crawler(session).fetch_pubmed(date(2026, 9, 18), date(2026, 10, 17))
    assert len(fetched) == 1
    assert fetched[0].metadata.study_design == 'Randomized Controlled Trial'
    term = session.post.call_args.kwargs['data']['term']
    assert term.count('"JAMA Neurology"[Journal]') == 1


def test_pipeline_saves_and_upgrades_low_jif_trials_without_accepting_title_claims(tmp_path):
    trial = article('trial')
    trial.metadata.source_society_or_journal = 'Headache'
    nontrial = article('nontrial', abstract='A randomized controlled trial for migraine is described.')
    nontrial.metadata.source_society_or_journal = 'Headache'
    nontrial.metadata.study_design = 'Not reported'
    path = tmp_path / 'papers.json'
    stats = run_pipeline(path, crawler=mock_crawler([trial, nontrial]))
    assert (stats.saved, stats.fallback_saved, stats.skipped_jif) == (1, 1, 1)
    assert [p.article_metadata.pmid_or_id for p in load_records(path)] == ['trial']
    ai = ai_summarizer()
    stats = run_pipeline(path, crawler=mock_crawler([trial, nontrial]), summarizer=ai)
    assert (stats.upgraded, stats.duplicates, stats.skipped_jif, stats.removed_jif) == (1, 1, 1, 0)
    ai.client.beta.chat.completions.parse.assert_called_once()
    assert not is_fallback(load_records(path)[0])


def test_low_jif_trial_must_still_pass_topic_gate_before_ai(tmp_path):
    trial = article()
    trial.metadata.source_society_or_journal = 'Neurology'
    trial.metadata.title = 'Treatment of epilepsy: a randomized controlled trial'
    trial.abstract = 'Seizures improved. Adverse events included nausea and headache.'
    ai = ai_summarizer()
    stats = run_pipeline(tmp_path / 'papers.json', crawler=mock_crawler([trial]), summarizer=ai)
    assert (stats.skipped_off_topic, stats.saved) == (1, 0)
    ai.client.beta.chat.completions.parse.assert_not_called()


# Scope fixtures describe research topics, not patient diagnosis criteria.


@pytest.mark.parametrize('entry', CATALOG.entries, ids=lambda entry: entry.code)
def test_every_supplied_syndrome_can_be_retrieved_with_appropriate_context(entry):
    context = {'migraine': 'Migraine-related ', 'headache': 'Headache in ',
               'ocular_pain': 'Ocular pain in ', 'craniofacial_pain': 'Facial pain in ',
               'syndrome': ''}[entry.context]
    title = context + entry.name + ': diagnosis and treatment'
    assert is_headache_topic(title, 'METHODS: We reviewed the clinical evidence.')
    # Every catalog item has a lexical route into the search, independently of
    # the post-fetch screen. PubMed ultimately decides indexed word matching.
    normalized = re.sub(r'[^a-z0-9*]+', ' ', title.lower()).strip()
    patterns = [r'\b' + re.escape(re.sub(r'[^a-z0-9*]+', ' ', term.lower())).replace(r'\*', r'\w*') + r'\b'
                for term in SEARCH_TERMS]
    lexical_route = any(re.search(pattern, normalized) for pattern in patterns)
    if entry.context == 'ocular_pain':
        lexical_route = 'optic neuritis' in normalized and 'pain' in normalized
        assert '"optic neuritis"[Title/Abstract]' in HEADACHE_QUERY
        assert 'AND pain*[Title/Abstract]' in HEADACHE_QUERY
    assert lexical_route, entry.code


def test_syndrome_catalog_preserves_user_beta_version_and_all_groups():
    assert len(CATALOG.entries) == 368
    assert len(CATALOG.groups) == 26
    assert {g.code for g in CATALOG.groups} == {str(i) for i in range(1, 15)} | {'A'+str(i) for i in range(1, 13)}
    entries = {entry.code: entry for entry in CATALOG.entries}
    assert entries['13.5'].name == 'Optic neuritis'  # Beta, not final ICHD-3's numbering.
    assert entries['A12.10'].name == 'Headache attributed to acute stress disorder'
    assert entries['A1.6.6'].name == 'Vestibular migraine'
    assert '[span_' not in CATALOG.model_dump_json()
    data = CATALOG.model_dump(mode='json')
    data['entries'].append(data['entries'][0])
    with pytest.raises(ValidationError, match='unique'):
        SyndromeCatalog.model_validate(data)


@pytest.mark.parametrize('title,abstract', [
    ('Glossopharyngeal neuralgia treatment', 'A clinical trial.'),
    ('Painful post-traumatic trigeminal neuropathy', 'A clinical trial.'),
    ('Occipital neuralgia', 'A clinical trial.'),
    ('Burning mouth syndrome', 'A clinical trial.'),
    ('Tolosa–Hunt syndrome', 'A clinical report.'),
    ("Raeder’s syndrome", 'A clinical report.'),
    ('Status migrainosus', 'A clinical trial.'),
    ('Epicrania fugax', 'A clinical report.'),
    ('Cyclic vomiting syndrome', 'A clinical trial.'),
    ('Infantile colic', 'A clinical trial.'),
    ('Alternating hemiplegia of childhood', 'A clinical trial.'),
    ('Central post-stroke pain affecting the face', 'A clinical study.'),
    ('Analgesia in optic neuritis', 'A clinical trial.'),
    ('A preventive intervention', 'OBJECTIVE: To reduce post-dural puncture headache.'),
    ('A neuroimmunology study', 'OBJECTIVES: To assess ocular pain in optic neuritis.'),
    ('A behavioral intervention', 'PURPOSE: To assess persistent idiopathic facial pain.'),
    ('A headache study', 'OBJECTIVE: Assess headache attributed to psychiatric disorder.'),
])
def test_expanded_scope_accepts_named_syndromes_and_focused_objectives(title, abstract):
    assert is_headache_topic(title, abstract)


@pytest.mark.parametrize('title,abstract', [
    ('Stroke thrombectomy outcomes', 'OBJECTIVE: To evaluate functional recovery.\nRESULTS: Symptoms included headache.'),
    ('Optic neuritis visual recovery', 'OBJECTIVE: To assess visual acuity.\nRESULTS: Some patients reported ocular pain.'),
    ('Central post-stroke pain in the lower limb', 'OBJECTIVE: To assess foot pain.'),
    ('CADASIL genetics', 'OBJECTIVE: To identify mutations.\nBACKGROUND: Migraine is common in CADASIL.'),
    ('MELAS outcomes', 'OBJECTIVE: To assess survival.\nBACKGROUND: Headache may occur.'),
    ('Recurrent gastrointestinal disturbance', 'OBJECTIVE: To assess infectious diarrhea.'),
    ('Benign paroxysmal positional vertigo', 'OBJECTIVE: To assess repositioning maneuvers.'),
    ('Infant feeding', 'RESULTS: Adverse events included infantile colic and diarrhea.'),
    ('Facial nerve anatomy', 'A study of trigeminal ganglion neurons.'),
    ('A cancer trial', 'OBJECTIVE: To evaluate adverse events including burning mouth syndrome.'),
])
def test_expanded_scope_does_not_admit_underlying_diseases_or_incidental_symptoms(title, abstract):
    assert not is_headache_topic(title, abstract)


@pytest.mark.parametrize('journal,design,accepted', [
    ('Headache', 'Clinical Trial', True), ('Headache', 'Observational Study', False),
    ('JAMA Neurology', 'Observational Study', True), ('Unknown journal', 'Clinical Trial', False),
])
def test_expanded_scope_still_obeys_jif_and_trial_policy(tmp_path, journal, design, accepted):
    source = article()
    source.metadata.title = 'Treatment of burning mouth syndrome'
    source.metadata.source_society_or_journal = journal
    source.metadata.study_design = design
    ai = ai_summarizer()
    stats = run_pipeline(tmp_path/'papers.json', crawler=mock_crawler([source]), summarizer=ai)
    assert stats.saved == int(accepted)
    assert ai.client.beta.chat.completions.parse.call_count == int(accepted)


@patch('src.crawler.time.sleep')
def test_long_custom_search_is_sent_intact_in_post_body(sleep):
    session = Mock()
    session.post.return_value = response(content=b'<eSearchResult><IdList/></eSearchResult>')
    query = '(' + ' OR '.join(f'"headache syndrome {n}"[Title/Abstract]' for n in range(300)) + ')'
    assert Crawler(session).fetch_pubmed(date.today(), date.today(), query=query) == []
    request = session.post.call_args
    assert query in request.kwargs['data']['term']
    assert len(request.kwargs['data']['term']) > 10000
    assert request.args[0].endswith('/esearch.fcgi')
    assert '?' not in request.args[0]
    session.get.assert_not_called()


def search_page(count, ids):
    return response(content=(f'<eSearchResult><Count>{count}</Count><IdList>' +
                    ''.join(f'<Id>{identity}</Id>' for identity in ids) + '</IdList></eSearchResult>').encode())


@patch('src.crawler.time.sleep')
def test_search_paginates_all_matches_without_silent_default_cap(sleep):
    session = Mock()
    session.post.side_effect = [search_page(5, ['1','2']), search_page(5, ['3','4']), search_page(5, ['5'])]
    with patch('src.crawler.SEARCH_PAGE_SIZE', 2):
        ids = Crawler(session).search_ids(date(2016,10,17), date(2026,10,17), 'migraine')
    assert ids == ['1','2','3','4','5']
    assert [c.kwargs['data']['retstart'] for c in session.post.call_args_list] == [0,2,4]
    assert sleep.call_args_list == [call(0.4)] * 3


@patch('src.crawler.time.sleep')
def test_explicit_candidate_cap_is_bounded_and_reported(sleep, caplog):
    session = Mock()
    session.post.side_effect = [search_page(5, ['1','2']), search_page(5, ['3'])]
    with patch('src.crawler.SEARCH_PAGE_SIZE', 2):
        ids = Crawler(session).search_ids(date(2016,10,17), date(2026,10,17), 'migraine', limit=3)
    assert ids == ['1','2','3']
    assert [c.kwargs['data']['retmax'] for c in session.post.call_args_list] == [2,1]
    assert '3/5' in caplog.text


@patch('src.crawler.time.sleep')
@pytest.mark.parametrize('last', [[], ['1','2']])
def test_pagination_rejects_early_empty_or_repeated_pages(sleep, last):
    session = Mock()
    session.post.side_effect = [search_page(4, ['1','2']), search_page(4, last)]
    with patch('src.crawler.SEARCH_PAGE_SIZE', 2), pytest.raises(SourceError):
        Crawler(session).search_ids(date(2016,10,17), date(2026,10,17), 'migraine')


@patch('src.crawler.time.sleep')
def test_pubmed_ceiling_splits_date_ranges_without_overlap(sleep):
    session = Mock()
    session.post.side_effect = [search_page(10001, ['unused']), search_page(2,['new','overlap']), search_page(2,['overlap','old'])]
    ids = Crawler(session).search_ids(date(2026,10,1), date(2026,10,4), 'migraine')
    assert ids == ['new','overlap','old']
    bounds = [(c.kwargs['data']['mindate'],c.kwargs['data']['maxdate']) for c in session.post.call_args_list]
    assert bounds == [('2026/10/01','2026/10/04'),('2026/10/03','2026/10/04'),('2026/10/01','2026/10/02')]


@patch('src.crawler.time.sleep')
def test_pubmed_one_day_ceiling_fails_visibly(sleep):
    session = Mock()
    session.post.return_value = search_page(10001, ['1'])
    with pytest.raises(SourceError, match='one day'):
        Crawler(session).search_ids(date(2026,10,17),date(2026,10,17),'migraine')


@patch('src.crawler.time.sleep')
def test_historical_stream_rejects_high_jif_nontrial_even_for_custom_queries(sleep):
    session = Mock()
    session.post.return_value = search_page(1,['12345'])
    session.get.return_value = response(content=PUBMED_XML.replace(b'<Title>Cephalalgia</Title>', b'<Title>JAMA Neurology</Title>').replace(b'Randomized Controlled Trial',b'Observational Study'))
    assert Crawler(session).fetch_pubmed(date(2016,10,17),date(2026,9,17),query='migraine',trials_only=True) == []
    assert session.post.call_args.kwargs['data']['term'].endswith('"Systematic Review"[Publication Type]))')


def test_ten_year_collection_deduplicates_and_rechecks_electronic_dates():
    recent = article('recent')
    old = article('old'); old.metadata.publication_date='2018-04-12'
    too_old = article('too-old'); too_old.metadata.publication_date='2016-10-16'
    crawler = Crawler()
    with patch.object(crawler,'fetch_pubmed',side_effect=[[recent],[recent,old,too_old]]) as fetch:
        result = crawler.collect(today=date(2026,10,17))
    assert result.successful_sources == 2
    assert [a.metadata.pmid_or_id for a in result.articles] == ['recent','old']
    assert fetch.call_args_list[1].kwargs['trials_only'] is True


@pytest.mark.parametrize('failed_first', [True,False])
def test_one_failed_time_window_preserves_other_results_and_reports_failure(failed_first):
    crawler= Crawler()
    outputs=[SourceError('unavailable'),[article()]] if failed_first else [[article()],SourceError('unavailable')]
    with patch.object(crawler,'fetch_pubmed',side_effect=outputs):
        result=crawler.collect(today=date(2026,10,17))
    assert result.successful_sources==1 and len(result.errors)==1
    assert len(result.articles)==1
    assert ('recent:' if failed_first else 'historical trials:') in result.errors[0]


@pytest.mark.parametrize('published,expected', [
    ('2016-10-17',True),('2016-10-16',False),('2026-10-17',True),('2026-10-18',False),
    ('Not reported',False),('2016',False),('2016-10',False),('2017',True),('2026-02-30',False),
])
def test_trial_publication_date_boundaries(published,expected):
    from src.time_windows import trial_date_eligible
    assert trial_date_eligible(published,date(2026,10,17)) is expected


def test_ten_calendar_year_window_handles_leap_day():
    from src.time_windows import years_before,trial_date_eligible
    assert years_before(date(2024,2,29),10)==date(2014,2,28)
    assert trial_date_eligible('2014-02-28',date(2024,2,29))
    assert not trial_date_eligible('2014-02-27',date(2024,2,29))


def test_old_trials_are_pruned_and_rejected_before_ai_but_other_archive_remains(tmp_path):
    old=article('old');old.metadata.publication_date='2015-12-01'
    other=article('other');other.metadata.study_design='Observational Study';other.metadata.publication_date='2025-01-01'
    path=tmp_path/'papers.json';save_records(path,[record(old),record(other)])
    ai=ai_summarizer()
    stats=run_pipeline(path,crawler=mock_crawler([old]),summarizer=ai)
    assert (stats.removed_trial_age,stats.skipped_trial_age)==(1,1)
    assert [r.article_metadata.pmid_or_id for r in load_records(path)]==['other']
    ai.client.beta.chat.completions.parse.assert_not_called()


def test_trial_date_pruning_respects_fetch_only(tmp_path):
    old=article('old');old.metadata.publication_date='2015-12-01'
    path=tmp_path/'papers.json';save_records(path,[record(old)]);before=path.read_bytes()
    stats=run_pipeline(path,crawler=mock_crawler([old]),fetch_only=True)
    assert stats.removed_trial_age==1 and stats.skipped_trial_age==1
    assert path.read_bytes()==before


def test_cli_can_override_trial_years_without_changing_recent_days(tmp_path):
    crawler=mock_crawler()
    with patch('src.main.Crawler',return_value=crawler):
        assert main(['--output',str(tmp_path/'papers.json'),'--trial-lookback-years','8'])==0
    assert crawler.collect.call_args.kwargs['trial_lookback_years']==8
    assert crawler.collect.call_args.kwargs['lookback_days']==30


def test_verified_journals_between_ten_and_fifteen_admit_nontrials():
    from src.journal_metrics import load_policy
    policy=load_policy()
    assert policy.eligible('Brain : a journal of neurology','Review')
    assert policy.eligible('JAMA Netw Open','Observational Study')
    assert not policy.eligible('Brain unknown journal','Clinical Trial')


@pytest.mark.parametrize('title,abstract', [
    ('Hypothermia after decompressive hemicraniectomy for malignant stroke',
     'OBJECTIVE: We aimed to evaluate survival after hemicraniectomy.'),
    ('Critical care after cerebral infarction',
     'OBJECTIVE: We assessed hemicraniectomy in patients with large strokes.'),
    ('An antiviral trial for influenza',
     'The primary outcome was resolution of symptoms (cough, sore throat, headache, fever and fatigue).'),
    ('A first-in-human dementia drug trial',
     'Safety and tolerability, the primary endpoints, were met: no deaths; headache and fatigue were mild.'),
])
def test_surgical_word_stems_and_incidental_safety_symptoms_are_not_headache_topics(title, abstract):
    assert not is_headache_topic(title, abstract)
