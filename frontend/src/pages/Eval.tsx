import { api } from "../api";
import { Loading, useData } from "../components/ui";

const METRICS: [string, string][] = [
  ["discovery_recall", "พบสิทธิบัตรที่อยู่ในรายงาน (recall)"],
  ["discovery_precision", "ความแม่นยำการค้นพบ (precision)"],
  ["category_accuracy", "ประเภทข้อถือสิทธิถูกต้อง"],
  ["status_accuracy", "สถานะถูกต้อง"],
  ["expiry_accuracy", "วันสิ้นอายุถูกต้อง"],
  ["verdict_accuracy", "ผลการประเมินตรงกัน"],
  ["avoid_recall", "รายการที่ต้องหลีกเลี่ยง (recall)"],
  ["avoid_precision", "รายการที่ต้องหลีกเลี่ยง (precision)"],
];
const pct = (v: number | null) => (v == null ? "—" : `${Math.round(v * 100)}%`);

export default function Eval() {
  const { data, error } = useData(() => api.evalLatest(), []);
  if (!data) return <Loading error={error} />;
  if (!data.available) return <div className="card empty">ยังไม่มีผลการประเมิน — รัน <span className="mono">python -m eval.score</span></div>;
  return (
    <>
      <div className="page-head"><div><h1>ผลการประเมินแบบ blind</h1>
        <div className="muted">เทียบสิ่งที่ระบบค้นพบเอง (ไม่เห็นคำตอบ) กับรายงาน อย. ณ {data.as_of} · รันเมื่อ {data.run_at}</div></div></div>
      <div className="grid cards" style={{ gridTemplateColumns: "repeat(auto-fill, minmax(200px, 1fr))" }}>
        {METRICS.map(([k, l]) => <div key={k} className="card"><div className="muted small">{l}</div>
          <div style={{ fontSize: 28, fontWeight: 600 }}>{pct(data.totals[k])}</div></div>)}
      </div>
      {data.regressions?.length > 0 && <div className="card" style={{ marginTop: 16, borderColor: "var(--st-critical)" }}>
        ⚠ ถดถอยจากรอบก่อน: {data.regressions.map((r: any) => `${r.metric} ${pct(r.previous)} → ${pct(r.now)}`).join(", ")}</div>}
      <div className="card" style={{ marginTop: 16 }}><h2>รายผลิตภัณฑ์</h2><div className="table-wrap"><table>
        <thead><tr><th>ผลิตภัณฑ์</th><th>พบ / ในรายงาน</th><th>พบเพิ่ม</th><th>ไม่พบ</th><th>ผลการประเมิน (ระบบ / รายงาน)</th><th>หลีกเลี่ยง recall</th></tr></thead>
        <tbody>{data.products.map((p: any) => <tr key={p.product}>
          <td>{p.product}</td>
          <td>{p.discovery ? `${p.discovery.tp}/${p.discovery.expected}` : "—"}</td>
          <td>{p.discovery?.extra.length ?? "—"}</td>
          <td className="mono small">{p.discovery?.missed.join(", ")}</td>
          <td>{p.verdict ? `${p.verdict.ok ? "✅" : "❌"} ${p.verdict.ours} / ${p.verdict.pdf}` : "—"}</td>
          <td>{p.verdict ? pct(p.verdict.avoid.recall) : "—"}</td>
        </tr>)}</tbody></table></div></div>
      <div className="card" style={{ marginTop: 16 }}><h2>รายการที่ต้องให้ผู้เชี่ยวชาญตัดสิน ({data.adjudicate.length})</h2>
        <div className="table-wrap"><table><thead><tr><th>ผลิตภัณฑ์</th><th>คำขอ</th><th>ประเด็น</th><th>เหตุผล / รายละเอียด</th></tr></thead>
          <tbody>{data.adjudicate.map((a: any, i: number) => <tr key={i}><td className="small">{a.product}</td><td className="mono">{a.appno}</td>
            <td className="small">{a.fields.join(", ")}</td><td className="small">{a.reason}{a.title ? ` — ${a.title}` : ""}</td></tr>)}</tbody></table></div></div>
    </>
  );
}
