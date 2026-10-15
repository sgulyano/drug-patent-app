"""Parse the Thai FDA (อย.) drug-patent reports in data/pdf/ ("รายงานข้อมูลสิทธิบัตรของรายการยาที่อยู่ในบัญชียามุ่งเป้า
ที่มีลำดับความสำคัญสูง …").

Each report has a table of contents listing the products and their start pages, then one section per product:
the substance's IUPAC name and formula, the report date ("ณ วันที่ …"), a 9-column patent table and a conclusion.
The tables are read geometrically: the column borders are the table's vertical rules, every character goes to the
column its centre falls in, and a new table row starts where the first column holds a row number. Rows continue
across page breaks.

    python -m etl.pdf_report data/pdf/<file>.pdf      # print what was parsed
"""
import re
import sys
from bisect import bisect_right
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path

import pymupdf

from etl.thai import parse_thai_date

COLUMNS = ["no", "application", "publication", "patent", "expiry", "type_status", "title", "main_claim", "summary"]
THAI_MONTH_NAMES = {m: i for i, m in enumerate(
    ["มกราคม", "กุมภาพันธ์", "มีนาคม", "เมษายน", "พฤษภาคม", "มิถุนายน", "กรกฎาคม", "สิงหาคม", "กันยายน", "ตุลาคม",
     "พฤศจิกายน", "ธันวาคม"], 1)}
_ROW_NO = re.compile(r"^\s*(\d{1,3})\s*\.*\s*$")
_TOC = re.compile(r"^\s*(?:\d{1,2}\.\s*)?(?P<name>[A-Za-z].*?)\s*\.{4,}\s*(?P<page>\d{1,3})\s*$")
_THAI = re.compile(r"[฀-๿]")


@dataclass
class Row:
    """One row of a patent table, i.e. one application, as printed."""
    no: int
    page: int
    cells: dict[str, str]


@dataclass
class Section:
    """One product's chapter of the report."""
    product: str
    first_page: int  # 1-based
    last_page: int
    text: list[str] = field(default_factory=list)  # lines outside the tables, in reading order
    rows: list[Row] = field(default_factory=list)


@dataclass
class Report:
    path: Path
    sections: list[Section]
    text: list[str]  # lines of the front matter (title page, disclaimer, contents)
    closing: list[str]  # lines after the last product section (the summary chapter)


# ------------------------------------------------------------------ text clean-up
def fix_thai(s: str) -> str:
    """The reports' fonts drop the nikhahit of sara am, which comes out as ' า' ("ส าหรับ" for "สำหรับ")."""
    s = re.sub(r"(?<=[ก-ฮ]) า", "ำ", s)
    s = re.sub(r"(?<=[ก-ฮ][่้๊๋]) า", "ำ", s)
    return s.replace(" ", " ")


def _join(lines: list[str]) -> str:
    """Join wrapped lines of one cell: no space between two Thai fragments (Thai has no word spaces)."""
    out = ""
    for ln in lines:
        ln = ln.strip()
        if not ln:
            continue
        if out and not (_THAI.match(out[-1]) and _THAI.match(ln[0])):
            out += " "
        out += ln
    return re.sub(r"[ \t]+", " ", out).strip()


# ------------------------------------------------------------------ page geometry
def _rules(page):
    """Vertical and horizontal rule segments: [(x, y0, y1)], [(y, x0, x1)]."""
    vert, horiz = [], []
    for dr in page.get_drawings():
        for it in dr["items"]:
            if it[0] == "l":
                a, b = it[1], it[2]
                if abs(a.x - b.x) < 1 and abs(a.y - b.y) > 4:
                    vert.append((a.x, min(a.y, b.y), max(a.y, b.y)))
                elif abs(a.y - b.y) < 1 and abs(a.x - b.x) > 4:
                    horiz.append((a.y, min(a.x, b.x), max(a.x, b.x)))
            elif it[0] == "re":
                r = it[1]
                if r.width < 2 and r.height > 4:
                    vert.append((r.x0, r.y0, r.y1))
                elif r.height < 2 and r.width > 4:
                    horiz.append((r.y0, r.x0, r.x1))
    return vert, horiz


