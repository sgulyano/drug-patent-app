"""FastAPI app: JSON API for the React frontend, CSV export, and (if built) the frontend itself.

    uvicorn backend.app:app --reload --port 8000
"""
import json
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Optional

from fastapi import Body, FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, Response
from fastapi.staticfiles import StaticFiles
from sqlalchemy import or_
from sqlmodel import func, select

from backend.db import ROOT, init_db, session
from backend.models import (Assessment, DrugProduct, ExternalPatentRef, Patent, PatentLink, ProductSubstance,
                            Substance)
from etl.assess import CATEGORY_TH, STATUS_TH, VERDICT_TH, assess, load_views
from etl.export_csv import TABLES, VIEWS, query, to_csv_bytes, view_sql
from etl.normalize import status_as_of
from etl.thai import to_be_str

app = FastAPI(title="Thai Drug–Patent Landscape")
app.add_middleware(CORSMiddleware, allow_origins=["http://localhost:5173", "http://127.0.0.1:5173"],
                   allow_methods=["*"], allow_headers=["*"])


def relevant_level(category: str | None) -> str:
    """Blocking level for a link an analyst confirmed as related (same rule as etl.match.classify_rules)."""
    return "blocking" if category == "drug_substance" else "avoidable"


@app.on_event("startup")
def _startup():
    init_db()
    # A confirmed link is related by definition; heal links confirmed before that was enforced.
    with session() as s:
        for l in s.exec(select(PatentLink).where(PatentLink.review_state == "confirmed",
                                                 or_(PatentLink.blocking_level.is_(None),
                                                     PatentLink.blocking_level == "not_relevant"))):
            l.blocking_level = relevant_level(l.claim_category)
            s.add(l)
        s.commit()


def _d(x):
    return x.isoformat() if isinstance(x, (date, datetime)) else x


def patent_row(p: Patent, as_of: date | None = None, full: bool = False) -> dict:
    out = {
        "id": p.id, "application_no": p.application_no, "filing_date": _d(p.filing_date),
        "filing_date_be": to_be_str(p.filing_date), "publication_no": p.publication_no,
        "publication_date": _d(p.publication_date), "publication_date_be": to_be_str(p.publication_date),
        "patent_no": p.patent_no, "grant_date": _d(p.grant_date), "grant_date_be": to_be_str(p.grant_date),
        "expiry_date": _d(p.expiry_date), "expiry_date_be": to_be_str(p.expiry_date), "expiry_source": p.expiry_source,
        "kind": p.kind, "status_raw": p.status_raw, "status_norm": p.status_norm,
        "status_th": STATUS_TH.get(p.status_norm), "title": p.title, "main_claim": p.main_claim,
        "summary": p.summary, "ipc": p.ipc, "applicant": p.applicant, "source_url": p.source_url,
        "industry": p.industry,
    }
    if as_of:
        st = status_as_of(p, as_of)
        out["status_as_of"], out["status_as_of_th"] = st, STATUS_TH.get(st)
    if full:
        out.update({"all_claims": p.all_claims, "inventors": p.inventors,
                    "discovered_via": json.loads(p.discovered_via or "[]"), "receive_date": _d(p.receive_date)})
    return out


def substance_row(s: Substance) -> dict:
    return {
        "id": s.id, "inn_name": s.inn_name, "salt_form": s.salt_form, "pubchem_cid": s.pubchem_cid,
        "parent_cid": s.parent_cid, "iupac_name": s.iupac_name, "molecular_formula": s.molecular_formula,
        "molecular_weight": s.molecular_weight, "smiles": s.smiles, "inchikey": s.inchikey,
        "structure_img_url": f"/api/structure/{s.pubchem_cid}.png" if s.pubchem_cid else None,
        "description": s.description, "indication": s.indication,
        "mechanism": s.mechanism, "atc_code": s.atc_code,
        "name_variants_th": json.loads(s.name_variants_th) if s.name_variants_th else None,
        "synonyms_en": json.loads(s.synonyms_en or "[]"),
        "pubchem_url": f"https://pubchem.ncbi.nlm.nih.gov/compound/{s.pubchem_cid}" if s.pubchem_cid else None,
    }


