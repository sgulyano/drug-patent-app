"""Build the held-out answer key (eval/golden/expected.yaml) from the อย. reports in data/pdf/.

    python -m eval.build_key            # rewrite expected.yaml from the PDFs and eval/golden/verdicts.yaml
    python -m eval.build_key --draft    # also draft verdicts.yaml entries for products it lacks (review by hand!)

For every product section the key lists the applications the report's table gives for it, with the claim type and
status as printed. The pipeline loads the same tables (etl.load_pdf) but never which product a patent was listed
under, nor the claim types or conclusions: those are what the matcher is scored on.

Per-product conclusions (verdict class, patents to avoid) live in eval/golden/verdicts.yaml, curated by hand from
each section's "สรุป" paragraph; --draft pre-fills missing entries by rules so the curator only has to check them.
"""
import argparse
import re
import sys
from datetime import date
from pathlib import Path

import yaml

from etl import pdf_report as R

ROOT = Path(__file__).resolve().parent.parent
GOLDEN = Path(__file__).resolve().parent / "golden"
KEY = GOLDEN / "expected.yaml"
VERDICTS = GOLDEN / "verdicts.yaml"
PDF_DIR = ROOT / "data" / "pdf"


def coarse_types(raw: str | None) -> list[str]:
    """'Drug substance and Product Claims' -> ['drug_substance', 'product']. Words are often split by spaces."""
    t = re.sub(r"\s+", "", (raw or "").lower())
    t = re.sub(r"\(priority.*", "", t)  # '(priority เป็น use claims)' describes the priority document
    out = []
    if re.search(r"substanc|sustance", t):
        out.append("drug_substance")
    if re.search(r"product|formul|combin", t):
        out.append("product")
    if "process" in t:
        out.append("process")
    if re.search(r"use|treatment", t):
        out.append("use")
    return out


def coarse_status(text: str | None) -> str | None:
    """The report's status on its own date, coarsened to the five values the report distinguishes."""
    t = re.sub(r"\s+", "", text or "")
    if not t:
        return None
    if re.search(r"ละทิ้ง|ยกคำขอ|ถอนคำขอ", t):
        return "abandoned"
    if "เพิกถอน" in t:
        return "revoked"
    if "สิ้นอายุ" in t and "จะสิ้นอายุ" not in t:
        return "expired"
    if re.search(r"รายปี|หนังสือสำคัญ|ต่ออายุ|จะสิ้นอายุ", t):
        return "granted_active"
    return "pending"


def conclusion(sec: R.Section) -> str:
    """The section's closing 'สรุป …' paragraph (the last one; the first is about generic registrations)."""
    starts = [i for i, ln in enumerate(sec.text) if re.match(r"\s*สรุป(?!ความเป็นไปได้)", R.fix_spacing(ln))]
    if not starts:
        return ""
    out = []
    for ln in sec.text[starts[-1]:]:
        if out and re.match(r"\s*(หมายเหตุ|ปัจจุบัน|ข้อมูล)", ln):
            break
        out.append(ln.strip())
    return R.fix_spacing(" ".join(out))


def draft_verdict(sec: R.Section) -> dict:
    """Rules only; a human checks every draft against the PDF before it is used."""
    c = conclusion(sec)
    if re.search(r"ยังไม่สามารถ", c):
        cls = "blocked_until"
    elif re.search(r"หลีกเลี่ยง|ต้องไม่ใช้|ต้องไม่ใช่", c):
        cls = "free_with_avoidance"
    else:
        cls = "free"
    by_patent = {R.patent(r)[0]: R.application(r)[0] for r in sec.rows if R.patent(r)[0]}
    apps = {R.application(r)[0] for r in sec.rows}
    avoid = []
    for n in re.findall(r"(?<![\d/])(\d{4,10})(?![\d/])", c):
        a = n if n in apps else by_patent.get(n)
        if a and a not in avoid:
            avoid.append(a)
    return {"class": cls, "avoid": avoid, "conclusion": c, "drafted": True}


