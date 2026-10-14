import { useMemo, useState } from "react";
import { Link } from "react-router-dom";
import type { Link as PLink, Patent } from "../api";
import { CATEGORIES, CATEGORY_LABEL, CATEGORY_SHORT, STATUS_LABEL, beDate, categoryColor, statusFill } from "../viz";

type Row = PLink & { patent: Patent };

const ROW_H = 26;
const BAR_H = 14;
const LABEL_W = 230;
const PAD_R = 24;
const AXIS_H = 26;

/** Patent landscape timeline for one drug: one bar per linked patent, filing → expiry.
 *  Colour = claim category. Fill = legal status as of the chosen date (solid / hatched / faded / outline). */
export function Timeline({ rows, asOf, freeDate, width = 1100 }: { rows: Row[]; asOf: string; freeDate?: string | null; width?: number }) {
  const [hover, setHover] = useState<{ x: number; y: number; r: Row } | null>(null);
  const data = useMemo(() => rows
    .filter((r) => r.blocking_level !== "not_relevant" && r.patent.filing_date)
    .sort((a, b) => (CATEGORIES.indexOf(a.claim_category as any) - CATEGORIES.indexOf(b.claim_category as any))
      || (a.patent.filing_date ?? "").localeCompare(b.patent.filing_date ?? "")), [rows]);

  if (!data.length) return <div className="empty">ไม่พบสิทธิบัตรที่เชื่อมโยงกับยานี้</div>;

  const years = data.flatMap((r) => [r.patent.filing_date, r.patent.expiry_date].filter(Boolean).map((d) => +d!.slice(0, 4)));
  const nowY = +asOf.slice(0, 4);
  const y0 = Math.min(...years, nowY) - 1;
  const y1 = Math.max(...years, nowY) + 1;
  const plotW = width - LABEL_W - PAD_R;
  const x = (iso: string) => {
    const d = new Date(iso);
    const t = d.getFullYear() + (d.getMonth() * 30.4 + d.getDate()) / 365.25;
    return LABEL_W + ((t - y0) / (y1 - y0)) * plotW;
  };
  const height = AXIS_H + data.length * ROW_H + 8;
  const ticks: number[] = [];
  const step = y1 - y0 > 30 ? 5 : 2;
  for (let y = Math.ceil(y0 / step) * step; y <= y1; y += step) ticks.push(y);

  return (
    <div style={{ position: "relative" }}>
      <TimelineLegend />
      <div className="table-wrap">
        <svg width={width} height={height} role="img" aria-label="ไทม์ไลน์สิทธิบัตร" style={{ display: "block" }}>
          <defs>
            {CATEGORIES.map((c, i) => (
              <pattern key={c} id={`hatch-${c}`} patternUnits="userSpaceOnUse" width="6" height="6" patternTransform="rotate(45)">
                <rect width="6" height="6" fill="var(--surface-1)" />
                <line x1="0" y1="0" x2="0" y2="6" stroke={`var(--cat-${i + 1})`} strokeWidth="3" />
              </pattern>
            ))}
          </defs>
          {/* axis + recessive grid */}
          {ticks.map((t) => (
            <g key={t}>
              <line x1={x(`${t}-01-01`)} x2={x(`${t}-01-01`)} y1={AXIS_H - 4} y2={height} stroke="var(--grid)" />
              <text x={x(`${t}-01-01`)} y={14} fontSize="11" textAnchor="middle" fill="var(--text-muted)">{t + 543}</text>
            </g>
          ))}
          {/* rows */}
          {data.map((r, i) => {
            const y = AXIS_H + i * ROW_H;
            const p = r.patent;
            const xs = x(p.filing_date!);
            const xe = p.expiry_date ? x(p.expiry_date) : xs + 4;
            const fill = statusFill(p.status_as_of);
            const col = categoryColor(r.claim_category);
            const w = Math.max(xe - xs, 4);
            const common = { x: xs, y: y + (ROW_H - BAR_H) / 2, width: w, height: BAR_H, rx: 4 };
            return (
              <g key={r.id} onMouseMove={(e) => setHover({ x: e.clientX, y: e.clientY, r })} onMouseLeave={() => setHover(null)}
                style={{ cursor: "pointer" }}>
                <rect x={0} y={y} width={width} height={ROW_H} fill="transparent" />
                <text x={8} y={y + ROW_H / 2 + 4} fontSize="12" fill="var(--text-primary)">
                  <tspan fontWeight={500}>{p.patent_no ? `#${p.patent_no}` : p.application_no}</tspan>
                  <tspan fill="var(--text-muted)"> · {CATEGORY_SHORT[r.claim_category ?? ""] ?? "?"}</tspan>
                </text>
                {fill === "solid" && <rect {...common} fill={col} />}
                {fill === "hatch" && <rect {...common} fill={`url(#hatch-${r.claim_category})`} stroke={col} strokeWidth={1.5} />}
                {fill === "faded" && <rect {...common} fill={col} opacity={0.28} />}
                {fill === "outline" && <rect {...common} fill="none" stroke={col} strokeWidth={1.5} strokeDasharray="4 3" />}
              </g>
            );
          })}
          {/* as-of marker */}
          <line x1={x(asOf)} x2={x(asOf)} y1={AXIS_H - 6} y2={height} stroke="var(--text-primary)" strokeWidth={1.5} />
          <text x={x(asOf) + 4} y={AXIS_H - 8} fontSize="11" fill="var(--text-primary)">ณ {beDate(asOf)}</text>
          {freeDate && <>
            <line x1={x(freeDate)} x2={x(freeDate)} y1={AXIS_H - 6} y2={height} stroke="var(--st-critical)" strokeWidth={2} strokeDasharray="5 3" />
            <text x={x(freeDate) + 4} y={height - 4} fontSize="11" fill="var(--text-primary)">ผลิตได้หลัง {beDate(freeDate)}</text>
          </>}
        </svg>
      </div>
      {hover && <div className="tooltip" style={{ left: Math.min(hover.x + 14, window.innerWidth - 380), top: hover.y + 14 }}>
        <div style={{ fontWeight: 600 }}>{hover.r.patent.patent_no ? `สิทธิบัตรเลขที่ ${hover.r.patent.patent_no}` : `คำขอเลขที่ ${hover.r.patent.application_no}`}</div>
        <div className="secondary">{hover.r.patent.title}</div>
        <div style={{ marginTop: 6 }} className="small">
          <div>ประเภท: {CATEGORY_LABEL[hover.r.claim_category ?? ""] ?? "—"}</div>
          <div>สถานะ ณ วันที่เลือก: {STATUS_LABEL[hover.r.patent.status_as_of ?? ""] ?? "—"}</div>
          <div>ยื่น {beDate(hover.r.patent.filing_date)} → สิ้นอายุ {beDate(hover.r.patent.expiry_date)}</div>
          {hover.r.design_around_note && <div className="muted">{hover.r.design_around_note}</div>}
        </div>
      </div>}
      <p className="small muted" style={{ margin: "8px 0 0" }}>
        ปีแสดงเป็น พ.ศ. · วันสิ้นอายุของคำขอที่ยังไม่ได้รับสิทธิบัตรคำนวณจากวันยื่น + 20 ปี ·
        ดูรายการเดียวกันในรูปแบบตารางด้านล่าง หรือ <Link to="/patents">หน้าสิทธิบัตร</Link>
      </p>
    </div>
  );
}

