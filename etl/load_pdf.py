"""Load the drugs and patents listed in the อย. drug-patent reports (data/pdf/*.pdf) into the database, replacing the
drug and patent data the DIP scraping pipeline put there. The scraping code and its raw page cache are untouched.

    python -m etl.load_pdf                  # back up the DB, wipe drug/patent data, load every data/pdf/*.pdf
    python -m etl.load_pdf --match          # … then learn Thai spellings, match and assess (rules + LLM if set)
    python -m etl.load_pdf --match --no-llm
    python -m etl.load_pdf --match --pubchem   # also add PubChem synonyms to the report's drug data

What is stored is what a patent record or a drug record holds on its own:
  * patent: the table columns (application, publication and patent numbers and dates, expiry, status, title,
    main claim, summary) and the date the status was observed (the section's report date);
  * drug: the product name and dosage form, and per substance the IUPAC name, formula, other names and
    indication printed under the heading.
Which product's section a patent was listed in, the report's claim type and its conclusions are NOT stored: they
are the answers the matcher is evaluated against, and finding them is the matcher's job. Patents are inserted in
application-number order so their ids carry no trace of the sections either.
"""
import argparse
import logging
import re
import sqlite3
import sys
from datetime import date, datetime
from pathlib import Path

from sqlalchemy import text
from sqlmodel import select

from backend.db import DATA_DIR, init_db, session
from backend.models import DrugProduct, ExpirySource, Patent, ProductSubstance, Substance
from etl import pdf_report as R
from etl.normalize import norm_status
from etl.resolve_substances import split_salt
from etl.scrape_detail import extract_main_claim, kind_from_appno

log = logging.getLogger(__name__)

PDF_DIR = DATA_DIR / "pdf"
FIRST_THAI_PATENT = date(1979, 1, 1)  # Patent Act B.E. 2522; an earlier date is a typo in the report
WIPE = ["patent_link", "assessment", "external_patent_ref", "product_substance", "substance", "drug_product",
        "patent", "status_map"]


def backup(db_path: str) -> str:
    dst = f"{db_path}.pre-pdf-{datetime.now():%Y-%m-%d-%H%M%S}"  # one per run: never overwrite an older backup
    src, out = sqlite3.connect(db_path), sqlite3.connect(dst)
    with out:
        src.backup(out)
    src.close()
    out.close()
    return dst


def _clean(s: str | None) -> str | None:
    s = re.sub(r"\s+", " ", s or "").strip()
    return None if s in ("", "-") else s


def _plausible(d: date | None, appno: str, field: str, latest: date | None) -> date | None:
    if d and (d < FIRST_THAI_PATENT or (latest and d > latest)):
        log.warning("%s: %s %s is impossible for a report dated %s; dropped", appno, field, d, latest)
        return None
    return d


def patent_record(row: R.Row, as_of: date | None, source: str) -> dict | None:
    appno, filing = R.application(row)
    if not appno:
        return None
    pub_no, pub_date = R.publication(row)
    pat_no, grant = R.patent(row)
    claims = _clean(row.cells.get("main_claim"))
    status = R.status_text(row)
    rec = {
        "application_no": appno, "filing_date": _plausible(filing, appno, "filing date", as_of),
        "publication_no": pub_no, "publication_date": _plausible(pub_date, appno, "publication date", as_of),
        "patent_no": pat_no, "grant_date": _plausible(grant, appno, "grant date", as_of),
        "kind": kind_from_appno(appno), "status_raw": status, "status_norm": norm_status(status) if status else None,
        "status_date": as_of, "title": _clean(row.cells.get("title")), "all_claims": claims,
        "main_claim": extract_main_claim(claims), "summary": _clean(row.cells.get("summary")),
        "discovered_via": [source],
    }
    exp = R.expiry(row)
    if exp:
        rec["expiry_date"], rec["expiry_source"] = exp, ExpirySource.STATED
    return rec


def _merge(old: dict, new: dict) -> dict:
    """The same application listed under several products: keep the most recent report's status, fill gaps."""
    newer = (new["status_date"] or date.min) >= (old["status_date"] or date.min)
    first, second = (new, old) if newer else (old, new)
    out = {k: first.get(k) if first.get(k) is not None else second.get(k) for k in set(old) | set(new)}
    out["discovered_via"] = sorted(set(old["discovered_via"]) | set(new["discovered_via"]))
    return out


def read_reports(paths: list[Path]) -> tuple[dict[str, dict], list[dict]]:
    """-> (patents by application number, products). Nothing ties a patent to a product."""
    patents, products = {}, []
    for path in paths:
        rep = R.parse(path)
        src = f"pdf:{path.name}"
        for sec in rep.sections:
            as_of = R.report_date(sec)
            name = R.product_name(sec)
            actives, form = R.split_product(name)
            products.append({"name": name, "dosage_form": form, "source": src, "substances": [
                {"name": a, **R.substance_facts(sec, a)} for a in actives]})
            for row in sec.rows:
                rec = patent_record(row, as_of, src)
                if rec:
                    a = rec["application_no"]
                    patents[a] = _merge(patents[a], rec) if a in patents else rec
        log.info("%s: %d products, %d table rows", path.name, len(rep.sections), sum(len(s.rows) for s in rep.sections))
    return patents, products


