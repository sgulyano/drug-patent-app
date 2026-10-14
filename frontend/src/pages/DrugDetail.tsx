import { useState } from "react";
import { Link, useParams } from "react-router-dom";
import { api, type DrugDetail as DD, type Link as PLink, type Substance } from "../api";
import { Timeline } from "../components/Timeline";
import { CategoryChip, ExportLink, Loading, ReviewBadge, StatusBadge, VerdictBadge, useAsOf, useData } from "../components/ui";
import { CATEGORIES, CATEGORY_LABEL, VERDICT, beDate } from "../viz";

export default function DrugDetail() {
  const { id = "" } = useParams();
  const { asOf } = useAsOf();
  const { data, error, reload } = useData(() => api.drug(id, asOf), [id, asOf]);
  if (!data) return <Loading error={error} />;
  const a = data.assessment;
  return (
    <>
      <div className="page-head">
        <div>
          <div className="muted"><Link to="/drugs">ยาเป้าหมาย</Link> / {data.name}</div>
          <h1>{data.name}</h1>
          <div className="row"><VerdictBadge verdict={a.verdict} freeDate={a.free_date_be} override={a.analyst_override} />
            <span className="muted small">ประเมิน ณ {beDate(a.as_of)}</span>
            <ReviewBadge review={data.review} /></div>
        </div>
        <ExportLink name="drug_patent_matrix" params={{ product: data.name }} label="Export ตารางยา–สิทธิบัตร" />
      </div>

      {data.substances.map((s) => <SubstanceCard key={s.id} s={s} dosageForm={data.dosage_form} />)}

      <div className="card" style={{ marginTop: 16 }}>
        <h2>ภาพรวมสิทธิบัตร (Timeline)</h2>
        <Timeline rows={data.links.filter((l) => l.review_state !== "rejected")}
          asOf={asOf} freeDate={a.free_date} />
      </div>

      <div className="grid cols-2" style={{ marginTop: 16 }}>
        <VerdictPanel d={data} onSaved={reload} />
        <div className="card">
          <h2>ข้อมูล Orange Book (สหรัฐอเมริกา)</h2>
          <p className="small">{data.orange_book_note ?? "—"}</p>
          {data.orange_book.length > 0 && <div className="table-wrap"><table>
            <thead><tr><th>US patent</th><th>ประเภท</th><th>สิ้นอายุ</th><th>ผลิตภัณฑ์</th></tr></thead>
            <tbody>{data.orange_book.map((r) => <tr key={r.patent_no}><td className="mono">{r.patent_no}</td>
              <td className="small">{r.claim_type}{r.use_code ? ` (${r.use_code})` : ""}</td><td className="small">{r.expiry}</td>
              <td className="small">{r.product_desc}</td></tr>)}</tbody></table></div>}
          <p className="muted small">ข้อมูล Orange Book ปัจจุบัน (ไม่ใช่ข้อมูลย้อนหลัง ณ วันที่ประเมิน)</p>
        </div>
      </div>

      <div className="card" style={{ marginTop: 16 }}>
        <h2>สิทธิบัตร/คำขอที่เชื่อมโยง ({data.links.length})</h2>
        <LinksTable links={data.links} onChanged={reload} />
      </div>
    </>
  );
}

function SubstanceCard({ s, dosageForm }: { s: Substance; dosageForm: string | null }) {
  const observed = s.name_variants_th?.observed ?? [];
  return (
    <div className="card grid cols-2" style={{ gridTemplateColumns: "minmax(220px, 320px) 1fr" }}>
      <div>{s.structure_img_url && <img className="structure" src={s.structure_img_url} alt={`โครงสร้างทางเคมีของ ${s.inn_name}`} />}
        {s.pubchem_url && <a className="small" href={s.pubchem_url} target="_blank" rel="noreferrer">PubChem CID {s.pubchem_cid} ↗</a>}</div>
      <div className="stack">
        <dl className="kv">
          <dt>ชื่อสามัญ (INN)</dt><dd><b>{s.inn_name}</b>{s.salt_form ? ` (${s.salt_form})` : ""}</dd>
          <dt>รูปแบบยา</dt><dd>{dosageForm ?? "—"}</dd>
          <dt>IUPAC name</dt><dd className="small">{s.iupac_name ?? "—"}</dd>
          <dt>สูตรโมเลกุล</dt><dd className="mono">{s.molecular_formula ?? "—"} {s.molecular_weight ? `· ${s.molecular_weight.toFixed(2)} g/mol` : ""}</dd>
          <dt>ATC</dt><dd>{s.atc_code ?? "—"}</dd>
          <dt>ชื่อภาษาไทยที่พบ</dt><dd>{observed.length ? observed.slice(0, 8).map((o) =>
            <span key={o.spelling} className="chip" title={`${o.docs} เอกสาร · ${o.source}`} style={{ marginRight: 4 }}>{o.spelling}</span>)
            : <span className="muted">ยังไม่พบในฐานข้อมูลสิทธิบัตร</span>}</dd>
          <dt>ชื่ออื่น</dt><dd className="small">{s.synonyms_en.slice(0, 8).join(", ")}</dd>
          <dt>Indication</dt><dd className="small">{s.indication ?? "—"}</dd>
        </dl>
        {(s.mechanism || s.description) && <details><summary className="muted">กลไกการออกฤทธิ์ / คำอธิบาย (PubChem)</summary>
          <p className="small prewrap">{s.mechanism}</p><p className="small prewrap">{s.description}</p></details>}
        {s.smiles && <details><summary className="muted">SMILES / InChIKey</summary><p className="mono small" style={{ wordBreak: "break-all" }}>{s.smiles}<br />{s.inchikey}</p></details>}
      </div>
    </div>
  );
}

