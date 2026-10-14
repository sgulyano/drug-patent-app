import { Fragment, type ReactNode } from "react";
import { Link, useParams } from "react-router-dom";
import { api } from "../api";
import { CategoryChip, Loading, StatusBadge, useData } from "../components/ui";

export default function PatentDetail() {
  const { appno = "" } = useParams();
  const { data: p, error } = useData(() => api.patent(appno), [appno]);
  if (!p) return <Loading error={error} />;
  const kv: [string, ReactNode][] = [
    ["เลขที่คำขอ / วันที่ยื่นคำขอ", <><span className="mono">{p.application_no}</span> · {p.filing_date_be ?? "—"}</>],
    ["เลขที่ประกาศโฆษณา / วันที่", <><span className="mono">{p.publication_no ?? "—"}</span> · {p.publication_date_be ?? "—"}</>],
    ["เลขที่สิทธิบัตร / วันที่ออก", <><span className="mono">{p.patent_no ?? "—"}</span> · {p.grant_date_be ?? "—"}</>],
    ["วันหมดอายุสิทธิบัตร", <>{p.expiry_date_be ?? "—"} <span className="muted small">
      ({p.expiry_source === "computed_invention_20y" ? "คำนวณ: วันยื่น + 20 ปี" : p.expiry_source === "computed_petty_10y" ? "คำนวณ: วันยื่น + 10 ปี" : p.expiry_source})</span></>],
    ["ประเภทคำขอ / สถานะ", <>{p.kind === "petty" ? "อนุสิทธิบัตร" : p.kind === "invention" ? "สิทธิบัตรการประดิษฐ์" : p.kind} · <StatusBadge status={p.status_norm} />
      <div className="muted small">{p.status_raw}</div></>],
    ["IPC", p.ipc ?? "—"],
    ["ผู้ขอ", p.applicant ?? "—"],
    ["ผู้ประดิษฐ์", p.inventors ?? "—"],
    ["พบจาก", (p.discovered_via ?? []).join(", ") || "—"],
  ];
  return (
    <>
      <div className="page-head">
        <div>
          <div className="muted"><Link to="/patents">สิทธิบัตร</Link> / {p.application_no}</div>
          <h1>{p.title}</h1>
        </div>
        {p.source_url && <a className="btn" href={p.source_url} target="_blank" rel="noreferrer">เปิดในฐานข้อมูลกรมทรัพย์สินทางปัญญา ↗</a>}
      </div>
      <div className="grid cols-2">
        <div className="card"><h2>ข้อมูลสิทธิบัตร</h2>
          <dl className="kv">{kv.map(([k, v]) => <Fragment key={k}><dt>{k}</dt><dd>{v}</dd></Fragment>)}</dl>
        </div>
        <div className="card"><h2>ยาที่เชื่อมโยง</h2>
          {!p.links.length && <div className="muted">ไม่มี</div>}
          <div className="stack">{p.links.map((l) => (
            <div key={l.id} className="stack" style={{ gap: 4 }}>
              <div className="row"><b>{l.substance}</b><CategoryChip cat={l.claim_category} />
                <span className="chip">{l.blocking_level === "blocking" ? "ขวางการผลิต" : l.blocking_level === "not_relevant" ? "ไม่เกี่ยวข้อง" : "หลีกเลี่ยงได้"}</span>
                <span className="chip">{l.review_state}</span><span className="muted small">{l.match_method} · {l.confidence?.toFixed(2)}</span></div>
              {l.design_around_note && <div className="small">{l.design_around_note}</div>}
              {l.thai_patentability_flag && <div className="small" style={{ color: "var(--st-critical)" }}>⚠ {l.thai_patentability_flag}</div>}
              {l.rationale && <div className="muted small">{l.rationale}</div>}
            </div>))}</div>
        </div>
      </div>
      <div className="grid cols-2" style={{ marginTop: 16 }}>
        <div className="card"><h2>ข้อถือสิทธิหลัก</h2><div className="prewrap small">{p.main_claim ?? "—"}</div>
          <details style={{ marginTop: 12 }}><summary className="muted">ข้อถือสิทธิทั้งหมด</summary>
            <div className="prewrap small">{p.all_claims}</div></details></div>
        <div className="card"><h2>บทสรุปการประดิษฐ์</h2><div className="prewrap small">{p.summary ?? "—"}</div></div>
      </div>
    </>
  );
}
