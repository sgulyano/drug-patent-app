"""Stage A (candidate links) + Stage B (claim classification) of the matching design.

    python -m etl.match            # rules, plus the Ollama Cloud LLM when credentials are configured
    python -m etl.match --no-llm
"""
import argparse
import logging
import re

from sqlmodel import select

from backend.db import init_db, session
from backend.models import (BlockingLevel as B, ClaimCategory as C, MatchMethod, Patent, PatentLink, ReviewState,
                            Substance)
from etl import llm, peptide
from etl.name_variants import variants_for
from etl.thai import normalize_text, squash

log = logging.getLogger(__name__)

PHARMA_IPC = ("A61K", "A61P", "A61M", "A61J", "C07", "C12", "B65D")

# ------------------------------------------------------------------ Stage A: candidates


def _english_terms(sub: Substance) -> list[str]:
    terms = [sub.inn_name] + [s for s in sub.synonyms_en or [] if " " not in s.strip()][:12]
    return list(dict.fromkeys(t.lower() for t in terms if len(t) >= 5))


def _sections(p: Patent) -> dict[str, str]:
    claims = p.all_claims or ""
    main = p.main_claim or ""
    dependent = claims[len(main):] if claims.startswith(main) else claims
    return {"title": p.title or "", "claim1": main, "claims": dependent, "summary": p.summary or ""}


def find_mentions(p: Patent, thai_terms: list[str], en_terms: list[str], sequence: str | None = None) -> dict[str, list[str]]:
    """section -> matched terms."""
    hits = {}
    for sec, text in _sections(p).items():
        if not text:
            continue
        sq = squash(text)
        low = text.lower()
        found = [t for t in thai_terms if squash(t) and squash(t) in sq]
        found += [t for t in en_terms if re.search(rf"(?<![a-z]){re.escape(t)}(?![a-z])", low)]
        if sequence and peptide.mentions(text, sequence):
            found.append(sequence)
        if found:
            hits[sec] = sorted(set(found))
    return hits


def snippet(p: Patent, term: str, width: int = 90) -> str | None:
    for text in (p.main_claim, p.title, p.all_claims, p.summary):
        if not text:
            continue
        t = re.sub(r"\s+", " ", text)
        i = t.lower().find(term.lower())
        if i < 0:
            continue
        return ("…" if i > width else "") + t[max(0, i - width): i + len(term) + width] + "…"
    return None


# ------------------------------------------------------------------ Stage B: rule classifier
R = lambda *words: re.compile("|".join(words), re.I)  # noqa: E731
USE_HEAD = R(r"^\W*\d*\.?\s*การใช้", r"^\W*\d*\.?\s*use of", r"^\W*\d*\.?\s*วิธี(การ)?(รักษา|ป้องกัน|บำบัด)",
             r"method (of|for) treat", r"^\W*\d*\.?\s*สาร(ป้องกัน|สำหรับป้องกัน|สำหรับรักษา)")
PROCESS_HEAD = R(r"^\W*\d*\.?\s*(กรรมวิธี|กระบวนการ|ขบวนการ|การสังเคราะห์|การเตรียม|วิธี(การ)?(เตรียม|ผลิต|สังเคราะห์)|process|method for (the )?(prepar|produc|manufactur))")
DEVICE = R(r"อุปกรณ์", r"เครื่องมือ", r"เครื่องสูด", r"กล้องสูด", r"เครื่องพ่น", r"inhaler", r"\bdevice\b", r"ภาชนะบรรจุ")
COMBINATION = R(r"การรวมกัน", r"ใช้ร่วมกับ", r"ร่วมกันของ", r"สารรวมทางยา", r"\bcombination\b", r"สารออกฤทธิ์อย่างน้อยสอง",
                r"ส่วนประกอบออกฤทธิ์สอง", r"ใช้สารร่วมกัน")
SOLID_FORM = R(r"ผลึก", r"โพลิม็อ?ฟ", r"โพลีมอร์ฟ", r"polymorph", r"อสัณฐาน", r"amorphous", r"ไฮเดร[ตท]", r"hydrate",
               r"โซลเว[ตท]", r"solvate", r"ไมโครนิ", r"microni[sz]", r"crystal", r"เกลือที่เติมด้วยกรด", r"เบสอิสระ")
FORMULATION_HEAD = R(r"^\W*\d*\.?\s*(สูตรผสม|สูตรตำรับ|องค์ประกอบ|สารผสม|ยาเตรียม|สารเตรียม|ยาปรุง|รูปยา|ผง|แคปซูล|ไมโครแคปซูล|ยาเม็ด|"
                     r"(pharmaceutical )?composition|formulation|tablet|capsule|powder)")
