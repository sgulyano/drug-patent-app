import { useState } from "react";
import { Link } from "react-router-dom";
import { api, type PatentLinkBrief } from "../api";
import { CategoryChip, ExportLink, Loading, StatusBadge, useData } from "../components/ui";

const STATUSES = [
  ["", "ทุกสถานะ"], ["granted_active", "มีผลคุ้มครอง"], ["pending_examining,pending_unexamined", "รอ/อยู่ระหว่างตรวจสอบ"],
  ["expired", "สิ้นอายุ"], ["abandoned", "ละทิ้ง/ยกคำขอ"], ["revoked", "เพิกถอน"],
];
const PAGE = 50;

export default function Patents() {
  const [q, setQ] = useState("");
  const [qDebounced, setQD] = useState("");
  const [status, setStatus] = useState("");
  const [kind, setKind] = useState("");
  const [linked, setLinked] = useState(false);
  const [offset, setOffset] = useState(0);
  const params = { q: qDebounced, status, kind, linked, limit: PAGE, offset };
  const { data, error, loading } = useData(() => api.patents(params), [qDebounced, status, kind, linked, offset]);

  const onSearch = (v: string) => { setQ(v); setOffset(0); window.clearTimeout((onSearch as any).t); (onSearch as any).t = window.setTimeout(() => setQD(v), 300); };

  return (
    <>
      <div className="page-head">
        <div>
          <h1>สิทธิบัตร</h1>
          <div className="muted">คำขอรับสิทธิบัตร/สิทธิบัตรจากฐานข้อมูลกรมทรัพย์สินทางปัญญา (อุตสาหกรรมยา + ค้นหาตามชื่อยา)</div>
        </div>
        <ExportLink name="patents" params={{ q: qDebounced, status, kind, linked }} />
      </div>
      <div className="card stack">
        <div className="row">
          <input type="search" placeholder="ค้นหา ชื่อการประดิษฐ์ / เลขที่คำขอ / เลขที่สิทธิบัตร / ผู้ขอ / IPC" value={q}
            onChange={(e) => onSearch(e.target.value)} style={{ flex: "1 1 320px" }} />
          <select value={status} onChange={(e) => { setStatus(e.target.value); setOffset(0); }}>
            {STATUSES.map(([v, l]) => <option key={v} value={v}>{l}</option>)}
          </select>
          <select value={kind} onChange={(e) => { setKind(e.target.value); setOffset(0); }}>
            <option value="">ทุกประเภท</option><option value="invention">สิทธิบัตรการประดิษฐ์</option><option value="petty">อนุสิทธิบัตร</option>
          </select>
          <label className="ctl"><input type="checkbox" checked={linked} onChange={(e) => { setLinked(e.target.checked); setOffset(0); }} />
            เฉพาะที่เชื่อมโยงกับยาเป้าหมาย</label>
        </div>
        {!data ? <Loading error={error} /> : (
          <>
            <div className="table-wrap" style={{ opacity: loading ? 0.6 : 1 }}>
              <table>
                <thead><tr>
                  <th>เลขที่คำขอ / วันที่ยื่น</th><th>เลขที่ประกาศโฆษณา / วันที่</th><th>เลขที่สิทธิบัตร / วันที่ออก</th>
                  <th>วันหมดอายุ</th><th>สถานะ</th><th>ชื่อการประดิษฐ์</th><th>ข้อถือสิทธิหลัก</th><th>ยาที่เกี่ยวข้อง</th>
                </tr></thead>
                <tbody>
                  {data.rows.map((p) => (
                    <tr key={p.id}>
                      <td><Link to={`/patents/${p.application_no}`} className="mono">{p.application_no}</Link><div className="muted small">{p.filing_date_be}</div></td>
                      <td className="mono">{p.publication_no ?? "—"}<div className="muted small">{p.publication_date_be}</div></td>
                      <td className="mono">{p.patent_no ?? "—"}<div className="muted small">{p.grant_date_be}</div></td>
                      <td className="small">{p.expiry_date_be ?? "—"}</td>
                      <td className="small"><StatusBadge status={p.status_norm} raw={p.status_raw} /><div className="muted">{p.kind === "petty" ? "อนุสิทธิบัตร" : ""}</div></td>
                      <td className="clip"><div className="clip-3">{p.title}</div></td>
                      <td className="clip small"><div className="clip-3">{p.main_claim}</div></td>
                      <td>{(p.links as PatentLinkBrief[] | undefined)?.map((l) => (
                        <div key={l.substance_id} className="row" style={{ gap: 4 }}>
                          <span className="small">{l.substance}</span><CategoryChip cat={l.category} />
                        </div>))}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
              {!data.rows.length && <div className="empty">ไม่พบข้อมูล</div>}
            </div>
            <div className="pager">
              <span className="muted small">{data.total ? `${offset + 1}–${Math.min(offset + PAGE, data.total)} จาก ${data.total.toLocaleString()}` : ""}</span>
              <button disabled={offset === 0} onClick={() => setOffset(Math.max(0, offset - PAGE))}>‹ ก่อนหน้า</button>
              <button disabled={offset + PAGE >= data.total} onClick={() => setOffset(offset + PAGE)}>ถัดไป ›</button>
            </div>
          </>
        )}
      </div>
    </>
  );
}
