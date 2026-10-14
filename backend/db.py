"""Engine, sessions, schema migration and the flattened SQL views used by the API, CSV export and any SQLite
browser."""
import json
import logging
import os
import sqlite3
from datetime import date
from pathlib import Path

from sqlalchemy import event, text
from sqlmodel import Session, SQLModel, create_engine

from backend import models  # noqa: F401  (registers tables)

log = logging.getLogger(__name__)

ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = Path(os.environ.get("PATENTAPP_DATA", ROOT / "data"))
DB_PATH = Path(os.environ.get("PATENTAPP_DB", DATA_DIR / "app.db"))

_engines = {}


def get_engine(db_path: Path | str | None = None):
    path = str(db_path or DB_PATH)
    if path not in _engines:
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        engine = create_engine(f"sqlite:///{path}", connect_args={"check_same_thread": False, "timeout": 120},
                               json_serializer=lambda o: json.dumps(o, ensure_ascii=False))  # keep Thai readable

        @event.listens_for(engine, "connect")
        def _pragmas(dbapi_conn, _):
            cur = dbapi_conn.cursor()
            cur.execute("PRAGMA busy_timeout=120000")
            try:
                cur.execute("PRAGMA journal_mode=WAL")
            except Exception:  # another process holds the db; WAL will be set next time
                pass
            cur.close()

        _engines[path] = engine
    return _engines[path]


def session(db_path=None) -> Session:
    return Session(get_engine(db_path))


# Dates are stored as ISO (CE). be() renders a BE (พ.ศ.) dd/mm/yyyy string for Excel readers.
_BE = "CASE WHEN {c} IS NULL THEN NULL ELSE strftime('%d/%m/', {c}) || (CAST(strftime('%Y', {c}) AS INTEGER) + 543) END"


def be(col: str) -> str:
    return _BE.format(c=col)


VIEWS = {
    "v_patents": f"""
        SELECT p.application_no, p.filing_date, {be('p.filing_date')} AS filing_date_be,
               p.publication_no, p.publication_date, {be('p.publication_date')} AS publication_date_be,
               p.patent_no, p.grant_date, {be('p.grant_date')} AS grant_date_be,
               p.expiry_date, {be('p.expiry_date')} AS expiry_date_be, p.expiry_source,
               p.kind, p.status_raw, p.status_norm, p.title, p.main_claim, p.summary,
               p.applicant, p.ipc, p.industry, p.source_url
        FROM patent p""",
    "v_drugs": """
        SELECT dp.name AS product, dp.dosage_form, s.inn_name, s.salt_form, s.pubchem_cid, s.parent_cid,
               s.iupac_name, s.molecular_formula, s.molecular_weight, s.smiles, s.inchikey,
               s.indication, s.mechanism, s.description, s.name_variants_th,
               dp.th_generic_registered, s.orange_book_note
        FROM drug_product dp
        JOIN product_substance ps ON ps.drug_product_id = dp.id
        JOIN substance s ON s.id = ps.substance_id""",
    "v_drug_patent_matrix": f"""
        SELECT dp.name AS product, s.inn_name, p.application_no, p.patent_no, p.title,
               l.claim_category, l.blocking_level, l.review_state, l.match_method, l.confidence,
               p.status_norm, p.status_raw, p.filing_date, {be('p.filing_date')} AS filing_date_be,
               p.expiry_date, {be('p.expiry_date')} AS expiry_date_be,
               l.design_around_note, l.thai_patentability_flag, l.evidence_snippet, p.main_claim
        FROM patent_link l
        JOIN patent p ON p.id = l.patent_id
        JOIN substance s ON s.id = l.substance_id
        JOIN product_substance ps ON ps.substance_id = s.id
        JOIN drug_product dp ON dp.id = ps.drug_product_id
        WHERE l.drug_product_id IS NULL OR l.drug_product_id = dp.id""",
    "v_assessments": f"""
        SELECT dp.name AS product, a.as_of_date, a.mode, a.verdict, a.free_date, {be('a.free_date')} AS free_date_be,
               a.fully_clear_date, {be('a.fully_clear_date')} AS fully_clear_date_be,
               a.analyst_override, a.analyst_note, a.auto_summary_th, a.created_at
        FROM assessment a
        JOIN drug_product dp ON dp.id = a.drug_product_id
        WHERE a.id IN (SELECT MAX(id) FROM assessment GROUP BY drug_product_id, mode)""",
}


