"""Generate plausible Thai spellings of an English drug name (INN), and match them loosely against text.

Thai patent documents transliterate drug names inconsistently (ท/ต, พ/ป, ิ/ี, ไ-/-ิ, with or without ก
before ซ for 'x' ...). We therefore:

  1. generate many candidate spellings with general English->Thai transliteration rules (no per-drug rules);
  2. reduce every spelling — and every corpus text — to a *phonetic skeleton* that erases the distinctions
     that vary between writers (tone marks, consonant classes, vowel length);
  3. find skeleton hits in the corpus and read back the *actual* spelling used there.

Step 3 lives in etl/name_variants.py.
"""
import heapq
import re
from itertools import product

VOWELS = set("aeiouy")

# Onsets: English cluster -> [(Thai consonant(s), weight)]. 'S' prefix clusters handled separately.
ONSET = {
    "b": [("บ", 1)], "c": [("ค", 1)], "ch": [("ค", .8), ("ช", .7)], "d": [("ด", 1)], "f": [("ฟ", 1)],
    "g": [("ก", 1)], "h": [("ฮ", 1)], "j": [("จ", 1)], "k": [("ค", 1), ("ก", .5)], "l": [("ล", 1)],
    "m": [("ม", 1)], "n": [("น", 1)], "p": [("พ", 1), ("ป", .8)], "ph": [("ฟ", 1)], "qu": [("คว", 1)],
    "r": [("ร", 1)], "s": [("ซ", 1), ("ส", .7)], "sh": [("ช", 1)], "t": [("ท", 1), ("ต", .7)],
    "th": [("ท", 1), ("ธ", .7)], "v": [("ว", 1)], "w": [("ว", 1)], "x": [("ซ", 1)], "y": [("ย", 1)],
    "z": [("ซ", 1)],
    "tr": [("ทร", 1), ("ตร", .6)], "pr": [("พร", 1), ("ปร", .8)], "br": [("บร", 1)], "cr": [("คร", 1)],
    "dr": [("ดร", 1)], "gr": [("กร", 1)], "fr": [("ฟร", 1)], "pl": [("พล", 1), ("ปล", .6)],
    "bl": [("บล", 1)], "cl": [("คล", 1)], "fl": [("ฟล", 1)], "gl": [("กล", 1)], "sl": [("สล", 1)],
    "phr": [("ฟร", 1)], "thr": [("ทร", 1)], "chl": [("คล", 1)], "chr": [("คร", 1)],
}
SOFT_C = [("ซ", 1)]
SOFT_G = [("จ", .9), ("ก", .7)]
ONSET_CLUSTERS = sorted([k for k in ONSET if len(k) > 1], key=len, reverse=True)

# Finals (single English letter)
FINAL = {
    "n": [("น", 1)], "m": [("ม", 1)], "l": [("ล", 1)], "s": [("ส", 1)], "t": [("ท", 1), ("ต", .8)],
    "d": [("ด", 1)], "c": [("ค", 1), ("ก", .8)], "k": [("ค", 1), ("ก", .8)], "g": [("ก", 1)],
    "p": [("พ", 1), ("ป", .7)], "b": [("บ", 1)], "f": [("ฟ", 1)], "v": [("ฟ", 1)], "z": [("ซ", 1)],
    "x": [("กซ", 1)], "h": [("", 1)], "y": [("ย", 1)], "w": [("ว", 1)], "j": [("จ", 1)],
}
FINAL_MULTI = {"ng": [("ง", 1)], "ck": [("ค", 1), ("ก", .8)], "ph": [("ฟ", 1)], "th": [("ท", 1)],
               "sh": [("ช", 1)], "ch": [("ช", 1)]}

