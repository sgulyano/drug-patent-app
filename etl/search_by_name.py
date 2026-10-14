"""Name-based discovery: search the DIP publication index (title substring) for every known spelling of each
substance, fetch the hits, re-learn spellings from the enlarged corpus, and repeat.

    python -m etl.search_by_name --rounds 2
"""
import argparse
import json
import logging
import re

from sqlmodel import select

from backend.db import DATA_DIR, init_db, session
from backend.models import Patent, Substance
from etl import name_variants
from etl.dip_search import search_publications
from etl.http import PoliteClient
from etl.scrape_detail import enqueue, process_queue

log = logging.getLogger(__name__)
SEARCHED = DATA_DIR / "searched_terms.json"
MAX_TERMS_PER_SUBSTANCE = 10
APPLICANT_MAX_PAGES = 8  # 200 applications per applicant and patent type


def _load_searched() -> dict:
    return json.loads(SEARCHED.read_text(encoding="utf-8")) if SEARCHED.exists() else {}


def search_terms(sub: Substance) -> list[str]:
    terms = name_variants.variants_for(sub)[:MAX_TERMS_PER_SUBSTANCE]
    terms.append(sub.inn_name)  # English names occasionally appear in Thai titles
    return list(dict.fromkeys(t for t in terms if len(t) >= 5))


def run(rounds: int = 2, client: PoliteClient | None = None, expand_applicants: bool = True):
    init_db()
    client = client or PoliteClient()
    searched = _load_searched()
    for r in range(1, rounds + 1):
        name_variants.run()  # (re)learn spellings from the current corpus
        new_hits = 0
        with session() as s:
            subs = list(s.exec(select(Substance)))
        for sub in subs:
            for term in search_terms(sub):
                if term in searched:
                    continue
                hits = []
                for ptype in ("invention", "petty"):
                    try:
                        hits += [row["application_no"] for row in search_publications(client, term, ptype)]
                    except Exception as e:
                        log.error("search '%s' (%s) failed: %s", term, ptype, e)
                searched[term] = sorted(set(hits))
                SEARCHED.write_text(json.dumps(searched, ensure_ascii=False, indent=1), encoding="utf-8")
                if hits:
                    enqueue(sorted(set(hits)), via=f"title:{term}")
                    new_hits += len(set(hits))
                log.info("round %d %s: '%s' -> %d hits", r, sub.inn_name, term, len(set(hits)))
        fetched = process_queue(client)
        log.info("round %d: %d title hits, %d details fetched", r, new_hits, fetched)
        if expand_applicants:
            name_variants.run()
            n_app = applicant_expansion(client, searched)
            new_hits += n_app
            log.info("round %d: %d applications via applicant expansion", r, n_app)
        if new_hits == 0:
            break
    name_variants.run()


def applicant_key(applicant: str) -> str | None:
    """Stable search key for an applicant: its first two words ('เบอห์ริงเกอร์ อินเกลไฮม์ ฟาร์มา ...')."""
    words = re.sub(r"[,.()]", " ", applicant or "").split()
    key = " ".join(words[:2]) if len(words) >= 2 else (words[0] if words else "")
    return key if len(key.replace(" ", "")) >= 4 else None


def applicant_expansion(client: PoliteClient, searched: dict, max_pages: int = APPLICANT_MAX_PAGES) -> int:
    """Originators file several applications per drug, many without the drug name in the title. For every
    patent that names a substance in its title, list all applications by the same applicant and
    fetch them, so their claims can be matched by full text."""
    from etl.match import _english_terms, find_mentions  # local import: match imports this module's siblings

    keys = set()
    with session() as s:
        patents = list(s.exec(select(Patent)))
        for sub in s.exec(select(Substance)):
            thai = name_variants.variants_for(sub, include_probes=False)
            en = _english_terms(sub)
            for p in patents:
                m = find_mentions(p, thai, en, sub.peptide_sequence)
                if "title" in m:
                    k = applicant_key(p.applicant)
                    if k:
                        keys.add(k)
    total = 0
    for k in sorted(keys):
        tag = f"applicant:{k}"
        if tag in searched:
            continue
        hits = []
        for ptype in ("invention", "petty"):
            try:
                hits += [row["application_no"] for row in search_publications(client, applicant=k, patent_type=ptype,
                                                                              max_pages=max_pages)]
            except Exception as e:
                log.error("applicant search '%s' failed: %s", k, e)
        searched[tag] = sorted(set(hits))
        SEARCHED.write_text(json.dumps(searched, ensure_ascii=False, indent=1), encoding="utf-8")
        log.info("applicant '%s' -> %d applications", k, len(set(hits)))
        if hits:
            enqueue(sorted(set(hits)), via=tag)
            total += len(set(hits))
    process_queue(client)
    return total


if __name__ == "__main__":
    import sys
    sys.stdout.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser()
    ap.add_argument("--rounds", type=int, default=2)
    ap.add_argument("--no-applicants", action="store_true")
    a = ap.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    logging.getLogger("httpx").setLevel(logging.WARNING)
    run(a.rounds, expand_applicants=not a.no_applicants)
