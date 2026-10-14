import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { api, type Link as PLink } from "../api";
import { BLOCKING_OPTIONS, CATEGORY_OPTIONS, Highlight, Loading, Picker, StatusBadge, useData } from "../components/ui";

const STATES = ["candidate", "confirmed", "rejected"];
// the chosen decision is filled in, the other one greyed out (still clickable to switch)
const decisionClass = (cur: string, mine: string) => (cur === "candidate" ? "" : cur === mine ? "on" : "dim");

export default function Review() {
  const [state, setState] = useState("candidate");
  const { data, error } = useData(() => api.links({ review_state: state, limit: 200 }), [state]);
  const counts = useData(() => Promise.all(STATES.map((s) => api.links({ review_state: s, limit: 0 }).then((r) => r.total))), []);
  // edits are merged in place rather than refetched, so a decided card stays visible (with undo) until the tab changes
  const [edits, setEdits] = useState<Record<number, Partial<PLink>>>({});
  useEffect(() => setEdits({}), [state]);
  const act = (l: PLink, body: Record<string, unknown>) => api.updateLink(l.id, body).then((r) => {
    setEdits((e) => ({ ...e, [l.id]: r }));
    if ("review_state" in body) counts.reload();
  });
  return (
    <>
      <div className="page-head">
        <div><h1>ตรวจทานการจับคู่</h1>
          <div className="muted">ลิงก์ที่ระบบพบอัตโนมัติ — ยืนยันหรือปฏิเสธ แก้ไขประเภทข้อถือสิทธิและข้อควรหลีกเลี่ยงได้</div></div>
        <div className="seg">{STATES.map((s, i) =>
          <button key={s} className={state === s ? "on" : ""} onClick={() => setState(s)}>
            {s}{counts.data && <span className="count">{counts.data[i]}</span>}</button>)}</div>
      </div>
      {!data ? <Loading error={error} /> : (
        <div className="stack">
          {data.rows.map((row) => ({ ...row, ...edits[row.id] })).map((l) => (
            <div key={l.id} className={`card stack decided-${l.review_state}`} style={{ gap: 8 }}>
              <div className="row">
                <b>{l.substance}</b><span className="muted">↔</span>
                <Link className="mono" to={`/patents/${l.patent!.application_no}`}>{l.patent!.application_no}</Link>
                {l.patent!.patent_no && <span className="mono muted">#{l.patent!.patent_no}</span>}
                <StatusBadge status={l.patent!.status_as_of} raw={l.patent!.status_raw} />
                <span className="muted small">สิ้นอายุ {l.patent!.expiry_date_be}</span>
                <span className="spacer" />
                <span className="muted small">{l.match_method} · ความมั่นใจ {l.confidence?.toFixed(2)} · พบใน {(l.matched_terms.sections ?? []).join(", ")}</span>
              </div>
              <div>{l.patent!.title}</div>
              {l.evidence_snippet && <div className="evidence"><Highlight text={l.evidence_snippet} terms={l.matched_terms.terms ?? []} /></div>}
              <details><summary className="muted small">ข้อถือสิทธิหลัก</summary><div className="prewrap small">{l.patent!.main_claim}</div></details>
              <div className="row">
                <Picker label="ประเภทข้อถือสิทธิ" value={l.claim_category ?? ""} options={CATEGORY_OPTIONS}
                  onChange={(v) => act(l, { claim_category: v })} />
                <Picker label="ระดับการขวางการผลิต" value={l.blocking_level ?? ""} options={BLOCKING_OPTIONS}
                  onChange={(v) => act(l, { blocking_level: v })} />
                <span className="spacer" />
                <button className={`ok ${decisionClass(l.review_state, "confirmed")}`} aria-pressed={l.review_state === "confirmed"}
                  onClick={() => act(l, { review_state: "confirmed" })}>✓ {l.review_state === "confirmed" ? "ยืนยันแล้ว" : "ยืนยัน"}</button>
                <button className={`no ${decisionClass(l.review_state, "rejected")}`} aria-pressed={l.review_state === "rejected"}
                  onClick={() => act(l, { review_state: "rejected" })}>✕ ไม่เกี่ยวข้อง</button>
                {l.review_state !== "candidate" &&
                  <button title="ยกเลิกการตัดสินใจ และย้ายกลับไปรอตรวจทาน" onClick={() => act(l, { review_state: "candidate" })}>↩ ยกเลิก</button>}
              </div>
              <div className="small secondary">{l.design_around_note}</div>
              {l.thai_patentability_flag && <div className="small" style={{ color: "var(--st-critical)" }}>⚠ {l.thai_patentability_flag}</div>}
            </div>
          ))}
          {!data.rows.length && <div className="empty">ไม่มีรายการ</div>}
        </div>
      )}
    </>
  );
}
