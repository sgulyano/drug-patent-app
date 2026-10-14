"""Blind evaluation: score what the pipeline discovered against the held-out answer key from the PDF.

    python -m eval.score                        # score the current database
    python -m eval.score --fresh                # rebuild into a fresh DB from seed/drugs.yaml first (no analyst input)
    python -m eval.score --fresh --skip-crawl

Only automated links are scored (match_method keyword/llm); analyst review decisions are ignored, so the score
measures the pipeline, not the analyst. Disagreements on items the key marks `suspect` go to adjudication
instead of counting as failures.
"""
import argparse
import csv
import json
import os
import shutil
import subprocess
import sys
from datetime import date, datetime
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent
KEY = Path(__file__).resolve().parent / "golden" / "expected.yaml"
OUT = Path(__file__).resolve().parent

COARSE = {"drug_substance": {"drug_substance"}, "solid_form": {"drug_substance", "product"},
          "process": {"process"}, "formulation": {"product"}, "device": {"product"}, "combination": {"product"},
          "use": {"use"}, None: set()}
STATUS_COARSE = {"granted_active": "granted_active", "pending_unexamined": "pending", "pending_examining": "pending",
                 "likely_abandoned": "abandoned", "abandoned": "abandoned", "expired": "expired", "revoked": "revoked",
                 "not_published": "not_published"}


def _prf(pred: set, exp: set, optional: set = frozenset()) -> dict:
    pred_s, exp_s = pred - optional, exp - optional
    tp = len(pred_s & exp_s)
    p = tp / len(pred_s) if pred_s else (1.0 if not exp_s else 0.0)
    r = tp / len(exp_s) if exp_s else 1.0
    return {"tp": tp, "pred": len(pred_s), "expected": len(exp_s), "precision": round(p, 3), "recall": round(r, 3),
            "missed": sorted(exp_s - pred_s), "extra": sorted(pred_s - exp_s)}


