import { useState } from "react";
import { Link } from "react-router-dom";
import { api, type Link as PLink } from "../api";
import { CategoryChip, Highlight, Loading, StatusBadge, useData } from "../components/ui";
import { CATEGORIES, CATEGORY_LABEL } from "../viz";

export default function Review() {
  const [state, setState] = useState("candidate");
  const { data, error, reload } = useData(() => api.links({ review_state: state, limit: 200 }), [state]);
  const act = (l: PLink, body: Record<string, unknown>) => api.updateLink(l.id, body).then(reload);
  return (
    <>
      <div className="page-head">
        <div><h1>ตรวจทานการจับคู่</h1>
          <div className="muted">ลิงก์ที่ระบบพบอัตโนมัติ — ยืนยันหรือปฏิเสธ แก้ไขประเภทข้อถือสิทธิและข้อควรหลีกเลี่ยงได้</div></div>
        <div className="seg">{["candidate", "confirmed", "rejected"].map((s) =>
          <button key={s} className={state === s ? "on" : ""} onClick={() => setState(s)}>{s}</button>)}</div>
      </div>
      {!data ? <Loading error={error} /> : (
        <div className="stack">
          <div className="muted small">{data.total} รายการ</div>
          {data.rows.map((l) => (
            <div key={l.id} className="card stack" style={{ gap: 8 }}>
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
                <CategoryChip cat={l.claim_category} />
                <select value={l.claim_category ?? ""} onChange={(e) => act(l, { claim_category: e.target.value })}>
                  {CATEGORIES.map((c) => <option key={c} value={c}>{CATEGORY_LABEL[c]}</option>)}</select>
                <select value={l.blocking_level ?? ""} onChange={(e) => act(l, { blocking_level: e.target.value })}>
                  <option value="blocking">ขวางการผลิต</option><option value="avoidable">หลีกเลี่ยงได้</option><option value="not_relevant">ไม่เกี่ยวข้อง</option></select>
                <span className="spacer" />
                <button className="ok" onClick={() => act(l, { review_state: "confirmed" })}>✓ ยืนยัน</button>
                <button className="no" onClick={() => act(l, { review_state: "rejected" })}>✕ ไม่เกี่ยวข้อง</button>
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
