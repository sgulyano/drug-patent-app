"""Stage D — verdict engine. A pure function of links, patent statuses and an as-of date.

    python -m etl.assess                       # as of today, both modes
    python -m etl.assess --as-of 2022-07-20
"""
import argparse
import json
import logging
from dataclasses import dataclass
from datetime import date

from sqlmodel import select

from backend.db import init_db, session
from backend.models import Assessment, DrugProduct, Patent, PatentLink, ProductSubstance, Substance
from etl.normalize import status_as_of
from etl.thai import to_be_str

log = logging.getLogger(__name__)

ACTIVE = {"granted_active", "pending_unexamined", "pending_examining"}
CATEGORY_TH = {
    "drug_substance": "ตัวยาสำคัญ (Drug substance)",
    "solid_form": "รูปแบบของสาร/เกลือ/ผลึก (Solid form)",
    "process": "กระบวนการผลิต (Process)",
    "formulation": "สูตรตำรับ/องค์ประกอบ (Formulation)",
    "device": "อุปกรณ์ให้ยา (Device)",
    "use": "การใช้/ข้อบ่งใช้ (Use)",
    "combination": "ยาผสม (Combination)",
    None: "ไม่ระบุ",
}
STATUS_TH = {
    "granted_active": "ได้รับสิทธิบัตร มีผลคุ้มครอง",
    "pending_unexamined": "ประกาศโฆษณาแล้ว ยังไม่ยื่นตรวจสอบ",
    "pending_examining": "อยู่ระหว่างตรวจสอบการประดิษฐ์",
    "likely_abandoned": "น่าจะถูกละทิ้ง (ไม่ยื่นตรวจสอบภายใน 5 ปี)",
    "expired": "สิ้นอายุ",
    "abandoned": "ละทิ้ง/ยกคำขอ",
    "revoked": "เพิกถอน",
    "not_published": "ยังไม่ประกาศโฆษณา ณ วันที่ประเมิน",
}
VERDICT_TH = {
    "free": "ผลิตได้",
    "free_with_avoidance": "ผลิตได้โดยหลีกเลี่ยง",
    "blocked_until": "ยังผลิตเพื่อจำหน่ายไม่ได้",
    "pending_review": "รอตรวจทานสิทธิบัตร",
    "unknown": "ไม่ทราบ",
}
BOLAR_NOTE = ("สามารถผลิต/ใช้ตามสิทธิบัตรที่ยังมีผลเพื่อการขึ้นทะเบียนตำรับยาได้ก่อนสิทธิบัตรสิ้นอายุ "
              "(ข้อยกเว้นตามมาตรา 36 วรรคสอง (7) พ.ร.บ.สิทธิบัตร) แต่ผลิตเพื่อจำหน่ายได้หลังสิทธิบัตรสิ้นอายุ")


@dataclass
class LinkView:
    link: PatentLink
    patent: Patent
    substance: Substance
    status: str


def assess(product: DrugProduct, items: list[LinkView], as_of: date, require_review: bool = False) -> dict:
    """require_review: a drug is only "free" once an expert has confirmed every related (non-rejected) link;
    until then a would-be "free" verdict becomes "pending_review"."""
    relevant = [i for i in items if i.link.blocking_level != "not_relevant" and i.status != "not_published"]
    active = [i for i in relevant if i.status in ACTIVE]
    blocking = [i for i in active if i.link.blocking_level == "blocking"]
    avoidable = [i for i in active if i.link.blocking_level != "blocking"]
    warnings = [i for i in relevant if i.status == "likely_abandoned"]

    if blocking:
        verdict = "blocked_until"
    elif avoidable:
        verdict = "free_with_avoidance"
    elif require_review and any(i.link.review_state != "confirmed" for i in items):
        verdict = "pending_review"
    else:
        verdict = "free"
    free_date = max((i.patent.expiry_date for i in blocking if i.patent.expiry_date), default=None)
    fully_clear = max((i.patent.expiry_date for i in active if i.patent.expiry_date), default=None)

    counts = {}
    for i in relevant:
        counts[i.status] = counts.get(i.status, 0) + 1

    def row(i: LinkView) -> dict:
        p = i.patent
        return {
            "application_no": p.application_no, "patent_no": p.patent_no, "title": p.title,
            "category": i.link.claim_category, "category_th": CATEGORY_TH.get(i.link.claim_category),
            "blocking_level": i.link.blocking_level, "status": i.status, "status_th": STATUS_TH.get(i.status),
            "expiry_date": p.expiry_date.isoformat() if p.expiry_date else None,
            "expiry_date_be": to_be_str(p.expiry_date), "note": i.link.design_around_note,
            "substance": i.substance.inn_name, "review_state": i.link.review_state,
        }

    avoid_rows = sorted((row(i) for i in avoidable), key=lambda r: (r["category"] or "", r["expiry_date"] or ""))
    return {
        "verdict": verdict,
        "free_date": free_date,
        "fully_clear_date": fully_clear,
        "blocking": [row(i) for i in blocking],
        "avoid": avoid_rows,
        "warnings": [row(i) for i in warnings],
        "counts": counts,
        "summary_th": summary_th(product, verdict, counts, blocking, avoid_rows, warnings, free_date, as_of),
    }


