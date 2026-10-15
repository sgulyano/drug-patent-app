"""Optional LLM calls via Ollama Cloud (JSON output). Everything here degrades gracefully to None when no
credentials are configured, so the rule-based pipeline always works on its own.

Prompts are generic: they describe the task and the categories only, never examples from the target drugs.
"""
import json
import logging
import os
from pathlib import Path

import httpx

log = logging.getLogger(__name__)


def _load_dotenv(path: Path = Path(__file__).resolve().parent.parent / ".env") -> None:
    """Read KEY=VALUE lines from the repo's .env; real environment variables win."""
    if not path.exists():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            k, v = line.split("=", 1)
            os.environ.setdefault(k.strip(), v.strip().strip("'\""))


_load_dotenv()

HOST = os.environ.get("OLLAMA_HOST", "https://ollama.com").rstrip("/")
MODEL = os.environ.get("PATENTAPP_LLM_MODEL", "gemma4:31b")
TIMEOUT = float(os.environ.get("PATENTAPP_LLM_TIMEOUT", "180"))
_client = None
_disabled = False


def enabled() -> bool:
    if _disabled or os.environ.get("PATENTAPP_LLM", "1") == "0":
        return False
    return bool(os.environ.get("OLLAMA_API_KEY"))


def _get_client() -> httpx.Client:
    global _client
    if _client is None:
        _client = httpx.Client(base_url=HOST, timeout=TIMEOUT,
                               headers={"Authorization": f"Bearer {os.environ['OLLAMA_API_KEY']}"})
    return _client


def _conforms(obj, schema: dict) -> bool:
    """Ollama does not enforce the schema, so check what callers rely on: required keys, types, enums."""
    types = {"object": dict, "array": list, "string": str, "boolean": bool, "number": (int, float)}
    t = schema.get("type")
    if t in types and (not isinstance(obj, types[t]) or (t == "number" and isinstance(obj, bool))):
        return False
    if "enum" in schema and obj not in schema["enum"]:
        return False
    if t == "object":
        props = schema.get("properties", {})
        return all(k in obj for k in schema.get("required", [])) and \
            all(_conforms(obj[k], s) for k, s in props.items() if k in obj)
    if t == "array" and "items" in schema:
        return all(_conforms(x, schema["items"]) for x in obj)
    return True


def ask_json(system: str, user: str, schema: dict, max_tokens: int = 4000) -> dict | None:
    """One JSON-output call. Returns the parsed object if it matches the schema, or None on any failure."""
    global _disabled
    if not enabled():
        return None
    system = f"{system}\n\nRespond with only a JSON object matching this JSON Schema:\n{json.dumps(schema)}"
    try:
        r = _get_client().post("/api/chat", json={
            "model": MODEL,
            "stream": False,
            "format": "json",
            "options": {"temperature": 0, "num_predict": max_tokens},
            "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}],
        })
    except httpx.HTTPError as e:
        log.warning("Ollama: network error (%s)", e)
        return None
    if r.status_code in (401, 403):
        log.warning("Ollama: no valid credentials; LLM steps disabled for this run")
        _disabled = True
        return None
    if r.status_code != 200:
        log.warning("Ollama API error %s: %s", r.status_code, r.text[:200])
        return None
    # Models may wrap the object in a code fence or keep talking after it: take the first JSON object.
    text = r.json().get("message", {}).get("content", "")
    start = text.find("{")
    try:
        data = json.JSONDecoder().raw_decode(text, start)[0] if start >= 0 else None
    except json.JSONDecodeError:
        log.warning("Ollama returned invalid JSON")
        return None
    if data is None or not _conforms(data, schema):
        log.warning("Ollama output does not match the schema")
        return None
    return data


# ------------------------------------------------------------------ Thai transliterations
TRANSLIT_SYSTEM = (
    "You are an expert in Thai pharmaceutical and patent terminology. Thai patent documents write "
    "international nonproprietary names (INNs) of drugs in Thai script, and different writers transliterate "
    "the same English name differently (consonant choice, vowel length, leading vowel forms, use of ์, "
    "whether 'x' becomes กซ or ซ, and so on). Given an English drug name, list the Thai spellings that are "
    "plausibly used in Thai patent applications and official documents, most likely first. Output only "
    "spellings of the drug name itself (no salt words unless asked, no explanations)."
)
TRANSLIT_SCHEMA = {
    "type": "object",
    "properties": {"spellings": {"type": "array", "items": {"type": "string"}}},
    "required": ["spellings"],
    "additionalProperties": False,
}


def thai_spellings(inn: str, n: int = 12) -> list[str]:
    out = ask_json(TRANSLIT_SYSTEM, f"English name: {inn}\nReturn up to {n} Thai spellings.", TRANSLIT_SCHEMA, 2000)
    return list(dict.fromkeys(s.strip() for s in (out or {}).get("spellings", []) if s and s.strip()))[:n]


# ------------------------------------------------------------------ claim classification
CATEGORIES = ["drug_substance", "solid_form", "process", "formulation", "device", "use", "combination", "unrelated"]
CLASSIFY_SYSTEM = """You are a pharmaceutical patent analyst assessing freedom-to-operate for generic drug manufacturing in Thailand.

You receive one Thai patent application (title, main claim, summary — mostly in Thai) and one active substance (English INN, salt form, synonyms, Thai spellings). Decide whether the patent's claims cover that substance, and if so what kind of claim it is.

claim_category (choose exactly one):
- drug_substance: claims the active compound itself (a specific compound or a generic/Markush formula covering it)
- solid_form: claims a particular salt, crystal/polymorph, hydrate/solvate, amorphous form, or particle-size form (e.g. micronised) of the substance
- process: claims a process for making the substance or its intermediates
- formulation: claims a pharmaceutical composition, dosage form, excipient system, release-control system, or capsule/powder preparation containing the substance
- device: claims an inhaler, delivery device or container (the substance may appear only as an example payload)
- use: claims a medical use, dosing regimen, or method of treatment with the substance
- combination: claims a combination product of the substance with another active ingredient
- unrelated: the substance is not covered (only mentioned in passing, or a different compound)

blocking_level:
- blocking: a generic of this substance cannot be made at all while this is in force (normally only drug_substance claims that cover the compound)
- avoidable: a generic can design around it (different form, formulation, process, device, or not using the claimed combination/use)
- not_relevant: unrelated

design_around_note_th: one short Thai sentence telling a generic manufacturer what to avoid (empty if unrelated).
rationale: one or two short English sentences citing the claim wording you relied on.
confidence: 0..1."""

CLASSIFY_SCHEMA = {
    "type": "object",
    "properties": {
        "is_about_substance": {"type": "boolean"},
        "claim_category": {"type": "string", "enum": CATEGORIES},
        "blocking_level": {"type": "string", "enum": ["blocking", "avoidable", "not_relevant"]},
        "design_around_note_th": {"type": "string"},
        "confidence": {"type": "number"},
        "rationale": {"type": "string"},
    },
    "required": ["is_about_substance", "claim_category", "blocking_level", "design_around_note_th",
                 "confidence", "rationale"],
    "additionalProperties": False,
}


def classify_claim(substance: dict, patent: dict) -> dict | None:
    user = json.dumps({"substance": substance, "patent": patent}, ensure_ascii=False, indent=1)
    return ask_json(CLASSIFY_SYSTEM, user, CLASSIFY_SCHEMA, 3000)
