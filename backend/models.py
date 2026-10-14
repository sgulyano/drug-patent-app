"""SQLModel tables. See the ER diagram in README.md."""
from datetime import date, datetime, timezone
from typing import Optional

from sqlmodel import Field, SQLModel, UniqueConstraint


class DrugProduct(SQLModel, table=True):
    __tablename__ = "drug_product"
    id: Optional[int] = Field(default=None, primary_key=True)
    name: str = Field(index=True, unique=True)
    dosage_form: Optional[str] = None
    route: Optional[str] = None
    strengths: Optional[str] = None
    on_target_list: bool = True
    th_generic_registered: Optional[bool] = None
    th_generic_note: Optional[str] = None
    orange_book_note: Optional[str] = None


class Substance(SQLModel, table=True):
    __tablename__ = "substance"
    id: Optional[int] = Field(default=None, primary_key=True)
    inn_name: str = Field(index=True, unique=True)
    salt_form: Optional[str] = None
    query_name: Optional[str] = None  # name used to resolve PubChem (e.g. "tiotropium bromide")
    pubchem_cid: Optional[int] = None  # CID of the salt/form named in the product
    parent_cid: Optional[int] = None
    iupac_name: Optional[str] = None
    molecular_formula: Optional[str] = None
    molecular_weight: Optional[float] = None
    smiles: Optional[str] = None
    inchikey: Optional[str] = None
    structure_img_url: Optional[str] = None
    description: Optional[str] = None
    indication: Optional[str] = None
    mechanism: Optional[str] = None
    atc_code: Optional[str] = None
    name_variants_th: Optional[str] = None  # JSON list
    synonyms_en: Optional[str] = None  # JSON list
    peptide_sequence: Optional[str] = None  # e.g. "pGlu-His-Trp-...", for peptide drugs named by sequence in patents
    enriched_at: Optional[datetime] = None


class ProductSubstance(SQLModel, table=True):
    __tablename__ = "product_substance"
    drug_product_id: int = Field(foreign_key="drug_product.id", primary_key=True)
    substance_id: int = Field(foreign_key="substance.id", primary_key=True)


class Patent(SQLModel, table=True):
    __tablename__ = "patent"
    id: Optional[int] = Field(default=None, primary_key=True)
    application_no: str = Field(index=True, unique=True)
    filing_date: Optional[date] = None
    receive_date: Optional[date] = None
    publication_no: Optional[str] = None
    publication_date: Optional[date] = None
    patent_no: Optional[str] = None
    grant_date: Optional[date] = None
    expiry_date: Optional[date] = None
    expiry_source: Optional[str] = None  # computed_invention_20y | computed_petty_10y | stated
    kind: Optional[str] = None  # invention | petty | design | unknown
    status_raw: Optional[str] = None
    status_norm: Optional[str] = None
    title: Optional[str] = None
    main_claim: Optional[str] = None
    all_claims: Optional[str] = None
    summary: Optional[str] = None
    applicant: Optional[str] = None
    inventors: Optional[str] = None
    ipc: Optional[str] = None
    industry: Optional[str] = None  # e.g. "อุตสาหกรรมยา" when found via the category crawl
    discovered_via: Optional[str] = None  # JSON list of how we found it
    source_url: Optional[str] = None
    raw_html_path: Optional[str] = None
    scraped_at: Optional[datetime] = None


class StatusMap(SQLModel, table=True):
    __tablename__ = "status_map"
    status_raw: str = Field(primary_key=True)
    status_norm: str


class PatentLink(SQLModel, table=True):
    __tablename__ = "patent_link"
    __table_args__ = (UniqueConstraint("patent_id", "substance_id", name="uq_link"),)
    id: Optional[int] = Field(default=None, primary_key=True)
    patent_id: int = Field(foreign_key="patent.id", index=True)
    substance_id: int = Field(foreign_key="substance.id", index=True)
    drug_product_id: Optional[int] = Field(default=None, foreign_key="drug_product.id")
    claim_category: Optional[str] = None
    blocking_level: Optional[str] = None  # blocking | avoidable | not_relevant
    design_around_note: Optional[str] = None
    match_method: Optional[str] = None  # keyword | llm | manual
    matched_terms: Optional[str] = None  # JSON list
    confidence: Optional[float] = None
    evidence_snippet: Optional[str] = None
    rationale: Optional[str] = None
    review_state: str = "candidate"  # candidate | confirmed | rejected
    reviewed_at: Optional[datetime] = None
    thai_patentability_flag: Optional[str] = None


class ExternalPatentRef(SQLModel, table=True):
    __tablename__ = "external_patent_ref"
    id: Optional[int] = Field(default=None, primary_key=True)
    substance_id: int = Field(foreign_key="substance.id", index=True)
    jurisdiction: str = "US"
    patent_no: str
    claim_type: Optional[str] = None  # drug_substance | drug_product | use
    use_code: Optional[str] = None
    expiry: Optional[date] = None
    product_desc: Optional[str] = None
    applicant: Optional[str] = None
    found_in_th: Optional[bool] = None


class Assessment(SQLModel, table=True):
    __tablename__ = "assessment"
    id: Optional[int] = Field(default=None, primary_key=True)
    drug_product_id: int = Field(foreign_key="drug_product.id", index=True)
    as_of_date: date
    mode: str = "confirmed"  # confirmed (reviewed links only) | auto (all non-rejected links)
    verdict: str  # free | free_with_avoidance | blocked_until | unknown
    free_date: Optional[date] = None
    fully_clear_date: Optional[date] = None
    avoid_json: Optional[str] = None
    counts_json: Optional[str] = None
    auto_summary_th: Optional[str] = None
    analyst_override: Optional[str] = None
    analyst_note: Optional[str] = None
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
