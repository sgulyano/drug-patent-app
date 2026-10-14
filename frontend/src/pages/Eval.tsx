import { useState, type FocusEvent, type MouseEvent } from "react";
import { Link } from "react-router-dom";
import { api } from "../api";
import { Loading, useData } from "../components/ui";
import { CATEGORY_LABEL, STATUS_LABEL, VERDICT, beDate } from "../viz";

// the report uses coarser claim types and statuses than ours
const PDF_TYPE: Record<string, string> = {
  drug_substance: "ตัวยาสำคัญ (Drug substance)", product: "ผลิตภัณฑ์ (Product)", process: "กระบวนการผลิต (Process)", use: "การใช้ (Use)",
};
const PDF_STATUS: Record<string, string> = { ...STATUS_LABEL, pending: "รอพิจารณา" };
const FIELD: Record<string, { label: string; ours: (v: string) => string; pdf: (v: string) => string }> = {
  category: { label: "ประเภทข้อถือสิทธิ", ours: (v) => CATEGORY_LABEL[v] ?? v ?? "—", pdf: (v) => PDF_TYPE[v] ?? v },
  status: { label: "สถานะ", ours: (v) => STATUS_LABEL[v] ?? v, pdf: (v) => PDF_STATUS[v] ?? v },
  expiry: { label: "วันสิ้นอายุ", ours: beDate, pdf: beDate },
};
const DRIFT_NOTE = "สถานะปัจจุบันอาจเปลี่ยนหลังวันที่ของรายงาน อย. แต่ข้อมูลสาธารณะไม่ระบุวันที่เปลี่ยน";

interface ErrorRow { product: string; appno: string; field: string; ours: string; pdf: string; note?: string }

// every disagreement with the report is a system error; missed patents are already listed per product above
function errorRows(data: any): ErrorRow[] {
  const titles = new Map<string, string>(data.adjudicate.filter((a: any) => a.title).map((a: any) => [`${a.product}|${a.appno}`, a.title]));
  const rows: ErrorRow[] = [];
  for (const p of data.products) {
    for (const d of p.matched ?? []) for (const k of Object.keys(FIELD)) {
      const f = d[k];
      if (!f || f.ok) continue;
      rows.push({ product: p.product, appno: d.appno, field: FIELD[k].label, ours: f.ours ? FIELD[k].ours(f.ours) : "—",
        pdf: FIELD[k].pdf(f.pdf), note: f.drift ? DRIFT_NOTE : d.suspect ? `หมายเหตุในคีย์คำตอบ: ${d.suspect}` : undefined });
    }
    for (const a of p.discovery?.extra ?? []) rows.push({ product: p.product, appno: a, field: "พบเพิ่ม",
      ours: "เชื่อมโยงกับยานี้", pdf: "ไม่อยู่ในรายงาน อย.", note: titles.get(`${p.product}|${a}`) });
  }
  return rows;
}

