"""Minimal PubChem PUG REST / PUG View client."""
import logging
import re
from urllib.parse import quote

from etl.http import PoliteClient

log = logging.getLogger(__name__)

PUG = "https://pubchem.ncbi.nlm.nih.gov/rest/pug"
VIEW = "https://pubchem.ncbi.nlm.nih.gov/rest/pug_view/data/compound"
IMG = PUG + "/compound/cid/{cid}/PNG?image_size=500x500"
PROPS = "IUPACName,MolecularFormula,MolecularWeight,SMILES,CanonicalSMILES,InChIKey"


class PubChem:
    def __init__(self, client: PoliteClient | None = None):
        self.c = client or PoliteClient(min_interval=0.25)  # PubChem allows 5 req/s

    def _json(self, url):
        r = self.c.get(url)
        if r.status_code == 404:
            return None
        r.raise_for_status()
        return r.json()

    def cid_by_name(self, name: str) -> int | None:
        j = self._json(f"{PUG}/compound/name/{quote(name)}/cids/JSON")
        ids = (j or {}).get("IdentifierList", {}).get("CID") or []
        return ids[0] if ids and ids[0] else None

    def parent_cid(self, cid: int) -> int:
        j = self._json(f"{PUG}/compound/cid/{cid}/cids/JSON?cids_type=parent")
        ids = (j or {}).get("IdentifierList", {}).get("CID") or []
        return ids[0] if ids else cid

    def properties(self, cid: int) -> dict:
        j = self._json(f"{PUG}/compound/cid/{cid}/property/{PROPS}/JSON")
        props = ((j or {}).get("PropertyTable", {}).get("Properties") or [{}])[0]
        return {
            "iupac_name": props.get("IUPACName"),
            "molecular_formula": props.get("MolecularFormula"),
            "molecular_weight": float(props["MolecularWeight"]) if props.get("MolecularWeight") else None,
            "smiles": props.get("SMILES") or props.get("CanonicalSMILES"),
            "inchikey": props.get("InChIKey"),
        }

    def synonyms(self, cid: int) -> list[str]:
        j = self._json(f"{PUG}/compound/cid/{cid}/synonyms/JSON")
        info = ((j or {}).get("InformationList", {}).get("Information") or [{}])[0]
        return info.get("Synonym") or []

    def description(self, cid: int) -> str | None:
        j = self._json(f"{PUG}/compound/cid/{cid}/description/JSON")
        infos = (j or {}).get("InformationList", {}).get("Information") or []
        descs = [i["Description"] for i in infos if i.get("Description")]
        return "\n\n".join(descs[:3]) or None

    def view_heading(self, cid: int, heading: str, max_items: int = 3) -> list[str]:
        j = self._json(f"{VIEW}/{cid}/JSON?heading={quote(heading)}")
        if not j:
            return []
        out = []

        def walk(node):
            if isinstance(node, dict):
                if "StringWithMarkup" in node:
                    for s in node["StringWithMarkup"]:
                        if s.get("String"):
                            out.append(s["String"])
                for v in node.values():
                    walk(v)
            elif isinstance(node, list):
                for v in node:
                    walk(v)

        walk(j.get("Record", {}))
        seen, uniq = set(), []
        for s in out:
            if s not in seen:
                seen.add(s)
                uniq.append(s)
        return uniq[:max_items]

    def atc_codes(self, cid: int) -> list[str]:
        texts = self.view_heading(cid, "ATC Code", max_items=20)
        return sorted({m for t in texts for m in re.findall(r"\b[A-Z]\d{2}[A-Z]{2}\d{2}\b", t)})


def clean_synonyms(syns: list[str], limit: int = 25) -> list[str]:
    """Keep word-like names (INNs, brand names); drop registry codes and long systematic names."""
    out = []
    for s in syns:
        s2 = s.strip()
        if not (4 <= len(s2) <= 40):
            continue
        if re.search(r"\d", s2) or re.search(r"[\[\]{}(),;=/]", s2):
            continue
        if re.fullmatch(r"[A-Z]{14}-[A-Z]{10}-[A-Z]", s2):  # InChIKey
            continue
        if re.fullmatch(r"[A-Z0-9\-]+", s2) and len(s2) < 8:  # codes like 'UNII'
            continue
        out.append(s2)
        if len(out) >= limit:
            break
    return out