def _tables(page) -> list[tuple[list[float], float, float]]:
    """Tables on the page as (column borders, top, bottom). A table is a run of ≥8 vertical rules sharing a band."""
    vert, _ = _rules(page)
    bands = defaultdict(list)
    for x, y0, y1 in vert:
        bands[(round(y0 / 3), round(y1 / 3))].append(x)
    # merge rules into tables by vertical overlap
    segs = sorted(vert, key=lambda v: v[1])
    tables = []
    for x, y0, y1 in segs:
        for t in tables:
            if y0 <= t["y1"] + 2 and y1 >= t["y0"] - 2:
                t["y0"], t["y1"] = min(t["y0"], y0), max(t["y1"], y1)
                t["xs"].append((x, y1 - y0))
                break
        else:
            tables.append({"y0": y0, "y1": y1, "xs": [(x, y1 - y0)]})
    out = []
    for t in tables:
        weight = Counter()
        for x, h in t["xs"]:
            weight[round(x)] += h
        height = t["y1"] - t["y0"]
        xs = sorted(x for x, w in weight.items() if w > 0.5 * height)
        merged = []
        for x in xs:
            if not merged or x - merged[-1] > 3:
                merged.append(x)
        if len(merged) == len(COLUMNS) + 1:
            out.append((merged, t["y0"], t["y1"]))
    return out


def _lines(page):
    """Text lines as (baseline y, x0, x1, [(char, x centre)]) with characters in content-stream order."""
    out = []
    for b in page.get_text("rawdict")["blocks"]:
        for ln in b.get("lines", []):
            for sp in ln["spans"]:
                chars = [(c["c"], (c["bbox"][0] + c["bbox"][2]) / 2, c["origin"][1]) for c in sp["chars"]]
                if not "".join(c for c, _, _ in chars).strip():
                    continue
                y = Counter(round(yy, 1) for _, _, yy in chars).most_common(1)[0][0]
                out.append((y, sp["bbox"][0], sp["bbox"][2], [(c, x) for c, x, _ in chars]))
    return out


def _page_content(page):
    """(cells, outside): cells = [(baseline y, column index, text)] inside the patent table(s), outside = text lines
    elsewhere on the page as (y, x0, text). The repeated header row is dropped."""
    tables = _tables(page)
    cells, outside = [], []
    for y, x0, x1, chars in _lines(page):
        tab = next((t for t in tables if t[1] - 2 <= y <= t[2] + 2), None)
        if not tab:
            outside.append((y, x0, "".join(c for c, _ in chars)))
            continue
        xs = tab[0]
        parts = defaultdict(str)
        for c, x in chars:
            col = min(max(bisect_right(xs, x) - 1, 0), len(COLUMNS) - 1)
            parts[col] += c
        for col, txt in parts.items():
            if txt.strip():
                cells.append((y, col, txt, tab))
    # header row: everything above the first baseline whose text is a row number or not header wording
    header_bottom = {}
    for tab in tables:
        mine = sorted((c for c in cells if c[3] is tab), key=lambda c: c[0])
        hdr = [c for c in mine if re.search(r"เลขที่|ลำดับ|ลาดับ|ล ำ|ชื่อการประดิษฐ์|ข้อถือสิทธิ|บทสรุป|วันหมดอายุ|ประเภท",
                                             fix_thai(c[2]))]
        if hdr and hdr[0][0] - tab[1] < 30:
            top_band = [c for c in mine if c[0] <= hdr[0][0] + 60]
            nums = [c[0] for c in mine if c[1] == 0 and _ROW_NO.match(c[2])]
            limit = min(nums) if nums else None
            band = [c[0] for c in top_band if (limit is None or c[0] < limit - 4)
                    and re.search(r"เลข|ลำดับ|ลาดับ|ล ำ|ดับ|วัน|ประกาศ|โฆษณา|สิทธิ|ขอ|ชื่อ|บทสรุป|ประเภท|สถานะ|ออก|อายุ|^\s*-?\s*$",
                                  fix_thai(c[2]))]
            header_bottom[id(tab)] = max(band) if band else tab[1]
    cells = [(y, col, txt) for y, col, txt, tab in cells if y > header_bottom.get(id(tab), tab[1] - 10)]
    return cells, outside


