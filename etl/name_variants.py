"""Discover the Thai spellings of each substance actually used in the scraped patent corpus.

Sources, all automatic:
  * rule-generated spellings (etl.thai_translit), matched by phonetic skeleton against the corpus;
  * "<Thai> (<english>)" / "<english> (<Thai>)" pairs harvested from patent text;
  * optional LLM suggestions (etl.llm), also validated against the corpus.

    python -m etl.name_variants
"""
import json
import logging
import re
from collections import Counter
from difflib import SequenceMatcher

from sqlmodel import select

from backend.db import init_db, session
from backend.models import Patent, Substance
from etl import llm
from etl.thai_translit import generate, skel, skeleton

log = logging.getLogger(__name__)

THAI = "฀-๿"
_TRAIL_MARKS = set("่้๊๋็์ิีึืุูั")
GENERATED_N = 60
MIN_SKEL_LEN = 6
MIN_SYNONYM_SKEL_LEN = 9


class Corpus:
    def __init__(self, texts: dict[str, str]):
        self.texts = texts
        self.skels = {k: skeleton(v) for k, v in texts.items()}

    @classmethod
    def from_db(cls, s) -> "Corpus":
        texts = {}
        for p in s.exec(select(Patent)):
            texts[p.application_no] = "\n".join(x for x in (p.title, p.all_claims, p.summary) if x)
        return cls(texts)

    def find_spellings(self, variant: str) -> Counter:
        """Actual spellings in the corpus whose skeleton equals the variant's skeleton -> doc frequency."""
        target = skel(variant).replace(" ", "")
        found = Counter()
        if len(target) < MIN_SKEL_LEN:
            return found
        for doc, (sk, idx) in self.skels.items():
            start = sk.find(target)
            seen_in_doc = set()
            while start != -1:
                a, b = idx[start], idx[start + len(target) - 1] + 1
                text = self.texts[doc]
                # absorb leading vowel prefixes and trailing combining marks that the skeleton dropped
                while b < len(text) and text[b] in _TRAIL_MARKS:
                    b += 1
                spelling = re.sub(r"\s+", "", text[a:b])
                if spelling and spelling not in seen_in_doc:
                    seen_in_doc.add(spelling)
                    found[spelling] += 1
                start = sk.find(target, start + 1)
        return found

    def harvest_parenthetical(self, english_names: list[str], ref_variants: list[str]) -> Counter:
        """Thai words written next to the English name in parentheses."""
        found = Counter()
        refs = [skel(v) for v in ref_variants[:10]]
        if not refs:
            return found
        ref_len = sorted(len(r) for r in refs)[len(refs) // 2]
        before = re.compile(rf"([{THAI}]+)\s*\(\s*$")       # "<thai> (" right before the English name
        after = re.compile(rf"^\s*\)?\s*\(\s*([{THAI}]+)")  # "<english> (<thai>"
        names = [n.lower() for n in english_names if n]
        for text in self.texts.values():
            low = text.lower()
            for name in names:
                i = low.find(name)
                while i != -1:
                    m = before.search(text[max(0, i - 80):i])
                    m2 = after.match(text[i + len(name): i + len(name) + 80])
                    for g in (m, m2):
                        if g:
                            best = _best_suffix(g.group(1)[-60:], refs, ref_len)
                            if best:
                                found[best] += 1
                    i = low.find(name, i + 1)
        return found


def _best_suffix(chunk: str, refs: list[str], ref_len: int) -> str | None:
    """The Thai run before '(' may include preceding words; keep the suffix most similar to a reference."""
    best, best_score = None, 0.0
    for start in range(len(chunk)):
        cand = chunk[start:]
        if cand[0] in _TRAIL_MARKS:
            continue
        sk = skel(cand)
        if not (0.6 * ref_len <= len(sk) <= 1.4 * ref_len):
            continue
        score = max(SequenceMatcher(None, sk, r).ratio() for r in refs)
        if score > best_score:
            best, best_score = cand, score
    return best if best_score >= 0.7 else None


def distinct_synonyms(sub: Substance, limit: int = 4) -> list[str]:
    """Synonyms that are genuinely different names (not the INN in another language, not codes), with
    chemical locant prefixes like 'O-' / 'N-' removed: 'O-Desmethylvenlafaxine' -> 'desmethylvenlafaxine'."""
    inn = sub.inn_name.lower()
    stem = inn[: max(4, len(inn) - 2)]
    out = []
    for s in json.loads(sub.synonyms_en or "[]"):
        name = re.sub(r"^[a-z]-", "", s.strip().lower())
        if not re.fullmatch(r"[a-z][a-z \-]{5,}", name):
            continue
        flat = name.replace("-", "")
        if flat == inn or (flat.startswith(stem) and abs(len(flat) - len(inn)) <= 3) or flat in out:
            continue  # the INN itself or a Latin/Spanish/French form of it
        out.append(flat)
        if len(out) >= limit:
            break
    return out


def discover(sub: Substance, corpus: Corpus) -> dict:
    english = [sub.inn_name] + json.loads(sub.synonyms_en or "[]")[:5]
    generated = [g for g, _ in generate(sub.inn_name, GENERATED_N)]
    synonym_generated = [g for syn in distinct_synonyms(sub) for g, _ in generate(syn, 25)]
    llm_spellings = llm.thai_spellings(sub.inn_name) if llm.enabled() else []

    observed = Counter()
    source = {}
    seen_skels = set()
    for v in llm_spellings + generated + synonym_generated:
        k = skel(v)
        if k in seen_skels:
            continue
        if v in synonym_generated and len(k) < MIN_SYNONYM_SKEL_LEN:
            continue  # short brand names transliterate into common Thai syllables (false matches)
        seen_skels.add(k)
        for spelling, n in corpus.find_spellings(v).items():
            observed[spelling] += n
            source.setdefault(spelling, "llm" if v in llm_spellings
                              else "synonym" if v in synonym_generated else "generated")
    for spelling, n in corpus.harvest_parenthetical(english, llm_spellings + generated).items():
        observed[spelling] += n
        source.setdefault(spelling, "parenthetical")
    # keep generated probes that are skeleton-distinct from what we observed, for site searches
    obs_skels = {skel(s) for s in observed}
    probes = []
    for v in llm_spellings + generated:
        if skel(v) not in obs_skels and v not in probes:
            probes.append(v)
    probes = probes[:5]
    for syn in distinct_synonyms(sub)[:2]:  # best spelling of the leading synonyms, e.g. a USAN name
        top = generate(syn, 1)
        if top and skel(top[0][0]) not in obs_skels:
            probes.append(top[0][0])
    if not observed:  # nothing seen yet: also probe with tail fragments, robust to first-syllable variation
        tails = []
        for v in llm_spellings + generated:
            t = tail_core(v)
            if t and t not in tails and not any(t in p for p in tails):
                tails.append(t)
            if len(tails) >= 3:
                break
        probes += tails
    return {
        "observed": [{"spelling": s, "docs": n, "source": source.get(s)} for s, n in observed.most_common()],
        "probes": probes,
    }


_LEADING = set("เแโใไ")
_COMBINING = set("ัิีึืุู็่้๊๋์ํ")


def tail_core(spelling: str, frac: float = 0.45, min_len: int = 5) -> str | None:
    """The trailing ~55% of a Thai spelling, cut at a syllable-safe position (never inside a vowel cluster)."""
    s = spelling.replace(" ", "")
    vowelish = _COMBINING | set("าะำอ")
    consonant = lambda ch: "ก" <= ch <= "ฮ"  # noqa: E731

    def onset(i: int) -> bool:
        if s[i - 1] in _LEADING:
            return False
        if s[i] in _LEADING:
            return True
        if not consonant(s[i]) or i + 1 >= len(s):
            return False
        if s[i] in "รล" and consonant(s[i - 1]) and s[i - 1] not in "รล":  # second half of a cluster (ทร, พล)
            return False
        nxt = s[i + 1]
        return nxt in vowelish or (s[i] in "กขคตทปพผฟบดส" and nxt in "รล" and i + 2 < len(s) and s[i + 2] in vowelish)

    cuts = [i for i in range(1, len(s)) if onset(i)]
    cuts = [i for i in cuts if len(s) - i >= min_len]
    if not cuts:
        return None
    i = min(cuts, key=lambda c: abs(c - len(s) * frac))
    return s[i:] if i > 0 else None


def run():
    init_db()
    with session() as s:
        corpus = Corpus.from_db(s)
        log.info("corpus: %d documents", len(corpus.texts))
        for sub in s.exec(select(Substance)):
            res = discover(sub, corpus)
            sub.name_variants_th = json.dumps(res, ensure_ascii=False)
            s.add(sub)
            log.info("%s: observed %s | probes %s", sub.inn_name,
                     [o["spelling"] for o in res["observed"][:6]], res["probes"][:3])
        s.commit()


def variants_for(sub: Substance, include_probes: bool = True) -> list[str]:
    data = json.loads(sub.name_variants_th or "{}") if sub.name_variants_th else {}
    out = [o["spelling"] for o in data.get("observed", [])]
    if include_probes:
        out += data.get("probes", [])
    return list(dict.fromkeys(out))


if __name__ == "__main__":
    import sys
    sys.stdout.reconfigure(encoding="utf-8")
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    run()