function VerdictPanel({ d, onSaved }: { d: DD; onSaved: () => void }) {
  const a = d.assessment;
  const { asOf } = useAsOf();
  const [verdict, setVerdict] = useState(a.analyst_override ?? "");
  const [note, setNote] = useState(a.analyst_note ?? "");
  const groups = CATEGORIES.map((c) => [c, a.avoid.filter((r) => r.category === c)] as const).filter(([, rows]) => rows.length);
  return (
    <div className="card stack">
      <h2>ความเป็นไปได้ในการผลิตเชิงพาณิชย์</h2>
      {a.blocking.length > 0 && <div><h3>ขวางการผลิต (ตัวยาสำคัญ)</h3>
        {a.blocking.map((r) => <div key={r.application_no} className="small">
          <Link to={`/patents/${r.application_no}`}>{r.patent_no ? `#${r.patent_no}` : r.application_no}</Link> — สิ้นอายุ {r.expiry_date_be}</div>)}</div>}
      {groups.length > 0 && <div><h3>ต้องหลีกเลี่ยง</h3>
        {groups.map(([c, rows]) => <div key={c} style={{ marginBottom: 8 }}><CategoryChip cat={c} />
          {rows.map((r) => <div key={r.application_no} className="small" style={{ marginLeft: 8 }}>
            <Link to={`/patents/${r.application_no}`} className="mono">{r.patent_no ? `#${r.patent_no}` : r.application_no}</Link>
            {" "}<StatusBadge status={r.status} /> · สิ้นอายุ {r.expiry_date_be} — {r.note}</div>)}</div>)}</div>}
      <details open><summary className="muted">สรุป (ข้อความอัตโนมัติ)</summary><div className="summary-box">{a.summary_th}</div></details>
      <div className="stack" style={{ gap: 6 }}>
        <h3>การแก้ไขผลโดยผู้วิเคราะห์</h3>
        <div className="row"><select value={verdict} onChange={(e) => setVerdict(e.target.value)}>
          <option value="">ใช้ผลอัตโนมัติ</option>
          {Object.entries(VERDICT).filter(([k]) => k !== "unknown" && k !== "pending_review").map(([k, v]) => <option key={k} value={k}>{v.label}</option>)}
        </select></div>
        <textarea placeholder="บันทึกของผู้วิเคราะห์" value={note} onChange={(e) => setNote(e.target.value)} />
        <div><button className="primary" onClick={() => api.override(d.id, { verdict, note, as_of: asOf, mode: a.mode }).then(onSaved)}>บันทึก</button></div>
      </div>
    </div>
  );
}

export function LinksTable({ links, onChanged }: { links: (PLink & { patent: any })[]; onChanged: () => void }) {
  const set = (l: PLink, body: Record<string, unknown>) => api.updateLink(l.id, body).then(onChanged);
  const sorted = [...links].sort((x, y) => (x.patent.filing_date ?? "").localeCompare(y.patent.filing_date ?? ""));
  return (
    <div className="table-wrap"><table>
      <thead><tr><th>คำขอ / สิทธิบัตร</th><th>ชื่อการประดิษฐ์</th><th>ประเภทข้อถือสิทธิ</th><th>สถานะ ณ วันที่เลือก</th>
        <th>สิ้นอายุ</th><th>ข้อควรหลีกเลี่ยง</th><th>ตรวจทาน</th></tr></thead>
      <tbody>{sorted.map((l) => (
        <tr key={l.id} style={{ opacity: l.review_state === "rejected" ? 0.45 : 1 }}>
          <td><Link className="mono" to={`/patents/${l.patent.application_no}`}>{l.patent.application_no}</Link>
            <div className="muted small mono">{l.patent.patent_no ? `#${l.patent.patent_no}` : ""}</div></td>
          <td className="clip small"><div className="clip-3">{l.patent.title}</div></td>
          <td><select value={l.claim_category ?? ""} onChange={(e) => set(l, { claim_category: e.target.value || null })}>
            {CATEGORIES.map((c) => <option key={c} value={c}>{CATEGORY_LABEL[c]}</option>)}</select>
            <div className="muted small">{l.blocking_level === "blocking" ? "ขวางการผลิต" : l.blocking_level === "not_relevant" ? "ไม่เกี่ยวข้อง" : "หลีกเลี่ยงได้"} · {l.match_method}</div></td>
          <td className="small"><StatusBadge status={l.patent.status_as_of} raw={l.patent.status_raw} /></td>
          <td className="small">{l.patent.expiry_date_be ?? "—"}</td>
          <td className="clip small">{l.design_around_note}</td>
          <td><div className="row" style={{ gap: 4 }}>
            <button className={l.review_state === "confirmed" ? "ok primary" : "ok"} title="ยืนยัน" onClick={() => set(l, { review_state: "confirmed" })}>✓</button>
            <button className={l.review_state === "rejected" ? "no primary" : "no"} title="ไม่เกี่ยวข้อง" onClick={() => set(l, { review_state: "rejected" })}>✕</button>
          </div><div className="muted small">{l.review_state}</div></td>
        </tr>))}</tbody>
    </table>{!links.length && <div className="empty">ไม่พบ</div>}</div>
  );
}