FORMULATION_ANY = R(r"สูตรผสม", r"องค์ประกอบทางเภสัช", r"สารผสม", r"ยาเตรียม", r"composition", r"formulation", r"ปลดปล่อย",
                    r"release", r"รูปยา")
GENERIC_ACTIVE = R(r"สาร(ที่)?ออกฤทธิ์", r"ตัวยา(สำคัญ|ออกฤทธิ์)?", r"เพปไทด์", r"เปปไทด์", r"peptide", r"active (ingredient|agent|substance)",
                   r"physiologically active", r"ยาที่ออกฤทธิ์", r"สารแสดงฤทธิ์", r"ส่วนประกอบออกฤทธิ์")
SUBSTANCE_HEAD = R(r"^\W*\d*\.?\s*(สารประกอบ|อนุพันธ์|compound)", r"สูตรทั่วไป", r"สูตร\s*\(\s*i\s*\)")

NOTES = {
    C.DRUG_SUBSTANCE: "ตัวยาสำคัญได้รับความคุ้มครอง: ผลิตเพื่อจำหน่ายไม่ได้จนกว่าสิทธิบัตรสิ้นอายุ (ใช้ผลิตเพื่อขึ้นทะเบียนตำรับได้)",
    C.SOLID_FORM: "หลีกเลี่ยงการใช้วัตถุดิบในรูปแบบ/ลักษณะของสาร (เกลือ ผลึก อสัณฐาน ขนาดอนุภาค) ตามข้อถือสิทธิ",
    C.PROCESS: "หลีกเลี่ยงการใช้วัตถุดิบที่สังเคราะห์/ผลิตด้วยกระบวนการตามข้อถือสิทธิ",
    C.FORMULATION: "หลีกเลี่ยงสูตรตำรับ/องค์ประกอบตามข้อถือสิทธิ",
    C.DEVICE: "หลีกเลี่ยงการใช้อุปกรณ์/เครื่องมือให้ยาตามข้อถือสิทธิ",
    C.USE: "หลีกเลี่ยงข้อบ่งใช้/วิธีการใช้ยาตามข้อถือสิทธิ",
    C.COMBINATION: "หลีกเลี่ยงการผลิตเป็นยาผสมกับตัวยาอื่นตามข้อถือสิทธิ",
}


class _Text:
    """Search a regex in both the spaced and the space-squashed form (DB text often has spaces mid-word)."""

    def __init__(self, s: str):
        self.n = normalize_text(s)
        self.q = squash(s)

    def __getitem__(self, sl):
        return _Text(self.n[sl])

    def has(self, rx) -> bool:
        return bool(rx.search(self.n) or rx.search(self.q))


def classify_rules(p: Patent, mentions: dict[str, list[str]], names: list[str] = ()) -> dict:
    title = _Text(p.title or "")
    if p.main_claim:
        head = _Text(normalize_text(p.main_claim)[:260])
    else:
        head = _Text((p.title or "") + " " + normalize_text(p.summary or "")[:260])
    both = _Text(head.n + " " + title.n)
    if head.has(USE_HEAD) or (not p.main_claim and title.has(USE_HEAD)):
        cat = C.USE
    elif head.has(PROCESS_HEAD) or title.has(PROCESS_HEAD) and not p.main_claim:
        cat = C.PROCESS
    elif head[:140].has(DEVICE) or (title.has(DEVICE) and not head.has(FORMULATION_HEAD)):
        cat = C.DEVICE
    elif both.has(COMBINATION):
        cat = C.COMBINATION
    elif head.has(FORMULATION_HEAD):
        cat = C.FORMULATION
    elif both.has(SOLID_FORM):
        cat = C.SOLID_FORM
    elif head.has(SUBSTANCE_HEAD):
        cat = C.DRUG_SUBSTANCE
    elif both.has(FORMULATION_ANY):
        cat = C.FORMULATION
    elif _starts_with_name(head, names):
        cat = C.DRUG_SUBSTANCE  # claim 1 claims the named compound itself
    else:
        cat = C.FORMULATION

    strong = "title" in mentions or "claim1" in mentions
    confidence = 0.6 if strong else 0.3
    if cat in (C.USE, C.PROCESS, C.DEVICE) and strong:
        confidence = 0.7
    blocking = B.BLOCKING if cat == C.DRUG_SUBSTANCE else B.AVOIDABLE
    if not strong:
        if "claims" in mentions and cat != C.DRUG_SUBSTANCE and head.has(GENERIC_ACTIVE):
            # claim 1 covers a class of actives ("a physiologically active peptide …") and a dependent claim
            # narrows it to this substance: the substance is a claimed species.
            blocking, confidence = B.AVOIDABLE, 0.45
        else:
            # named only in passing (summary, or as a co-therapy partner of a different compound): not the
            # claimed invention for a single-ingredient generic. Kept as a candidate so an analyst can upgrade it.
            blocking, confidence = B.NOT_RELEVANT, 0.3
    note = NOTES[cat]
    if cat in (C.SOLID_FORM, C.FORMULATION):
        forms = sorted({m.group(0) for m in SOLID_FORM.finditer(head.n)})
        if forms:
            note += f" (เช่น {', '.join(forms[:4])})"
    flag = None
    if cat == C.USE and re.search(r"วิธีการรักษา|method (of|for) treat", head.n, re.I):
        flag = "ข้อถือสิทธิวิธีการรักษาโรค — ไม่สามารถรับความคุ้มครองตามมาตรา 9 พ.ร.บ.สิทธิบัตร"
    elif cat == C.USE:
        flag = "ข้อถือสิทธิการใช้ (use claim) — ตรวจสอบความสามารถรับความคุ้มครองตามมาตรา 9"
    return {"claim_category": cat, "blocking_level": blocking, "confidence": confidence,
            "design_around_note": note, "thai_patentability_flag": flag,
            "rationale": f"rules: category from {'main claim' if p.main_claim else 'title/summary'}; "
                         f"mentioned in {', '.join(mentions)}"}


