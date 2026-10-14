"""Discovery crawlers for patentpub.ipthailand.go.th (ASP.NET WebForms, driven with plain HTTP postbacks).

Two entry points:
  * RegistrationEnding.aspx — granted patents whose term ends within 5 years or has ended, filterable by
    industry (กลุ่มอุตสาหกรรม). Used to crawl the whole อุตสาหกรรมยา category (a broad corpus).
  * PublicDocument.aspx — every published application (pending, abandoned, granted ...), searchable by
    title substring. Used for name-based discovery. Needs a publication-date range to return anything.

Notes from probing the site:
  * The ยาเคมี sub-category checkbox is not honoured by plain postbacks, so the category crawl takes the whole
    อุตสาหกรรมยา group (a superset).
  * RegistrationEnding only lists patents expiring within 5 years or already expired; granted patents with
    longer remaining terms are only reachable through PublicDocument.
"""
import logging
import re
from datetime import date
from typing import Iterator

from selectolax.lexbor import LexborHTMLParser

from etl.http import PoliteClient, postback
from etl.thai import parse_thai_date

log = logging.getLogger(__name__)

BASE = "https://patentpub.ipthailand.go.th/_AppPublic/"
REG_URL = BASE + "RegistrationEnding.aspx"
PUB_URL = BASE + "PublicDocument.aspx"
K = "ctl00$contentMain$"
NEXT_PAGE = K + "gvMain$ctl01$ctl05"

INDUSTRY = {"all": "0", "chemical": "10", "pharma": "20", "electrical": "30",
            "instrument": "40", "machinery": "50", "materials": "60"}
INDUSTRY_TH = {"20": "อุตสาหกรรมยา"}
TIME_LEFT = {"<=5y": "60", "expired": "0"}  # "ไม่เกิน 5 ปี" already includes the shorter windows


def _record_info(html: str) -> tuple[int, int, int] | None:
    m = re.search(r"Record ([\d,]+) to ([\d,]+) of ([\d,]+)", html)
    if not m:
        return None
    return tuple(int(x.replace(",", "")) for x in m.groups())


def _rows(html: str) -> list[list[str]]:
    t = LexborHTMLParser(html)
    table = t.css_first("#contentMain_gvMain")
    if not table:
        return []
    out = []
    # The pager sits in a nested table inside <thead>; data rows are the ones whose 2nd cell is an appno.
    for tr in table.css("tr"):
        tds = [td for td in tr.iter() if td.tag == "td"]
        if len(tds) < 6:
            continue
        cells = [td.text(separator=" ", strip=True).replace("\xa0", " ").strip() for td in tds]
        if re.fullmatch(r"\d{6,}", cells[1]):
            out.append(cells)
    return out


def _paginate(client: PoliteClient, url: str, html: str, max_pages: int | None = None) -> Iterator[str]:
    page = 1
    while True:
        yield html
        info = _record_info(html)
        if not info or info[1] >= info[2]:
            return
        if max_pages and page >= max_pages:
            return
        html = postback(client, url, html, NEXT_PAGE)
        page += 1
        if page % 10 == 0:
            log.info("  page %d (%s)", page, info)


# ---------------------------------------------------------------- RegistrationEnding (category crawl)
def parse_registration_row(cells: list[str]) -> dict | None:
    # #, appno, filing date, pub no, pub date, patent no, register date, IPC, title, protection period
    if len(cells) < 10 or not re.fullmatch(r"\d{6,}", cells[1].strip()):
        return None
    return {
        "application_no": cells[1].strip(),
        "filing_date": parse_thai_date(cells[2]),
        "publication_no": cells[3].strip() or None,
        "publication_date": parse_thai_date(cells[4]),
        "patent_no": cells[5].strip() or None,
        "grant_date": parse_thai_date(cells[6]),
        "ipc": cells[7].strip() or None,
        "title": cells[8].strip() or None,
        "protection_left": cells[9].strip() or None,
    }


def crawl_registration(client: PoliteClient, industry: str = "pharma", time_left: str = "expired",
                       keyword: str = "", max_pages: int | None = None) -> Iterator[dict]:
    cat = INDUSTRY[industry]
    html = client.get(REG_URL).text
    html = postback(client, REG_URL, html, K + "lnbRadioChange",
                    **{K + "hdfCatId": cat, K + "cmbTimeLeft": TIME_LEFT[time_left], K + "txtKeyword": keyword})
    log.info("RegistrationEnding industry=%s time_left=%s: %s", industry, time_left, _record_info(html))
    for page_html in _paginate(client, REG_URL, html, max_pages):
        for cells in _rows(page_html):
            row = parse_registration_row(cells)
            if row:
                row["industry"] = INDUSTRY_TH.get(cat)
                yield row


# ---------------------------------------------------------------- PublicDocument (title search)
def parse_public_row(cells: list[str]) -> dict | None:
    # #, appno, filing date, pub no, pub date, title, book/year, applicant, (link)
    if len(cells) < 8 or not re.fullmatch(r"\d{6,}", cells[1].strip()):
        return None
    return {
        "application_no": cells[1].strip(),
        "filing_date": parse_thai_date(cells[2]),
        "publication_no": cells[3].strip() or None,
        "publication_date": parse_thai_date(cells[4]),
        "title": cells[5].strip() or None,
        "applicant": cells[7].strip() or None,
    }


def _be(d: date) -> str:
    return f"{d.day:02d}/{d.month:02d}/{d.year + 543}"


PATENT_TYPES = {"invention": None, "petty": K + "rdoPep"}


def search_publications(client: PoliteClient, title: str = "", patent_type: str = "invention",
                        pub_from: date = date(1957, 1, 1), pub_to: date | None = None,
                        max_pages: int | None = 40, applicant: str = "") -> Iterator[dict]:
    pub_to = pub_to or date.today()
    html = client.get(PUB_URL).text
    if PATENT_TYPES[patent_type]:
        html = postback(client, PUB_URL, html, PATENT_TYPES[patent_type],
                        **{K + "PATTYPE": PATENT_TYPES[patent_type].split("$")[-1]})
    html = postback(client, PUB_URL, html, K + "lnbSearch", **{
        K + "txtSName": title,
        K + "txtSApplicant": applicant,
        K + "tdmSPublicFrom$txtDate": _be(pub_from),
        K + "tdmSPublicTo$txtDate": _be(pub_to),
    })
    info = _record_info(html)
    log.info("PublicDocument title~'%s' applicant~'%s' (%s): %s", title, applicant, patent_type, info)
    if not info:
        return
    for page_html in _paginate(client, PUB_URL, html, max_pages):
        for cells in _rows(page_html):
            row = parse_public_row(cells)
            if row:
                yield row
