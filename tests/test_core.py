"""Unit tests on synthetic data (no answer-key content)."""
from datetime import date
from pathlib import Path

from backend.models import DrugProduct, Patent, PatentLink, Substance
from etl.assess import LinkView, assess
from etl.normalize import compute_expiry, norm_status, status_as_of
from etl.scrape_detail import extract_main_claim, kind_from_appno, parse_detail
from etl.thai import parse_thai_date, to_be_str
from etl.thai_translit import generate, skel

FIX = Path(__file__).parent / "fixtures"


def test_thai_dates():
    assert parse_thai_date("19/03/2546") == date(2003, 3, 19)
    assert parse_thai_date("5/03/2546") == date(2003, 3, 5)
    assert parse_thai_date("11 ต.ค. 2554") == date(2011, 10, 11)
    assert parse_thai_date("") is None
    assert to_be_str(date(2003, 3, 19)) == "19/03/2546"


def test_expiry_invention_and_petty():
    p = Patent(application_no="9901999999", filing_date=date(2000, 1, 15), kind="invention")
    assert compute_expiry(p) == (date(2020, 1, 14), "computed_invention_20y")
    p.kind = "petty"
    assert compute_expiry(p)[0] == date(2010, 1, 14)
    assert kind_from_appno("1203001234") == "petty"
    assert kind_from_appno("1201001234") == "invention"


def test_status_map():
    assert norm_status("สิ้นอายุสิทธิบัตร/อนุสิทธิบัตร") == "expired"
    assert norm_status("ละทิ้งคำขอรับสิทธิบัตรหลังประกาศโฆษณา") == "abandoned"
    assert norm_status("ยื่นคำขอชำระค่าธรรมเนียมรายปี") == "granted_active"
    assert norm_status("ยื่นขอให้ตรวจสอบการประดิษฐ์") == "pending_examining"
    assert norm_status("แจ้งเพิกถอน") == "revoked"


def test_status_as_of_point_in_time():
    p = Patent(application_no="x", filing_date=date(2005, 1, 1), expiry_date=date(2024, 12, 31),
               publication_date=date(2007, 1, 1), grant_date=date(2015, 1, 1), status_norm="expired")
    assert status_as_of(p, date(2025, 6, 1)) == "expired"
    assert status_as_of(p, date(2020, 1, 1)) == "granted_active"
    assert status_as_of(p, date(2010, 1, 1)) == "pending_examining"
    assert status_as_of(p, date(2006, 1, 1)) == "not_published"
    q = Patent(application_no="y", filing_date=date(2010, 1, 1), expiry_date=date(2029, 12, 31),
               publication_date=date(2012, 1, 1), status_norm="pending_unexamined")
    assert status_as_of(q, date(2016, 1, 1)) == "pending_unexamined"
    assert status_as_of(q, date(2018, 1, 1)) == "likely_abandoned"
    q.status_norm = "abandoned"
    assert status_as_of(q, date(2015, 1, 1)) == "pending_unexamined"  # exam window still open then
    assert status_as_of(q, date(2020, 1, 1)) == "abandoned"


def test_main_claim_extraction():
    txt = "แก้ไข 13/01/2563\nไม่มีข้อถือสิทธิ\n---------\n1. สารประกอบ 2-(4-คลอโร)ฟีนิล\nและเกลือ\n 2. สารผสมตามข้อ 1"
    assert extract_main_claim(txt) == "1. สารประกอบ 2-(4-คลอโร)ฟีนิล\nและเกลือ"
    assert extract_main_claim("1.ยาเม็ดขนาด 2.5 มก. ที่มี\n2.ยาเม็ดตามข้อ 1").startswith("1. ยาเม็ดขนาด 2.5 มก.")


def test_detail_parser_on_cached_page():
    d = parse_detail((FIX / "detail_sample.html").read_text(encoding="utf-8"))
    assert d["application_no"] and d["filing_date"] and d["title"]
    assert d["main_claim"].startswith("1.")
    assert d["status_raw"]