def link_row(l: PatentLink) -> dict:
    return {
        "id": l.id, "patent_id": l.patent_id, "substance_id": l.substance_id, "drug_product_id": l.drug_product_id,
        "claim_category": l.claim_category, "category_th": CATEGORY_TH.get(l.claim_category),
        "blocking_level": l.blocking_level, "design_around_note": l.design_around_note,
        "match_method": l.match_method, "matched_terms": json.loads(l.matched_terms or "{}"),
        "confidence": l.confidence, "evidence_snippet": l.evidence_snippet, "rationale": l.rationale,
        "review_state": l.review_state, "reviewed_at": _d(l.reviewed_at),
        "thai_patentability_flag": l.thai_patentability_flag,
    }


def product_substances(s, product_id: int) -> list[Substance]:
    ids = [ps.substance_id for ps in s.exec(select(ProductSubstance).where(ProductSubstance.drug_product_id == product_id))]
    return [s.get(Substance, i) for i in ids]


def review_status(s, dp: DrugProduct) -> dict:
    """A drug counts as confirmed once every related (non-rejected) patent link has been reviewed by an expert."""
    views = load_views(s, dp, date.today(), "auto")
    pending = sum(1 for v in views if v.link.review_state == "candidate")
    return {"total": len(views), "pending": pending, "confirmed": len(views) > 0 and pending == 0}


def live_assessment(s, dp: DrugProduct, as_of: date, mode: str) -> dict:
    res = assess(dp, load_views(s, dp, as_of, mode), as_of, require_review=True)
    stored = s.exec(select(Assessment).where(Assessment.drug_product_id == dp.id, Assessment.mode == mode,
                                             Assessment.analyst_override.is_not(None))
                    .order_by(Assessment.id.desc())).first()
    return {
        "verdict": res["verdict"], "verdict_th": VERDICT_TH[res["verdict"]],
        "free_date": _d(res["free_date"]), "free_date_be": to_be_str(res["free_date"]),
        "fully_clear_date": _d(res["fully_clear_date"]), "fully_clear_date_be": to_be_str(res["fully_clear_date"]),
        "blocking": res["blocking"], "avoid": res["avoid"], "warnings": res["warnings"], "counts": res["counts"],
        "summary_th": res["summary_th"], "as_of": as_of.isoformat(), "mode": mode,
        "analyst_override": stored.analyst_override if stored else None,
        "analyst_note": stored.analyst_note if stored else None,
    }


# ------------------------------------------------------------------ stats
@app.get("/api/stats")
def stats():
    with session() as s:
        return {
            "patents": s.exec(select(func.count()).select_from(Patent)).one(),
            "products": s.exec(select(func.count()).select_from(DrugProduct)).one(),
            "links": s.exec(select(func.count()).select_from(PatentLink)).one(),
            "candidates": s.exec(select(func.count()).select_from(PatentLink).where(PatentLink.review_state == "candidate")).one(),
            "confirmed": s.exec(select(func.count()).select_from(PatentLink).where(PatentLink.review_state == "confirmed")).one(),
            "last_scraped": _d(s.exec(select(func.max(Patent.scraped_at))).one()),
        }


# ------------------------------------------------------------------ patents
def _patent_query(q, status, kind, linked, substance_id, expiry_from, expiry_to):
    stmt = select(Patent)
    if q:
        like = f"%{q}%"
        stmt = stmt.where(or_(Patent.title.like(like), Patent.application_no.like(like), Patent.patent_no.like(like),
                              Patent.applicant.like(like), Patent.main_claim.like(like), Patent.ipc.like(like)))
    if status:
        stmt = stmt.where(Patent.status_norm.in_(status.split(",")))
    if kind:
        stmt = stmt.where(Patent.kind == kind)
    if expiry_from:
        stmt = stmt.where(Patent.expiry_date >= expiry_from)
    if expiry_to:
        stmt = stmt.where(Patent.expiry_date <= expiry_to)
    if linked or substance_id:
        sub = select(PatentLink.patent_id).where(PatentLink.review_state != "rejected")
        if substance_id:
            sub = sub.where(PatentLink.substance_id == substance_id)
        stmt = stmt.where(Patent.id.in_(sub))
    return stmt