def _starts_with_name(head: "_Text", names) -> bool:
    start = re.sub(r"^\W*\d*\.?\s*", "", head.q)[:40]
    return any(start.startswith(squash(n)) for n in names if n)


def classify_llm(sub: Substance, p: Patent, thai_terms: list[str]) -> dict | None:
    data = llm.classify_claim(
        {"inn": sub.inn_name, "salt_form": sub.salt_form, "thai_spellings": thai_terms[:6],
         "synonyms": (sub.synonyms_en or [])[:8]},
        {"title": p.title, "main_claim": (p.main_claim or "")[:4000], "summary": (p.summary or "")[:3000],
         "ipc": p.ipc, "applicant": p.applicant},
    )
    if not data:
        return None
    cat = data["claim_category"]
    if cat == "unrelated" or not data["is_about_substance"]:
        cat, blocking = None, B.NOT_RELEVANT
    else:
        blocking = data["blocking_level"]
    return {"claim_category": cat, "blocking_level": blocking, "confidence": float(data["confidence"]),
            "design_around_note": data["design_around_note_th"] or (NOTES.get(cat) if cat else None),
            "rationale": "llm: " + data["rationale"]}


# ------------------------------------------------------------------ driver
def run(use_llm: bool = True, substance: str | None = None):
    init_db()
    n_new = n_upd = 0
    with session() as s:
        patents = list(s.exec(select(Patent)))
        subs = list(s.exec(select(Substance)))
        for sub in subs:
            if substance and sub.inn_name != substance:
                continue
            thai_terms = variants_for(sub, include_probes=False)
            en_terms = _english_terms(sub)
            for p in patents:
                mentions = find_mentions(p, thai_terms, en_terms, sub.peptide_sequence)
                if not mentions:
                    continue
                strong = "title" in mentions or "claim1" in mentions
                if not strong and not (p.ipc or "").upper().startswith(PHARMA_IPC):
                    continue
                link = s.exec(select(PatentLink).where(PatentLink.patent_id == p.id,
                                                       PatentLink.substance_id == sub.id)).first()
                if link and link.review_state != ReviewState.CANDIDATE:
                    continue  # never overwrite an analyst's decision
                result = None
                if use_llm and llm.enabled():
                    result = classify_llm(sub, p, thai_terms)
                    method = MatchMethod.LLM
                if result is None:
                    result = classify_rules(p, mentions, thai_terms + en_terms)
                    method = MatchMethod.KEYWORD
                if link is None:
                    link = PatentLink(patent_id=p.id, substance_id=sub.id)
                    n_new += 1
                else:
                    n_upd += 1
                terms = sorted({t for ts in mentions.values() for t in ts})
                link.match_method = method
                link.matched_terms = {"terms": terms, "sections": sorted(mentions)}
                link.evidence_snippet = snippet(p, terms[0]) if terms else None
                for k, v in result.items():
                    setattr(link, k, v)
                s.add(link)
        s.commit()
    log.info("links: %d new, %d updated", n_new, n_upd)
    return n_new, n_upd


if __name__ == "__main__":
    import sys
    sys.stdout.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser()
    ap.add_argument("--no-llm", action="store_true")
    ap.add_argument("--substance")
    a = ap.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    run(not a.no_llm, a.substance)
