"""Fetch and parse ApplicationDetail.aspx pages.

    python -m etl.scrape_detail --appno 0123456789 [--refresh]
    python -m etl.scrape_detail --queue          # fetch every queued appno (data/queue.txt)
"""
import argparse
import json
import logging
import re
from datetime import datetime, timezone
from pathlib import Path

from selectolax.lexbor import LexborHTMLParser
from sqlmodel import select

from backend.db import DATA_DIR, init_db, session
from backend.models import Patent
from etl.http import PoliteClient
from etl.thai import parse_thai_date

log = logging.getLogger(__name__)

BASE = "https://patentpub.ipthailand.go.th/_AppPublic/"
DETAIL_URL = BASE + "ApplicationDetail.aspx?appno={appno}"
RAW_DIR = DATA_DIR / "raw" / "detail"
QUEUE = DATA_DIR / "queue.jsonl"

FIELDS = {
    "application_no": "lblAppNo",
    "filing_date": "lblSubmitDate",
    "receive_date": "lblReceiveDate",
    "publication_no": "lblPublicNo",
    "publication_date": "lblPublicDate",
    "patent_no": "lblPatentNo",
    "grant_date": "lblPatentDate",
    "title": "lblAppName",
    "ipc": "lblIPC",
    "applicant": "lblApplicant",
    "inventors": "lblInventor",
    "status_raw": "lblStatus",
    "summary": "lblAbstract",
    "all_claims": "lblClaims",
}
DATE_FIELDS = {"filing_date", "receive_date", "publication_date", "grant_date"}


def _clean(s: str) -> str:
    s = re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f]", "", s)  # stray control characters from the source site
    s = s.replace("\xa0", " ")
    s = re.sub(r"[ \t]+", " ", s)
    s = re.sub(r"\n\s*\n+", "\n", s)
    return s.strip()


_CLAIM1 = re.compile(r"(?:^|\n|\s)1\s*\.\s*(?=[^\d\s])")
_CLAIM2_LINE = re.compile(r"\n\s*2\s*\.\s*(?=[^\d\s])")
_CLAIM2_INLINE = re.compile(r"\s2\s*\.\s*(?=[^\d\s.])")


def extract_main_claim(claims: str | None) -> str | None:
    """Claim 1 of the latest claim set. Claims text may start with amendment headers ('แก้ไข dd/mm/yyyy',
    'ไม่มีข้อถือสิทธิ', '-----') and contain chemical locants like '2-(...)', so anchor on '1.' / '2.' markers."""
    if not claims:
        return None
    text = claims.strip()
    m1 = _CLAIM1.search(text)
    if not m1:
        return text[:3000] or None
    body = text[m1.end():]
    m2 = _CLAIM2_LINE.search(body) or _CLAIM2_INLINE.search(body)
    claim = body[: m2.start()] if m2 else body
    # stop at a separator line that introduces an older claim set
    claim = re.split(r"\n-{5,}", claim)[0]
    return ("1. " + claim.strip())[:3000]


def kind_from_appno(appno: str) -> str:
    """Thai DIP application numbers: YY + type (01 invention, 02 design, 03 petty patent) + serial."""
    t = appno[2:4] if len(appno) >= 4 else ""
    return {"01": "invention", "02": "design", "03": "petty"}.get(t, "unknown")


def parse_detail(html: str) -> dict | None:
    t = LexborHTMLParser(html)
    out = {}
    for field, lbl in FIELDS.items():
        n = t.css_first(f"#contentMain_{lbl}")
        val = _clean(n.text(separator="\n")) if n else ""
        out[field] = val or None
    if not out.get("application_no"):
        return None
    for f in DATE_FIELDS:
        out[f] = parse_thai_date(out.get(f))
    out["main_claim"] = extract_main_claim(out.get("all_claims"))
    out["kind"] = kind_from_appno(out["application_no"])
    links = {}
    for a in t.css("a[id^=contentMain_hp]"):
        href = a.attributes.get("href")
        if href:
            links[a.attributes["id"].replace("contentMain_hp", "")] = href if href.startswith("http") else BASE + href
    out["doc_links"] = links
    return out