@app.get("/api/patents")
def list_patents(q: Optional[str] = None, status: Optional[str] = None, kind: Optional[str] = None,
                 linked: bool = False, substance_id: Optional[int] = None, expiry_from: Optional[date] = None,
                 expiry_to: Optional[date] = None, sort: str = "-filing_date", limit: int = Query(50, le=500),
                 offset: int = 0):
    with session() as s:
        stmt = _patent_query(q, status, kind, linked, substance_id, expiry_from, expiry_to)
        total = s.exec(select(func.count()).select_from(stmt.subquery())).one()
        col = getattr(Patent, sort.lstrip("-"), Patent.filing_date)
        stmt = stmt.order_by(col.desc() if sort.startswith("-") else col.asc()).offset(offset).limit(limit)
        rows = [patent_row(p) for p in s.exec(stmt)]
        ids = [r["id"] for r in rows]
        links = s.exec(select(PatentLink, Substance).join(Substance, Substance.id == PatentLink.substance_id)
                       .where(PatentLink.patent_id.in_(ids), PatentLink.review_state != "rejected")).all()
        by_p = {}
        for l, sub in links:
            by_p.setdefault(l.patent_id, []).append({"substance": sub.inn_name, "substance_id": sub.id,
                                                     "category": l.claim_category, "review_state": l.review_state})
        for r in rows:
            r["links"] = by_p.get(r["id"], [])
            r.pop("summary")
        return {"total": total, "rows": rows}


@app.get("/api/patents/{appno}")
def get_patent(appno: str):
    with session() as s:
        p = s.exec(select(Patent).where(Patent.application_no == appno)).first()
        if not p:
            raise HTTPException(404)
        out = patent_row(p, as_of=date.today(), full=True)
        out["links"] = []
        for l in s.exec(select(PatentLink).where(PatentLink.patent_id == p.id)):
            row = link_row(l)
            row["substance"] = s.get(Substance, l.substance_id).inn_name
            out["links"].append(row)
        return out


# ------------------------------------------------------------------ drugs
@app.get("/api/drugs")
def list_drugs(as_of: Optional[date] = None, mode: str = "auto"):
    as_of = as_of or date.today()
    with session() as s:
        out = []
        for dp in s.exec(select(DrugProduct).order_by(DrugProduct.id)):
            subs = product_substances(s, dp.id)
            a = live_assessment(s, dp, as_of, mode)
            n_links = sum(1 for _ in s.exec(select(PatentLink.id).where(
                PatentLink.substance_id.in_([x.id for x in subs]), PatentLink.review_state != "rejected")))
            out.append({"id": dp.id, "name": dp.name, "dosage_form": dp.dosage_form,
                        "orange_book_note": dp.orange_book_note,
                        "substances": [substance_row(x) for x in subs], "n_links": n_links,
                        "assessment": {k: a[k] for k in ("verdict", "verdict_th", "free_date", "free_date_be",
                                                         "fully_clear_date", "counts", "analyst_override")},
                        "n_avoid": len(a["avoid"]), "n_blocking": len(a["blocking"]),
                        "review": review_status(s, dp)})
        return out


@app.get("/api/drugs/{product_id}")
def get_drug(product_id: int, as_of: Optional[date] = None, mode: str = "auto"):
    as_of = as_of or date.today()
    with session() as s:
        dp = s.get(DrugProduct, product_id)
        if not dp:
            raise HTTPException(404)
        subs = product_substances(s, dp.id)
        links = []
        for sub in subs:
            for l in s.exec(select(PatentLink).where(PatentLink.substance_id == sub.id)):
                p = s.get(Patent, l.patent_id)
                links.append({**link_row(l), "substance": sub.inn_name, "patent": patent_row(p, as_of)})
        refs = [{"jurisdiction": r.jurisdiction, "patent_no": r.patent_no, "claim_type": r.claim_type,
                 "use_code": r.use_code, "expiry": _d(r.expiry), "product_desc": r.product_desc,
                 "applicant": r.applicant}
                for sub in subs for r in s.exec(select(ExternalPatentRef).where(ExternalPatentRef.substance_id == sub.id))]
        return {"id": dp.id, "name": dp.name, "dosage_form": dp.dosage_form, "orange_book_note": dp.orange_book_note,
                "th_generic_registered": dp.th_generic_registered, "th_generic_note": dp.th_generic_note,
                "substances": [substance_row(x) for x in subs], "links": links, "orange_book": refs,
                "assessment": live_assessment(s, dp, as_of, mode), "review": review_status(s, dp)}


