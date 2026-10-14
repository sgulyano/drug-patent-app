"""Engine, sessions and the flattened SQL views used by the API, CSV export and any SQLite browser."""
import os
from pathlib import Path

from sqlalchemy import event, text
from sqlmodel import Session, SQLModel, create_engine

from backend import models  # noqa: F401  (registers tables)

ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = Path(os.environ.get("PATENTAPP_DATA", ROOT / "data"))
DB_PATH = Path(os.environ.get("PATENTAPP_DB", DATA_DIR / "app.db"))

_engines = {}


def get_engine(db_path: Path | str | None = None):
    path = str(db_path or DB_PATH)
    if path not in _engines:
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        engine = create_engine(f"sqlite:///{path}", connect_args={"check_same_thread": False, "timeout": 120})

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
               s.indication, s.mechanism, s.description, s.structure_img_url, s.name_variants_th,
               dp.th_generic_registered, dp.orange_book_note
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


def init_db(db_path=None):
    engine = get_engine(db_path)
    SQLModel.metadata.create_all(engine)
    with engine.begin() as conn:
        for table in SQLModel.metadata.sorted_tables:  # add columns introduced after the table was created
            have = {r[1] for r in conn.execute(text(f"PRAGMA table_info({table.name})"))}
            for col in table.columns:
                if col.name not in have:
                    conn.execute(text(f"ALTER TABLE {table.name} ADD COLUMN {col.name} {col.type.compile(engine.dialect)}"))
        existing = dict(conn.execute(text("SELECT name, sql FROM sqlite_master WHERE type='view'")).all())
        for name, sql in VIEWS.items():
            ddl = f"CREATE VIEW {name} AS {sql}"
            if existing.get(name) == ddl:
                continue
            conn.execute(text(f"DROP VIEW IF EXISTS {name}"))
            conn.execute(text(ddl))
    return engine