# Vowel templates: C = onset consonant(s), F = final(s). open = no final, closed = has final.
V_OPEN = {
    "a": [("Cา", 1), ("แC", .5), ("Cะ", .2)],
    "e": [("เC", 1), ("Cี", .5)],
    "i": [("Cิ", 1), ("Cี", .7), ("ไC", .7)],
    "y": [("Cิ", .8), ("ไC", 1)],
    "o": [("โC", 1), ("Cอ", .4)],
    "u": [("Cู", 1), ("Cุ", .6)],
    "ai": [("ไC", 1)], "ay": [("ไC", 1)], "ei": [("ไC", .8), ("เC", .7)], "ey": [("เC", 1)],
    "au": [("Cอ", 1), ("เCา", .7)], "aw": [("Cอ", 1)], "ou": [("Cู", 1), ("เCา", .5)],
    "oo": [("Cู", 1)], "ee": [("Cี", 1)], "ea": [("Cี", 1), ("เCีย", .5)], "eu": [("Cู", 1), ("Cิว", .8), ("Cิวโ", .1)],
    "ia": [("เCีย", 1), ("Cีอา", .5)], "io": [("Cิโอ", 1), ("ไCโอ", 1), ("Cีโอ", .5)], "iu": [("Cิว", 1)],
    "ua": [("Cัว", 1), ("Cูอา", .6)], "ue": [("Cู", 1)], "ui": [("Cุย", 1), ("Cูอิ", .5)],
    "oa": [("โCอา", 1)], "ie": [("Cี", 1)], "oi": [("Cอย", 1)], "oy": [("Cอย", 1)], "ae": [("Cี", 1)],
    "eo": [("Cีโอ", 1)], "ii": [("Cีอิ", 1)],
}
V_CLOSED = {
    "a": [("แCF", 1), ("CาF", .9), ("CัF", .6)],
    "e": [("เCF", 1), ("CีF", .4), ("เC็F", .3)],
    "i": [("CิF", 1), ("CีF", .5)],
    "y": [("CิF", 1)],
    "o": [("CอF", .8), ("โCF", .8), ("C็อF", .6)],
    "u": [("CัF", .9), ("CุF", .7), ("CูF", .5)],
    "ia": [("เCียF", 1)], "iu": [("เCียF", 1), ("CิอุF", .4)], "io": [("Cิโอ" + "F", 1), ("ไCอF", .6)],
    "ou": [("CูF", 1)], "oo": [("CูF", 1)], "ee": [("CีF", 1)], "ea": [("CีF", 1)], "eu": [("CูF", 1)],
    "ai": [("ไCF", 1)], "ei": [("เCF", 1)], "au": [("CอF", 1)], "ue": [("CูF", 1)], "ua": [("CัวF", 1)],
}
# Silent final 'e' lengthens the vowel: -ate -ine -one -ule ...
V_LONG = {
    "a": [("เCF", 1), ("แCF", .3)],
    "i": [("CีF", 1), ("ไCF์", .6)],
    "o": [("โCF", 1)],
    "u": [("CูF", 1)],
    "e": [("CีF", 1)],
    "y": [("ไCF์", 1)],
}
# Vowel + r: the r is not pronounced as a final in Thai spellings of English words
V_R = {
    "a": [("Cาร์", 1), ("Cา", .4)], "e": [("เCอร์", 1), ("เCอ", .4)], "i": [("เCอร์", 1)],
    "o": [("Cอร์", 1), ("Cอ", .4)], "u": [("เCอร์", 1)], "y": [("เCอร์", 1)],
}


def _onsets(cluster: str, next_vowel: str) -> list[tuple[str, str, float]]:
    """-> [(prefix, consonant(s), weight)]; prefix is a free-standing ส for s-clusters (st-, sp-, sc-)."""
    if cluster == "":
        return [("", "อ", 1)]
    if len(cluster) >= 2 and cluster[0] == "s" and cluster[1] in "tpckm" and cluster not in ONSET:
        return [("ส", c, w) for _, c, w in _onsets(cluster[1:], next_vowel)]
    if cluster == "c":
        alts = SOFT_C if next_vowel[:1] in "eiy" else ONSET["c"]
        return [("", c, w) for c, w in alts]
    if cluster == "g":
        alts = SOFT_G if next_vowel[:1] in "eiy" else ONSET["g"]
        return [("", c, w) for c, w in alts]
    if cluster in ONSET:
        return [("", c, w) for c, w in ONSET[cluster]]
    # unknown multi-consonant onset: render letter by letter, extra letters with karan-free consonants
    parts = [ONSET.get(ch, [(ch, 1)])[0][0] for ch in cluster]
    return [("", "".join(parts), .5)]