def product_key(sec: R.Section, verdicts: dict, report: str) -> dict:
    name = R.product_name(sec)
    patents = []
    for r in sec.rows:
        appno, filing = R.application(r)
        if not appno:
            continue
        pub_no, pub_date = R.publication(r)
        e = {"appno": appno}
        if R.patent(r)[0]:
            e["patent_no"] = R.patent(r)[0]
        if filing:
            e["filing"] = filing
        if R.expiry(r):
            e["expiry"] = R.expiry(r)
        raw = R.claim_type(r)
        if coarse_types(raw):
            e["pdf_type"] = coarse_types(raw)
        if raw:
            e["pdf_type_raw"] = raw
        if coarse_status(R.status_text(r)):
            e["pdf_status"] = coarse_status(R.status_text(r))
        if not pub_no and not pub_date:
            e["unpublished"] = True
        patents.append(e)
    v = verdicts.get(name, {})
    for e in patents:
        if e["appno"] in v.get("suspect", {}):
            e["suspect"] = v["suspect"][e["appno"]]
    return {"product": name, "report": report, "as_of": R.report_date(sec), "patents": patents,
            "verdict": {k: v[k] for k in ("class", "avoid", "avoid_optional") if k in v}}


def build(draft: bool = False) -> dict:
    verdicts = yaml.safe_load(VERDICTS.read_text(encoding="utf-8")) if VERDICTS.exists() else {}
    verdicts = verdicts or {}
    products, drafted = [], {}
    for path in sorted(PDF_DIR.glob("*.pdf")):
        for sec in R.parse(path).sections:
            name = R.product_name(sec)
            if name not in verdicts:
                drafted[name] = draft_verdict(sec)
                if not draft:
                    raise SystemExit(f"{name}: no verdict in {VERDICTS.name}; run with --draft and review it")
            products.append(product_key(sec, {**verdicts, **drafted}, path.name))
    if drafted:
        with VERDICTS.open("a", encoding="utf-8") as f:
            f.write(yaml.safe_dump(drafted, allow_unicode=True, sort_keys=False, width=120))
        print(f"drafted {len(drafted)} verdicts into {VERDICTS} — review them against the PDFs", file=sys.stderr)
    return {"source": [p.name for p in sorted(PDF_DIR.glob("*.pdf"))], "products": products}


HEADER = """\
# HELD-OUT ANSWER KEY — generated by `python -m eval.build_key` from the อย. reports in data/pdf/
# ("รายงานข้อมูลสิทธิบัตรของรายการยาที่อยู่ในบัญชียามุ่งเป้าที่มีลำดับความสำคัญสูง", 40 products as of 2017–2019 and
# 10 products as of 20 Jul 2022). Do not edit by hand: change the parser or eval/golden/verdicts.yaml and rebuild.
#
# ONLY eval/ may read this file. tests/test_no_leak.py fails the build if pipeline/backend code reads eval/ or
# contains any application/patent number below.
#
# Per product: as_of = the section's report date; patents = the applications its table lists, with
#   pdf_type (claim type(s) as printed, coarsened to drug_substance / product / process / use; pdf_type_raw verbatim),
#   pdf_status (status on the report date: granted_active / pending / abandoned / expired / revoked),
#   unpublished: true -> no publication number or date: not discoverable from public text, excluded from recall,
#   suspect: "..." -> the PDF itself looks inconsistent here (from verdicts.yaml), shown next to any disagreement;
# verdict = the section's conclusion: class, avoid (applications it tells manufacturers to avoid) and
#   avoid_optional (listed as still alive in the text but left out of, or garbled in, the conclusion).
"""


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser()
    ap.add_argument("--draft", action="store_true")
    key = build(ap.parse_args().draft)
    KEY.write_text(HEADER + yaml.safe_dump(key, allow_unicode=True, sort_keys=False, width=120), encoding="utf-8")
    n = sum(len(p["patents"]) for p in key["products"])
    print(f"wrote {KEY}: {len(key['products'])} products, {n} listed applications")