# ------------------------------------------------------------------ document
def _toc(lines: list[str]) -> list[tuple[str, int]]:
    out = []
    for ln in lines:
        m = _TOC.match(ln)
        if m and not ln.lstrip().startswith("บทที่"):
            out.append((re.sub(r"\s+", " ", m["name"]).strip(), int(m["page"])))
    return out


def _page_lines(outside) -> list[str]:
    """Merge outside-table fragments sharing a baseline into lines, top to bottom."""
    by_y = defaultdict(list)
    for y, x0, txt in outside:
        key = next((k for k in by_y if abs(k - y) < 2.5), y)
        by_y[key].append((x0, txt))
    lines = (fix_thai(" ".join(t for _, t in sorted(v))) for _, v in sorted(by_y.items()))
    return [re.sub(r"[-]", "", ln).strip() for ln in lines]  # drop private-use bullet glyphs


def parse(path: str | Path) -> Report:
    path = Path(path)
    doc = pymupdf.open(path)
    pages = [_page_content(p) for p in doc]
    front = []
    for i in range(min(6, len(doc))):
        front += _page_lines(pages[i][1])
    toc = _toc(front)
    if not toc:
        raise ValueError(f"{path.name}: no table of contents found")
    # the chapter after the product list (สรุป …) starts at the last TOC page number not attached to a product
    chapter_pages = [int(m.group(1)) for ln in front if ln.lstrip().startswith(("บทที่ 2", "บทที 2"))
                     for m in [re.search(r"(\d{1,3})\s*$", ln)] if m]
    end = chapter_pages[0] - 1 if chapter_pages else len(doc)
    if not chapter_pages or end <= toc[-1][1]:  # chapter 2's contents line lost its title: next TOC page number
        tail = [int(m.group(1)) for ln in front for m in [re.search(r"\.{4,}\s*(\d{1,3})\s*$", ln)] if m]
        later = [p for p in tail if p > toc[-1][1]]
        end = min(later) - 1 if later else len(doc)
    sections = []
    for k, (name, start) in enumerate(toc):
        last = toc[k + 1][1] - 1 if k + 1 < len(toc) else end
        sections.append(Section(product=name, first_page=start, last_page=max(start, last)))
    for sec in sections:
        current: Row | None = None
        buf: dict[str, list[str]] = {}
        for pno in range(sec.first_page, sec.last_page + 1):
            cells, outside = pages[pno - 1]
            sec.text += [ln for ln in _page_lines(outside) if ln and not re.fullmatch(r"\d{1,3}", ln)]
            by_line = defaultdict(dict)
            for y, col, txt in cells:
                key = next((k for k in by_line if abs(k - y) < 2.5), y)
                by_line[key][col] = by_line[key].get(col, "") + txt
            for y in sorted(by_line):
                parts = by_line[y]
                m = _ROW_NO.match(parts.get(0, ""))
                if m and any(parts.get(c, "").strip() for c in range(1, 4)):
                    if current:
                        current.cells = {COLUMNS[c]: fix_thai(_join(v)) for c, v in buf.items()}
                        sec.rows.append(current)
                    current, buf = Row(no=int(m.group(1)), page=pno, cells={}), defaultdict(list)
                    continue_cols = range(1, len(COLUMNS))
                else:
                    continue_cols = range(len(COLUMNS))
                if current is None:
                    continue
                for c in continue_cols:
                    if parts.get(c, "").strip():
                        buf[c].append(parts[c])
        if current:
            current.cells = {COLUMNS[c]: fix_thai(_join(v)) for c, v in buf.items()}
            sec.rows.append(current)
    closing = []
    for pno in range(end + 1, len(doc) + 1):
        closing += [ln for ln in _page_lines(pages[pno - 1][1]) if ln and not re.fullmatch(r"\d{1,3}", ln)]
    return Report(path=path, sections=sections, text=front, closing=closing)


# ------------------------------------------------------------------ fields of a row
_DATE = r"\d{1,2}\s*/+\s*\d{1,2}\s*/+\s*\d\s*\d\s*\d\s*\d"


