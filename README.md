# Thai Drug–Patent Landscape

A local web app that answers, for each target generic drug: **which Thai patents and applications relate to it,
what kind of claim each one makes, whether it is still in force, and therefore whether a Thai manufacturer can
make the generic now, only by avoiding something, or only after a given date.**

It rebuilds, as a live tool, the analysis in the Thai FDA/DIP report on the 10 high-priority target-list drugs
(`รายงานข้อมูลสิทธิบัตรของรายการยาในบัญชียามุ่งเป้า…`, data as of 20 Jul 2022).

- **Drugs**: each product with its PubChem profile (structure image, IUPAC name, formula, ATC code, indication,
  mechanism), the Thai spellings found in patent text, and its Orange Book (US) context.
- **Patents**: the 8 fields from the report (application no./date, publication no./date, patent no./grant date,
  expiry, type/status, title, main claim, summary), searchable and filterable.
- **Matching**: links between drugs and patents, each with a claim category and a blocking level. Includes a
  per-drug timeline, a product × claim-category matrix, a network graph, the verdict with a generated Thai
  summary, and a review queue.
- **Eval**: a blind score of the pipeline against the report (see below).

## Quick start

```bash
python -m venv .venv && .venv/Scripts/pip install -r requirements.txt     # Windows; use .venv/bin on macOS/Linux
.venv/Scripts/python -m pytest -q                                           # tests, including the leakage guard

# 1. Build the data (first run ~1 h: crawls ~1,650 pharma patents at 1 request/s, then searches by drug name)
.venv/Scripts/python -m etl.pipeline --as-of 2022-07-20
#    later runs reuse cached pages:   --skip-crawl

# 2. Run the app
.venv/Scripts/uvicorn backend.app:app --port 8000
cd frontend && npm install && npm run dev        # http://localhost:5173  (proxies /api to :8000)
#    or a single server:  npm run build  → then just open http://localhost:8000
#    if port 8000 is taken: uvicorn ... --port 8077  and  API_PORT=8077 npm run dev
```

Optional: set `ANTHROPIC_API_KEY` to have Claude (`claude-opus-5-5`, strict JSON output) suggest Thai
transliterations and classify claims. Without it, the rule-based classifier is used. Set `PATENTAPP_LLM=0` to
force rules only.

## Data model

```
DRUG_PRODUCT ──< PRODUCT_SUBSTANCE >── SUBSTANCE ──< EXTERNAL_PATENT_REF  (US Orange Book)
     │                                     │
     ├──< ASSESSMENT (verdict history)     │
     └──(optional, product-specific)──< PATENT_LINK >── PATENT ──> STATUS_MAP
```

- **substance**: the active ingredient (chemistry, PubChem, Thai spellings). Substance, salt and crystal-form
  patents attach here.
- **drug_product**: the target-list item (dosage form). Device and formulation patents may attach here.
- **patent_link**: the match. It holds `claim_category` (drug_substance / solid_form / process / formulation /
  device / use / combination), `blocking_level` (blocking / avoidable / not_relevant), the design-around note,
  evidence, `match_method` and `review_state`.
- **assessment**: the verdict as of a date: `free` / `free_with_avoidance` / `blocked_until`, with
  `free_date`, the avoid list and the generated Thai summary. Analyst overrides are stored separately.

## Pipeline (`etl/`)

| step | module | what it does |
|---|---|---|
| 1 | `resolve_substances` | splits product names into substance + salt + dosage form; PubChem lookup |
| 2 | `crawl_category` | RegistrationEnding.aspx, อุตสาหกรรมยา (all granted pharma patents ≤5 years left or expired) |
| 3 | `search_by_name` | PublicDocument.aspx title search (all published applications, incl. pending/abandoned) for every Thai spelling; repeats as new spellings are learned |
| – | `thai_translit`, `name_variants` | rule-based English→Thai transliteration + phonetic-skeleton matching to find the spellings actually used |
| 4 | `normalize` | status mapping (`seed/status_map.yaml`), expiry = filing + 20 y (petty: 10 y), point-in-time status |
| 5 | `orange_book` | FDA Orange Book patents/exclusivities, generic (ANDA) counts |
| 6 | `match` | candidate links (name found in title / claim 1 / claims / summary) + claim classification |
| 7 | `assess` | verdict engine |

Site notes: the ยาเคมี sub-category filter is not honoured by plain HTTP postbacks, so the crawl takes the whole
อุตสาหกรรมยา group. RegistrationEnding only lists patents with ≤5 years left; longer-lived patents and all
pending or abandoned applications are found through the PublicDocument title search.

## Export to Excel

```bash
.venv/Scripts/python -m etl.export_csv --out exports --xlsx     # add --tables for every raw table
```

This writes `patents.csv`, `drugs.csv`, `drug_patent_matrix.csv` and `assessments.csv` as UTF-8 with BOM, so Thai
displays correctly in Excel, plus `patent_landscape.xlsx` with one sheet per view, frozen headers and filters.
Every table page in the app has an **Export CSV** button that respects the current filters. The same SQL views
(`v_patents`, `v_drugs`, `v_drug_patent_matrix`, `v_assessments`) are in `data/app.db`, so you can also browse
them with DB Browser for SQLite.

## Blind evaluation

The PDF is **not** an input. `seed/drugs.yaml` holds only the 10 product names. The report's answers live in
`eval/golden/expected.yaml`, and only `eval/score.py` reads that file. `tests/test_no_leak.py` fails the build if
any pipeline code imports `eval`, references the answer key, or contains any of its application or patent
numbers.

```bash
.venv/Scripts/python -m eval.score                         # score the current DB (automated links only)
.venv/Scripts/python -m eval.score --fresh --skip-crawl    # rebuild a fresh DB from the seed, then score
```

The scorer reports discovery recall and precision, claim-category accuracy, status and expiry accuracy, the
verdict, and avoid-list recall and precision, with regressions against the previous run. Disagreements on items
where the PDF itself is inconsistent go to a human-adjudication list rather than counting as failures. The
results are in `eval/report.md`, `eval/report.csv` and the app's **Eval** page.

### Current score (as of 20 Jul 2022, rules only, no LLM)

| metric | value |
|---|---|
| verdict matches the report | 9/10 |
| avoid-list recall / precision | 78% / 88% |
| claim-category accuracy (matched patents) | 96% |
| expiry-date accuracy | 92% |
| discovery recall / precision | 53% / 81% |

The one verdict miss (lercanidipine) is data drift. The amorphous-HCl patent is "แจ้งเพิกถอน (ไม่ชำระค่าธรรมเนียม)"
today, and the public site gives no date for the revocation, so the pipeline cannot know it was still in force in
2022. Most missed patents are not in the corpus at all (see below).

## Known limits

- Discovery recall is limited by coverage. The public site searches titles and applicants only (no full-text
  search, no wildcards, and the publication index ignores the industry filter). Patents whose titles don't name
  the drug are reached only through the pharma-category crawl or applicant expansion. Next step: crawl the whole
  publication listing month by month (title + applicant, about 1–2 h at 1 request/s) and match titles and
  applicants phonetically.
- Discovery uses names, synonyms (PubChem), parenthetical "Thai (english)" pairs, and amino-acid sequences for
  peptide drugs. A Markush genus claim that never names the compound can't be linked by text.
- The DIP site shows no legal-event history, so the date an application was abandoned is unknown. Point-in-time
  status uses the 5-year examination-request window.
- The Orange Book data is current, not historical.
