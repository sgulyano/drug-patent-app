"""Load US FDA Orange Book data (products.txt, patent.txt) for each substance.

    python -m etl.orange_book            # downloads the current EOB zip if missing

Note: this is today's Orange Book, not a historical snapshot. It is context for the analyst and is not scored
by the blind evaluation.
"""
import csv
import io
import logging
import zipfile
from collections import defaultdict
from datetime import datetime

from sqlmodel import delete, select

from backend.db import DATA_DIR, init_db, session
from backend.models import ExternalPatentRef, Substance
from etl.http import PoliteClient

log = logging.getLogger(__name__)
URL = "https://www.fda.gov/media/76860/download?attachment"
DIR = DATA_DIR / "orange_book"


def _read(name: str) -> list[dict]:
    text = (DIR / name).read_text(encoding="latin-1")
    return list(csv.DictReader(io.StringIO(text), delimiter="~"))


def ensure_files(refresh: bool = False):
    if refresh or not (DIR / "patent.txt").exists():
        DIR.mkdir(parents=True, exist_ok=True)
        r = PoliteClient().get(URL)
        zipfile.ZipFile(io.BytesIO(r.content)).extractall(DIR)


def _date(s: str):
    try:
        return datetime.strptime(s.strip(), "%b %d, %Y").date()
    except ValueError:
        return None


def run(refresh: bool = False):
    init_db()
    ensure_files(refresh)
    products = _read("products.txt")
    patents = _read("patent.txt")
    by_appl = defaultdict(list)
    for p in patents:
        by_appl[(p["Appl_Type"], p["Appl_No"])].append(p)
    with session() as s:
        for sub in s.exec(select(Substance)):
            # US names can differ from the INN (USAN), so also try single-word PubChem synonyms
            syns = [x for x in sub.synonyms_en or [] if x.isalpha()][:15]
            keys = {sub.inn_name.upper(), *(x.upper() for x in syns)}
            mine = [p for p in products if keys & set(p["Ingredient"].upper().replace(",", " ").replace(";", " ").split())]
            ndas = {(p["Appl_Type"], p["Appl_No"]) for p in mine if p["Appl_Type"] == "N"}
            andas = {p["Appl_No"] for p in mine if p["Appl_Type"] == "A"}
            s.exec(delete(ExternalPatentRef).where(ExternalPatentRef.substance_id == sub.id))
            seen = {}
            for appl in ndas:
                prod = next(p for p in mine if (p["Appl_Type"], p["Appl_No"]) == appl)
                for pt in by_appl.get(appl, []):
                    no = pt["Patent_No"]
                    kind = ("drug_substance" if pt["Drug_Substance_Flag"] == "Y"
                            else "drug_product" if pt["Drug_Product_Flag"] == "Y"
                            else "use" if pt["Patent_Use_Code"] else "other")
                    if no in seen:
                        continue
                    seen[no] = ExternalPatentRef(
                        substance_id=sub.id, jurisdiction="US", patent_no=no, claim_type=kind,
                        use_code=pt["Patent_Use_Code"] or None, expiry=_date(pt["Patent_Expire_Date_Text"]),
                        product_desc=f'{prod["Trade_Name"]} ({prod["DF;Route"]})', applicant=prod["Applicant_Full_Name"])
            for ref in seen.values():
                s.add(ref)
            # substance-level note, shown on every product that contains it
            kinds = defaultdict(int)
            for ref in seen.values():
                kinds[ref.claim_type] += 1
            latest = max((r.expiry for r in seen.values() if r.expiry), default=None)
            brands = sorted({p["Trade_Name"] for p in mine if p["Appl_Type"] == "N"})
            if mine:
                note = (f"ยาต้นแบบ {', '.join(brands[:3]) or '-'}; สิทธิบัตรที่ขึ้นทะเบียน {len(seen)} ฉบับ "
                        f"(ตัวยา {kinds['drug_substance']}, ผลิตภัณฑ์ {kinds['drug_product']}, การใช้ {kinds['use']})"
                        + (f", สิ้นอายุล่าสุด {latest.isoformat()}" if latest else "")
                        + f"; ยาชื่อสามัญ (ANDA) ได้รับอนุมัติ {len(andas)} รายการ")
            else:
                note = "ไม่พบใน Orange Book (ยาไม่ได้รับอนุมัติในสหรัฐอเมริกา)"
            sub.orange_book_note = note
            s.add(sub)
            log.info("%s: %s", sub.inn_name, note)
        s.commit()


if __name__ == "__main__":
    import sys
    sys.stdout.reconfigure(encoding="utf-8")
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    run()