def score(as_of: date, db_path: str | None = None) -> dict:
    if db_path:
        os.environ["PATENTAPP_DB"] = db_path
    from sqlmodel import select

    from backend.db import init_db, session
    from backend.models import DrugProduct, Patent, PatentLink, ProductSubstance, Substance
    from etl.assess import LinkView, assess
    from etl.normalize import status_as_of

    key = yaml.safe_load(KEY.read_text(encoding="utf-8"))
    init_db(db_path)
    report = {"as_of": as_of.isoformat(), "run_at": datetime.now().isoformat(timespec="seconds"),
              "db": str(db_path or "default"), "products": [], "adjudicate": []}
    tot = {"disc_tp": 0, "disc_pred": 0, "disc_exp": 0, "cat_ok": 0, "cat_n": 0, "status_ok": 0, "status_n": 0,
           "exp_ok": 0, "exp_n": 0, "verdict_ok": 0, "verdict_n": 0, "avoid_tp": 0, "avoid_pred": 0, "avoid_exp": 0}
    with session(db_path) as s:
        for item in key["products"]:
            dp = s.exec(select(DrugProduct).where(DrugProduct.name == item["product"])).first()
            row = {"product": item["product"], "found_in_db": bool(dp)}
            if not dp:
                report["products"].append(row)
                continue
            sub_ids = [ps.substance_id for ps in s.exec(select(ProductSubstance).where(ProductSubstance.drug_product_id == dp.id))]
            views = []
            for l in s.exec(select(PatentLink).where(PatentLink.substance_id.in_(sub_ids))):
                if l.match_method == "manual" or l.drug_product_id not in (None, dp.id):
                    continue
                p = s.get(Patent, l.patent_id)
                if p.publication_date and p.publication_date > as_of:
                    continue
                views.append(LinkView(l, p, s.get(Substance, l.substance_id), status_as_of(p, as_of)))
            pred = {v.patent.application_no: v for v in views if v.link.blocking_level != "not_relevant"}
            exp = {e["appno"]: e for e in item["patents"] if not e.get("unpublished")}

            disc = _prf(set(pred), set(exp))
            row["discovery"] = disc
            tot["disc_tp"] += disc["tp"]; tot["disc_pred"] += disc["pred"]; tot["disc_exp"] += disc["expected"]

            details = []
            for appno in sorted(set(pred) & set(exp)):
                v, e = pred[appno], exp[appno]
                d = {"appno": appno, "suspect": e.get("suspect")}
                if e.get("pdf_type"):
                    ok = e["pdf_type"] in COARSE.get(v.link.claim_category, set())
                    d["category"] = {"ours": v.link.claim_category, "pdf": e["pdf_type"], "ok": ok}
                    tot["cat_n"] += 1; tot["cat_ok"] += ok
                ours_st = STATUS_COARSE.get(v.status, v.status)
                ok = ours_st == e["pdf_status"]
                d["status"] = {"ours": v.status, "pdf": e["pdf_status"], "ok": ok}
                tot["status_n"] += 1; tot["status_ok"] += ok
                if e.get("expiry"):
                    ours_exp = v.patent.expiry_date
                    ok = bool(ours_exp) and abs((ours_exp - e["expiry"]).days) <= 1
                    d["expiry"] = {"ours": ours_exp.isoformat() if ours_exp else None, "pdf": e["expiry"].isoformat(), "ok": ok}
                    tot["exp_n"] += 1; tot["exp_ok"] += ok
                bad = [k for k in ("category", "status", "expiry") if k in d and not d[k]["ok"]]
                drift = (not d["status"]["ok"] and ours_st in ("revoked", "abandoned")
                         and e["pdf_status"] in ("granted_active", "pending"))
                if drift:
                    d["status"]["drift"] = True
                    report["adjudicate"].append({
                        "product": item["product"], "appno": appno, "fields": ["status"],
                        "reason": f"status today is '{v.patent.status_raw}' — the change probably happened after the "
                                  f"report date, but public data has no date for it", "status": d["status"]})
                if bad and e.get("suspect"):
                    report["adjudicate"].append({"product": item["product"], "appno": appno, "fields": bad,
                                                 "reason": e["suspect"], **{k: d[k] for k in bad}})
                details.append(d)
            row["matched"] = details
            for appno in disc["extra"]:
                v = pred[appno]
                report["adjudicate"].append({"product": item["product"], "appno": appno, "fields": ["extra"],
                                             "reason": "found by the pipeline but not in the PDF",
                                             "title": v.patent.title, "category": v.link.claim_category,
                                             "status": v.status, "evidence": v.link.evidence_snippet})

            res = assess(dp, list(pred.values()), as_of)
            vk = item["verdict"]
            ok = res["verdict"] == vk["class"]
            tot["verdict_n"] += 1; tot["verdict_ok"] += ok
            ours_avoid = {r["application_no"] for r in res["avoid"] + res["blocking"]}
            av = _prf(ours_avoid, set(vk.get("avoid", [])) | set(vk.get("avoid_optional", [])),
                      set(vk.get("avoid_optional", [])))
            tot["avoid_tp"] += av["tp"]; tot["avoid_pred"] += av["pred"]; tot["avoid_exp"] += av["expected"]
            row["verdict"] = {"ours": res["verdict"], "pdf": vk["class"], "ok": ok,
                              "free_date": res["free_date"].isoformat() if res["free_date"] else None, "avoid": av}
            report["products"].append(row)

    def ratio(a, b):
        return round(a / b, 3) if b else None
    report["totals"] = {
        "discovery_recall": ratio(tot["disc_tp"], tot["disc_exp"]),
        "discovery_precision": ratio(tot["disc_tp"], tot["disc_pred"]),
        "category_accuracy": ratio(tot["cat_ok"], tot["cat_n"]),
        "status_accuracy": ratio(tot["status_ok"], tot["status_n"]),
        "expiry_accuracy": ratio(tot["exp_ok"], tot["exp_n"]),
        "verdict_accuracy": ratio(tot["verdict_ok"], tot["verdict_n"]),
        "avoid_recall": ratio(tot["avoid_tp"], tot["avoid_exp"]),
        "avoid_precision": ratio(tot["avoid_tp"], tot["avoid_pred"]),
        **{k: v for k, v in tot.items()},
    }
    return report


