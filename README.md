# Headache Digest

A zero-database Python pipeline and responsive Tailwind CSS reader. Articles live in `data/headache_papers.json`. **Three real, source-verified historical papers make the dashboard work immediately without an API key.**

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

Adding a key also upgrades stored fallback records, including those outside the fetch window, using the abstract retained in `methods`. Upgrades replace records in place without duplicate PMIDs. No additional mode/provenance fields are added to the public schema. The dashboard recognizes fallback records by the exact marker and labels the three curated seed PMIDs separately.

The existing Traditional Chinese interface and summary language are retained, with bilingual clinical field labels. Set `SUMMARY_LANGUAGE=English` locally or as a repository variable to change extraction language. English missing-information markers remain explicit. `OPENAI_MODEL` defaults to `gpt-4o-mini`.

## Verified seed papers

These historical examples are clearly labeled in the reader; they are not recent query results or current recommendations.

| Coverage | Paper | Source |
| --- | --- | --- |
| IHS / Cephalalgia | ARISE erenumab RCT, 2018: 577 randomized; 570 in efficacy analyses | [PMID 29471679](https://pubmed.ncbi.nlm.nih.gov/29471679/), [DOI](https://doi.org/10.1177/0333102418759786) |
| EHF / JHP | Neuromodulation of chronic headaches: EHF position statement, 2013; no new enrolled cohort | [PMID 24144382](https://pubmed.ncbi.nlm.nih.gov/24144382/), [DOI](https://doi.org/10.1186/1129-2377-14-86) |
| AAN / Neurology | Supraorbital transcutaneous stimulation RCT, 2013: 67 randomized and included in intention-to-treat analysis | [PMID 23390177](https://pubmed.ncbi.nlm.nih.gov/23390177/), [DOI](https://doi.org/10.1212/WNL.0b013e3182825055) |

Metadata and clinical claims were checked against keyless PubMed EFetch on **2026-10-17**. Source excerpts are retained in `tests/fixtures/seed_pubmed.xml` for offline provenance checks. Full texts were not used. The Neurology example distinguishes within-group p-values from the between-group responder comparison. Unreported exclusion criteria and arm sizes are not invented.

## PubMed ingestion

The default query exactly follows the requested coverage:

```text
(("Cephalalgia"[Journal] OR "The Journal of Headache and Pain"[Journal] OR "Headache"[Journal] OR "Neurology"[Journal] OR "Annals of Neurology"[Journal] OR "Acta Neurologica Taiwanica"[Journal] OR "Intern Med"[Journal] OR "Japanese Headache Society"[Affiliation] OR "Taiwan Headache Society"[Affiliation]) AND (migraine[Title/Abstract] OR headache[Title/Abstract] OR trigeminal[Title/Abstract]) AND hasabstract[text])
```

ESearch and EFetch both use XML. Every request, including retries, is preceded by `time.sleep(0.4)`. Calls run sequentially with timeouts, bounded retries for transient failures and EFetch batches of 100 IDs. No NCBI API key is sent. PubMed query warnings are logged. Affiliation searches depend on indexed author metadata and do not capture every society member's work.

The default window is **14 calendar days including today**, based on UTC publication dates (for example, October 4–17 inclusive). Up to 1,000 candidates are fetched per run. All unique candidates are processed unless an optional attempt cap is supplied. This is a recent-publication digest: delayed indexing may place older articles outside the window. Increase the lookback/candidate limit for backfills.

```bash
# Live source check without model calls or file writes.
python src/main.py --fetch-only --pubmed-limit 3

# Optional bounded backfill.
python src/main.py --lookback-days 30 --pubmed-limit 200 --max-new 20
```

Other options: `--output`, `--query`, `--model`, `--language`. OpenAI usage occurs only when a key is supplied; `--max-new` limits record attempts, including upgrades, rather than dollar spend.

Existing JSON is validated before network work. Deduplication covers the saved archive and repeated IDs within a fetched batch. New records are prepended; existing records retain their order. Each success is checkpointed with `indent=2`, `ensure_ascii=False`, `fsync` and atomic replacement. Failures preserve existing data. Invalid JSON or duplicate persisted IDs fail explicitly rather than resetting the archive. Unchanged runs do not rewrite the file. Use one local writer per JSON file; CI serializes runs.

## Daily GitHub Actions and Pages

After merging the workflow to `main`, the daily schedule is `0 0 * * *`: **00:00 UTC / 08:00 Asia/Taipei**. Manual `workflow_dispatch` is also supported.

1. Open **Actions → Daily headache digest → Run workflow** on `main` to run immediately.
2. No secret is required for fallback mode. Later, add the repository Actions secret **OPENAI_API_KEY** to enable extraction.
3. The `sync` job grants **contents: write** and uses the automatic `GITHUB_TOKEN` to commit only changed `data/headache_papers.json` back to `main`.

Repository rules must permit bot commits; branch protection is not bypassed. Partial successes are committed before extraction failures are surfaced, with a seven-day recovery artifact. Tests run without secrets on pull requests, main pushes and scheduled/manual runs. Ordinary pushes only test/deploy and do not call PubMed or OpenAI. GitHub schedules may be delayed or disabled for inactive public repositories.

**Optional Pages deployment:** set **Settings → Pages → Source** to **GitHub Actions**, then create repository Actions **variable** `DEPLOY_PAGES=true`. A separate job publishes the static assets and latest JSON after successful tests/sync. Data sync works independently of Pages setup. Explicit deployment avoids relying on bot commits to trigger another workflow. The site can also be deployed to any static host.

## Dashboard

The viewer fetches `./data/headache_papers.json` and supports keyword search, date sorting, all seven clinical fields, source/DOI links, and these filter chips: **All**, **IHS / Cephalalgia**, **EHF / JHP**, **AAN / ANA**, **JHS**, **THS**, and **Headache**.

AAN / ANA groups *Neurology* and *Annals of Neurology*. THS includes *Acta Neurologica Taiwanica* as the requested regional journal grouping, without implying every paper is society-authored. JHS/THS also match explicit society affiliation tags retained in `source_society_or_journal`. *Intern Med* is searchable under All; it is not automatically labeled JHS solely because it is a Japanese journal.

Titles and abstracts are inserted as text, never HTML. Relative URLs work under GitHub Pages project paths. Loading, fetch errors, retries, empty results and cached/offline data are handled. The existing app manifest and service worker support installation/offline reading after an online visit; production requires HTTPS.

Tailwind CSS is compiled and checked in as `assets/tailwind.css`; no runtime CDN or Node installation is required. Rebuild after changing utility classes with:

```bash
npm_config_cache=/tmp/headache-npm-cache npx --yes tailwindcss@3.4.17 \
  -i assets/tailwind.input.css -o assets/tailwind.css \
  --content './index.html,./assets/app.js' --minify
```

## Verification

Run `pytest`. Tests use `unittest.mock`, clear API-key environment variables, block real HTTP requests and prevent real OpenAI client creation. Coverage includes the exact schema, XML/DOI/date parsing, query coverage, rate limiting/retries, 14-day defaults, mocked Structured Outputs, missing/blank keys, deduplication, prepending, upgrades, partial failures, atomic writes and three verified seeds. Tests require no network access, external API key or billing.