def fetch_detail(client: PoliteClient, appno: str, refresh: bool = False) -> tuple[dict | None, Path]:
    RAW_DIR.mkdir(parents=True, exist_ok=True)
    path = RAW_DIR / f"{appno}.html"
    if refresh or not path.exists():
        r = client.get(DETAIL_URL.format(appno=appno))
        path.write_text(r.text, encoding="utf-8")
    return parse_detail(path.read_text(encoding="utf-8")), path


def upsert_patent(s, parsed: dict, raw_path: Path, via: str | None = None, industry: str | None = None) -> Patent:
    p = s.exec(select(Patent).where(Patent.application_no == parsed["application_no"])).first()
    if p is None:
        p = Patent(application_no=parsed["application_no"])
    for k, v in parsed.items():
        if k == "doc_links":
            continue
        if hasattr(p, k):
            setattr(p, k, v)
    p.source_url = DETAIL_URL.format(appno=p.application_no)
    p.raw_html_path = str(raw_path)
    p.scraped_at = datetime.now(timezone.utc)
    if via:
        vias = set(json.loads(p.discovered_via or "[]"))
        vias.add(via)
        p.discovered_via = json.dumps(sorted(vias), ensure_ascii=False)
    if industry and not p.industry:
        p.industry = industry
    s.add(p)
    return p


def enqueue(appnos, via: str, industry: str | None = None):
    QUEUE.parent.mkdir(parents=True, exist_ok=True)
    with QUEUE.open("a", encoding="utf-8") as f:
        for a in appnos:
            f.write(json.dumps({"appno": a, "via": via, "industry": industry}, ensure_ascii=False) + "\n")


def process_queue(client: PoliteClient | None = None, refresh: bool = False) -> int:
    if not QUEUE.exists():
        return 0
    client = client or PoliteClient()
    items = {}
    for line in QUEUE.read_text(encoding="utf-8").splitlines():
        if line.strip():
            j = json.loads(line)
            items.setdefault(j["appno"], []).append(j)
    n = 0
    with session() as s:
        for i, (appno, entries) in enumerate(items.items(), 1):
            try:
                parsed, path = fetch_detail(client, appno, refresh=refresh)
            except Exception as e:  # keep going; the queue is re-runnable
                log.error("detail %s failed: %s", appno, e)
                continue
            if not parsed:
                log.warning("no detail for %s", appno)
                continue
            for e in entries:
                upsert_patent(s, parsed, path, via=e.get("via"), industry=e.get("industry"))
            n += 1
            s.commit()  # short write transactions: other processes can use the db meanwhile
            if i % 50 == 0:
                log.info("details: %d/%d", i, len(items))
        s.commit()
    QUEUE.unlink()
    return n


def reparse_cached() -> int:
    """Re-parse every cached detail page into the DB (after parser improvements). No network."""
    n = 0
    with session() as s:
        for p in s.exec(select(Patent)):
            path = RAW_DIR / f"{p.application_no}.html"
            if not path.exists():
                continue
            parsed = parse_detail(path.read_text(encoding="utf-8"))
            if parsed:
                for k, v in parsed.items():
                    if k != "doc_links" and hasattr(p, k):
                        setattr(p, k, v)
                s.add(p)
                n += 1
        s.commit()
    return n


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--appno", nargs="*")
    ap.add_argument("--queue", action="store_true")
    ap.add_argument("--refresh", action="store_true")
    ap.add_argument("--reparse", action="store_true", help="re-parse cached pages into the DB")
    a = ap.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    init_db()
    if a.reparse:
        print("reparsed", reparse_cached())
    if a.appno:
        enqueue(a.appno, via="manual")
    print("fetched", process_queue(refresh=a.refresh))


if __name__ == "__main__":
    main()
