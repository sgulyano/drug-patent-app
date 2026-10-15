"""SQLModel tables and the controlled vocabularies their string columns take. See the ER diagram in README.md.

Conventions for every table:
- Dates are stored as ISO dates in the Common Era (CE). The API and exports add a Buddhist Era (พ.ศ.) dd/mm/yyyy
  rendering alongside (``*_be`` fields); nothing in the database is stored in BE.
- Timestamps are UTC.
- Vocabulary columns are stored as plain strings. The StrEnum classes below list the allowed values; a StrEnum
  member compares and hashes like its string value, so ``link.review_state == ReviewState.CONFIRMED`` and
  ``== "confirmed"`` are equivalent.
- JSON columns hold Python lists/dicts. Reassign them instead of mutating in place: in-place changes are not
  tracked and would not be saved.
"""
from datetime import date, datetime, timezone
from enum import StrEnum
from typing import Optional

from sqlalchemy import JSON
from sqlmodel import AutoString, Field, SQLModel, UniqueConstraint


# ------------------------------------------------------------------ vocabularies
class PatentKind(StrEnum):
    """Type of Thai IP right, from digits 3–4 of the application number."""
    INVENTION = "invention"  # สิทธิบัตรการประดิษฐ์ (01), 20-year term from filing
    DESIGN = "design"  # สิทธิบัตรการออกแบบผลิตภัณฑ์ (02), 10-year term
    PETTY = "petty"  # อนุสิทธิบัตร (03), 10-year term including renewals
    UNKNOWN = "unknown"  # application number didn't match the pattern


class ExpirySource(StrEnum):
    """How Patent.expiry_date was obtained."""
    COMPUTED_INVENTION_20Y = "computed_invention_20y"  # filing date + 20 years − 1 day
    COMPUTED_PETTY_10Y = "computed_petty_10y"  # filing date + 10 years − 1 day
    COMPUTED_DESIGN_10Y = "computed_design_10y"  # filing date + 10 years − 1 day
    STATED = "stated"  # given explicitly by the source


class PatentStatus(StrEnum):
    """Normalised legal status. The first six are stored in Patent.status_norm (mapped from the DIP status text by
    seed/status_map.yaml); the last two only come from etl.normalize.status_as_of for a past as-of date."""
    GRANTED_ACTIVE = "granted_active"  # granted and in force
    PENDING_UNEXAMINED = "pending_unexamined"  # published, examination not yet requested
    PENDING_EXAMINING = "pending_examining"  # substantive examination requested / under way
    EXPIRED = "expired"  # term ended
    ABANDONED = "abandoned"  # application abandoned or refused
    REVOKED = "revoked"  # granted patent revoked (e.g. unpaid annual fees)
    NOT_PUBLISHED = "not_published"  # as-of only: not yet published on the as-of date
    LIKELY_ABANDONED = "likely_abandoned"  # as-of only: 5-year exam-request window passed with no request


class ClaimCategory(StrEnum):
    """What claim 1 of a patent protects, relative to the linked substance."""
    DRUG_SUBSTANCE = "drug_substance"  # the active compound itself (or a genus covering it)
    SOLID_FORM = "solid_form"  # a salt, crystal/polymorph, amorphous form, hydrate, solvate or particle size
    PROCESS = "process"  # a process for making the substance
    FORMULATION = "formulation"  # a composition / dosage form containing it
    DEVICE = "device"  # a delivery device (e.g. an inhaler)
    USE = "use"  # a medical use / method of treatment
    COMBINATION = "combination"  # a combination with another active


class BlockingLevel(StrEnum):
    """Whether an in-force patent stops a generic from being sold."""
    BLOCKING = "blocking"  # cannot be designed around: no generic until it ends
    AVOIDABLE = "avoidable"  # a generic can be made by avoiding what the claim covers
    NOT_RELEVANT = "not_relevant"  # the substance is mentioned but not claimed; ignored by the verdict


class MatchMethod(StrEnum):
    """Who decided a link's claim category and blocking level."""
    KEYWORD = "keyword"  # rule-based classifier (etl.match.classify_rules)
    LLM = "llm"  # LLM classifier (etl.match.classify_llm)
    MANUAL = "manual"  # an analyst created or edited the link


