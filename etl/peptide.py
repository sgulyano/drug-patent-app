"""Peptide drugs are often named in patents only by their amino-acid sequence ("pGlu-His-Trp-...").
Match the internal residue run of the drug's sequence (the termini vary in how they are written)."""
import re

AA = "Ala|Arg|Asn|Asp|Cys|Gln|Glu|Gly|His|Ile|Leu|Lys|Met|Phe|Pro|Ser|Thr|Trp|Tyr|Val|Pyr|pGlu|Sar|Nle|Orn|Aib"
_RES = rf"(?:[DL]-?)?(?:{AA})(?:\([^)]*\))?"
_RUN = re.compile(rf"{_RES}(?:\s*-\s*{_RES}){{3,}}")
_TOK = re.compile(rf"(?:([DL])-?)?({AA})")


def residues(s: str) -> list[str]:
    return [(d or "") + aa for d, aa in _TOK.findall(s)]


def find_sequence(synonyms: list[str]) -> str | None:
    best = None
    for s in synonyms:
        if len(residues(s)) >= 5 and _RUN.search(s) and (best is None or len(s) < len(best)):
            best = s
    return best


def plain(s: str) -> list[str]:
    """Residue codes without D/L stereo marks (they are written variably: D-Leu, DLeu, D Leu)."""
    return [aa for _, aa in _TOK.findall(s)]


def core(sequence: str) -> tuple[str, ...]:
    """Internal residues, dropping both termini (pGlu / 5-oxo-Pro, -NHEt / -NH-C2H5 ...)."""
    res = plain(sequence)
    return tuple(res[1:-1]) if len(res) > 4 else tuple(res)


def mentions(text: str, sequence: str) -> bool:
    target = core(sequence)
    if len(target) < 4:
        return False
    for m in _RUN.finditer(text or ""):
        toks = tuple(plain(m.group(0)))
        n = len(target)
        if any(toks[i:i + n] == target for i in range(len(toks) - n + 1)):
            return True
    return False
