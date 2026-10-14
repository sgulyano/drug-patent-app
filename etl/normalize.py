"""Normalise patent status and compute expiry; point-in-time status for any as-of date.

    python -m etl.normalize
"""
from datetime import date, timedelta
from pathlib import Path

import yaml
from sqlmodel import select

from backend.db import init_db, session
from backend.models import Patent, StatusMap
from etl.thai import add_years

ROOT = Path(__file__).resolve().parent.parent
_RULES = yaml.safe_load((ROOT / "seed" / "status_map.yaml").read_text(encoding="utf-8"))

ACTIVE = {"granted_active", "pending_unexamined", "pending_examining"}
EXAM_REQUEST_YEARS = 5  # Thai Patent Act s.29: request examination within 5 years of publication


def norm_status(raw: str | None, overrides: dict | None = None) -> str:
    raw = (raw or "").strip()
    if overrides and raw in overrides:
        return overrides[raw]
    for r in _RULES["rules"]:
        if r["contains"] in raw:
            return r["status"]
    return _RULES["default"]


def compute_expiry(p: Patent) -> tuple[date | None, str | None]:
    if not p.filing_date:
        return None, None
    if p.kind == "petty":
        return add_years(p.filing_date, 10) - timedelta(days=1), "computed_petty_10y"
    if p.kind == "design":
        return add_years(p.filing_date, 10) - timedelta(days=1), "computed_design_10y"
    return add_years(p.filing_date, 20) - timedelta(days=1), "computed_invention_20y"


def status_as_of(p: Patent, as_of: date) -> str:
    """Best estimate of the patent's legal state on `as_of`, given today's recorded status.

    Returns one of: not_published, granted_active, pending_unexamined, pending_examining,
    likely_abandoned, expired, abandoned, revoked.
    """
    if p.publication_date and p.publication_date > as_of:
        return "not_published"
    if p.expiry_date and p.expiry_date < as_of:
        return "expired"
    norm = p.status_norm or "pending_unexamined"
    exam_deadline = add_years(p.publication_date, EXAM_REQUEST_YEARS) if p.publication_date else None
    granted_by_then = bool(p.grant_date and p.grant_date <= as_of)
    if norm == "revoked":
        return "revoked"
    if norm in ("granted_active", "expired"):
        return "granted_active" if granted_by_then or not p.grant_date else "pending_examining"
    if norm == "abandoned":
        # Date of abandonment is unknown; if the exam-request window was still open on as_of, it was still alive.
        if exam_deadline and exam_deadline >= as_of:
            return "pending_unexamined"
        return "abandoned"
    if norm == "pending_unexamined" and exam_deadline and exam_deadline < as_of:
        return "likely_abandoned"
    return norm


def run():
    init_db()
    with session() as s:
        overrides = {m.status_raw: m.status_norm for m in s.exec(select(StatusMap))}
        seen = {}
        for p in s.exec(select(Patent)):
            p.status_norm = norm_status(p.status_raw, overrides)
            exp, src = compute_expiry(p)
            if exp:
                p.expiry_date, p.expiry_source = exp, src
            if p.status_raw:
                seen[p.status_raw] = p.status_norm
            s.add(p)
        for raw, norm in seen.items():
            if raw not in overrides:
                s.add(StatusMap(status_raw=raw, status_norm=norm))
        s.commit()
    return len(seen)


if __name__ == "__main__":
    print("distinct raw statuses:", run())
