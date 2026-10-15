"""Blind evaluation: score the matcher's patent-product links against the held-out answer key from the อย. reports.

    python -m eval.score                        # score the current database
    python -m eval.score --fresh                # load the PDFs into a fresh DB, match (rules + LLM if set), score
    python -m eval.score --fresh --no-llm       # rules only
    python -m eval.score --fresh --pubchem      # also give the matcher PubChem synonyms (not in the reports)

The database holds every patent listed anywhere in the two reports, as one pool, and every product, with nothing
saying which patent was listed under which product (etl.load_pdf). The matcher has to put them back together. Each
product is scored as of its own report date. Only automated links are scored (match_method keyword/llm); analyst
review decisions are ignored, so the score measures the pipeline, not the analyst. The PDF is ground truth: every
disagreement counts as a pipeline error, including on items the key marks `suspect`; those are also listed under
`adjudicate` with the note, for context.
"""
import argparse
import csv
import json
import os
import subprocess
import sys
from datetime import date, datetime
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent
KEY = Path(__file__).resolve().parent / "golden" / "expected.yaml"
OUT = Path(__file__).resolve().parent
SOURCE = "pdf"  # what this evaluation scores: matching over the patents loaded from the อย. reports

COARSE = {"drug_substance": {"drug_substance"}, "solid_form": {"drug_substance", "product"},
          "process": {"process"}, "formulation": {"product"}, "device": {"product"}, "combination": {"product"},
          "use": {"use"}, None: set()}
STATUS_COARSE = {"granted_active": "granted_active", "pending_unexamined": "pending", "pending_examining": "pending",
                 "likely_abandoned": "abandoned", "abandoned": "abandoned", "expired": "expired", "revoked": "revoked",
                 "not_published": "not_published"}
METRICS = ("discovery_recall", "discovery_precision", "category_accuracy", "status_accuracy", "expiry_accuracy",
           "verdict_accuracy", "avoid_recall", "avoid_precision")


def _prf(pred: set, exp: set, optional: set = frozenset()) -> dict:
    pred_s, exp_s = pred - optional, exp - optional
    tp = len(pred_s & exp_s)
    p = tp / len(pred_s) if pred_s else (1.0 if not exp_s else 0.0)
    r = tp / len(exp_s) if exp_s else 1.0
    return {"tp": tp, "pred": len(pred_s), "expected": len(exp_s), "precision": round(p, 3), "recall": round(r, 3),
            "missed": sorted(exp_s - pred_s), "extra": sorted(pred_s - exp_s)}


def _new_tally() -> dict:
    return {k: 0 for k in ("disc_tp", "disc_pred", "disc_exp", "cat_ok", "cat_n", "status_ok", "status_n", "exp_ok",
                           "exp_n", "verdict_ok", "verdict_n", "avoid_tp", "avoid_pred", "avoid_exp", "products")}


def _ratios(t: dict) -> dict:
    def ratio(a, b):
        return round(t[a] / t[b], 3) if t[b] else None
    return {"discovery_recall": ratio("disc_tp", "disc_exp"), "discovery_precision": ratio("disc_tp", "disc_pred"),
            "category_accuracy": ratio("cat_ok", "cat_n"), "status_accuracy": ratio("status_ok", "status_n"),
            "expiry_accuracy": ratio("exp_ok", "exp_n"), "verdict_accuracy": ratio("verdict_ok", "verdict_n"),
            "avoid_recall": ratio("avoid_tp", "avoid_exp"), "avoid_precision": ratio("avoid_tp", "avoid_pred"), **t}