def _finals(coda: str) -> list[tuple[str, float]]:
    if not coda:
        return [("", 1)]
    if coda in FINAL_MULTI:
        return FINAL_MULTI[coda]
    if len(coda) == 1:
        return FINAL.get(coda, [(coda, .3)])
    first = _finals(coda[0]) if coda[:2] not in FINAL_MULTI else FINAL_MULTI[coda[:2]]
    rest = coda[1:] if coda[:2] not in FINAL_MULTI else coda[2:]
    rest_th = "".join(ONSET.get(ch, [(ch, 1)])[0][0] for ch in rest)
    return [(f + rest_th + ("์" if rest_th else ""), w) for f, w in first]


def syllabify(word: str) -> list[dict]:
    """Split a lowercase INN into syllables {onset, vowel, coda, long}."""
    w = re.sub(r"[^a-z]", "", word.lower())
    if not w:
        return []
    long_last = False
    if re.search(r"[aeiouy][bcdfgklmnprstvz]e$", w) and not re.search(r"[aeiou]{2}[bcdfgklmnprstvz]e$", w):
        w, long_last = w[:-1], True  # silent e
    # tokens: alternate consonant runs and vowel runs; 'y' is a vowel unless word-initial before a vowel
    toks = []
    i = 0
    while i < len(w):
        is_v = w[i] in VOWELS and not (w[i] == "y" and (i == 0 or (i + 1 < len(w) and w[i + 1] in "aeiou")))
        j = i
        while j < len(w):
            jv = w[j] in VOWELS and not (w[j] == "y" and (j == 0 or (j + 1 < len(w) and w[j + 1] in "aeiou")))
            if jv != is_v:
                break
            j += 1
        toks.append(("V" if is_v else "C", w[i:j]))
        i = j
    # split long vowel runs into known digraphs
    vt = []
    for kind, s in toks:
        if kind == "V" and len(s) > 2:
            k = 0
            while k < len(s):
                pair = s[k:k + 2]
                if pair in V_OPEN and len(pair) == 2:
                    vt.append(("V", pair)); k += 2
                else:
                    vt.append(("V", s[k])); k += 1
        elif kind == "V" and len(s) == 2 and s not in V_OPEN:
            vt.extend([("V", s[0]), ("V", s[1])])
        else:
            vt.append((kind, s))
    # assemble syllables
    syls = []
    pending_onset = ""
    idx = 0
    if vt and vt[0][0] == "C":
        pending_onset = vt[0][1]
        idx = 1
    while idx < len(vt):
        kind, s = vt[idx]
        if kind != "V":
            idx += 1
            continue
        syl = {"onset": pending_onset, "vowel": s, "coda": "", "long": False, "x_split": False}
        pending_onset = ""
        nxt = vt[idx + 1] if idx + 1 < len(vt) else None
        if nxt and nxt[0] == "C":
            run = nxt[1]
            last_syllable = idx + 2 >= len(vt)
            if last_syllable:
                syl["coda"] = run
            elif len(run) == 1:
                pending_onset = run
                if run == "x":
                    syl["x_split"] = True
            else:
                cut = len(run) - 1
                for cl in ONSET_CLUSTERS:
                    if run.endswith(cl) and len(cl) < len(run) + 1:
                        cut = len(run) - len(cl)
                        break
                if cut == 0:  # whole run is a cluster
                    pending_onset = run
                else:
                    syl["coda"], pending_onset = run[:cut], run[cut:]
            idx += 2
        else:
            idx += 1
        syls.append(syl)
    if syls and long_last:
        syls[-1]["long"] = True
    return syls