def test_transliteration_and_skeleton():
    gens = [g for g, _ in generate("amlodipine", 30)]
    assert any(skel(g) == skel("แอมโลดิปีน") for g in gens)
    assert skel("ทิโอโทรเพียม") == skel("ติโอโตรเปียม")  # consonant-class / vowel-length variants collapse


def _view(appno, cat, blocking, status, exp):
    p = Patent(application_no=appno, patent_no=None, title="t", expiry_date=exp)
    return LinkView(PatentLink(patent_id=1, substance_id=1, claim_category=cat, blocking_level=blocking,
                               design_around_note="n"), p, Substance(inn_name="x"), status)


def test_verdict_engine():
    dp = DrugProduct(name="X tablet")
    asof = date(2022, 1, 1)
    assert assess(dp, [], asof)["verdict"] == "free"
    r = assess(dp, [_view("a", "drug_substance", "blocking", "expired", date(2020, 1, 1)),
                    _view("b", "formulation", "avoidable", "granted_active", date(2030, 1, 1))], asof)
    assert r["verdict"] == "free_with_avoidance" and [x["application_no"] for x in r["avoid"]] == ["b"]
    r = assess(dp, [_view("a", "drug_substance", "blocking", "granted_active", date(2025, 5, 5)),
                    _view("b", "process", "avoidable", "pending_examining", date(2033, 1, 1))], asof)
    assert r["verdict"] == "blocked_until" and r["free_date"] == date(2025, 5, 5)
    assert r["fully_clear_date"] == date(2033, 1, 1)
    r = assess(dp, [_view("c", "use", "avoidable", "likely_abandoned", date(2030, 1, 1))], asof)
    assert r["verdict"] == "free" and len(r["warnings"]) == 1
    # with review required, "free" needs every related link confirmed by an expert
    assert assess(dp, [], asof, require_review=True)["verdict"] == "free"
    r = assess(dp, [_view("a", "drug_substance", "blocking", "expired", date(2020, 1, 1))], asof, require_review=True)
    assert r["verdict"] == "pending_review"
    v = _view("a", "drug_substance", "blocking", "expired", date(2020, 1, 1))
    v.link.review_state = "confirmed"
    assert assess(dp, [v], asof, require_review=True)["verdict"] == "free"


def test_peptide_sequence_matching():
    from etl import peptide
    seq = "pGlu-His-Trp-Ser-Tyr-D-Leu-Leu-Arg-Pro-NHEt"  # a generic LH-RH analogue pattern
    assert peptide.core(seq) == ("His", "Trp", "Ser", "Tyr", "Leu", "Leu", "Arg")
    assert peptide.mentions("5-oxo-Pro-His-Trp-Ser-Tyr-DLeu-Leu-Arg-Pro-NH-C2H5", seq)
    assert not peptide.mentions("pGlu-His-Trp-Ser-Tyr-D-Trp-Leu-Arg-Pro-Gly-NH2", seq)  # different analogue
    assert not peptide.mentions("Tyr-Y-Leu-Arg-Pro-Z", seq)


def test_every_field_is_described():
    from sqlmodel import SQLModel
    for table in SQLModel.metadata.sorted_tables:
        model = next(m for m in SQLModel.__subclasses__() if getattr(m, "__tablename__", None) == table.name)
        assert (model.__doc__ or "").strip(), f"{model.__name__} has no docstring"
        missing = [n for n, f in model.model_fields.items() if not (f.description or "").strip()]
        assert not missing, f"{model.__name__} fields without a description: {missing}"