def summary_th(product, verdict, counts, blocking, avoid_rows, warnings, free_date, as_of) -> str:
    lines = [f"ความเป็นไปได้ในการผลิตเชิงพาณิชย์ {product.name} ในประเด็นที่เกี่ยวข้องกับสิทธิบัตร "
             f"(ประเมิน ณ วันที่ {to_be_str(as_of)})"]
    if product.orange_book_note:
        lines.append(f"ข้อมูล Orange Book (สหรัฐอเมริกา): {product.orange_book_note}")
    total = sum(counts.values())
    if total == 0:
        lines.append("ในประเทศไทยไม่พบสิทธิบัตรหรือคำขอรับสิทธิบัตรที่เกี่ยวข้อง")
    else:
        parts = ", ".join(f"{STATUS_TH[k]} {v}" for k, v in sorted(counts.items(), key=lambda kv: -kv[1]))
        lines.append(f"ในประเทศไทยพบสิทธิบัตร/คำขอรับสิทธิบัตรที่เกี่ยวข้อง {total} รายการ ({parts})")
    if blocking:
        lines.append("สิทธิบัตรคุ้มครองตัวยาสำคัญที่ยังมีผล:")
        for i, b in enumerate(blocking, 1):
            pno = f"สิทธิบัตรเลขที่ {b.patent.patent_no}" if b.patent.patent_no else f"คำขอเลขที่ {b.patent.application_no}"
            lines.append(f"  {i}. {pno} — {b.patent.title} (สิ้นอายุ {to_be_str(b.patent.expiry_date)})")
    if avoid_rows:
        lines.append("สิทธิบัตร/คำขอรับสิทธิบัตรที่ยังมีผลซึ่งต้องหลีกเลี่ยง:")
        for i, r in enumerate(avoid_rows, 1):
            pno = f"สิทธิบัตรเลขที่ {r['patent_no']}" if r["patent_no"] else f"คำขอเลขที่ {r['application_no']}"
            lines.append(f"  {i}. {pno} [{r['category_th']}; {r['status_th']}; สิ้นอายุ {r['expiry_date_be']}] "
                         f"{r['title'] or ''} — {r['note'] or ''}")
    if warnings:
        lines.append("คำขอที่น่าจะถูกละทิ้ง (ควรตรวจสอบแฟ้มคำขอที่กรมทรัพย์สินทางปัญญา): "
                     + ", ".join(w.patent.application_no for w in warnings))
    if verdict == "blocked_until":
        concl = (f"ยังไม่สามารถผลิตยาชื่อสามัญ {product.name} เพื่อจำหน่ายได้จนกว่าสิทธิบัตรตัวยาสำคัญสิ้นอายุ "
                 f"({to_be_str(free_date)})")
    elif verdict == "free_with_avoidance":
        concl = f"บริษัทยาสามารถผลิตยาชื่อสามัญ {product.name} จำหน่ายได้ โดยต้องหลีกเลี่ยงรายการข้างต้น"
    elif verdict == "pending_review":
        concl = (f"ยังไม่พบสิทธิบัตรที่มีผลคุ้มครอง {product.name} แต่ผู้เชี่ยวชาญยังตรวจทานสิทธิบัตรที่เกี่ยวข้องไม่ครบ "
                 f"จึงยังไม่สามารถยืนยันได้ว่าผลิตได้")
    else:
        concl = f"บริษัทยาสามารถผลิตยาชื่อสามัญ {product.name} จำหน่ายได้"
    lines.append("สรุป: " + concl)
    if blocking or avoid_rows:
        lines.append("หมายเหตุ: " + BOLAR_NOTE)
    return "\n".join(lines)


def load_views(s, product: DrugProduct, as_of: date, mode: str) -> list[LinkView]:
    sub_ids = [ps.substance_id for ps in s.exec(select(ProductSubstance).where(ProductSubstance.drug_product_id == product.id))]
    out = []
    for sid in sub_ids:
        sub = s.get(Substance, sid)
        for link in s.exec(select(PatentLink).where(PatentLink.substance_id == sid)):
            if link.drug_product_id not in (None, product.id):
                continue
            if link.review_state == "rejected":
                continue
            if mode == "confirmed" and link.review_state != "confirmed":
                continue
            p = s.get(Patent, link.patent_id)
            out.append(LinkView(link, p, sub, status_as_of(p, as_of)))
    return out


def run(as_of: date | None = None, modes=("auto", "confirmed"), db_path=None) -> list[dict]:
    as_of = as_of or date.today()
    init_db(db_path)
    results = []
    with session(db_path) as s:
        for product in s.exec(select(DrugProduct)):
            for mode in modes:
                res = assess(product, load_views(s, product, as_of, mode), as_of, require_review=True)
                s.add(Assessment(
                    drug_product_id=product.id, as_of_date=as_of, mode=mode, verdict=res["verdict"],
                    free_date=res["free_date"], fully_clear_date=res["fully_clear_date"],
                    avoid_json=json.dumps({"blocking": res["blocking"], "avoid": res["avoid"],
                                           "warnings": res["warnings"]}, ensure_ascii=False),
                    counts_json=json.dumps(res["counts"]), auto_summary_th=res["summary_th"]))
                results.append({"product": product.name, "mode": mode, **res})
        s.commit()
    return results


if __name__ == "__main__":
    import sys
    sys.stdout.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser()
    ap.add_argument("--as-of", type=date.fromisoformat)
    a = ap.parse_args()
    for r in run(a.as_of):
        if r["mode"] == "auto":
            print(f"{r['product']}: {r['verdict']} free_date={r['free_date']} avoid={len(r['avoid'])}")
