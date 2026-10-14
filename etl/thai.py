"""Thai text and Buddhist-Era date helpers."""
import re
import unicodedata
from datetime import date

THAI_MONTHS = {
    "ม.ค.": 1, "ก.พ.": 2, "มี.ค.": 3, "เม.ย.": 4, "พ.ค.": 5, "มิ.ย.": 6,
    "ก.ค.": 7, "ส.ค.": 8, "ก.ย.": 9, "ต.ค.": 10, "พ.ย.": 11, "ธ.ค.": 12,
}


def be_to_ce_year(y: int) -> int:
    return y - 543 if y > 2400 else y


def parse_thai_date(s: str | None) -> date | None:
    """Parse '19/03/2546', '5/03/2546' or '11 ต.ค. 2554' (BE) into a CE date."""
    if not s:
        return None
    s = s.replace("\xa0", " ").strip()
    m = re.match(r"^(\d{1,2})/(\d{1,2})/(\d{4})$", s)
    if m:
        d, mo, y = map(int, m.groups())
        try:
            return date(be_to_ce_year(y), mo, d)
        except ValueError:
            return None
    m = re.match(r"^(\d{1,2})\s+(\S+)\s+(\d{4})$", s)
    if m and m.group(2) in THAI_MONTHS:
        try:
            return date(be_to_ce_year(int(m.group(3))), THAI_MONTHS[m.group(2)], int(m.group(1)))
        except ValueError:
            return None
    return None


def to_be_str(d: date | None) -> str | None:
    return None if d is None else f"{d.day:02d}/{d.month:02d}/{d.year + 543}"


def add_years(d: date, years: int) -> date:
    try:
        return d.replace(year=d.year + years)
    except ValueError:  # 29 Feb
        return d.replace(year=d.year + years, day=28)


_ZW = re.compile("[​‌‍﻿]")


def normalize_text(s: str | None) -> str:
    """Normalise Thai/English text for matching: NFC, no zero-width chars, sara am unified, spaces collapsed, lowercase."""
    if not s:
        return ""
    s = unicodedata.normalize("NFC", s)
    s = _ZW.sub("", s)
    s = s.replace("ํา", "ำ")  # nikhahit + sara aa -> sara am
    s = s.replace("\xa0", " ")
    s = re.sub(r"\s+", " ", s)
    return s.lower().strip()


def squash(s: str | None) -> str:
    """normalize_text with all whitespace removed — Thai text in the DB often has spaces inserted mid-word."""
    return normalize_text(s).replace(" ", "")
