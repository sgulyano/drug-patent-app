"""Run the whole pipeline from the seed list.

    python -m etl.pipeline                     # incremental: reuses cached pages
    python -m etl.pipeline --skip-crawl        # skip the (slow) whole-category crawl
    python -m etl.pipeline --as-of 2022-07-20  # also assess as of a past date

The only drug input is seed/drugs.yaml (product names). Everything else is discovered.
"""
import argparse
import logging
import sys
from datetime import date

from etl import assess, crawl_category, match, normalize, orange_book, resolve_substances, scrape_detail, search_by_name

log = logging.getLogger("pipeline")


def run(skip_crawl: bool = False, rounds: int = 2, use_llm: bool = True, as_of: list[date] | None = None,
        orange: bool = True):
    log.info("1/7 resolve substances + PubChem")
    resolve_substances.run()
    if not skip_crawl:
        log.info("2/7 crawl อุตสาหกรรมยา category")
        crawl_category.run("pharma")
    log.info("3/7 name-based discovery (%d rounds)", rounds)
    search_by_name.run(rounds)
    log.info("4/7 re-parse cached pages, normalise statuses and expiry")
    scrape_detail.reparse_cached()
    normalize.run()
    if orange:
        log.info("5/7 Orange Book")
        try:
            orange_book.run()
        except Exception as e:  # network optional
            log.warning("Orange Book skipped: %s", e)
    log.info("6/7 match + classify")
    match.run(use_llm)
    log.info("7/7 assess")
    for d in (as_of or []) + [date.today()]:
        assess.run(d)


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser()
    ap.add_argument("--skip-crawl", action="store_true")
    ap.add_argument("--rounds", type=int, default=2)
    ap.add_argument("--no-llm", action="store_true")
    ap.add_argument("--no-orange-book", action="store_true")
    ap.add_argument("--as-of", type=date.fromisoformat, action="append")
    a = ap.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    logging.getLogger("httpx").setLevel(logging.WARNING)
    run(a.skip_crawl, a.rounds, not a.no_llm, a.as_of, not a.no_orange_book)