class ReviewState(StrEnum):
    """Analyst review state of a patent link."""
    CANDIDATE = "candidate"  # proposed by the pipeline, not yet reviewed
    CONFIRMED = "confirmed"  # an analyst confirmed the patent relates to the substance
    REJECTED = "rejected"  # an analyst rejected the link; ignored everywhere


class Verdict(StrEnum):
    """Can a Thai manufacturer make and sell the generic on the as-of date?"""
    FREE = "free"  # no in-force related patent
    FREE_WITH_AVOIDANCE = "free_with_avoidance"  # yes, by avoiding the avoidable patents
    BLOCKED_UNTIL = "blocked_until"  # no, until Assessment.free_date
    PENDING_REVIEW = "pending_review"  # would be free, but not every link has been reviewed
    UNKNOWN = "unknown"  # not enough data


class AssessmentMode(StrEnum):
    """Which patent links an assessment counts."""
    CONFIRMED = "confirmed"  # only links an analyst confirmed
    AUTO = "auto"  # every link that isn't rejected


class OrangeBookClaimType(StrEnum):
    """Claim type of a US Orange Book patent, from its Drug_Substance / Drug_Product flags and use code."""
    DRUG_SUBSTANCE = "drug_substance"
    DRUG_PRODUCT = "drug_product"
    USE = "use"
    OTHER = "other"


def _json(**kw):
    """A JSON column (NULL stays SQL NULL rather than the JSON text 'null')."""
    return Field(default=None, sa_type=JSON(none_as_null=True), **kw)


# ------------------------------------------------------------------ tables
class DrugProduct(SQLModel, table=True):
    """A finished product on the target list (บัญชียามุ่งเป้า), as named in seed/drugs.yaml."""
    __tablename__ = "drug_product"
    id: Optional[int] = Field(default=None, primary_key=True, description="Surrogate key.")
    name: str = Field(index=True, unique=True,
                      description="Product name exactly as in seed/drugs.yaml, e.g. 'Febuxostat tablet'. Natural key.")
    dosage_form: Optional[str] = Field(
        default=None, description="Dosage form(s) split off the name, e.g. 'tablet' or 'cap for inhaler / solution "
                                  "for inhalation'; null if none was recognised.")
    th_generic_registered: Optional[bool] = Field(
        default=None, description="Analyst-entered: a Thai generic of this product is already registered with the "
                                  "Thai FDA (อย.). Null = not checked.")
    th_generic_note: Optional[str] = Field(
        default=None, description="Analyst-entered free text about Thai generic registrations (registration "
                                  "numbers, companies).")


class Substance(SQLModel, table=True):
    """An active ingredient (by INN). Patents link to substances, so one substance patent covers every product
    that contains it."""
    __tablename__ = "substance"
    id: Optional[int] = Field(default=None, primary_key=True, description="Surrogate key.")
    inn_name: str = Field(index=True, unique=True,
                          description="International non-proprietary name, lower case, without the salt, e.g. "
                                      "'lercanidipine'. Natural key.")
    salt_form: Optional[str] = Field(
        default=None, description="Salt or hydrate named in the product, e.g. 'hydrochloride'; null for the free "
                                  "base/acid.")
    query_name: Optional[str] = Field(
        default=None, description="Name used for the PubChem lookup, i.e. INN plus salt as written in the product "
                                  "name, e.g. 'tiotropium bromide'.")
    pubchem_cid: Optional[int] = Field(
        default=None, description="PubChem CID of the salt/form named in the product (the neutral parent when no "
                                  "salt is named). Also used to build the structure image and PubChem URLs.")
    parent_cid: Optional[int] = Field(
        default=None, description="PubChem CID of the parent compound (salt stripped); equals pubchem_cid when no "
                                  "salt.")
    iupac_name: Optional[str] = Field(default=None, description="IUPAC name of pubchem_cid (PubChem IUPACName).")
    molecular_formula: Optional[str] = Field(
        default=None, description="Molecular formula of pubchem_cid (PubChem MolecularFormula), e.g. 'C36H42ClN3O6'.")
    molecular_weight: Optional[float] = Field(
        default=None, description="Molecular weight of pubchem_cid in g/mol (PubChem MolecularWeight).")
    smiles: Optional[str] = Field(default=None, description="SMILES of pubchem_cid (PubChem SMILES).")
    inchikey: Optional[str] = Field(default=None, description="InChIKey of pubchem_cid (PubChem InChIKey).")
    description: Optional[str] = Field(
        default=None, description="Short English description from PubChem (parent compound first).")
    indication: Optional[str] = Field(
        default=None, description="PubChem 'Drug Indication' text, up to two entries joined by newlines.")
    mechanism: Optional[str] = Field(
        default=None, description="PubChem 'Mechanism of Action' text, up to two entries joined by newlines.")
    atc_code: Optional[str] = Field(
        default=None, description="WHO ATC code(s) from PubChem, comma-separated, e.g. 'C08CA13'.")
    name_variants_th: Optional[dict] = _json(
        description="Thai spellings of the name, learned from patent text by etl.name_variants: "
                    "{'observed': [{'spelling', 'docs' (number of patents using it), 'source' (how it was found: "
                    "generated / synonym / llm / parenthetical)}], 'probes': [extra spellings to try in site "
                    "searches]}. Null until name discovery has run.")
    synonyms_en: Optional[list[str]] = _json(
        description="Cleaned English synonyms from PubChem (parent compound first), used for matching patent text "
                    "and Orange Book ingredients.")
    peptide_sequence: Optional[str] = Field(
        default=None, description="Amino-acid sequence for peptide drugs, e.g. 'pGlu-His-Trp-…', found in the "
                                  "PubChem synonyms; patents often name peptides only by sequence.")
    orange_book_note: Optional[str] = Field(
        default=None, description="Thai one-line summary of this substance's US Orange Book entry: reference brand "
                                  "names, number of listed patents by claim type, latest expiry and number of "
                                  "approved generics (ANDA). Written by etl.orange_book.")
    enriched_at: Optional[datetime] = Field(
        default=None, description="UTC time the PubChem data was last fetched; null = never (forces a lookup).")