export function TimelineLegend() {
  const sw = (fill: string) => {
    const p = { x: 1, y: 3, width: 22, height: 10, rx: 3 };
    return <svg width="24" height="16" aria-hidden>
      <defs><pattern id="lg-h" patternUnits="userSpaceOnUse" width="5" height="5" patternTransform="rotate(45)">
        <line x1="0" y1="0" x2="0" y2="5" stroke="var(--text-secondary)" strokeWidth="2.5" /></pattern></defs>
      {fill === "solid" && <rect {...p} fill="var(--text-secondary)" />}
      {fill === "hatch" && <rect {...p} fill="url(#lg-h)" stroke="var(--text-secondary)" />}
      {fill === "faded" && <rect {...p} fill="var(--text-secondary)" opacity={0.28} />}
      {fill === "outline" && <rect {...p} fill="none" stroke="var(--text-secondary)" strokeDasharray="4 3" />}
    </svg>;
  };
  return <div className="stack" style={{ gap: 4, marginBottom: 8 }}>
    <div className="viz-legend">{CATEGORIES.map((c) => (
      <span key={c} className="item"><span className="swatch" style={{ background: categoryColor(c) }} />{CATEGORY_LABEL[c]}</span>
    ))}</div>
    <div className="viz-legend">
      <span className="item">{sw("solid")}มีผลคุ้มครอง</span>
      <span className="item">{sw("hatch")}รอ/อยู่ระหว่างตรวจสอบ</span>
      <span className="item">{sw("faded")}สิ้นอายุ</span>
      <span className="item">{sw("outline")}ละทิ้ง / เพิกถอน / น่าจะถูกละทิ้ง</span>
    </div>
  </div>;
}