const METRICS: [string, string, string][] = [
  ["discovery_recall", "พบสิทธิบัตรที่อยู่ในรายงาน อย. (recall)",
    "จากสิทธิบัตรทั้งหมดที่รายงาน อย. ระบุ ระบบค้นพบเองได้กี่ % — ไม่นับสิทธิบัตรที่ยังไม่ประกาศโฆษณา ณ วันที่ของรายงาน อย. และนับเฉพาะการเชื่อมโยงอัตโนมัติ (ไม่รวมการตัดสินของผู้เชี่ยวชาญ)"],
  ["discovery_precision", "ความแม่นยำการค้นพบ (precision)",
    "จากสิทธิบัตรที่ระบบเชื่อมโยงกับยา (ยกเว้นที่ระบบจัดว่าไม่เกี่ยวข้อง) มีกี่ % ที่อยู่ในรายงาน อย. — รายการที่พบเพิ่มนับเป็นข้อผิดพลาด"],
  ["category_accuracy", "ประเภทข้อถือสิทธิถูกต้อง",
    "เฉพาะสิทธิบัตรที่พบตรงกับรายงาน อย.: ประเภทข้อถือสิทธิของระบบตรงกับประเภทในรายงาน อย. (ตัวยาสำคัญ / ผลิตภัณฑ์ / กระบวนการผลิต / การใช้) หรือไม่ ประเภทของระบบละเอียดกว่า เช่น รูปผลึกนับว่าตรงได้ทั้งตัวยาสำคัญและผลิตภัณฑ์"],
  ["status_accuracy", "สถานะถูกต้อง",
    "เฉพาะสิทธิบัตรที่พบตรงกับรายงาน อย.: สถานะ ณ วันที่ของรายงาน อย. (มีผลคุ้มครอง / รอพิจารณา / ละทิ้ง / สิ้นอายุ / เพิกถอน) ตรงกับรายงาน อย. หรือไม่"],
  ["expiry_accuracy", "วันสิ้นอายุถูกต้อง",
    "เฉพาะสิทธิบัตรที่พบตรงกันและรายงาน อย. ระบุวันสิ้นอายุ: วันสิ้นอายุของระบบคลาดเคลื่อนไม่เกิน 1 วัน"],
  ["verdict_accuracy", "ผลการประเมินตรงกัน",
    "ข้อสรุปรายผลิตภัณฑ์ของระบบ (ผลิตได้ / ผลิตได้โดยหลีกเลี่ยง / ยังผลิตเพื่อจำหน่ายไม่ได้) ตรงกับข้อสรุปของรายงาน อย. กี่ % ของผลิตภัณฑ์"],
  ["avoid_recall", "พบสิทธิบัตรที่ต้องหลีกเลี่ยง (recall)",
    "จากสิทธิบัตรที่รายงาน อย. สรุปว่าผู้ผลิตต้องหลีกเลี่ยง ระบบระบุว่า \"หลีกเลี่ยงได้\" หรือ \"ขวางการผลิต\" ได้กี่ % — รวมทุกผลิตภัณฑ์ รายการที่รายงาน อย. ระบุไม่ชัดเจน (optional) ไม่นับ"],
  ["avoid_precision", "ความแม่นยำสิทธิบัตรที่ต้องหลีกเลี่ยง (precision)",
    "จากสิทธิบัตรที่ระบบระบุว่าหลีกเลี่ยงได้หรือขวางการผลิต มีกี่ % ที่รายงาน อย. ก็สรุปว่าต้องหลีกเลี่ยงเช่นกัน"],
];
const DIP = "https://patentpub.ipthailand.go.th/_AppPublic/ApplicationDetail.aspx?appno=";
const pct = (v: number | null) => (v == null ? "—" : `${Math.round(v * 100)}%`);
const TIP_W = 280;

// the tip is position:fixed and clamped to the viewport, so it never spills past a table edge or the window
function Help({ text }: { text: string }) {
  const [pos, setPos] = useState<{ left: number; top: number } | null>(null);
  const show = (e: MouseEvent<HTMLElement> | FocusEvent<HTMLElement>) => {
    const r = e.currentTarget.getBoundingClientRect();
    setPos({ left: Math.max(8, Math.min(r.left + r.width / 2 - TIP_W / 2, window.innerWidth - TIP_W - 8)), top: r.bottom + 6 });
  };
  const hide = () => setPos(null);
  return <span className="help" tabIndex={0} aria-label={text} onMouseEnter={show} onFocus={show} onMouseLeave={hide} onBlur={hide}>
    ?{pos && <span className="tip" style={{ ...pos, width: TIP_W }}>{text}</span>}</span>;
}

function Verdict({ k }: { k: string }) {
  const v = VERDICT[k] ?? VERDICT.unknown;
  return <span><span aria-hidden style={{ color: v.color }}>{v.icon}</span> {v.label}</span>;
}

