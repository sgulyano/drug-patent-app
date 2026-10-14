# Thai Drug–Patent Landscape

A local web app that maps Thai patents onto target generic drugs. For each drug it shows which Thai patents and
applications relate to it, what kind of claim each one makes, and whether each one is still in force. From that
it judges whether a Thai manufacturer can make the generic now, only by avoiding certain patents, or only after a
given date.

The pipeline starts from nothing but the product names. It finds the active ingredients, discovers the related
patents on the Thai Department of Intellectual Property (DIP) site, classifies each claim and computes a verdict.
The results are checked against the รายงาน อย. on the 10 high-priority drugs in the target list
(`รายงานข้อมูลสิทธิบัตรของรายการยาในบัญชียามุ่งเป้า…`, data as of 20 Jul 2022). That report serves as the ground
truth, and the patent–substance links the pipeline finds on its own are scored against it (see
[Evaluation](#evaluation-against-the-รายงาน-อย)).

The app has five pages:

- **ยา (Drugs)**: each product with its PubChem profile (structure image, IUPAC name, formula, ATC code,
  indication, mechanism), the Thai spellings found in patent text, its US Orange Book context, and its verdict
  with a generated Thai summary and a patent timeline.
- **สิทธิบัตร (Patents)**: the fields the รายงาน อย. uses (application no./date, publication no./date, patent
  no./grant date, expiry, type/status, title, main claim, summary), searchable and filterable.
- **การจับคู่ (Matching)**: a network graph of drugs, substances and patents, coloured by claim category and
  blocking level.
- **ตรวจทาน (Review)**: a queue where an analyst confirms or rejects each automatic link and corrects its claim
  category or blocking level.
- **ประเมินผล (Evaluation)**: the pipeline's score against the รายงาน อย., per metric and per product, with
  every disagreement listed.

## Data sources

All data comes from public sources.

| source | used for | module |
|---|---|---|
| [PubChem PUG REST / PUG View](https://pubchem.ncbi.nlm.nih.gov/docs/pug-rest) (NIH) | substance identity, structure, IUPAC name, formula, synonyms, ATC code, indication, mechanism | `pubchem`, `resolve_substances` |
| [FDA Orange Book data files](https://www.fda.gov/drugs/drug-approvals-and-databases/orange-book-data-files) | US patents and exclusivities for the reference product, number of approved generics (ANDA) | `orange_book` |
| [Ollama Cloud](https://docs.ollama.com/cloud) (optional) | Thai transliteration suggestions and claim classification by an LLM | `llm` |

The รายงาน อย. itself is used only by the evaluation (`eval/golden/expected.yaml`), never by the pipeline.

## Quick start

```bash
python -m venv .venv && .venv/Scripts/pip install -r requirements.txt     # Windows; use .venv/bin on macOS/Linux
.venv/Scripts/python -m pytest -q                                           # tests

# 1. Build the data (first run ~1 h: crawls ~1,650 pharma patents at 1 request/s, then searches by drug name)
.venv/Scripts/python -m etl.pipeline --as-of 2022-07-20
#    later runs reuse cached pages:   --skip-crawl

# 2. Run the app
.venv/Scripts/uvicorn backend.app:app --port 8000
cd frontend && npm install && npm run dev        # http://localhost:5173  (proxies /api to :8000)
#    or a single server:  npm run build  → then just open http://localhost:8000
#    if port 8000 is taken: uvicorn ... --port 8077  and  API_PORT=8077 npm run dev
```

## Data model

The database is SQLite (`data/app.db`). [backend/models.py](backend/models.py) is the reference: every column
there has a description (what it means, where it comes from, its format, what null means), and a test fails if
one is missing.

| table | one row is | written by |
|---|---|---|
| `drug_product` | a product on the target list | step 1; the analyst fills in `th_generic_*` |
| `substance` | an active ingredient: PubChem profile, Thai spellings, Orange Book note | steps 1, 3 and 5 |
| `product_substance` | "this product contains this substance" | step 1 |
| `patent` | a Thai patent or patent application | steps 2–4 |
| `status_map` | a status text seen on the DIP site and the status it maps to | step 4 |
| `patent_link` | "this patent claims something about this substance", with the claim category and how much it blocks a generic | step 6; then the analyst confirms or rejects it |
| `external_patent_ref` | a US Orange Book patent for the substance | step 5 |
| `assessment` | the verdict for one product on one as-of date | step 7; an analyst override adds a new row |

```mermaid
---
config:
  flowchart:
    curve: step
---
flowchart LR
    drug_product["<b>drug_product</b><br/>id · PK<br/>name · UK<br/>dosage_form<br/>th_generic_registered<br/>th_generic_note"]
    product_substance["<b>product_substance</b><br/>drug_product_id · PK, FK<br/>substance_id · PK, FK"]
    substance["<b>substance</b><br/>id · PK<br/>inn_name · UK<br/>salt_form<br/>query_name<br/>pubchem_cid<br/>parent_cid<br/>iupac_name<br/>molecular_formula<br/>molecular_weight<br/>smiles<br/>inchikey<br/>description<br/>indication<br/>mechanism<br/>atc_code<br/>name_variants_th<br/>synonyms_en<br/>peptide_sequence<br/>orange_book_note<br/>enriched_at"]
    patent_link["<b>patent_link</b><br/>id · PK<br/>patent_id · FK<br/>substance_id · FK<br/>drug_product_id · FK<br/>claim_category<br/>blocking_level<br/>design_around_note<br/>match_method<br/>matched_terms<br/>confidence<br/>evidence_snippet<br/>rationale<br/>review_state<br/>reviewed_at<br/>thai_patentability_flag"]
    patent["<b>patent</b><br/>id · PK<br/>application_no · UK<br/>filing_date<br/>receive_date<br/>publication_no<br/>publication_date<br/>patent_no<br/>grant_date<br/>expiry_date<br/>expiry_source<br/>kind<br/>status_raw<br/>status_norm<br/>title<br/>main_claim<br/>all_claims<br/>summary<br/>applicant<br/>inventors<br/>ipc<br/>industry<br/>discovered_via<br/>source_url<br/>raw_html_path<br/>scraped_at"]
    status_map["<b>status_map</b><br/>status_raw · PK<br/>status_norm"]
    assessment["<b>assessment</b><br/>id · PK<br/>drug_product_id · FK<br/>as_of_date<br/>mode<br/>verdict<br/>free_date<br/>fully_clear_date<br/>avoid_lists<br/>status_counts<br/>auto_summary_th<br/>analyst_override<br/>analyst_note<br/>created_at"]
    external_patent_ref["<b>external_patent_ref</b><br/>id · PK<br/>substance_id · FK<br/>jurisdiction<br/>patent_no<br/>claim_type<br/>use_code<br/>expiry<br/>product_desc<br/>applicant"]

    drug_product ---|"1 : n"| product_substance
    product_substance ---|"n : 1"| substance
    substance ---|"1 : n"| patent_link
    patent_link ---|"n : 1"| patent
    patent ---|"n : 1, by status_raw"| status_map
    drug_product ---|"1 : n"| assessment
    substance ---|"1 : n"| external_patent_ref
    drug_product -.-|"0..1 : n, optional"| patent_link

    classDef table fill:#f5f7ff,stroke:#7486c9,color:#1d2440
    class drug_product,product_substance,substance,patent_link,patent,status_map,assessment,external_patent_ref table
```

How to read it: each box is a table with all its columns. `PK` marks the primary key, `FK` a foreign key and
`UK` a unique column. Lines join tables on their keys, and the label gives the cardinality: `1 : n` means one row
in the table on the left of the label matches many in the table on its right. The dotted line is optional: a link
can name one product.

- A patent links to a **substance**, not a product, so a substance, salt or crystal-form patent applies to every
  product containing that substance. An analyst can narrow a link to one **drug product** (`drug_product_id`)
  when the claim only covers one dosage form, such as a device or a formulation.
- `patent.status_norm` is the status **today**. The status on a past date (for example the report date) is
  computed when needed and never stored.
- The verdict is recomputed live for any as-of date. `assessment` keeps the results of pipeline runs and the
  analyst overrides, which are added as new rows rather than replacing old ones.
- Dates are stored in the Common Era (ISO). Screens and exports add a Buddhist Era (พ.ศ.) copy, in columns
  ending `_be`.

**Value lists.** These columns only take the values below. Each list is a `StrEnum` in `backend/models.py`.
The values are stored as plain text, and an enum member equals its text (`ReviewState.CONFIRMED == "confirmed"`).

| column | values | enum |
|---|---|---|
| `patent.kind` | `invention`, `petty`, `design`, `unknown` | `PatentKind` |
| `patent.expiry_source` | `computed_invention_20y`, `computed_petty_10y`, `computed_design_10y`, `stated` | `ExpirySource` |
| `patent.status_norm`, `status_map.status_norm` | `granted_active`, `pending_unexamined`, `pending_examining`, `expired`, `abandoned`, `revoked` | `PatentStatus` |
| status on a past date (computed) | the six above, plus `not_published` and `likely_abandoned` | `PatentStatus` |
| `patent_link.claim_category` | `drug_substance`, `solid_form`, `process`, `formulation`, `device`, `use`, `combination` | `ClaimCategory` |
| `patent_link.blocking_level` | `blocking` (no generic until it ends), `avoidable` (design around it), `not_relevant` (mentioned, not claimed) | `BlockingLevel` |
| `patent_link.match_method` | `keyword` (rules), `llm`, `manual` (an analyst edited it) | `MatchMethod` |
| `patent_link.review_state` | `candidate`, `confirmed`, `rejected` | `ReviewState` |
| `assessment.mode` | `auto` (every link not rejected), `confirmed` (confirmed links only) | `AssessmentMode` |
| `assessment.verdict`, `analyst_override` | `free`, `free_with_avoidance`, `blocked_until`, `pending_review`, `unknown` | `Verdict` |
| `external_patent_ref.claim_type` | `drug_substance`, `drug_product`, `use`, `other` | `OrangeBookClaimType` |

**JSON columns.** In Python these are plain lists and dicts. They are stored as UTF-8 JSON text, so Thai stays
readable. To change one, assign a new list or dict; editing it in place is not saved.

| column | shape |
|---|---|
| `substance.synonyms_en` | `["Leuprorelin", "leuprolide", …]` |
| `substance.name_variants_th` | `{"observed": [{"spelling", "docs", "source"}, …], "probes": [spellings still to try]}` |
| `patent.discovered_via` | `["category:pharma", "title:<spelling>", "applicant:<name>"]` |
| `patent_link.matched_terms` | `{"terms": [names found], "sections": ["title", "claim1", "claims", "summary"]}` |
| `assessment.avoid_lists` | `{"blocking": [rows], "avoid": [rows], "warnings": [rows]}`, one row per patent |
| `assessment.status_counts` | `{"expired": 1, "granted_active": 1}` |

**Upgrading an older database.** `init_db()` runs at app start-up and at the start of every pipeline step. It
adds missing tables and columns, and applies the renames and drops listed in `RENAMED` and `DROPPED` in
[backend/db.py](backend/db.py), after first backing the database up to `app.db.pre-migration-<date>`. A
database built before the October 2026 refactor is upgraded on its first start:
- unused columns are dropped: `drug_product.route`, `strengths`, `on_target_list`, `external_patent_ref.found_in_th`
  and `substance.structure_img_url` (now built from `pubchem_cid`);
- `orange_book_note` moves from `drug_product` to `substance`;
- `assessment.avoid_json` and `counts_json` are renamed `avoid_lists` and `status_counts`.

## Pipeline

`python -m etl.pipeline` runs seven steps ([etl/pipeline.py](etl/pipeline.py)). The only drug input is
`seed/drugs.yaml`, which holds the 10 product names. Every step reads what earlier steps stored in the database,
so each one can also be run on its own (`python -m etl.<module>`).

```mermaid
flowchart TD
    seed[/"seed/drugs.yaml<br/>10 product names"/]
    s1["<b>1. resolve_substances</b><br/>split each name into substance, salt and dosage form<br/>writes drug_product, substance, product_substance"]
    s2["<b>2. crawl_category</b><br/>list the patents in the pharma category on the DIP site<br/>skipped with --skip-crawl"]
    s3["<b>3. search_by_name</b><br/>search patent titles for every Thai spelling of each substance,<br/>then search by the applicants found<br/>2 rounds, learning new spellings each time<br/>writes substance.name_variants_th"]
    s4["<b>4. scrape_detail + normalize</b><br/>read each patent page: dates, claims, status<br/>map the status, compute the expiry date<br/>writes patent, status_map"]
    s5["<b>5. orange_book</b><br/>US patents and approved generics per substance<br/>writes external_patent_ref, substance.orange_book_note"]
    s6["<b>6. match</b><br/>find each substance in patent text<br/>classify claim 1 and how much it blocks a generic<br/>writes patent_link as candidates"]
    review{{"Analyst, ตรวจทาน page<br/>confirm or reject each link"}}
    s7["<b>7. assess</b><br/>verdict for each --as-of date and today<br/>writes assessment"]

    pubchem(["PubChem"])
    ob(["FDA Orange Book"])
    llm(["Ollama Cloud LLM<br/>optional"])

    seed --> s1 --> s2 --> s3 --> s4 --> s5 --> s6 --> s7
    s6 -.-> review -.-> s7
    pubchem -.-> s1
    ob -.-> s5
    llm -.-> s3
    llm -.-> s6

    classDef ext fill:#eef4ff,stroke:#6b8fd6,color:#1d2b4f
    classDef human fill:#fff5e6,stroke:#d69a3a,color:#4a3210
    class pubchem,ob,llm ext
    class review human
```

| step | module | what it does |
|---|---|---|
| 1 | `resolve_substances` | splits product names into substance + salt + dosage form; PubChem lookup |
| 2 | `crawl_category` | crawls `RegistrationEnding.aspx` for the อุตสาหกรรมยา group (granted pharma patents with ≤5 years left, or expired) |
| 3 | `search_by_name` | searches `PublicDocument.aspx` titles (all published applications, including pending and abandoned) for every Thai spelling, then expands by applicant; repeats as new spellings are learned |
| – | `thai_translit`, `name_variants` | rule-based English→Thai transliteration and phonetic-skeleton matching to find the spellings patents actually use |
| 4 | `scrape_detail`, `normalize` | parses detail pages; maps statuses (`seed/status_map.yaml`); computes expiry (filing + 20 y, petty patents 10 y) and point-in-time status |
| 5 | `orange_book` | FDA Orange Book patents and exclusivities, generic (ANDA) counts |
| 6 | `match` | candidate links (drug name found in title / claim 1 / claims / summary) and claim classification |
| 7 | `assess` | verdict engine |

Site notes: the DIP site doesn't honour the ยาเคมี sub-category filter on plain HTTP postbacks, so the crawl
takes the whole อุตสาหกรรมยา group. `RegistrationEnding.aspx` only lists patents with ≤5 years left, so
longer-lived patents and all pending or abandoned applications are found through the title search.

### Example: one product through the pipeline

This follows **Leuprorelin powder and solvent for prolonged-release suspension for injection** from its seed
line to its verdict as of 20 Jul 2022, in `confirmed` mode. The `id` values are the real row ids, so you can see
how the rows join. Rows are trimmed to the fields that matter.

**Step 1. The product and its substance.** The seed line is just the name:

```yaml
- name: Leuprorelin powder and solvent for prolonged-release suspension for injection
```

The dosage form is split off. No salt word is left, so `salt_form` is null. PubChem supplies the rest, including
the amino-acid sequence, which matters later because patents often name peptides only by their sequence.

```json
{
  "drug_product": {"id": 3, "name": "Leuprorelin powder and solvent for prolonged-release suspension for injection",
                   "dosage_form": "powder and solvent for prolonged-release suspension for injection"},
  "product_substance": {"drug_product_id": 3, "substance_id": 3},
  "substance": {"id": 3, "inn_name": "leuprorelin", "salt_form": null, "pubchem_cid": 657181,
                "molecular_formula": "C59H84N16O12", "atc_code": "L02AE02",
                "synonyms_en": ["Leuprorelin", "leuprolide", "Leuprorelina", "…"],
                "peptide_sequence": "pGlu-His-Trp-Ser-Tyr-D-Leu-Leu-Arg-Pro-NHEt"}
}
```

**Step 3. Thai spellings.** Candidate spellings come from transliterating the name and its synonyms, plus the
LLM's suggestions when it is configured. The ones
actually found in patent text are kept with the number of patents that use them. The rest stay as probes for
the next search.

```json
{
  "substance.name_variants_th": {
    "observed": [{"spelling": "ลิวโพรไลด์", "docs": 3, "source": "synonym"},
                 {"spelling": "ลูโพรเรลิน", "docs": 1, "source": "generated"},
                 {"spelling": "ลิวโพรลีด", "docs": 1, "source": "synonym"}],
    "probes": ["ลิวโพรเรลิน", "ลิวโปรเรลิน", "ลูโพรรีลิน", "…"]
  }
}
```

**Steps 2 and 4. A patent.** The category crawl lists application 0701006463. Its detail page gives the dates,
status and claims. The expiry date is computed as filing date + 20 years − 1 day. The status text
"ยื่นคำขอชำระค่าธรรมเนียมรายปี" (annual fee filed) maps to `granted_active` through `seed/status_map.yaml`.

```json
{
  "patent": {"id": 683, "application_no": "0701006463", "kind": "invention",
             "filing_date": "2007-12-17", "publication_date": "2013-02-14",
             "patent_no": "76833", "grant_date": "2020-06-24",
             "expiry_date": "2027-12-16", "expiry_source": "computed_invention_20y",
             "status_raw": "ยื่นคำขอชำระค่าธรรมเนียมรายปี", "status_norm": "granted_active",
             "title": "องค์ประกอบที่มีการปลดปล่อยแบบทยอยและวิธีการสำหรับการผลิตองค์ประกอบ",
             "applicant": "ทาเคดะ ฟาร์มาซูติคอล คัมปะนี ลิมิเต็ด", "ipc": "A61K 38/00 A61K 47/12",
             "industry": "อุตสาหกรรมยา", "discovered_via": ["category:pharma"]}
}
```

**Step 5. US context.** The Orange Book lists 10 US patents for leuprorelin products. One `external_patent_ref`
row is stored for each, and a summary goes on the substance:

```json
{
  "external_patent_ref": {"substance_id": 3, "patent_no": "11717555", "claim_type": "drug_product",
                          "expiry": "2039-01-01", "product_desc": "CAMCEVI KIT (EMULSION;SUBCUTANEOUS)"},
  "substance.orange_book_note": "ยาต้นแบบ CAMCEVI ETM, CAMCEVI KIT, ELIGARD KIT; สิทธิบัตรที่ขึ้นทะเบียน 10 ฉบับ (ตัวยา 2, ผลิตภัณฑ์ 6, การใช้ 2), สิ้นอายุล่าสุด 2041-12-22; ยาชื่อสามัญ (ANDA) ได้รับอนุมัติ 10 รายการ"
}
```

**Step 6. The link.** Claim 1 of the patent covers sustained-release microcapsules of an LH-RH derivative
written as a pattern, `5-Oxo-Pro-His-Trp-Ser-Tyr-Y-Leu-Arg-Pro-Z`. A dependent claim narrows it to
`5-oxo-Pro-His-Trp-Ser-Tyr-DLeu-Leu-Arg-Pro-NH-C2H5`, which is leuprorelin's sequence in another notation, so
the match is found through `peptide_sequence`. The drug is not named in the title or in claim 1, but claim 1
claims a class of actives and a dependent claim picks out this one. That makes it a claimed formulation, which a
generic can avoid. The rules give confidence 0.45 and store the link as a candidate.

```json
{
  "patent_link": {"id": 30, "patent_id": 683, "substance_id": 3, "drug_product_id": null,
                  "claim_category": "formulation", "blocking_level": "avoidable",
                  "match_method": "keyword", "confidence": 0.45,
                  "matched_terms": {"terms": ["pGlu-His-Trp-Ser-Tyr-D-Leu-Leu-Arg-Pro-NHEt"], "sections": ["claims"]},
                  "design_around_note": "หลีกเลี่ยงสูตรตำรับ/องค์ประกอบตามข้อถือสิทธิ",
                  "review_state": "candidate"}
}
```

An analyst then opens it on the **ตรวจทาน** page and confirms it, so `review_state` becomes `confirmed` and
`reviewed_at` is set.

**Step 7. The verdict.** Leuprorelin has 6 links. `confirmed` mode ignores the 2 that are still candidates. Each
of the other 4 gets its status as of 20 Jul 2022:

| application | claim | status on 20 Jul 2022 | effect |
|---|---|---|---|
| 0701006463 (patent 76833) | formulation, avoidable | `granted_active`, ends 16 Dec 2027 | must be avoided |
| 0201002351 (patent 64759) | formulation, avoidable | `expired` on 25 Jun 2022 | none |
| 2 later applications | – | `not_published` (published in 2023 and 2024) | none: not public yet |

One active patent is avoidable and none is blocking, so the verdict is `free_with_avoidance`. The generic is
fully clear once the last active patent ends.

```json
{
  "assessment": {"drug_product_id": 3, "as_of_date": "2022-07-20", "mode": "confirmed",
                 "verdict": "free_with_avoidance", "free_date": null, "fully_clear_date": "2027-12-16",
                 "status_counts": {"expired": 1, "granted_active": 1},
                 "avoid_lists": {"blocking": [], "warnings": [],
                                 "avoid": [{"application_no": "0701006463", "patent_no": "76833",
                                            "category": "formulation", "status": "granted_active",
                                            "expiry_date": "2027-12-16", "review_state": "confirmed", "…": "…"}]}}
}
```

`auto_summary_th`, as shown on the drug page (abridged):

```text
ข้อมูล Orange Book (สหรัฐอเมริกา): ยาต้นแบบ CAMCEVI ETM, CAMCEVI KIT, ELIGARD KIT; สิทธิบัตรที่ขึ้นทะเบียน 10 ฉบับ …
ในประเทศไทยพบสิทธิบัตร/คำขอรับสิทธิบัตรที่เกี่ยวข้อง 2 รายการ (สิ้นอายุ 1, ได้รับสิทธิบัตร มีผลคุ้มครอง 1)
สิทธิบัตร/คำขอรับสิทธิบัตรที่ยังมีผลซึ่งต้องหลีกเลี่ยง:
  1. สิทธิบัตรเลขที่ 76833 [สูตรตำรับ/องค์ประกอบ (Formulation); ได้รับสิทธิบัตร มีผลคุ้มครอง; สิ้นอายุ 16/12/2570] … — หลีกเลี่ยงสูตรตำรับ/องค์ประกอบตามข้อถือสิทธิ
สรุป: บริษัทยาสามารถผลิตยาชื่อสามัญ Leuprorelin … จำหน่ายได้ โดยต้องหลีกเลี่ยงรายการข้างต้น
```

## Export to Excel

```bash
.venv/Scripts/python -m etl.export_csv --out exports --xlsx     # add --tables for every raw table
```

This writes `patents.csv`, `drugs.csv`, `drug_patent_matrix.csv` and `assessments.csv` as UTF-8 with BOM, so Thai
displays correctly in Excel, plus `patent_landscape.xlsx` with one sheet per file, frozen headers and filters.
Every table page in the app has an **Export CSV** button that respects the current filters.

## Evaluation against the รายงาน อย.

The รายงาน อย. is the ground truth. Its patents, claim types, statuses, expiry dates and per-drug conclusions
are transcribed into `eval/golden/expected.yaml`, and `eval/score.py` compares them with the patent–substance
links the pipeline found automatically. Links an analyst added or decided by hand are left out, so the score
measures the pipeline rather than the analyst.

The evaluation is blind: the pipeline never sees the answers. Only `eval/score.py` reads the answer key, and
`tests/test_no_leak.py` fails the build if any pipeline code imports `eval`, references the answer key, or
contains any of its application or patent numbers.

```bash
.venv/Scripts/python -m eval.score                         # score the current DB (automated links only)
.venv/Scripts/python -m eval.score --fresh --skip-crawl    # rebuild a fresh DB from the seed, then score
```

| metric | meaning |
|---|---|
| discovery recall / precision | share of the report's patents the pipeline found / share of the pipeline's links that the report lists |
| claim-category accuracy | for patents found by both, the pipeline's claim category matches the report's type |
| status / expiry accuracy | status as of the report date matches; expiry date within one day |
| verdict accuracy | the per-drug conclusion (free / free with avoidance / blocked) matches |
| avoid-list recall / precision | the patents the report says to avoid vs those the pipeline marks avoidable or blocking |

Every disagreement counts as a pipeline error. Results go to `eval/report.md`, `eval/report.csv` and the app's
**ประเมินผล** page, which lists each disagreement with the pipeline's and the report's values side by side and
flags regressions against the previous run.

### Current score (as of 20 Jul 2022, rules only, no LLM)

| metric | value |
|---|---|
| verdict matches the รายงาน อย. | 9/10 |
| avoid-list recall / precision | 78% / 88% |
| claim-category accuracy (matched patents) | 96% |
| expiry-date accuracy | 92% |
| status accuracy | 76% |
| discovery recall / precision | 53% / 81% |

The one verdict miss (lercanidipine) is data drift. The amorphous-HCl patent is "แจ้งเพิกถอน (ไม่ชำระค่าธรรมเนียม)"
today, and the public site gives no date for the revocation, so the pipeline cannot know it was still in force in
2022. Most missed patents are not in the corpus at all (see below).

## Known limits

- Discovery recall is limited by coverage. Patents whose titles don't name the drug are reached only through the
  pharma-category crawl or applicant expansion. Next step: crawl the whole publication listing month by month
  (title + applicant, about 1–2 h at 1 request/s) and match titles and applicants phonetically.
- Discovery uses names, synonyms (PubChem), parenthetical "Thai (english)" pairs, and amino-acid sequences for
  peptide drugs. A Markush genus claim that never names the compound can't be linked by text.
- The DIP site shows no legal-event history, so the date an application was abandoned or revoked is unknown.
  Point-in-time status uses the 5-year examination-request window.
- The Orange Book data is current, not historical.
