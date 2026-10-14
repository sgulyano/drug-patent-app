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