def write_reports(report: dict):
    runs = OUT / "runs"
    runs.mkdir(exist_ok=True)
    prev_files = sorted(runs.glob("*.json"))
    prev = json.loads(prev_files[-1].read_text(encoding="utf-8")) if prev_files else None
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
    md = [f"# Blind evaluation — as of {report['as_of']} (run {report['run_at']})", "",
          "| metric | value |", "|---|---|"]
    for k in ("discovery_recall", "discovery_precision", "category_accuracy", "status_accuracy", "expiry_accuracy",
              "verdict_accuracy", "avoid_recall", "avoid_precision"):
        md.append(f"| {k} | {t[k]} |")
    if report.get("regressions"):
        md += ["", "**Regressions vs previous run:** " + ", ".join(
            f"{r['metric']} {r['previous']} → {r['now']}" for r in report["regressions"])]
    md += ["", "| product | found/expected | extra | verdict (ours / PDF) | avoid recall |", "|---|---|---|---|---|"]
    for p in report["products"]:
        if "discovery" not in p:
            md.append(f"| {p['product']} | not in DB | | | |")
            continue
        d, v = p["discovery"], p["verdict"]
        md.append(f"| {p['product']} | {d['tp']}/{d['expected']} | {len(d['extra'])} | "
                  f"{'✅' if v['ok'] else '❌'} {v['ours']} / {v['pdf']} | {v['avoid']['recall']} |")
    md += ["", "## Missed (in the PDF, not found)", ""]
    for p in report["products"]:
        if p.get("discovery", {}).get("missed"):
            md.append(f"- {p['product']}: {', '.join(p['discovery']['missed'])}")
    md += ["", "## For human adjudication", ""]
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
                w.writerow([p["product"], appno, "extra", "found", "", "", ""])
            for d in p.get("matched", []):
                for k in ("category", "status", "expiry"):
                    if k in d:
                        w.writerow([p["product"], d["appno"], k, d[k]["ours"], d[k]["pdf"], d[k]["ok"], d["suspect"] or ""])
            if "verdict" in p:
                w.writerow([p["product"], "", "verdict", p["verdict"]["ours"], p["verdict"]["pdf"], p["verdict"]["ok"], ""])


def fresh_db(skip_crawl: bool) -> str:
    """Build a brand-new DB from the seed list only (raw page caches are reused; they hold no answers)."""
    runs = OUT / "runs"
    runs.mkdir(exist_ok=True)
    db = runs / f"fresh-{datetime.now().strftime('%Y%m%d-%H%M%S')}.db"
    env = {**os.environ, "PATENTAPP_DB": str(db)}
    # seed the fresh DB with the cached category corpus so the crawl does not need to re-list
    cmd = [sys.executable, "-m", "etl.pipeline", "--as-of", key_as_of().isoformat()]
    if skip_crawl:
        cmd.append("--skip-crawl")
        shutil.copy(ROOT / "data" / "app.db", db)  # corpus only; links/assessments are wiped below
        import sqlite3
        con = sqlite3.connect(db)
        con.executescript("DELETE FROM patent_link; DELETE FROM assessment; UPDATE substance SET name_variants_th=NULL;")
        con.commit()
        con.close()
    subprocess.run(cmd, cwd=ROOT, env=env, check=True)
    return str(db)


def key_as_of() -> date:
    return yaml.safe_load(KEY.read_text(encoding="utf-8"))["as_of"]


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser()
    ap.add_argument("--as-of", type=date.fromisoformat)
    ap.add_argument("--db")
    ap.add_argument("--fresh", action="store_true")
    ap.add_argument("--skip-crawl", action="store_true")
    a = ap.parse_args()
    db = fresh_db(a.skip_crawl) if a.fresh else a.db
    rep = score(a.as_of or key_as_of(), db)
    write_reports(rep)
    print((OUT / "report.md").read_text(encoding="utf-8"))