# Schema changes create_all can't make on an existing table. Each step is skipped once applied.
RENAMED = {("assessment", "avoid_json"): "avoid_lists", ("assessment", "counts_json"): "status_counts"}
DROPPED = {
    "drug_product": ["route", "strengths", "on_target_list", "orange_book_note"],  # note moved to substance
    "substance": ["structure_img_url"],  # built from pubchem_cid instead
    "external_patent_ref": ["found_in_th"],
}


def _columns(conn, table: str) -> set[str]:
    return {r[1] for r in conn.execute(text(f"PRAGMA table_info({table})"))}


def _pending_migration(conn) -> bool:
    return (any(old in _columns(conn, t) for (t, old) in RENAMED)
            or any(c in _columns(conn, t) for t, cols in DROPPED.items() for c in cols))


def _backup(db_path: str):
    """Copy the database (WAL included) next to itself before a destructive migration."""
    dst = f"{db_path}.pre-migration-{date.today().isoformat()}"
    src, out = sqlite3.connect(db_path), sqlite3.connect(dst)
    with out:
        src.backup(out)
    src.close()
    out.close()
    log.warning("schema migration: backed up %s to %s", db_path, dst)


def init_db(db_path=None):
    engine = get_engine(db_path)
    with engine.connect() as conn:
        migrate = _pending_migration(conn)
    if migrate:
        _backup(engine.url.database)
    SQLModel.metadata.create_all(engine)
    with engine.begin() as conn:
        # views first: SQLite won't drop or rename a column a view uses
        existing = dict(conn.execute(text("SELECT name, sql FROM sqlite_master WHERE type='view'")).all())
        if migrate:
            for name in existing:
                conn.execute(text(f"DROP VIEW IF EXISTS {name}"))
            existing = {}
        renamed_from = {(t, new): old for (t, old), new in RENAMED.items()}
        for table in SQLModel.metadata.sorted_tables:  # add columns introduced after the table was created
            have = _columns(conn, table.name)
            for col in table.columns:
                if col.name in have or renamed_from.get((table.name, col.name)) in have:
                    continue  # present, or about to be renamed into place
                conn.execute(text(f"ALTER TABLE {table.name} ADD COLUMN {col.name} {col.type.compile(engine.dialect)}"))
        if "orange_book_note" in _columns(conn, "drug_product"):
            conn.execute(text("""
                UPDATE substance SET orange_book_note = (
                    SELECT dp.orange_book_note FROM drug_product dp
                    JOIN product_substance ps ON ps.drug_product_id = dp.id
                    WHERE ps.substance_id = substance.id AND dp.orange_book_note IS NOT NULL LIMIT 1)
                WHERE orange_book_note IS NULL"""))
        for (table, old), new in RENAMED.items():
            have = _columns(conn, table)
            if old in have and new not in have:
                conn.execute(text(f"ALTER TABLE {table} RENAME COLUMN {old} TO {new}"))
        for table, cols in DROPPED.items():
            for col in cols:
                if col in _columns(conn, table):
                    conn.execute(text(f"ALTER TABLE {table} DROP COLUMN {col}"))
        for name, sql in VIEWS.items():
            ddl = f"CREATE VIEW {name} AS {sql}"
            if existing.get(name) == ddl:
                continue
            conn.execute(text(f"DROP VIEW IF EXISTS {name}"))
            conn.execute(text(ddl))
    return engine