def _date(s: str | None) -> date | None:
    m = re.search(_DATE, s or "")
    return parse_thai_date(re.sub(r"/+", "/", re.sub(r"\s", "", m.group(0)))) if m else None


def _split_no_date(s: str | None) -> tuple[str | None, date | None]:
    """'9901001234/ 18/12/2542' -> ('9901001234', 1999-12-18). The number may be wrapped mid-way
    ('990100123 4') or carry stray punctuation ('9901001234)')."""
    if not s or not s.strip(" -"):
        return None, None
    m = re.search(_DATE, s)
    if m and "/" not in s[: m.start()]:  # '12345 14/06/2556': no slash between number and date
        head, tail = s[: m.start()], s[m.start():]
    else:
        head, _, tail = re.sub(r"/+", "/", s).partition("/")
    num = re.sub(r"[\s)(]", "", head)
    num = num if re.search(r"\d", num) else None
    return num, _date(tail or s)


def application(row: Row) -> tuple[str | None, date | None]:
    return _split_no_date(row.cells.get("application"))


def publication(row: Row) -> tuple[str | None, date | None]:
    return _split_no_date(row.cells.get("publication"))


def patent(row: Row) -> tuple[str | None, date | None]:
    return _split_no_date(row.cells.get("patent"))


def expiry(row: Row) -> date | None:
    return _date(row.cells.get("expiry"))


def fix_spacing(s: str) -> str:
    """Drop the spaces the PDF puts inside Thai words ('สิ้น อายุ' -> 'สิ้นอายุ'); keep the others."""
    return re.sub(r"(?<=[฀-๿]) +(?=[฀-๿])", "", s)


def _type_status(row: Row) -> tuple[str, str | None]:
    """Split the 'ประเภทคำขอ/สถานะ' cell into the claim type and the status. The status is usually labelled
    'สถานะ:' (sometimes 'สถาน :'); otherwise it follows the '/' after the claim type."""
    s = re.sub(r"\s+", " ", row.cells.get("type_status", "")).strip()
    m = re.search(r"สถาน\s*ะ?\s*:?", s)
    if m:
        return s[: m.start()], s[m.end():]
    m = re.search(r"(claims?|treatment)\s*\)?\s*/+", s, re.I)
    if m:
        return s[: m.end()], s[m.end():]
    return s, None


def claim_type(row: Row) -> str | None:
    """The report's claim type as printed ('Process Claim', 'Drug substance claims (polymorph)', …)."""
    s = re.sub(_DATE, " ", _type_status(row)[0])
    s = re.sub(r"\s+", " ", s).strip(" /-")
    return s or None


def status_text(row: Row) -> str | None:
    """The status as printed, e.g. 'สิ้นอายุสิทธิบัตร' or 'ประกาศโฆษณาคำขอรับสิทธิบัตร 24/01/2557'."""
    s = fix_spacing(re.sub(r"\s+", " ", _type_status(row)[1] or "")).strip(" :/")
    return s or None


def report_date(sec: Section) -> date | None:
    """The 'ณ วันที่ 20 กรกฎาคม 2565' date in the section heading (it may wrap onto the next line)."""
    head = fix_spacing(" ".join(sec.text[:30]))
    m = re.search(r"ณ\s*วันที่\s*(\d{1,2})\s*([ก-๙]+)\s*(?:พ\.ศ\.)?\s*(\d{4})", head)
    if m and m.group(2) in THAI_MONTH_NAMES:
        y = int(m.group(3))
        return date(y - 543 if y > 2400 else y, THAI_MONTH_NAMES[m.group(2)], int(m.group(1)))
    return None


# ------------------------------------------------------------------ the product
_FORM_WORDS = re.compile(
    r"\b(tablets?|capsules?|cap|injection|sterile|solution|powder|inhaler|dpi|orodispersible|disintegrating|"
    r"sustained|prolonged-release|extended|enteric-coated|gastro-resistant|transdermal|oral|suspension|film|for|"
    r"sr|odt|delayed|infusion|syrup|cream|gel|patch|spray)\b|\d|\(", re.I)