def _syllable_options(syl: dict) -> list[tuple[str, float]]:
    v, coda = syl["vowel"], syl["coda"]
    out = []
    onsets = _onsets(syl["onset"], v)
    if coda.startswith("r") and len(v) == 1 and v in V_R:
        rest = coda[1:]
        rest_opts = _finals(rest) if rest else [("", 1)]
        for (pre, c, wo), (tpl, wt), (f, wf) in product(onsets, V_R[v], rest_opts):
            out.append((pre + tpl.replace("C", c) + f, wo * wt * wf))
        return out
    if syl["long"] and coda and v in V_LONG:
        templates = V_LONG[v]
    elif coda:
        templates = V_CLOSED.get(v) or [("C" + "".join(t for t, _ in V_OPEN.get(v, [("", 1)])[:1]) + "F", .3)]
    else:
        templates = V_OPEN.get(v) or [("C", .2)]
    finals = _finals(coda)
    if syl.get("x_split"):  # medial x: either a ก final before the ซ onset, or nothing
        templates_k = V_CLOSED.get(v) or templates
        for (pre, c, wo), (tpl, wt) in product(onsets, templates_k):
            out.append((pre + tpl.replace("C", c).replace("F", "ก"), wo * wt))
        for (pre, c, wo), (tpl, wt) in product(onsets, V_OPEN.get(v, templates)):
            out.append((pre + tpl.replace("C", c), wo * wt * .6))
        return out
    for (pre, c, wo), (tpl, wt), (f, wf) in product(onsets, templates, finals):
        # coda n before a g/k onset is often written ง
        out.append((pre + tpl.replace("C", c).replace("F", f), wo * wt * wf))
    return out


def generate(inn: str, n: int = 40) -> list[tuple[str, float]]:
    """Top-n Thai spellings (by rule weight) for an English name. Multi-word names are joined by a space."""
    words = [w for w in re.split(r"[\s\-]+", inn.lower()) if w]
    per_word = []
    for w in words:
        syls = syllabify(w)
        options = []
        for i, s in enumerate(syls):
            opts = _syllable_options(s)
            if s["coda"] == "n" and i + 1 < len(syls) and syls[i + 1]["onset"][:1] in ("g", "k"):
                opts += [(o.replace("น", "ง", 1) if o.endswith("น") else o, wt * .9) for o, wt in opts]
            options.append(sorted(opts, key=lambda x: -x[1])[:12])
        per_word.append(_beam(options, n * 3))
    beams = [("", 1.0)]
    for word_opts in per_word:
        beams = heapq.nlargest(n * 3, ((b + (" " if b else "") + o, bw * ow) for b, bw in beams for o, ow in word_opts),
                               key=lambda x: x[1])
    seen, out = set(), []
    for s, wt in beams:
        if s not in seen:
            seen.add(s)
            out.append((s, wt))
        if len(out) >= n:
            break
    return out


def _beam(options: list[list[tuple[str, float]]], width: int) -> list[tuple[str, float]]:
    beams = [("", 1.0)]
    for opts in options:
        beams = heapq.nlargest(width, ((b + o, bw * ow) for b, bw in beams for o, ow in opts), key=lambda x: x[1])
    return beams


# ------------------------------------------------------------------ phonetic skeleton
_SKEL = {}
for chars, code in [
    ("ทธตฒฑฐถ", "T"), ("พปผภ", "P"), ("ฟฝ", "F"), ("สซศษ", "S"), ("คกขฆฃฅ", "K"), ("ดฎ", "D"),
    ("บ", "B"), ("จชฉฌ", "J"), ("นณ", "N"), ("ลฬ", "L"), ("ร", "R"), ("ม", "M"), ("ยญ", "Y"), ("ว", "W"),
    ("ฮห", "H"), ("อ", "O"), ("ง", "G"),
    ("ิีึื", "i"), ("ุู", "u"), ("าั", "a"), ("เ", "e"), ("แ", "e"), ("โ", "o"), ("ไใ", "y"), ("ำ", "am"),
]:
    for ch in chars:
        _SKEL[ch] = code
_DROP = set("่้๊๋็์ะ ํ-")


def skeleton(text: str) -> tuple[str, list[int]]:
    """Map Thai text to a coarse phonetic skeleton; also return skeleton-index -> original-index."""
    out, idx = [], []
    for i, ch in enumerate(text):
        if ch in _DROP or ch.isspace():
            continue
        code = _SKEL.get(ch)
        if code is None:
            if "฀" <= ch <= "๿":
                continue
            code = ch.lower()
        for c in code:
            if c.isupper() and out and out[-1] == c:
                continue  # doubled consonant (ทธ, สซ): written variably, pronounced once
            out.append(c)
            idx.append(i)
    return "".join(out), idx


def skel(text: str) -> str:
    return skeleton(text)[0]
