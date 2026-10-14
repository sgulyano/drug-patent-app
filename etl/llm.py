"""Optional Claude calls (structured JSON output). Everything here degrades gracefully to None when no
credentials are configured, so the rule-based pipeline always works on its own.

Prompts are generic: they describe the task and the categories only, never examples from the target drugs.
"""
import json
import logging
import os
from pathlib import Path

log = logging.getLogger(__name__)

MODEL = os.environ.get("PATENTAPP_LLM_MODEL", "claude-opus-5-5")
EFFORT = os.environ.get("PATENTAPP_LLM_EFFORT", "medium")
_client = None
_disabled = False


def enabled() -> bool:
    if _disabled or os.environ.get("PATENTAPP_LLM", "1") == "0":
        return False
    return bool(os.environ.get("ANTHROPIC_API_KEY") or os.environ.get("ANTHROPIC_AUTH_TOKEN")
                or (Path.home() / ".config" / "anthropic").exists())


def _get_client():
    global _client
    if _client is None:
        import anthropic
        _client = anthropic.Anthropic()
    return _client


def ask_json(system: str, user: str, schema: dict, max_tokens: int = 4000) -> dict | None:
    """One structured-output call. Returns the parsed JSON object, or None on any failure/refusal."""
    global _disabled
    if not enabled():
        return None
    import anthropic
    try:
        resp = _get_client().beta.messages.create(
            model=MODEL,
            max_tokens=max_tokens,
            betas=["server-side-fallback-2026-07-01"],
            fallbacks="default",
            output_config={"effort": EFFORT, "format": {"type": "json_schema", "schema": schema}},
            system=[{"type": "text", "text": system, "cache_control": {"type": "ephemeral"}}],
            messages=[{"role": "user", "content": user}],
        )
    except anthropic.AuthenticationError:
        log.warning("Claude: no valid credentials; LLM steps disabled for this run")
        _disabled = True
        return None
    except anthropic.RateLimitError as e:
        log.warning("Claude rate limited: %s", e.message)
        return None
    except anthropic.APIStatusError as e:
        log.warning("Claude API error %s: %s", e.status_code, e.message)
        return None
    except anthropic.APIConnectionError:
        log.warning("Claude: network error")
        return None
    if resp.stop_reason == "refusal":
        log.warning("Claude declined (%s)", getattr(resp.stop_details, "category", None))
        return None
    text = next((b.text for b in resp.content if b.type == "text"), None)
    try:
        return json.loads(text) if text else None
    except json.JSONDecodeError:
        log.warning("Claude returned invalid JSON")
        return None


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
    return [s.strip() for s in (out or {}).get("spellings", []) if s and s.strip()][:n]


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