_FORMULA = re.compile(r"^C\d+H\d+(?:[A-Z][a-z]?\d*)*(?:[•.·][A-Za-z0-9]+)*")
_CHEMICAL = re.compile(r"\d[,\-]\d|\d-[A-Za-z(\[{]|[\[({]\d|yl|yl[\])]|oic|amide|ate|ol|one", re.I)


def product_name(sec: Section) -> str:
    """The product as headed in its section ('5. Rivastigmine … 4.6 mg/แผ่น'), else as in the contents."""
    key = sec.product[:15].lower()
    for ln in sec.text[:4]:
        name = re.sub(r"\s+", " ", re.sub(r"^\s*\d{1,2}\.\s*", "", ln)).strip()
        if name.lower().startswith(key) and len(name) >= len(sec.product) - 2:
            return name
    return sec.product


def split_product(name: str) -> tuple[list[str], str | None]:
    """'Dutasteride + Tamsulosin Capsule 0.5 mg + 0.4 mg' -> (['Dutasteride', 'Tamsulosin'], 'Capsule 0.5 mg + 0.4 mg')."""
    m = _FORM_WORDS.search(name)
    actives, form = (name[: m.start()], name[m.start():]) if m else (name, None)
    parts = [re.sub(r"\s+", " ", a).strip(" ,") for a in actives.split("+")]
    return [a for a in parts if a], (form.strip() if form else None)


def substance_facts(sec: Section, substance: str) -> dict:
    """IUPAC name, formula, other names and indication printed under the section heading for one substance.
    The 2019 report labels each block with the substance ('Bosentan : …'); the 2023 one leaves the label out,
    so there every unlabelled ':' line belongs to the product's (only) substance."""
    out = {"iupac_name": None, "molecular_formula": None, "synonyms": [], "indication": None}
    head = []
    for ln in sec.text[:16]:
        if re.search(r"ณ\s*วัน|ผลการตรวจสอบ|รายงานการตรวจสอบ|^\s*สรุป|ข้อมูลจาก|ความเป็นไปได้", fix_spacing(ln)):
            if head:
                break
            continue
        head.append(ln)
    first = substance.split()[0].lower()
    blocks: dict[str | None, list[str]] = defaultdict(list)
    current = None
    for ln in head[1:]:  # head[0] is the product heading
        m = re.match(r"^\s*([A-Za-z][A-Za-z \-]{2,40}?)\s*:\s*(.*)$", ln)
        if m:
            label = m.group(1).strip().lower()
            current = ("indication" if label.startswith("indication")
                       else first if label.startswith((first, "other name")) else label)
            blocks[current].append(m.group(2))
        elif ln.lstrip().startswith(":"):
            blocks[current].append(ln.lstrip()[1:])
        elif ln.lower().lstrip().startswith(first):  # 'Omalizumab is a recombinant …'
            current = first
        elif blocks[current]:
            blocks[current][-1] += ("" if blocks[current][-1].endswith("-") else " ") + ln.strip()
    for v in blocks.get(first) or blocks.get(None) or []:
        v = re.sub(r"\s+", " ", v).strip()
        formula = _FORMULA.match(v.replace(" ", ""))
        if formula:
            out["molecular_formula"] = out["molecular_formula"] or formula.group(0)
        elif _CHEMICAL.search(v):  # a systematic name; a second one is an alternative spelling of the first
            out["iupac_name"] = out["iupac_name"] or v
        elif v:  # other names: 'Rapamycin', 'rhuMAb VEGF, anti-VEGF'
            out["synonyms"] += [x.strip(" .") for x in re.split(r"[;,]", v) if 3 <= len(x.strip(" .")) <= 40]
    if blocks.get("indication"):
        out["indication"] = re.sub(r"\s+", " ", " ".join(blocks["indication"])).strip()
    return out


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    rep = parse(sys.argv[1])
    for sec in rep.sections:
        print(f"== {sec.product}  pp.{sec.first_page}-{sec.last_page}  date={report_date(sec)}  rows={len(sec.rows)}")
        for r in sec.rows:
            a = application(r)
            print(f"   {r.no:>3} {a[0]} {a[1]} | pub {publication(r)} | pat {patent(r)} | exp {expiry(r)} | "
                  f"{claim_type(r)} | {status_text(r)} | {(r.cells.get('title') or '')[:50]}")