class ProductSubstance(SQLModel, table=True):
    """Which substances a product contains (more than one for a combination product)."""
    __tablename__ = "product_substance"
    drug_product_id: int = Field(foreign_key="drug_product.id", primary_key=True, description="The product.")
    substance_id: int = Field(foreign_key="substance.id", primary_key=True, description="A substance it contains.")


class Patent(SQLModel, table=True):
    """A Thai patent or patent application, as shown on the DIP ApplicationDetail.aspx page."""
    __tablename__ = "patent"
    id: Optional[int] = Field(default=None, primary_key=True, description="Surrogate key.")
    application_no: str = Field(index=True, unique=True,
                                description="เลขที่คำขอ: DIP application number (lblAppNo): YY + type (PatentKind) "
                                            "+ serial, e.g. '1201001234'. Natural key.")
    filing_date: Optional[date] = Field(default=None, description="วันยื่นคำขอ: filing date (lblSubmitDate).")
    receive_date: Optional[date] = Field(
        default=None, description="วันที่รับคำขอ: date DIP received the application (lblReceiveDate).")
    publication_no: Optional[str] = Field(default=None, description="เลขที่ประกาศโฆษณา: publication number (lblPublicNo).")
    publication_date: Optional[date] = Field(
        default=None, description="วันประกาศโฆษณา: publication date (lblPublicDate); null if not yet published.")
    patent_no: Optional[str] = Field(
        default=None, description="เลขที่สิทธิบัตร: patent number once granted (lblPatentNo); null while pending.")
    grant_date: Optional[date] = Field(default=None, description="วันออกสิทธิบัตร: grant date (lblPatentDate).")
    expiry_date: Optional[date] = Field(
        default=None, description="Last day of protection; computed from filing_date and kind (see expiry_source).")
    expiry_source: Optional[ExpirySource] = Field(
        default=None, sa_type=AutoString, description="How expiry_date was obtained (ExpirySource).")
    kind: Optional[PatentKind] = Field(
        default=None, sa_type=AutoString, description="Invention, petty patent or design (PatentKind), from the "
                                                      "application number.")
    status_raw: Optional[str] = Field(
        default=None, description="สถานะ: current status text exactly as shown on the DIP site (lblStatus).")
    status_norm: Optional[PatentStatus] = Field(
        default=None, sa_type=AutoString,
        description="status_raw mapped to a normalised status (PatentStatus, first six values) via "
                    "seed/status_map.yaml or a status_map override. This is the status on status_date (today when "
                    "that is null); the status on another date is computed by etl.normalize.status_as_of.")
    status_date: Optional[date] = Field(
        default=None, description="Date status_raw was observed: the report date ('ณ วันที่ …') for a patent loaded "
                                  "from an อย. report (etl.load_pdf). Null = taken as today's status (DIP pages).")
    title: Optional[str] = Field(default=None, description="ชื่อที่แสดงถึงการประดิษฐ์: title, usually Thai (lblAppName).")
    main_claim: Optional[str] = Field(
        default=None, description="Claim 1 of the latest claim set, extracted from all_claims (max 3,000 chars).")
    all_claims: Optional[str] = Field(
        default=None, description="ข้อถือสิทธิ: full claims text including amendment headers (lblClaims).")
    summary: Optional[str] = Field(default=None, description="บทสรุปการประดิษฐ์: abstract (lblAbstract).")
    applicant: Optional[str] = Field(default=None, description="ผู้ขอรับสิทธิบัตร: applicant(s) (lblApplicant).")
    inventors: Optional[str] = Field(default=None, description="ผู้ประดิษฐ์: inventor(s) (lblInventor).")
    ipc: Optional[str] = Field(
        default=None, description="IPC classification codes (lblIPC), e.g. 'A61K 31/4422'. A61K/A61P/C07… mark "
                                  "pharma patents for matching.")
    industry: Optional[str] = Field(
        default=None, description="Thai industry group the patent was listed under in the RegistrationEnding.aspx "
                                  "category crawl, e.g. 'อุตสาหกรรมยา'; null if found only by search.")
    discovered_via: Optional[list[str]] = _json(
        description="Every way the pipeline found this application, sorted: 'category:<industry>' (category "
                    "crawl), 'title:<term>' (title search), 'applicant:<name>' (applicant expansion).")
    source_url: Optional[str] = Field(default=None, description="URL of the DIP detail page.")
    raw_html_path: Optional[str] = Field(
        default=None, description="Local path of the cached detail page (data/raw/detail/<appno>.html).")
    scraped_at: Optional[datetime] = Field(default=None, description="UTC time the detail page was last parsed.")