def test_migration_from_old_schema(tmp_path):
    """An app.db from before the model refactor keeps its data: renamed and dropped columns, the Orange Book note
    moved to the substance, JSON text read back as lists/dicts."""
    import sqlite3
    from sqlmodel import Session, select
    from backend.db import init_db
    from backend.models import Assessment

    path = tmp_path / "old.db"
    con = sqlite3.connect(path)
    con.executescript("""
        CREATE TABLE drug_product (id INTEGER PRIMARY KEY, name VARCHAR UNIQUE, dosage_form VARCHAR, route VARCHAR,
            strengths VARCHAR, on_target_list BOOLEAN, th_generic_registered BOOLEAN, th_generic_note VARCHAR,
            orange_book_note VARCHAR);
        CREATE TABLE substance (id INTEGER PRIMARY KEY, inn_name VARCHAR UNIQUE, structure_img_url VARCHAR,
            synonyms_en VARCHAR, name_variants_th VARCHAR);
        CREATE TABLE product_substance (drug_product_id INTEGER, substance_id INTEGER,
            PRIMARY KEY (drug_product_id, substance_id));
        CREATE TABLE external_patent_ref (id INTEGER PRIMARY KEY, substance_id INTEGER, jurisdiction VARCHAR,
            patent_no VARCHAR, found_in_th BOOLEAN);
        CREATE TABLE assessment (id INTEGER PRIMARY KEY, drug_product_id INTEGER, as_of_date DATE, mode VARCHAR,
            verdict VARCHAR, avoid_json VARCHAR, counts_json VARCHAR, created_at DATETIME);
        CREATE VIEW v_drugs AS SELECT dp.orange_book_note, s.structure_img_url FROM drug_product dp, substance s;
        INSERT INTO drug_product (id, name, route, orange_book_note) VALUES (1, 'X tablet', 'oral', 'OB note');
        INSERT INTO substance (id, inn_name, structure_img_url, synonyms_en, name_variants_th)
            VALUES (1, 'x', 'http://img', '["X-1", "Xeno"]', '{"observed": [{"spelling": "เอ็กซ์", "docs": 1}]}');
        INSERT INTO product_substance VALUES (1, 1);
        INSERT INTO assessment (id, drug_product_id, as_of_date, mode, verdict, avoid_json, counts_json, created_at)
            VALUES (1, 1, '2022-07-20', 'auto', 'free', '{"avoid": []}', '{"expired": 2}', '2022-07-20 00:00:00');
    """)
    con.close()

    engine = init_db(path)
    init_db(path)  # second run is a no-op
    con = sqlite3.connect(path)
    cols = lambda t: {r[1] for r in con.execute(f"PRAGMA table_info({t})")}  # noqa: E731
    assert not cols("drug_product") & {"route", "strengths", "on_target_list", "orange_book_note"}
    assert "structure_img_url" not in cols("substance") and "found_in_th" not in cols("external_patent_ref")
    assert {"avoid_lists", "status_counts"} <= cols("assessment")
    assert not cols("assessment") & {"avoid_json", "counts_json"}
    con.close()
    with Session(engine) as s:
        sub = s.get(Substance, 1)
        assert sub.orange_book_note == "OB note"
        assert sub.synonyms_en == ["X-1", "Xeno"] and sub.name_variants_th["observed"][0]["spelling"] == "เอ็กซ์"
        a = s.exec(select(Assessment)).one()
        assert a.avoid_lists == {"avoid": []} and a.status_counts == {"expired": 2}
    engine.dispose()
    assert list(tmp_path.glob("old.db.pre-migration-*"))


def test_status_map_pdf_vocabulary():
    # an amendment filed *before* registration is still under examination, not granted
    assert norm_status("ยื่นคำขอแก้ไขเพิ่มเติมก่อนรับจดทะเบียนเข้ามาเอง 02/06/2558") == "pending_examining"
    assert norm_status("ยื่นคำขอแก้ไขเพิ่มเติมก่อนการรับจดทะเบียนเข้ามาเอง") == "pending_examining"
    assert norm_status("ยื่นชำระค่าธรรมเนียมรายปีจะสิ้นอายุสิทธิบัตร 05/06/2561") == "granted_active"
    assert norm_status("แจ้งนำส่งหนังสือสำคัญ 10/11/2559") == "granted_active"
    assert norm_status("ยื่นเอกสารสิทธิบัตรและรายงานการตรวจสอบที่ได้รับจากต่างประเทศ") == "pending_examining"
    assert norm_status("ประกาศโฆษณา 09/01/2549 *น่าจะละทิ้งแล้ว") == "abandoned"