def load(paths: list[Path] | None = None, db_path=None) -> tuple[int, int]:
    paths = paths or sorted(PDF_DIR.glob("*.pdf"))
    if not paths:
        raise SystemExit(f"no PDF reports in {PDF_DIR}")
    engine = init_db(db_path)
    log.info("backed up the database to %s", backup(engine.url.database))
    patents, products = read_reports(paths)
    with engine.begin() as conn:
        for t in WIPE:
            conn.execute(text(f"DELETE FROM {t}"))
    with engine.connect().execution_options(isolation_level="AUTOCOMMIT") as conn:
        conn.execute(text("VACUUM"))  # give back the pages the scraped corpus used
    with session(db_path) as s:
        for appno in sorted(patents):
            s.add(Patent(**patents[appno]))
        for prod in products:
            dp = DrugProduct(name=prod["name"], dosage_form=prod["dosage_form"])
            s.add(dp)
            s.flush()
            for a in prod["substances"]:
                inn, salt = split_salt(a["name"].lower())
                sub = s.exec(select(Substance).where(Substance.inn_name == inn)).first()
                if sub is None:
                    sub = Substance(inn_name=inn, salt_form=salt, query_name=a["name"].lower())
                sub.iupac_name = sub.iupac_name or a["iupac_name"]
                sub.molecular_formula = sub.molecular_formula or a["molecular_formula"]
                sub.indication = sub.indication or a["indication"]
                if a["synonyms"]:
                    sub.synonyms_en = list(dict.fromkeys((sub.synonyms_en or []) + a["synonyms"]))
                s.add(sub)
                s.flush()
                if not s.get(ProductSubstance, (dp.id, sub.id)):
                    s.add(ProductSubstance(drug_product_id=dp.id, substance_id=sub.id))
        s.commit()
    log.info("loaded %d patents and %d products", len(patents), len(products))
    return len(patents), len(products)


def add_pubchem(db_path=None):
    """Optional: add PubChem synonyms (and any field the report left empty) to every substance, as the scraping
    pipeline's step 1 does. Values printed in the report win."""
    from etl.pubchem import PubChem
    from etl.resolve_substances import enrich
    pc = PubChem()
    with session(db_path) as s:
        for sub in s.exec(select(Substance)):
            tmp = Substance(inn_name=sub.inn_name, salt_form=sub.salt_form, query_name=sub.query_name)
            try:
                enrich(tmp, pc)
            except Exception as e:  # network trouble: keep the report's data for this one
                log.warning("PubChem %s: skipped (%s)", sub.inn_name, e)
                continue
            for f in ("pubchem_cid", "parent_cid", "iupac_name", "molecular_formula", "molecular_weight", "smiles",
                      "inchikey", "description", "indication", "mechanism", "atc_code", "peptide_sequence",
                      "enriched_at"):
                if getattr(sub, f) is None:
                    setattr(sub, f, getattr(tmp, f))
            sub.synonyms_en = list(dict.fromkeys((sub.synonyms_en or []) + (tmp.synonyms_en or []))) or None
            s.add(sub)
            log.info("PubChem %s: cid %s, %d synonyms", sub.inn_name, sub.pubchem_cid, len(sub.synonyms_en or []))
        s.commit()


def report_dates(paths: list[Path] | None = None) -> list[date]:
    """The distinct report dates, for assessing every product as of its report."""
    paths = paths or sorted(PDF_DIR.glob("*.pdf"))
    return sorted({d for p in paths for sec in R.parse(p).sections if (d := R.report_date(sec))})


def run(match: bool = False, use_llm: bool = True, pubchem: bool = False):
    from etl import assess, llm, match as matcher, name_variants, normalize
    load()
    if pubchem:
        add_pubchem()
    if not match:
        return
    if not use_llm:
        llm._disabled = True
    normalize.run()  # status_map rows, computed expiry where the report states none
    name_variants.run()  # Thai spellings actually used in the loaded patent text
    matcher.run(use_llm)
    for d in report_dates() + [date.today()]:
        assess.run(d)


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser()
    ap.add_argument("--match", action="store_true", help="also learn spellings, match and assess")
    ap.add_argument("--no-llm", action="store_true")
    ap.add_argument("--pubchem", action="store_true", help="add PubChem synonyms to the report's drug data")
    a = ap.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    logging.getLogger("httpx").setLevel(logging.WARNING)
    run(a.match, not a.no_llm, a.pubchem)