class StatusMap(SQLModel, table=True):
    """Every DIP status text seen so far and the normalised status it maps to. Edit status_norm here to override
    seed/status_map.yaml for one exact text; etl.normalize applies overrides first."""
    __tablename__ = "status_map"
    status_raw: str = Field(primary_key=True, description="Exact status text from the DIP site (Patent.status_raw).")
    status_norm: PatentStatus = Field(sa_type=AutoString,
                                      description="Normalised status it maps to (PatentStatus, first six values).")


class PatentLink(SQLModel, table=True):
    """A claim that a patent relates to a substance, with the claim's category and how much it blocks a generic.
    Created by the pipeline as a candidate, then confirmed or rejected by an analyst. One link per patent and
    substance."""
    __tablename__ = "patent_link"
    __table_args__ = (UniqueConstraint("patent_id", "substance_id", name="uq_link"),)
    id: Optional[int] = Field(default=None, primary_key=True, description="Surrogate key.")
    patent_id: int = Field(foreign_key="patent.id", index=True, description="The patent.")
    substance_id: int = Field(foreign_key="substance.id", index=True, description="The substance it relates to.")
    drug_product_id: Optional[int] = Field(
        default=None, foreign_key="drug_product.id",
        description="Set by an analyst when the claim covers only one product (e.g. a device or one dosage form). "
                    "Null = applies to every product containing the substance.")
    claim_category: Optional[ClaimCategory] = Field(
        default=None, sa_type=AutoString,
        description="What claim 1 protects (ClaimCategory); null when the LLM judged the patent unrelated.")
    blocking_level: Optional[BlockingLevel] = Field(
        default=None, sa_type=AutoString,
        description="How much the patent blocks a generic while in force (BlockingLevel). A confirmed link is "
                    "never not_relevant.")
    design_around_note: Optional[str] = Field(
        default=None, description="Thai advice on what a generic must avoid to stay clear of the claim.")
    match_method: Optional[MatchMethod] = Field(
        default=None, sa_type=AutoString,
        description="Who set the category and blocking level (MatchMethod). Manual links are left out of the "
                    "evaluation.")
    matched_terms: Optional[dict] = _json(
        description="Why the link was proposed: {'terms': [names found], 'sections': [where: title, claim1, "
                    "claims, summary]}.")
    confidence: Optional[float] = Field(
        default=None, description="Classifier confidence, 0–1 (rules: 0.3 weak mention, 0.45–0.7 named in title "
                                  "or claim 1).")
    evidence_snippet: Optional[str] = Field(
        default=None, description="Text around the first matched term (claim 1 first), for the reviewer.")
    rationale: Optional[str] = Field(
        default=None, description="Classifier explanation, prefixed 'rules:' or 'llm:'.")
    review_state: ReviewState = Field(
        default=ReviewState.CANDIDATE, sa_type=AutoString,
        description="Analyst review state (ReviewState). The pipeline never overwrites a reviewed link.")
    reviewed_at: Optional[datetime] = Field(
        default=None, description="UTC time of the last confirm/reject; null while a candidate.")
    thai_patentability_flag: Optional[str] = Field(
        default=None, description="Thai warning when the claim may be unpatentable in Thailand (use or "
                                  "method-of-treatment claims, Patent Act s.9).")