@app.patch("/api/drugs/{product_id}")
def update_drug(product_id: int, body: dict = Body(...)):
    with session() as s:
        dp = s.get(DrugProduct, product_id)
        if not dp:
            raise HTTPException(404)
        for k in ("th_generic_registered", "th_generic_note"):
            if k in body:
                setattr(dp, k, body[k])
        s.add(dp)
        s.commit()
        return {"ok": True}


@app.post("/api/drugs/{product_id}/override")
def override(product_id: int, body: dict = Body(...)):
    """Analyst override of the verdict; stored separately from the generated summary."""
    as_of = date.fromisoformat(body.get("as_of") or date.today().isoformat())
    mode = body.get("mode", "confirmed")
    with session() as s:
        dp = s.get(DrugProduct, product_id)
        if not dp:
            raise HTTPException(404)
        res = assess(dp, load_views(s, dp, as_of, mode), as_of, require_review=True)
        a = Assessment(drug_product_id=dp.id, as_of_date=as_of, mode=mode, verdict=res["verdict"],
                       free_date=res["free_date"], fully_clear_date=res["fully_clear_date"],
                       auto_summary_th=res["summary_th"], analyst_override=body.get("verdict") or None,
                       analyst_note=body.get("note") or None,
                       avoid_json=json.dumps({"blocking": res["blocking"], "avoid": res["avoid"]}, ensure_ascii=False),
                       counts_json=json.dumps(res["counts"]))
        s.add(a)
        s.commit()
        return {"ok": True}


# ------------------------------------------------------------------ matching
@app.get("/api/matching/graph")
def graph(as_of: Optional[date] = None, mode: str = "auto"):
    as_of = as_of or date.today()
    with session() as s:
        nodes, edges = [], []
        seen_p = set()
        for dp in s.exec(select(DrugProduct)):
            nodes.append({"id": f"d{dp.id}", "type": "product", "label": dp.name})
            for sub in product_substances(s, dp.id):
                if not any(n["id"] == f"s{sub.id}" for n in nodes):
                    nodes.append({"id": f"s{sub.id}", "type": "substance", "label": sub.inn_name})
                edges.append({"source": f"d{dp.id}", "target": f"s{sub.id}", "type": "contains"})
            for v in load_views(s, dp, as_of, mode):
                if v.link.blocking_level == "not_relevant":
                    continue
                pid = f"p{v.patent.id}"
                if pid not in seen_p:
                    seen_p.add(pid)
                    nodes.append({"id": pid, "type": "patent", "label": v.patent.patent_no or v.patent.application_no,
                                  "title": v.patent.title, "status": v.status, "application_no": v.patent.application_no,
                                  "expiry_date": _d(v.patent.expiry_date)})
                eid = (f"s{v.substance.id}", pid)
                if not any((e["source"], e["target"]) == eid for e in edges):
                    edges.append({"source": eid[0], "target": pid, "type": "link", "category": v.link.claim_category,
                                  "blocking_level": v.link.blocking_level, "review_state": v.link.review_state})
        return {"nodes": nodes, "edges": edges}


# ------------------------------------------------------------------ review queue
@app.get("/api/links")
def list_links(review_state: Optional[str] = "candidate", substance_id: Optional[int] = None,
               limit: int = Query(100, le=1000), offset: int = 0):
    with session() as s:
        stmt = select(PatentLink)
        if review_state:
            stmt = stmt.where(PatentLink.review_state == review_state)
        if substance_id:
            stmt = stmt.where(PatentLink.substance_id == substance_id)
        total = s.exec(select(func.count()).select_from(stmt.subquery())).one()
        stmt = stmt.order_by(PatentLink.substance_id, PatentLink.confidence.desc()).offset(offset).limit(limit)
        rows = []
        for l in s.exec(stmt):
            p = s.get(Patent, l.patent_id)
            rows.append({**link_row(l), "substance": s.get(Substance, l.substance_id).inn_name,
                         "patent": patent_row(p, date.today())})
        return {"total": total, "rows": rows}