def score(as_of: date | None = None, db_path: str | None = None) -> dict:
    """as_of overrides every product's report date (normally left None)."""
    if db_path:
        os.environ["PATENTAPP_DB"] = db_path
    from sqlmodel import select

    from backend.db import init_db, session
    from backend.models import DrugProduct, Patent, PatentLink, ProductSubstance, Substance
    from etl.assess import LinkView, assess
    from etl.normalize import status_as_of

    key = yaml.safe_load(KEY.read_text(encoding="utf-8"))
    init_db(db_path)
    dates = sorted({str(as_of or p["as_of"]) for p in key["products"]})
    report = {"source": SOURCE, "key_source": key.get("source"), "as_of": dates[0] if len(dates) == 1 else
              f"{dates[0]} – {dates[-1]} (each product as of its report date)",
              "run_at": datetime.now().isoformat(timespec="seconds"), "db": str(db_path or "default"),
              "products": [], "adjudicate": []}
    tallies = {"all": _new_tally()}
    with session(db_path) as s:
        methods = [l.match_method for l in s.exec(select(PatentLink))]
        report["config"] = {"llm": "llm" in methods,
                            "pubchem": any(x.pubchem_cid for x in s.exec(select(Substance)))}
        for item in key["products"]:
            day = as_of or item["as_of"]
            tot = [tallies["all"], tallies.setdefault(item["report"], _new_tally())]

            def add(field, n):
                for t in tot:
                    t[field] += n
            dp = s.exec(select(DrugProduct).where(DrugProduct.name == item["product"])).first()
            row = {"product": item["product"], "report": item["report"], "as_of": str(day), "found_in_db": bool(dp)}
            if not dp:
                report["products"].append(row)
                continue
            add("products", 1)
            sub_ids = [ps.substance_id for ps in s.exec(select(ProductSubstance).where(ProductSubstance.drug_product_id == dp.id))]
            views = []
            for l in s.exec(select(PatentLink).where(PatentLink.substance_id.in_(sub_ids))):
                if l.match_method == "manual" or l.drug_product_id not in (None, dp.id):
                    continue
                p = s.get(Patent, l.patent_id)
                if p.publication_date and p.publication_date > day:
                    continue  # not public yet on the report date
                views.append(LinkView(l, p, s.get(Substance, l.substance_id), status_as_of(p, day)))
            # a patent linked through two substances of a combination product counts once
            pred = {}
            for v in views:
                if v.link.blocking_level != "not_relevant":
                    pred.setdefault(v.patent.application_no, v)
            exp = {e["appno"]: e for e in item["patents"] if not e.get("unpublished")}

            disc = _prf(set(pred), set(exp))
            row["discovery"] = disc
            add("disc_tp", disc["tp"]); add("disc_pred", disc["pred"]); add("disc_exp", disc["expected"])

            details = []
            for appno in sorted(set(pred) & set(exp)):
                v, e = pred[appno], exp[appno]
                d = {"appno": appno, "suspect": e.get("suspect")}
                if e.get("pdf_type"):
                    ok = bool(COARSE.get(v.link.claim_category, set()) & set(e["pdf_type"]))
                    d["category"] = {"ours": v.link.claim_category, "pdf": "/".join(e["pdf_type"]), "ok": ok}
                    add("cat_n", 1); add("cat_ok", ok)
                ours_st = STATUS_COARSE.get(v.status, v.status)
                if e.get("pdf_status"):
                    ok = ours_st == e["pdf_status"]
                    d["status"] = {"ours": v.status, "pdf": e["pdf_status"], "ok": ok}
                    add("status_n", 1); add("status_ok", ok)
                if e.get("expiry"):
                    ours_exp = v.patent.expiry_date
                    ok = bool(ours_exp) and abs((ours_exp - e["expiry"]).days) <= 1
                    d["expiry"] = {"ours": ours_exp.isoformat() if ours_exp else None, "pdf": e["expiry"].isoformat(), "ok": ok}
                    add("exp_n", 1); add("exp_ok", ok)
                bad = [k for k in ("category", "status", "expiry") if k in d and not d[k]["ok"]]
                if bad and e.get("suspect"):
                    report["adjudicate"].append({"product": item["product"], "appno": appno, "fields": bad,
                                                 "reason": e["suspect"], **{k: d[k] for k in bad}})
                details.append(d)
            row["matched"] = details
            for appno in disc["extra"]:
                v = pred[appno]
                listed = [p["product"] for p in key["products"] if any(e["appno"] == appno for e in p["patents"])]
                report["adjudicate"].append({
                    "product": item["product"], "appno": appno, "fields": ["extra"],
                    "reason": "linked by the matcher but not listed for this product"
                              + (f" (listed for: {'; '.join(listed)})" if listed else ""),
                    "title": v.patent.title, "category": v.link.claim_category, "status": v.status,
                    "evidence": v.link.evidence_snippet})

            res = assess(dp, list(pred.values()), day)
            vk = item["verdict"]
            ok = res["verdict"] == vk["class"]
            add("verdict_n", 1); add("verdict_ok", ok)
            ours_avoid = {r["application_no"] for r in res["avoid"] + res["blocking"]}
            av = _prf(ours_avoid, set(vk.get("avoid", [])) | set(vk.get("avoid_optional", [])),
                      set(vk.get("avoid_optional", [])))
            add("avoid_tp", av["tp"]); add("avoid_pred", av["pred"]); add("avoid_exp", av["expected"])
            row["verdict"] = {"ours": res["verdict"], "pdf": vk["class"], "ok": ok,
                              "free_date": res["free_date"].isoformat() if res["free_date"] else None, "avoid": av}
            report["products"].append(row)

    report["totals"] = _ratios(tallies.pop("all"))
    report["by_report"] = {k: _ratios(t) for k, t in tallies.items()}
    return report