export default function Eval() {
  const { data, error } = useData(() => api.evalLatest(), []);
  if (!data) return <Loading error={error} />;
  if (!data.available) return <div className="card empty">ยังไม่มีผลการประเมิน — รัน <span className="mono">python -m eval.score</span></div>;
  const drugIds: Record<string, number> = data.drug_ids ?? {};
  const known = new Set<string>(data.known_patents ?? []);
  const errors = errorRows(data);
  const product = (name: string) => drugIds[name] ? <Link to={`/drugs/${drugIds[name]}`}>{name}</Link> : name;
  // patents the pipeline never fetched have no local page, so link those to the DIP record instead
  const patent = (a: string) => <span style={{ whiteSpace: "nowrap" }}>{known.has(a) ? <Link to={`/patents/${a}`}>{a}</Link>
    : <a href={`${DIP}${a}`} target="_blank" rel="noreferrer" title="ไม่มีในฐานข้อมูล — เปิดที่กรมทรัพย์สินทางปัญญา">{a} ↗</a>}</span>;
  const patents = (list?: string[]) => list?.length ? list.map((a, i) => <span key={a}>{i > 0 && ", "}{patent(a)}</span>) : "—";
  return (
    <>
      <div className="page-head"><div><h1>ผลการประเมินแบบ blind</h1>
        <div className="muted">เทียบสิ่งที่ระบบตรวจพบ กับ รายงาน อย. ณ {data.as_of} · รันเมื่อ {data.run_at}</div></div></div>
      <div className="grid cards" style={{ gridTemplateColumns: "repeat(auto-fill, minmax(250px, 1fr))" }}>
        {METRICS.map(([k, l, h]) => <div key={k} className="card"><div className="muted small">{l}<Help text={h} /></div>
          <div style={{ fontSize: 28, fontWeight: 600 }}>{pct(data.totals[k])}</div></div>)}
      </div>
      {data.regressions?.length > 0 && <div className="card" style={{ marginTop: 16, borderColor: "var(--st-critical)" }}>
        ⚠ ถดถอยจากรอบก่อน: {data.regressions.map((r: any) => `${r.metric} ${pct(r.previous)} → ${pct(r.now)}`).join(", ")}</div>}
      <div className="card" style={{ marginTop: 16 }}><h2>รายผลิตภัณฑ์</h2><div className="table-wrap"><table>
        <thead><tr><th>ผลิตภัณฑ์</th>
          <th>พบ / ในรายงาน อย. (+พบเพิ่ม)<Help text="จำนวนสิทธิบัตรในรายงาน อย. ที่ระบบค้นพบ / จำนวนสิทธิบัตรทั้งหมดในรายงาน อย. — ในวงเล็บคือจำนวนสิทธิบัตรที่ระบบพบเพิ่มนอกรายงาน อย." /></th>
          <th>สิทธิบัตรที่พบเพิ่ม<Help text="สิทธิบัตรที่ระบบเชื่อมโยงกับยานี้ แต่ไม่อยู่ในรายงาน อย. — นับเป็นข้อผิดพลาด ดูรายละเอียดในตารางด้านล่าง" /></th>
          <th>สิทธิบัตรที่ไม่พบ<Help text="สิทธิบัตรที่รายงาน อย. ระบุ แต่ระบบไม่ได้เชื่อมโยงกับยานี้" /></th>
          <th>ผลการประเมิน<Help text="ข้อสรุปของระบบเทียบกับรายงาน อย. — ถ้าไม่ตรงกันจะแสดงทั้งสองค่า" /></th>
          <th>พบสิทธิบัตรที่ต้องหลีกเลี่ยง<Help text="จำนวนสิทธิบัตรที่รายงาน อย. สรุปว่าต้องหลีกเลี่ยง ซึ่งระบบระบุว่า &quot;หลีกเลี่ยงได้&quot; หรือ &quot;ขวางการผลิต&quot; / จำนวนที่รายงาน อย. สรุปว่าต้องหลีกเลี่ยง (ไม่นับรายการ optional)" /></th></tr></thead>
        <tbody>{data.products.map((p: any) => <tr key={p.product}>
          <td>{product(p.product)}</td>
          <td style={{ whiteSpace: "nowrap" }}>{p.discovery ? <>{p.discovery.tp}/{p.discovery.expected}
            {p.discovery.extra.length > 0 && <span className="muted"> (+{p.discovery.extra.length})</span>}</> : "—"}</td>
          <td className="mono small">{p.discovery ? patents(p.discovery.extra) : "—"}</td>
          <td className="mono small">{p.discovery ? patents(p.discovery.missed) : "—"}</td>
          <td>{!p.verdict ? "—" : p.verdict.ok ? <>✅ <Verdict k={p.verdict.ours} /></>
            : <>❌ ระบบ: <Verdict k={p.verdict.ours} /><br /><span style={{ marginLeft: 22 }}>รายงาน อย.: <Verdict k={p.verdict.pdf} /></span></>}</td>
          <td>{!p.verdict ? "—" : p.verdict.avoid.expected === 0 ? <span className="muted">ไม่มีรายการ</span>
            : `${pct(p.verdict.avoid.recall)} (${p.verdict.avoid.tp}/${p.verdict.avoid.expected})`}</td>
        </tr>)}</tbody></table></div></div>
      <div className="card" style={{ marginTop: 16 }}><h2>ข้อผิดพลาดเทียบกับรายงาน อย. ({errors.length})
        <Help text="ถือรายงาน อย. เป็นคำตอบที่ถูกต้อง ทุกจุดที่ระบบไม่ตรงกับรายงาน อย. นับเป็นข้อผิดพลาดในตัวชี้วัดด้านบน — สิทธิบัตรที่ระบบไม่พบแสดงในตารางรายผลิตภัณฑ์แล้ว จึงไม่ซ้ำที่นี่" /></h2>
        <div className="table-wrap"><table><thead><tr><th>ผลิตภัณฑ์</th><th>คำขอ</th><th>ข้อผิดพลาด</th><th>ระบบ</th><th>รายงาน อย.</th><th>หมายเหตุ</th></tr></thead>
          <tbody>{errors.map((e, i) => <tr key={i}><td className="small">{product(e.product)}</td>
            <td className="mono">{patent(e.appno)}</td><td className="small">{e.field}</td>
            <td className="small">{e.ours}</td><td className="small">{e.pdf}</td>
            <td className="small muted">{e.note}</td></tr>)}</tbody></table></div></div>
    </>
  );
}