@app.patch("/api/links/{link_id}")
def update_link(link_id: int, body: dict = Body(...)):
    with session() as s:
        l = s.get(PatentLink, link_id)
        if not l:
            raise HTTPException(404)
        for k in ("review_state", "claim_category", "blocking_level", "design_around_note", "drug_product_id"):
            if k in body:
                setattr(l, k, body[k])
        if l.review_state == "confirmed" and l.blocking_level in (None, "not_relevant"):
            l.blocking_level = relevant_level(l.claim_category)
        if "review_state" in body:
            l.reviewed_at = None if body["review_state"] == "candidate" else datetime.now(timezone.utc)
        if any(k in body for k in ("claim_category", "blocking_level", "design_around_note")):
            l.match_method = "manual"
        s.add(l)
        s.commit()
        return link_row(l)


@app.post("/api/links")
def add_link(body: dict = Body(...)):
    """Manually link a patent (by application no.) to a substance."""
    with session() as s:
        p = s.exec(select(Patent).where(Patent.application_no == body["application_no"])).first()
        if not p:
            raise HTTPException(404, "patent not in database — scrape it first")
        l = s.exec(select(PatentLink).where(PatentLink.patent_id == p.id,
                                            PatentLink.substance_id == body["substance_id"])).first()
        l = l or PatentLink(patent_id=p.id, substance_id=body["substance_id"])
        l.claim_category = body.get("claim_category")
        l.blocking_level = body.get("blocking_level", "avoidable")
        l.design_around_note = body.get("design_around_note")
        l.match_method, l.review_state, l.reviewed_at = "manual", "confirmed", datetime.now(timezone.utc)
        s.add(l)
        s.commit()
        return link_row(l)


# ------------------------------------------------------------------ export
@app.get("/api/export/{name}.csv")
def export_csv(name: str, product: Optional[str] = None, q: Optional[str] = None, status: Optional[str] = None,
               kind: Optional[str] = None, linked: bool = False, substance_id: Optional[int] = None):
    if name not in VIEWS and name not in TABLES:
        raise HTTPException(404)
    if name == "patents" and any((q, status, kind, linked, substance_id)):
        with session() as s:
            ids = [p.id for p in s.exec(_patent_query(q, status, kind, linked, substance_id, None, None))]
        id_list = ",".join(str(i) for i in ids) or "NULL"
        sql = f"SELECT v.* FROM v_patents v JOIN patent p ON p.application_no = v.application_no WHERE p.id IN ({id_list})"
        cols, rows = query(sql)
    elif product and name in ("drugs", "drug_patent_matrix", "assessments"):
        cols, rows = query(view_sql(name) + " WHERE product = :product", {"product": product})
    else:
        cols, rows = query(view_sql(name))
    stamp = date.today().isoformat()
    return Response(to_csv_bytes(cols, rows), media_type="text/csv; charset=utf-8",
                    headers={"Content-Disposition": f'attachment; filename="{name}_{stamp}.csv"'})


# ------------------------------------------------------------------ structure images (cached PubChem PNGs)
IMG_DIR = ROOT / "data" / "img"


@app.get("/api/structure/{cid}.png")
def structure(cid: int):
    path = IMG_DIR / f"{cid}.png"
    if not path.exists():
        import httpx
        from etl.pubchem import IMG
        try:
            r = httpx.get(IMG.format(cid=cid), timeout=30, follow_redirects=True)
            r.raise_for_status()
        except httpx.HTTPError:
            raise HTTPException(502, "PubChem image unavailable")
        IMG_DIR.mkdir(parents=True, exist_ok=True)
        path.write_bytes(r.content)
    return FileResponse(path, media_type="image/png", headers={"Cache-Control": "max-age=604800"})


# ------------------------------------------------------------------ eval
@app.get("/api/eval/latest")
def eval_latest():
    path = ROOT / "eval" / "report.json"
    if not path.exists():
        return {"available": False}
    return {"available": True, **json.loads(path.read_text(encoding="utf-8"))}


# ------------------------------------------------------------------ frontend (production build)
DIST = ROOT / "frontend" / "dist"
if DIST.exists():
    app.mount("/assets", StaticFiles(directory=DIST / "assets"), name="assets")

    @app.get("/{path:path}")
    def spa(path: str):
        f = DIST / path
        return FileResponse(f if path and f.is_file() else DIST / "index.html")
