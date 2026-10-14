"""Crawl a whole industry category from RegistrationEnding.aspx and fetch every detail page.

    python -m etl.crawl_category --industry pharma            # ~2.6k granted pharma patents
    python -m etl.crawl_category --industry pharma --max-pages 2
"""
import argparse
import json
import logging

from backend.db import DATA_DIR, init_db
from etl.dip_search import TIME_LEFT, crawl_registration
from etl.http import PoliteClient
from etl.scrape_detail import enqueue, process_queue

log = logging.getLogger(__name__)


def run(industry: str = "pharma", max_pages: int | None = None, details: bool = True):
    init_db()
    client = PoliteClient()
    listing = DATA_DIR / f"listing_{industry}.jsonl"
    seen = set()
    with listing.open("w", encoding="utf-8") as f:
        for tl in TIME_LEFT:
            for row in crawl_registration(client, industry, tl, max_pages=max_pages):
                if row["application_no"] in seen:
                    continue
                seen.add(row["application_no"])
                f.write(json.dumps(row, ensure_ascii=False, default=str) + "\n")
                industry_th = row.get("industry")
    log.info("listing: %d applications", len(seen))
    enqueue(sorted(seen), via=f"category:{industry}", industry=industry_th if seen else None)
    if details:
        n = process_queue(client)
        log.info("details fetched: %d", n)


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--industry", default="pharma")
    ap.add_argument("--max-pages", type=int)
    ap.add_argument("--no-details", action="store_true")
    a = ap.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    logging.getLogger("httpx").setLevel(logging.WARNING)
    run(a.industry, a.max_pages, not a.no_details)