def test_status_observed_on_a_date_is_trusted_from_then_on():
    q = Patent(application_no="y", filing_date=date(2010, 1, 1), publication_date=date(2016, 1, 1),
               status_norm="abandoned", status_date=date(2019, 7, 15))
    assert status_as_of(q, date(2019, 7, 15)) == "abandoned"  # the report saw it abandoned on its date
    assert status_as_of(q, date(2018, 1, 1)) == "pending_unexamined"  # before that: exam window still open
    q.expiry_date = date(2029, 12, 31)
    assert status_as_of(q, date(2031, 1, 1)) == "abandoned"  # it was never granted, so it doesn't "expire"
    q.status_date = None
    assert status_as_of(q, date(2019, 7, 15)) == "pending_unexamined"  # unknown date: old behaviour


def _row(**cells):
    from etl.pdf_report import Row
    return Row(no=1, page=1, cells=cells)


def test_pdf_row_fields():
    from etl import pdf_report as R
    r = _row(application="990100123 4/ 18/12/ 2542", publication="12345 07/11/2546", patent="-",
             expiry="28//06/2580", type_status="Drug substance claims (polymorph) / สถาน : สิ้น อายุสิทธิบัตร")
    assert R.application(r) == ("9901001234", date(1999, 12, 18))
    assert R.publication(r) == ("12345", date(2003, 11, 7))
    assert R.patent(r) == (None, None)
    assert R.expiry(r) == date(2037, 6, 28)
    assert R.claim_type(r) == "Drug substance claims (polymorph)"
    assert R.status_text(r) == "สิ้นอายุสิทธิบัตร"
    unlabelled = _row(type_status="Product claim/ ยื่นคำขอแก้ไขเพิ่มเติม 2 มิ.ย.58")
    assert R.claim_type(unlabelled) == "Product claim" and R.status_text(unlabelled).startswith("ยื่นคำขอแก้ไข")
    assert R.fix_thai("ส าหรับ น้ า") == "สำหรับ น้ำ"


def test_pdf_product_and_substance_header():
    from etl import pdf_report as R
    assert R.split_product("Abcxine + Defzole Capsule 0.5 mg + 0.4 mg") == (["Abcxine", "Defzole"], "Capsule 0.5 mg + 0.4 mg")
    assert R.split_product("Abcxine Sodium Enteric-coated Tablet 20 mg") == (["Abcxine Sodium"], "Enteric-coated Tablet 20 mg")
    sec = R.Section(product="Abcxine Tablet 5 mg", first_page=1, last_page=1, text=[
        "1. Abcxine Tablet 5 mg", "Abcxine : 2-[4-(2-hydroxyethyl)phenyl]-2-methylpropanoic acid", ": C 12 H 16 O 3",
        ": Abcmycin; Xyz-1", "Indication : pain", "ผลการตรวจสอบข้อมูลสิทธิบัตรยา Abcxine Tablet 5 mg ณ วันที่ 20 มิถุนายน 2560"])
    f = R.substance_facts(sec, "Abcxine")
    assert f["molecular_formula"] == "C12H16O3" and f["iupac_name"].startswith("2-[4-")
    assert f["synonyms"] == ["Abcmycin", "Xyz-1"] and f["indication"] == "pain"
    assert R.report_date(sec) == date(2017, 6, 20)


def test_pdf_loader_keeps_no_trace_of_the_product_section():
    """A patent record holds patent fields only: not the product it was listed under, nor the report's claim type."""
    from etl.load_pdf import patent_record
    rec = patent_record(_row(application="9901001234/ 18/12/2542", publication="12345/ 07/11/2546",
                             type_status="Process Claim/ สถานะ: ละทิ้งคำขอรับสิทธิบัตรหลังประกาศโฆษณา",
                             title="กรรมวิธี", main_claim="1. กรรมวิธี 2. ตามข้อ 1"), date(2019, 7, 15), "pdf:x.pdf")
    assert set(rec) <= set(Patent.model_fields)
    assert rec["discovered_via"] == ["pdf:x.pdf"] and rec["status_norm"] == "abandoned"
    assert "process" not in " ".join(str(v) for v in rec.values()).lower()  # the claim type is the matcher's job
    assert rec["main_claim"] == "1. กรรมวิธี"
