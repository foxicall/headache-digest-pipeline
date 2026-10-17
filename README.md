# Headache Digest

A zero-database Python pipeline and responsive Tailwind CSS reader. Articles live in `data/headache_papers.json`. **A verified two-year Journal Impact Factor > 10 admits all study designs; JIF < 10 requires an indexed PubMed clinical trial.** Unknown, missing, stale-year, or boundary-value metrics are rejected.

## Quick start

Requires Python 3.10+; CI uses Python 3.12.

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt
pytest

# Missing OPENAI_API_KEY is supported: save raw abstracts without AI calls.
python src/main.py

# Serve the dashboard and JSON from the repository root.
python -m http.server 8000 --bind 127.0.0.1
```

Open <http://localhost:8000>. Use HTTP rather than `file://`, which cannot reliably fetch JSON. No database, frontend build or NCBI key is required.

## Strict Journal Impact Factor policy

The policy is a versioned, manually verified snapshot of **2025 two-year JIF (2026 JCR release)**, checked on **2026-10-17**. It is maintained in `data/journal_metrics.json`, independent of the unchanged seven-field clinical summary schema. This is a curated list of verified journals, not an exhaustive JCR catalog.

| Journal | 2025 JIF | Evidence |
| --- | ---: | --- |
| The Lancet Neurology | 54.6 | [Source](https://info.thelancet.com/laneur/request-access) |
| JAMA Neurology | 23.6 | [Source](https://www.wolterskluwer.com/en/solutions/ovid/jama-neurology-262) |
| Nature Reviews Neurology | 31.2 | [Source](https://www.nature.com/nature-portfolio/about-journals/journal-metrics) |
| Nature Neuroscience | 20.3 | [Source](https://www.nature.com/nature-portfolio/about-journals/journal-metrics) |
| Nature Medicine | 52.5 | [Source](https://journalcitationreports.zendesk.com/hc/en-gb/articles/47808147130129-2026) |
| Nature Communications | 18.1 | [Source](https://www.nature.com/ncomms/ncomms/journal-impact) |
| Nature | 56.1 | [Source](https://www.nature.com/nature/journal-impact) |
| The New England Journal of Medicine | 84.5 | [Source](https://www.nejm.org/media-center/fact-sheet) |
| JAMA | 65.4 | [Source](https://jamanetwork.com/journals/jama/fullarticle/2854582) |
| The BMJ | 55.1 | [Source](https://bmjgroup.com/about-us/) |
| Cephalalgia | 5.8 | [Source](https://www.wolterskluwer.com/en/solutions/ovid/cephalalgia-1040) |
| The Journal of Headache and Pain | 8.7 | [Source](https://link.springer.com/journal/10194) |
| Headache | 3.9 | [Source](https://www.wolterskluwer.com/en/solutions/ovid/headache-the-journal-of-head-and-face-pain-992) |
| Neurology | 8.9 | [Source](https://www.wolterskluwer.com/en/solutions/ovid/neurology-288) |
| Annals of Neurology | 7.5 | [Source](https://www.wolterskluwer.com/en/solutions/ovid/annals-of-neurology-2161) |
| Internal Medicine | 1.0 | [Source](https://internmed.jp/modules/about/index.php?content_id=1) |
| Journal of the Chinese Medical Association (Taiwan) | 2.3 | [Source](https://www.wolterskluwer.com/en/solutions/ovid/journal-of-the-chinese-medical-association-14834) |
| Brain | 12.6 | [Source](https://academic.oup.com/brain/pages/About) |
| JAMA Network Open | 11.7 | [Source](https://www.wolterskluwer.com/en/solutions/ovid/jama-network-open-14871) |

The rule is **JIF > 10 OR (JIF < 10 AND PubMed clinical trial)**. Exactly 10 remains excluded. The backend uses decimal comparisons. These pinned 2025 journal metrics apply to both recent papers and historical trials; they are not claims about a journal’s IF in each paper’s publication year. Null/unknown metrics, unlisted journals and metric years different from the policy year fail closed. CiteScore, SJR, five-year JIF and values inferred by AI are never substitutes. Complete normalized title/alias matching prevents a journal from inheriting another journal's value by prefix or society tag.

Clinical trials are identified by exact PubMed publication types retained in `study_design`: Clinical Trial, phases I–IV, Controlled Clinical Trial, Randomized Controlled Trial, Pragmatic Clinical Trial, Adaptive Clinical Trial, and Equivalence Trial. This includes nonrandomized interventional trials. Protocols, veterinary trials, observational studies and reviews/meta-analyses do not qualify for the exception, even if conflicting trial tags are present. Title/abstract mentions, Clinical Study, Multicenter Study and AI-inferred labels are insufficient. See [NLM publication type definitions](https://www.nlm.nih.gov/mesh/pubtypes.html). Recently published trials awaiting indexing can be missed until PubMed adds a qualifying type.

The gate is applied in the PubMed query, to fetched metadata, to every new-record/upgrade attempt, to the existing archive on every normal run, and again in the reader. Custom queries, society affiliations and historical seeds cannot bypass it. Previously excluded papers can be fetched again if they meet the revised rule within the publication window; old examples are not restored automatically. An empty archive is valid and is not filled with nonqualifying examples.

To update after a new JCR release, verify **two-year** values and the metric year against Clarivate or publisher sources, update the year/release/verification date and each entry's value, year, aliases and evidence URL, then run tests and the pipeline. New and updated entries require review; the nightly job does not scrape or guess JIF values. The pinned year remains visible until a verified update is committed. Journal-level JIF is an admission criterion, not a quality score for an individual article.

## Exact data contract

Each `PaperRecord` contains exactly `article_metadata` and `structured_summary`. Deduplication uses `article_metadata.pmid_or_id`.

`ArticleMetadata` has six required string fields: `pmid_or_id`, `doi`, `title`, `source_society_or_journal`, `publication_date`, and `study_design`. Missing DOI/date values use `Not reported`. Dates retain their available ISO precision: year, year-month or full date. Study design comes from PubMed publication types; funding tags are not treated as designs.

`StructuredSummary` has exactly seven required fields:

| Field | Type |
| --- | --- |
| `methods` | string |
| `inclusion_criteria` | string array |
| `exclusion_criteria` | string array |
| `number_of_inclusion` | string |
| `mechanism_of_action_moa` | string |
| `conclusion` | string |
| `pitfalls_and_limitations` | string array |

Pydantic v2 rejects extra fields and empty summary strings/lists. Participant counts remain strings to preserve distinctions such as “577 randomized; 570 included in efficacy analyses.”

## No-key fallback and AI extraction

**Without a key:** missing, empty or whitespace-only `OPENAI_API_KEY` logs a warning. Both `methods` and `conclusion` contain the complete raw PubMed abstract. The other five fields contain `Requires OPENAI_API_KEY for AI extraction`, wrapped in a one-element list for list fields. No OpenAI client is constructed. An unexpectedly missing abstract is explicitly marked.

**With a key:** `gpt-4o-mini` uses OpenAI Structured Outputs with `StructuredSummary` as the response model. The prompt distinguishes `Not reported` (relevant information omitted) from `Not applicable` (irrelevant to the study design). It forbids inventing sample sizes, p-values, mechanisms or eligibility criteria, treats source text as evidence rather than instructions, and separates author-reported limitations from limits of abstract-only reading. Refusals or invalid/incomplete output are reported as failures and remain retryable.

Adding a key also upgrades stored fallback records, including those outside the fetch window, using the abstract retained in `methods`. Upgrades replace records in place without duplicate PMIDs. No additional mode/provenance fields are added to the public schema. The dashboard recognizes fallback records by the exact marker. All upgrades remain subject to the JIF gate.

The existing Traditional Chinese interface and summary language are retained, with bilingual clinical field labels. Set `SUMMARY_LANGUAGE=English` locally or as a repository variable to change extraction language. English missing-information markers remain explicit. `OPENAI_MODEL` defaults to `gpt-4o-mini`.

## Historical test fixtures

The original three historical examples are preserved in `tests/fixtures/historical_seeds.json`, with XML evidence in `tests/fixtures/seed_pubmed.xml`. They are not automatically restored to the published archive. A matching PMID may re-enter only through the live trial query and current admission rules; it is then displayed as an ordinary sourced record, not labeled a seed merely because of its PMID. These fixtures do not represent recent query results or current recommendations.

| Coverage | Paper | Source |
| --- | --- | --- |
| IHS / Cephalalgia | ARISE erenumab RCT, 2018: 577 randomized; 570 in efficacy analyses | [PMID 29471679](https://pubmed.ncbi.nlm.nih.gov/29471679/), [DOI](https://doi.org/10.1177/0333102418759786) |
| EHF / JHP | Neuromodulation of chronic headaches: EHF position statement, 2013; no new enrolled cohort | [PMID 24144382](https://pubmed.ncbi.nlm.nih.gov/24144382/), [DOI](https://doi.org/10.1186/1129-2377-14-86) |
| AAN / Neurology | Supraorbital transcutaneous stimulation RCT, 2013: 67 randomized and included in intention-to-treat analysis | [PMID 23390177](https://pubmed.ncbi.nlm.nih.gov/23390177/), [DOI](https://doi.org/10.1212/WNL.0b013e3182825055) |

Metadata and clinical claims were checked against keyless PubMed EFetch on **2026-10-17**. Source excerpts are retained in `tests/fixtures/seed_pubmed.xml` for offline provenance checks. Full texts were not used. The Neurology example distinguishes within-group p-values from the between-group responder comparison. Unreported exclusion criteria and arm sizes are not invented.

## Syndrome coverage and version

`data/headache_syndromes.json` preserves all **368 classification entries in the user-supplied ICHD-3 beta (2013) list**, including the 14 main chapters and 12 supplied appendix groups (26 groups in total). Parent classifications and appendix variants are included in this count; it is not a count of unique diseases or available papers. Citation placeholders were removed. The catalog retains beta codes, for example **13.5 Optic neuritis**; it does not silently substitute the [2018 final classification](https://ichd-3.org/classification-outline/). [Original beta publication](https://www.ichd-3.org/wp-content/uploads/2016/08/International-Headache-Classification-III-ICHD-III-2013-Beta-1.pdf).

Coverage includes migraine and its episodic syndromes, tension-type headache, TACs, other primary headaches, all supplied secondary-headache groups, painful cranial neuropathies/facial pains, unspecified headaches and appendix conditions. Names without the word headache—such as glossopharyngeal/occipital neuralgia, burning mouth syndrome, epicrania fugax, cyclical vomiting syndrome, benign paroxysmal vertigo/torticollis and infantile colic—have explicit retrieval routes. Common spelling and punctuation variants are supported. Benign paroxysmal positional vertigo is not treated as benign paroxysmal vertigo.

Underlying diseases are not unconditional topic matches: CADASIL/MELAS need a headache focus, recurrent gastrointestinal disturbance needs migraine context, optic neuritis needs a pain focus, and central neuropathic/post-stroke pain needs craniofacial involvement. Generic stroke, infection, tumor, IIH/SIH, psychiatric illness and peripheral/limb neuropathic pain remain excluded without an included research focus. Named migraine-related episodic/appendix syndromes can be in scope without current head pain because the user explicitly included them. Inclusion is a literature-topic decision, not a diagnosis or proof that ICHD criteria were met.

The expandable dashboard catalog supports code/name/Chinese chapter search and primary, secondary, cranial/other and appendix sections. It is independent of the article search and never fabricates a paper for a listed syndrome. Catalog fetch errors have their own retry and do not block the literature reader. All papers still pass the existing verified-JIF/clinical-trial policy; the clinical summary remains exactly seven fields. The default PubMed windows are 30 days for other studies and ten calendar years for clinical trials.

## PubMed ingestion

The default query in `src/crawler.py` combines the verified JIF allowlist with the ICHD-3 beta scope terms in `src/headache_scope.py`, and requires an abstract. The prior society journal restriction is superseded by the JIF allowlist. The following source classifications remain available for metadata and filters, but never bypass the JIF/design policy. Lower-JIF journals are queried only in conjunction with clinical-trial publication types. Original target journals without a verified metric for the policy year remain excluded, including Cephalalgia Reports, Acta Neurologica Taiwanica, Japanese Journal of Headache, Neurology and Clinical Neuroscience and Rinsho Shinkeigaku. Generic trigeminal anatomy is not a standalone topic; a trigeminal location qualifier is used only together with central neuropathic pain.

| Source group | Journals and additional evidence |
| --- | --- |
| IHS | *Cephalalgia*, *Cephalalgia Reports* |
| EHF | *The Journal of Headache and Pain* / *J Headache Pain* |
| ANA / AAN / AHS | *Annals of Neurology*, *Neurology*, *Headache*, respectively |
| JHS / 日本頭痛學會 | *Japanese Journal of Headache*, *Neurology and Clinical Neuroscience*, *Internal Medicine* / *Intern Med*, *Rinsho Shinkeigaku*; explicit Japanese Headache Society affiliation, collective author, or title/abstract mention; Japan affiliation in *Cephalalgia*, *JHP*, or *Headache* |
| THS / 台灣頭痛學會 | *Acta Neurologica Taiwanica* / *Acta Neurol Taiwan*, *Journal of the Chinese Medical Association* / *J Chin Med Assoc*; explicit Taiwan Headache Society affiliation, collective author, or title/abstract mention |

These are requested journal/affiliation source groups, including associated journals; membership does not establish society authorship or endorsement. A paper can belong to multiple groups. The schema is unchanged: journal and ordered society tags share `source_society_or_journal`, separated by ` | `. *Journal of the Chinese Medical Association* is the formal English name of a **Taiwan journal**, displayed with `（台灣）`. Neither “Chinese” nor a China affiliation is treated as evidence of THS membership.

**Topic screening:** after fetching XML, a deterministic screen requires a topic in the title, an explicit objective or primary outcome about an included headache/cranial pain syndrome, or a sustained opening focus in an unstructured abstract. A named migraine-specific therapy study is also retained when its abstract explicitly identifies migraine treatment/prevention, cluster-headache treatment, or a migraine patient population (for example, galcanezumab pregnancy safety). Background-only mentions, symptom/adverse-event lists (including case-presentation titles), generic trigeminal anatomy and incidental headache in other diseases fail. A journal or affiliation never bypasses the screen. The pipeline repeats this check before any AI call, including custom queries and fallback upgrades.

This conservative lexical rule favors precision and may miss relevant papers without explicit topic wording; it is not a clinical relevance classifier. SIH/IIH and other neurologic diseases are not automatically included without a headache study focus. Verbatim excerpts for ambiguous cases are in `tests/fixtures/scope_pubmed.xml`; earlier records remain available in Git history. Future runs retain the archive and do not delete older papers merely because they fall outside the query window.

ESearch uses a form-encoded POST body so the full syndrome query cannot exceed URL length limits; EFetch uses GET. Both return XML. Every request, including retries, is preceded by `time.sleep(0.4)`. Calls run sequentially with timeouts, bounded retries for transient failures and EFetch batches of 100 IDs. No NCBI API key is sent. PubMed query warnings are logged. Some journal names may be unmapped; indexed aliases are included in the allowlist query. Sources absent from PubMed cannot be retrieved by this pipeline. Affiliation searches depend on indexed author metadata and do not capture every society member's work.

The pipeline combines two PubMed `pdat` windows using the UTC date:

- **Recent publications:** the last 30 calendar days including today (for example, September 18–October 17, 2026).
- **Clinical trials:** the preceding ten-calendar-year anniversary through today (for example, October 17, 2016–October 17, 2026). The historical search ends the day before the recent window begins; recent trials are already included in the first search. The extra trial publication-type constraint is also checked after XML parsing, even for high-IF journals and custom queries. The February 29 anniversary clamps to February 28 in a non-leap target year.

All matching IDs are paginated by default, with date-range subdivision when a PubMed search exceeds its 10,000-ID retrieval ceiling. An unresolvable single-day ceiling, missing page or repeated page fails visibly. `--pubmed-limit` is an optional diagnostic cap **per window**, with a logged truncation warning; ordinary scheduled runs have no candidate cap. PMIDs returned in both windows are deduplicated. A failed window is reported while successful results can still be saved.

The displayed date prefers PubMed electronic publication date, which can precede the indexed issue date used by the search. Clinical trials additionally require this source date to lie within the ten-year window: older, future or unavailable dates are rejected before AI calls, existing trial records are pruned on normal runs, and the reader applies the same UTC age check. Partial year/month dates use the first day conservatively, so uncertain boundary dates may be excluded. Other study types remain subject to the 30-day **retrieval** window; their previously saved archive is retained. Trial backfills therefore cover the full requested period without expanding old observational studies or reviews to ten years.

```bash
# Live source check without model calls or file writes.
python src/main.py --fetch-only --pubmed-limit 3

# Optional bounded backfill.
python src/main.py --lookback-days 30 --trial-lookback-years 10 --pubmed-limit 200 --max-new 20
```

Other options: `--output`, `--query`, `--model`, `--language`. OpenAI usage occurs only when a key is supplied; `--max-new` limits record attempts, including upgrades, rather than dollar spend.

Existing JSON is validated before network work. Deduplication covers the saved archive and repeated IDs within a fetched batch. New records are prepended; existing records retain their order. Each success is checkpointed with `indent=2`, `ensure_ascii=False`, `fsync` and atomic replacement. Failures preserve eligible records. The JIF and trial-age gates prune ineligible records before fetching, even if PubMed subsequently fails; `--fetch-only` never changes the file. Invalid JSON or duplicate persisted IDs fail explicitly rather than resetting the archive. Unchanged runs do not rewrite the file. Use one local writer per JSON file; CI serializes runs.

## Daily GitHub Actions and Pages

Every push to `main`, manual `workflow_dispatch`, and daily schedule runs tests, fetches/summarizes papers, commits updated JSON, and deploys GitHub Pages. The daily schedule is `0 0 * * *`: **00:00 UTC / 08:00 Asia/Taipei**.

1. Open **Actions → Daily headache digest → Run workflow** on `main` to run immediately.
2. No secret is required for fallback mode. Later, add the repository Actions secret **OPENAI_API_KEY** to enable extraction.
3. The `sync` job grants **contents: write** and uses the automatic `GITHUB_TOKEN` to commit only changed `data/headache_papers.json` back to `main`.

Repository rules must permit bot commits; branch protection is not bypassed. Partial successes are committed before extraction failures are surfaced, with a seven-day recovery artifact. Tests run without secrets before each sync. Automated data commits include `[skip ci]` to prevent recursive runs, in addition to GitHub's normal suppression of workflows triggered by `GITHUB_TOKEN`. GitHub schedules may be delayed or disabled for inactive public repositories.

**Pages setup:** set **Settings → Pages → Source** to **GitHub Actions**. The deployment job automatically publishes the static assets and latest JSON after successful tests/sync; no `DEPLOY_PAGES` variable is required. Explicit deployment in the same run avoids relying on bot commits to trigger another workflow. Pipeline or deployment failures remain visible rather than being suppressed to make a run appear successful.

## Dashboard

The viewer fetches both `./data/headache_papers.json` and `./data/journal_metrics.json`, validates the policy and filters again before displaying or counting papers. Invalid or unavailable metrics stop display instead of falling back to an unrestricted archive. Each card links its JIF value and year to the verified source. All indexed clinical-trial cards display a PubMed clinical-trial badge, including those from journals above the IF threshold. Keyword search, date sorting, seven clinical fields and source/DOI links are retained. Chips show All, journals currently represented, and any society groups with matching records; a society match never bypasses JIF. Empty results are explicitly labeled as having no qualifying publications.

Titles and abstracts are inserted as text, never HTML. Relative URLs work under GitHub Pages project paths. Loading, fetch errors, retries, empty results and cached/offline data are handled. The existing app manifest and service worker support installation/offline reading after an online visit; production requires HTTPS.

Two literature dropdowns combine with keyword search and journal/society chips using AND:

- **Syndrome:** all 368 catalog codes, grouped into 26 chapters, plus an unclassified option. Matching uses the title, stored methods and positive inclusion-criteria text, with common bilingual synonyms/abbreviations and context guards. Descendants also count toward their parents. Duplicate appendix headings require explicit appendix/alternative-criteria wording or a matching appendix descendant. These are text-based literature facets, not assigned diagnoses or verification of ICHD diagnostic criteria.
- **Study design:** clinical trials, RCTs/phases, case reports/series, cohort, case-control, cross-sectional, observational, reviews/meta-analyses, guidelines/consensus, protocols, and other designs/attributes. Clinical-trial/RCT/phase facets use exact PubMed types; the other facets also recognize explicit English or Chinese title/methods wording. Reviews/protocols do not inherit the primary designs they discuss; missing design information is not inferred from sample size or use of a control group.

Options show counts for the full eligible archive, including zero-count categories; overlapping categories need not sum to the archive total. The result count reflects the combined filters. Changing a dropdown resets pagination, and **Clear all filters** resets the keyword, both dropdowns and journal selection. The catalog browser remains independent. If catalog loading fails, the syndrome dropdown is disabled with retry guidance while study-design filtering and the reader remain usable. These filters do not relax the IF, topic or date admission rules. Four historical incidental-headache/hemicraniectomy false positives were removed after source rechecking; regression tests prevent their recurrence.

Source checking also removed unsupported age/consent and exclusion criteria from the stored SUNHA trial summary (PMID 39301810); absent eligibility details are marked `Not reported`.

Tailwind CSS is compiled and checked in as `assets/tailwind.css`; no runtime CDN or Node installation is required. Rebuild after changing utility classes with:

```bash
npm_config_cache=/tmp/headache-npm-cache npx --yes tailwindcss@3.4.17 \
  -i assets/tailwind.input.css -o assets/tailwind.css \
  --content './index.html,./assets/app.js' --minify
```

## Verification

Run `pytest`. Tests use `unittest.mock`, clear API-key environment variables, block real HTTP requests and prevent real OpenAI client creation. Coverage includes the exact schema, XML/DOI/date parsing, query coverage, rate limiting/retries, 30-day/ten-year defaults and overrides, leap-year and electronic-date boundaries, pagination and PubMed ceiling splits, journal aliases and overlapping source groups, Taiwan classification, strict topic screening, all 368 catalog entries, underlying-disease exclusions, long-query POST transport and source-based regressions, mocked Structured Outputs, missing/blank keys, deduplication, prepending, upgrades, partial failures, atomic writes, historical seed fixtures, strict JIF boundaries, low-JIF clinical-trial exceptions and exclusions, unknown/wrong-year metrics, custom-query protection and archive pruning. Tests require no network access, external API key or billing.

Run `node --test tests/paper_filters.test.cjs` with Node 22+ for the DOM-free dropdown classifiers. Tests cover all catalog names and parent codes, bilingual aliases, overlapping research designs, negated/unknown designs, protocol/review exclusions, case-report-form false positives and the current cohort record. GitHub Actions runs both suites before synchronization/deployment; Node is not required by the Python pipeline or the deployed reader.
