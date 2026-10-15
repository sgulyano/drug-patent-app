"""Load seed/drugs.yaml, split each product name into dosage form + active substance(s), resolve them in
PubChem and store chemistry data.

    python -m etl.resolve_substances
"""
import logging
import re
from datetime import datetime, timezone
from pathlib import Path

import yaml
from sqlmodel import select

from backend.db import init_db, session
from backend.models import DrugProduct, ProductSubstance, Substance
from etl.peptide import find_sequence
from etl.pubchem import PubChem, clean_synonyms

log = logging.getLogger(__name__)
ROOT = Path(__file__).resolve().parent.parent

# Generic dosage-form vocabulary (not drug-specific).
DOSAGE_TERMS = [
    "powder and solvent for prolonged-release suspension for injection", "prolonged-release suspension",
    "powder for solution", "solution for inhalation", "cap for inhaler", "capsule for inhaler",
    "gastro-resistant tablet", "prolonged-release tablet", "extended-release tablet", "modified-release tablet",
    "film-coated tablet", "orally disintegrating tablet", "powder and solvent", "for injection", "for inhalation",
    "injection", "tablets", "tablet", "capsules", "capsule", "cap", "inhaler", "solution", "suspension",
    "powder", "solvent", "syrup", "cream", "ointment", "gel", "patch", "spray", "drops", "infusion",
]
SALT_WORDS = {
    "bromide", "chloride", "hydrochloride", "hcl", "hydrobromide", "sodium", "potassium", "calcium",
    "magnesium", "succinate", "mesylate", "mesilate", "maleate", "acetate", "citrate", "tartrate", "sulfate",
    "sulphate", "besylate", "besilate", "fumarate", "phosphate", "tosylate", "lactate", "nitrate",
    "monohydrate", "dihydrate", "hydrate", "anhydrous",
}


def split_product(name: str) -> tuple[str, str | None]:
    """'Lercanidipine hydrochloride tablet' -> ('lercanidipine hydrochloride', 'tablet')."""
    low = " " + re.sub(r"\s+", " ", name.lower().replace("/", " / ")) + " "
    forms = []
    for term in sorted(DOSAGE_TERMS, key=len, reverse=True):
        pat = " " + term + " "
        if pat in low:
            forms.append(term)
            low = low.replace(pat, " ")
    active = re.sub(r"\b(for|and|of|with)\b", " ", low)
    active = re.sub(r"[/,]", " ", active)
    active = re.sub(r"\s+", " ", active).strip()
    return active, " / ".join(forms) or None


def split_salt(active: str) -> tuple[str, str | None]:
    words = active.split()
    inn = [w for w in words if w not in SALT_WORDS]
    salt = [w for w in words if w in SALT_WORDS]
    return " ".join(inn), " ".join(salt) or None


def enrich(sub: Substance, pc: PubChem):
    q = sub.query_name or sub.inn_name
    cid = pc.cid_by_name(q) or pc.cid_by_name(sub.inn_name)
    if not cid:
        log.warning("PubChem: no CID for %s", q)
        return
    parent = pc.parent_cid(cid)
    if not sub.salt_form and parent != cid:
        cid = parent  # e.g. a name that resolves to an ion: show the neutral parent compound
    props = pc.properties(cid)
    for k, v in props.items():
        setattr(sub, k, v)
    sub.pubchem_cid, sub.parent_cid = cid, parent
    raw_syns = pc.synonyms(parent)
    sub.peptide_sequence = find_sequence(raw_syns)
    syns = clean_synonyms(raw_syns) + clean_synonyms(pc.synonyms(cid), 10)
    sub.synonyms_en = list(dict.fromkeys(syns))
    sub.description = pc.description(parent) or pc.description(cid)
    ind = pc.view_heading(parent, "Drug Indication", 2)
    sub.indication = "\n".join(ind) or None
    moa = pc.view_heading(parent, "Mechanism of Action", 2)
    sub.mechanism = "\n".join(moa) or None
    atc = pc.atc_codes(parent) or (pc.atc_codes(cid) if cid != parent else [])
    sub.atc_code = ", ".join(atc) or None
    sub.enriched_at = datetime.now(timezone.utc)


def run(refresh: bool = False):
    init_db()
    seed = yaml.safe_load((ROOT / "seed" / "drugs.yaml").read_text(encoding="utf-8"))
    pc = PubChem()
    with session() as s:
        for item in seed["products"]:
            name = item["name"]
            active, form = split_product(name)
            dp = s.exec(select(DrugProduct).where(DrugProduct.name == name)).first() or DrugProduct(name=name)
            dp.dosage_form = item.get("dosage_form") or form
            s.add(dp)
            s.flush()
            # combination products are written "a + b"
            for part in [p.strip() for p in re.split(r"\s\+\s", active) if p.strip()]:
                inn, salt = split_salt(part)
                sub = s.exec(select(Substance).where(Substance.inn_name == inn)).first()
                if sub is None:
                    sub = Substance(inn_name=inn, salt_form=salt, query_name=part)
                if refresh or not sub.enriched_at:
                    log.info("PubChem: %s (query '%s')", inn, part)
                    enrich(sub, pc)
                s.add(sub)
                s.flush()
                if not s.get(ProductSubstance, (dp.id, sub.id)):
                    s.add(ProductSubstance(drug_product_id=dp.id, substance_id=sub.id))
        s.commit()


if __name__ == "__main__":
    import argparse
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    logging.getLogger("httpx").setLevel(logging.WARNING)
    ap = argparse.ArgumentParser()
    ap.add_argument("--refresh", action="store_true")
    run(ap.parse_args().refresh)