class ExternalPatentRef(SQLModel, table=True):
    """A foreign patent listed for the reference product, currently only the US FDA Orange Book. Context for the
    analyst; not used by the verdict or the evaluation. Rebuilt on every Orange Book load."""
    __tablename__ = "external_patent_ref"
    id: Optional[int] = Field(default=None, primary_key=True, description="Surrogate key.")
    substance_id: int = Field(foreign_key="substance.id", index=True, description="The substance.")
    jurisdiction: str = Field(default="US", description="Country of the patent (ISO 3166 alpha-2).")
    patent_no: str = Field(description="US patent number as listed (Orange Book Patent_No).")
    claim_type: Optional[OrangeBookClaimType] = Field(
        default=None, sa_type=AutoString, description="Claim type from the listing flags (OrangeBookClaimType).")
    use_code: Optional[str] = Field(
        default=None, description="Orange Book patent use code (e.g. 'U-123') for use patents.")
    expiry: Optional[date] = Field(default=None, description="Patent expiry date as listed (Patent_Expire_Date_Text).")
    product_desc: Optional[str] = Field(
        default=None, description="Reference product the patent is listed for: 'Trade name (dosage form; route)'.")
    applicant: Optional[str] = Field(default=None, description="NDA holder (Applicant_Full_Name).")


class Assessment(SQLModel, table=True):
    """A stored verdict for one product on one as-of date. etl.assess writes one per product and mode on each
    pipeline run; an analyst override adds a new row rather than editing an old one. The app recomputes verdicts
    live and reads this table only for the latest analyst override."""
    __tablename__ = "assessment"
    id: Optional[int] = Field(default=None, primary_key=True, description="Surrogate key; higher = newer.")
    drug_product_id: int = Field(foreign_key="drug_product.id", index=True, description="The product assessed.")
    as_of_date: date = Field(description="Date the patent statuses were evaluated at.")
    mode: AssessmentMode = Field(default=AssessmentMode.CONFIRMED, sa_type=AutoString,
                                 description="Which links were counted (AssessmentMode).")
    verdict: Verdict = Field(sa_type=AutoString, description="Computed verdict (Verdict).")
    free_date: Optional[date] = Field(
        default=None, description="If blocked: the day the last blocking patent ends, after which the generic can "
                                  "be sold.")
    fully_clear_date: Optional[date] = Field(
        default=None, description="The day the last in-force related patent (blocking or avoidable) ends.")
    avoid_lists: Optional[dict] = _json(
        description="Patents behind the verdict: {'blocking': [...], 'avoid': [...], 'warnings': [...]}, each row "
                    "with application_no, patent_no, title, category, blocking_level, status, expiry_date, note, "
                    "substance and review_state. 'warnings' are likely-abandoned applications.")
    status_counts: Optional[dict[str, int]] = _json(
        description="Number of related patents per as-of status (PatentStatus value → count).")
    auto_summary_th: Optional[str] = Field(default=None, description="Generated Thai summary of the verdict.")
    analyst_override: Optional[Verdict] = Field(
        default=None, sa_type=AutoString,
        description="Verdict set by an analyst, shown instead of the computed one; null = no override.")
    analyst_note: Optional[str] = Field(default=None, description="Analyst's reason for the override.")
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc),
                                 description="UTC time the row was written.")
