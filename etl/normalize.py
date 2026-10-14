"""Normalise patent status and compute expiry; point-in-time status for any as-of date.

    python -m etl.normalize
"""
from datetime import date, timedelta
from pathlib import Path

import yaml
from sqlmodel import select

from backend.db import init_db, session
from backend.models import ExpirySource as E, Patent, PatentKind, PatentStatus as S, StatusMap
from etl.thai import add_years

ROOT = Path(__file__).resolve().parent.parent
_RULES = yaml.safe_load((ROOT / "seed" / "status_map.yaml").read_text(encoding="utf-8"))

ACTIVE = {S.GRANTED_ACTIVE, S.PENDING_UNEXAMINED, S.PENDING_EXAMINING}  # statuses that still restrict a generic
EXAM_REQUEST_YEARS = 5  # Thai Patent Act s.29: request examination within 5 years of publication


def norm_status(raw: str | None, overrides: dict | None = None) -> str:
    raw = (raw or "").strip()
    if overrides and raw in overrides:
        return overrides[raw]
    for r in _RULES["rules"]:
        if r["contains"] in raw:
            return r["status"]
    return _RULES["default"]


def compute_expiry(p: Patent) -> tuple[date | None, E | None]:
    if not p.filing_date:
        return None, None
    if p.kind == PatentKind.PETTY:
        return add_years(p.filing_date, 10) - timedelta(days=1), E.COMPUTED_PETTY_10Y
    if p.kind == PatentKind.DESIGN:
        return add_years(p.filing_date, 10) - timedelta(days=1), E.COMPUTED_DESIGN_10Y
    return add_years(p.filing_date, 20) - timedelta(days=1), E.COMPUTED_INVENTION_20Y


def status_as_of(p: Patent, as_of: date) -> str:
    """Best estimate of the patent's legal state on `as_of`, given today's recorded status.

    Returns a PatentStatus, including the as-of-only not_published and likely_abandoned.
    """
    if p.publication_date and p.publication_date > as_of:
        return S.NOT_PUBLISHED
    if p.expiry_date and p.expiry_date < as_of:
        return S.EXPIRED
    norm = p.status_norm or S.PENDING_UNEXAMINED
    exam_deadline = add_years(p.publication_date, EXAM_REQUEST_YEARS) if p.publication_date else None
    granted_by_then = bool(p.grant_date and p.grant_date <= as_of)
    if norm == S.REVOKED:
        return S.REVOKED
    if norm in (S.GRANTED_ACTIVE, S.EXPIRED):
        return S.GRANTED_ACTIVE if granted_by_then or not p.grant_date else S.PENDING_EXAMINING
    if norm == S.ABANDONED:
        # Date of abandonment is unknown; if the exam-request window was still open on as_of, it was still alive.
        if exam_deadline and exam_deadline >= as_of:
            return S.PENDING_UNEXAMINED
        return S.ABANDONED
    if norm == S.PENDING_UNEXAMINED and exam_deadline and exam_deadline < as_of:
        return S.LIKELY_ABANDONED
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