def write_reports(report: dict):
    runs = OUT / "runs"
    runs.mkdir(exist_ok=True)
    prev = None
    for f in sorted(runs.glob("*.json"), reverse=True):  # latest earlier run of the same kind
        r = json.loads(f.read_text(encoding="utf-8"))
        if r.get("source") == report["source"] and r.get("config") == report["config"]:
            prev = r
            break
    if prev:
        report["regressions"] = [
            {"metric": k, "previous": prev["totals"].get(k), "now": v}
            for k, v in report["totals"].items()
            if isinstance(v, float) and isinstance(prev["totals"].get(k), float) and v < prev["totals"][k]
        ]
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    (runs / f"{stamp}.json").write_text(json.dumps(report, ensure_ascii=False, indent=1, default=str), encoding="utf-8")
    (OUT / "report.json").write_text(json.dumps(report, ensure_ascii=False, indent=1, default=str), encoding="utf-8")

    t = report["totals"]
    cfg = report["config"]
    md = [f"# Blind evaluation — matching over the อย. report patents, as of {report['as_of']} (run {report['run_at']})",
          "", f"Matcher: {'rules + LLM' if cfg['llm'] else 'rules only'}; drug data: "
              f"{'report + PubChem synonyms' if cfg['pubchem'] else 'report only'}.",
          "", "| metric | all | " + " | ".join(report["by_report"]) + " |",
          "|---|---|" + "---|" * len(report["by_report"])]
    for k in METRICS:
        md.append(f"| {k} | {t[k]} | " + " | ".join(str(r[k]) for r in report["by_report"].values()) + " |")
    md.append(f"| products | {t['products']} | " + " | ".join(str(r["products"]) for r in report["by_report"].values()) + " |")
    if report.get("regressions"):
        md += ["", "**Regressions vs previous run:** " + ", ".join(
            f"{r['metric']} {r['previous']} → {r['now']}" for r in report["regressions"])]
    md += ["", "| product | as of | found/expected | extra | verdict (ours / PDF) | avoid recall |", "|---|---|---|---|---|---|"]
    for p in report["products"]:
        if "discovery" not in p:
            md.append(f"| {p['product']} | {p['as_of']} | not in DB | | | |")
            continue
        d, v = p["discovery"], p["verdict"]
        md.append(f"| {p['product']} | {p['as_of']} | {d['tp']}/{d['expected']} | {len(d['extra'])} | "
                  f"{'✅' if v['ok'] else '❌'} {v['ours']} / {v['pdf']} | {v['avoid']['recall']} |")
    md += ["", "## Missed (listed in the PDF, not linked)", ""]
    for p in report["products"]:
        if p.get("discovery", {}).get("missed"):
            md.append(f"- {p['product']}: {', '.join(p['discovery']['missed'])}")
    md += ["", "## Notes on errors (extra links, suspect key items)", ""]
    for a in report["adjudicate"]:
        md.append(f"- **{a['product']}** {a['appno']} [{', '.join(a['fields'])}] — {a['reason']}"
                  + (f" — {a.get('title')}" if a.get("title") else ""))
    (OUT / "report.md").write_text("\n".join(md), encoding="utf-8")

    with (OUT / "report.csv").open("w", encoding="utf-8-sig", newline="") as f:
        w = csv.writer(f)
        w.writerow(["product", "appno", "kind", "ours", "pdf", "ok", "suspect"])
        for p in report["products"]:
            for appno in p.get("discovery", {}).get("missed", []):
                w.writerow([p["product"], appno, "missed", "", "listed", False, ""])
            for appno in p.get("discovery", {}).get("extra", []):
                w.writerow([p["product"], appno, "extra", "linked", "", "", ""])
            for d in p.get("matched", []):
                for k in ("category", "status", "expiry"):
                    if k in d:
                        w.writerow([p["product"], d["appno"], k, d[k]["ours"], d[k]["pdf"], d[k]["ok"], d["suspect"] or ""])
            if "verdict" in p:
                w.writerow([p["product"], "", "verdict", p["verdict"]["ours"], p["verdict"]["pdf"], p["verdict"]["ok"], ""])


def fresh_db(use_llm: bool, pubchem: bool = False) -> str:
    """Load the PDFs into a brand-new DB and run the matcher (no analyst input)."""
    runs = OUT / "runs"
    runs.mkdir(exist_ok=True)
    db = runs / f"fresh-{datetime.now().strftime('%Y%m%d-%H%M%S')}.db"
    env = {**os.environ, "PATENTAPP_DB": str(db)}
    cmd = ([sys.executable, "-m", "etl.load_pdf", "--match"] + ([] if use_llm else ["--no-llm"])
           + (["--pubchem"] if pubchem else []))
    subprocess.run(cmd, cwd=ROOT, env=env, check=True)
    for f in runs.glob(f"{db.name}.pre-pdf-*"):
        f.unlink()  # backup of the empty DB
    return str(db)


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser()
    ap.add_argument("--as-of", type=date.fromisoformat, help="score every product as of this date instead")
    ap.add_argument("--db")
    ap.add_argument("--fresh", action="store_true")
    ap.add_argument("--no-llm", action="store_true")
    ap.add_argument("--pubchem", action="store_true", help="with --fresh: add PubChem synonyms to the drug data")
    a = ap.parse_args()
    db = fresh_db(not a.no_llm, a.pubchem) if a.fresh else a.db
    rep = score(a.as_of, db)
    write_reports(rep)
    print((OUT / "report.md").read_text(encoding="utf-8"))
