import cytoscape from "cytoscape";
import { useEffect, useRef, useState } from "react";
import type { GraphData } from "../api";
import { CATEGORIES, CATEGORY_LABEL, STATUS_LABEL, beDate } from "../viz";

function cssVar(name: string) {
  return getComputedStyle(document.documentElement).getPropertyValue(name).trim() || "#888";
}

type View = "all" | "drug" | "patent";
const VIEWS: [View, string][] = [["all", "ทั้งหมด"], ["drug", "ยา – ตัวยา"], ["patent", "ตัวยา – สิทธิบัตร"]];

/** Sample of an edge style for the legend. */
function LineSample({ color = "var(--text-secondary)", width = 2, dashed = false }) {
  return <svg width="22" height="8" aria-hidden style={{ flex: "none" }}>
    <line x1="1" y1="4" x2="21" y2="4" stroke={color} strokeWidth={width} strokeDasharray={dashed ? "4 3" : undefined} />
  </svg>;
}

/** Product → substance → patent network. Edge colour = claim category; patent node fill = in force or not. */
export function Graph({ data }: { data: GraphData }) {
  const ref = useRef<HTMLDivElement>(null);
  const cyRef = useRef<cytoscape.Core | null>(null);
  const [view, setView] = useState<View>("all");
  const [hover, setHover] = useState<{ x: number; y: number; d: any } | null>(null);
  const [sel, setSel] = useState<{ x: number; y: number; d: any; links: any[]; substances: string[] } | null>(null);

  const showDrugs = view !== "patent";
  const showPatents = view !== "drug";
  // Legend items come from the whole data set so the legend keeps its size across views;
  // items for hidden node types are made invisible rather than removed.
  const links = data.edges.filter((e) => e.type === "link");
  const cats = CATEGORIES.filter((c) => links.some((e) => e.category === c));
  const patents = data.nodes.filter((n) => n.type === "patent");
  const active = new Set(["granted_active", "pending_examining", "pending_unexamined"]);
  const hasActive = patents.some((n) => n.status && active.has(n.status));
  const hasInactive = patents.some((n) => !n.status || !active.has(n.status));
  const hasBlocking = links.some((e) => e.blocking_level === "blocking");
  const hasCandidate = links.some((e) => e.review_state === "candidate");
  const shown = (on: boolean) => on ? {} : { style: { visibility: "hidden" as const }, "aria-hidden": true };

  // Built once per data set, with every node, so positions stay put when the view changes.
  useEffect(() => {
    if (!ref.current) return;
    const catColor = (c?: string) => {
      const i = CATEGORIES.indexOf((c ?? "") as any);
      return i >= 0 ? cssVar(`--cat-${i + 1}`) : cssVar("--inactive");
    };
    const cy = cytoscape({
      container: ref.current,
      elements: [
        ...data.nodes.map((n) => ({ data: { ...n, active: n.status ? active.has(n.status) : undefined } })),
        ...data.edges.map((e) => ({ data: { id: `${e.source}-${e.target}`, ...e, color: e.type === "link" ? catColor(e.category) : cssVar("--border") } })),
      ],
      style: [
        { selector: "node", style: { label: "data(label)", "font-size": 10, color: cssVar("--text-primary"),
          "text-valign": "bottom", "text-margin-y": 4, "font-family": "IBM Plex Sans Thai, sans-serif" } },
        { selector: 'node[type="product"]', style: { shape: "diamond", width: 26, height: 26,
          "background-color": cssVar("--text-primary"), "font-weight": 600, "text-wrap": "ellipsis", "text-max-width": "140px" } },
        { selector: 'node[type="substance"]', style: { shape: "ellipse", width: 22, height: 22,
          "background-color": cssVar("--accent"), "font-size": 11, "font-weight": 600 } },
        { selector: 'node[type="patent"]', style: { shape: "round-rectangle", width: 12, height: 12,
          "background-color": cssVar("--surface-1"), "border-width": 2, "border-color": cssVar("--text-secondary"), "font-size": 9,
          color: cssVar("--text-secondary") } },
        { selector: 'node[type="patent"][?active]', style: { "background-color": cssVar("--st-critical"), "border-color": cssVar("--st-critical") } },
        { selector: "edge", style: { width: 1.5, "line-color": "data(color)", "curve-style": "bezier", opacity: 0.85 } },
        { selector: 'edge[blocking_level="blocking"]', style: { width: 3 } },
        { selector: 'edge[review_state="candidate"]', style: { "line-style": "dashed" } },
      ],
      layout: { name: "cose", animate: false, nodeRepulsion: () => 9000, idealEdgeLength: () => 60, padding: 20 } as any,
      wheelSensitivity: 0.3,
    });
    cy.on("tap", 'node[type="patent"], node[type="product"]', (e) => {
      const n = e.target;
      const p = n.renderedPosition();
      // Patent: substances it is linked to, with the claim category of each link.
      const links = n.connectedEdges('[type="link"]').map((ed: any) => ({
        substance: ed.source().data("label"), category: ed.data("category"),
        blocking: ed.data("blocking_level") === "blocking", candidate: ed.data("review_state") === "candidate",
      }));
      // Product: its substances.
      const substances = n.connectedEdges('[type="contains"]').map((ed: any) => ed.target().data("label"));
      setHover(null);
      setSel({ x: p.x, y: p.y, d: n.data(), links, substances });
    });
    cy.on("tap", (e) => { if (e.target === cy) setSel(null); });
    cy.on("pan zoom", () => setSel(null));
    cy.on("mouseover", "node", (e) => {
      const p = e.renderedPosition; const box = ref.current!.getBoundingClientRect();
      setHover({ x: box.left + p.x, y: box.top + p.y, d: e.target.data() });
    });
    cy.on("mouseout", "node", () => setHover(null));
    cyRef.current = cy;
    return () => { cy.destroy(); cyRef.current = null; };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [data]);

  // Switching the view only hides/shows nodes; edges to hidden nodes hide with them.
  useEffect(() => {
    const cy = cyRef.current;
    if (!cy) return;
    setSel(null); setHover(null);
    cy.nodes().forEach((n) => {
      const t = n.data("type");
      n.style("display", (t === "product" && !showDrugs) || (t === "patent" && !showPatents) ? "none" : "element");
    });
  }, [data, view, showDrugs, showPatents]);

  const selHref = sel && (sel.d.type === "patent" ? `/patents/${sel.d.application_no}` : `/drugs/${String(sel.d.id).slice(1)}`);

  return (
    <div style={{ position: "relative" }}>
      <div className="row" style={{ marginBottom: 10 }}>
        <span className="small muted">แสดง</span>
        <div className="seg" role="group" aria-label="เลือกส่วนของเครือข่ายที่จะแสดง">
          {VIEWS.map(([v, label]) =>
            <button key={v} className={view === v ? "on" : ""} aria-pressed={view === v} onClick={() => setView(v)}>{label}</button>)}
        </div>
      </div>
      <div className="viz-legend">
        <span className="item" {...shown(showDrugs)}><span className="swatch" style={{ background: "var(--text-primary)", transform: "rotate(45deg) scale(0.8)" }} />ยา (ผลิตภัณฑ์)</span>
        <span className="item"><span className="dot" style={{ background: "var(--accent)" }} />ตัวยาสำคัญ</span>
        {hasActive && <span className="item" {...shown(showPatents)}><span className="swatch" style={{ background: "var(--st-critical)" }} />สิทธิบัตร/คำขอที่ยังมีผล</span>}
        {hasInactive && <span className="item" {...shown(showPatents)}><span className="swatch" style={{ border: "2px solid var(--text-secondary)" }} />สิทธิบัตร/คำขอที่สิ้นผลแล้ว</span>}
        <span className="item" {...shown(showDrugs)}><LineSample color="var(--border)" />ยามีตัวยานี้</span>
      </div>
      <div className="viz-legend" {...shown(showPatents)}>
        <span className="small muted">ประเภทข้อถือสิทธิ (สีเส้น):</span>
        {cats.map((c) => <span key={c} className="item"><LineSample color={`var(--cat-${CATEGORIES.indexOf(c) + 1})`} width={3} />{CATEGORY_LABEL[c]}</span>)}
      </div>
      {(hasBlocking || hasCandidate) && <div className="viz-legend" {...shown(showPatents)}>
        {hasBlocking && <span className="item"><LineSample width={3.5} />เส้นหนา = คุ้มครองตัวยาสำคัญ</span>}
        {hasCandidate && <span className="item"><LineSample dashed />เส้นประ = ยังไม่ได้ตรวจทาน</span>}
      </div>}
      <div className="small muted" style={{ marginBottom: 6 }}>คลิกที่ยาหรือสิทธิบัตรเพื่อดูรายละเอียด</div>
      <div style={{ position: "relative" }}>
        <div ref={ref} className="graph-box" />
        {sel && <div className="graph-popup" style={{ left: Math.max(8, Math.min(sel.x + 14, (ref.current?.clientWidth ?? 0) - 336)),
          top: Math.max(8, Math.min(sel.y + 14, (ref.current?.clientHeight ?? 0) - 220)) }}>
          <button className="close" aria-label="ปิด" onClick={() => setSel(null)}>×</button>
          <div className="small muted">{sel.d.type === "patent" ? "สิทธิบัตร / คำขอ" : "ยา (ผลิตภัณฑ์)"}</div>
          <div style={{ fontWeight: 600 }}>{sel.d.label}</div>
          {sel.d.title && <div className="secondary" style={{ marginTop: 2 }}>{sel.d.title}</div>}
          {sel.d.status && <div className="small" style={{ marginTop: 4 }}>{STATUS_LABEL[sel.d.status]} · สิ้นอายุ {beDate(sel.d.expiry_date)}</div>}
          {sel.links.length > 0 && <ul className="small" style={{ margin: "6px 0 0", paddingLeft: 18 }}>
            {sel.links.map((l, i) => <li key={i}>{l.substance} — {CATEGORY_LABEL[l.category as keyof typeof CATEGORY_LABEL] ?? l.category}
              {l.blocking && " · คุ้มครองตัวยาสำคัญ"}{l.candidate && " · ยังไม่ได้ตรวจทาน"}</li>)}
          </ul>}
          {sel.substances.length > 0 && <div className="small" style={{ marginTop: 6 }}>ตัวยาสำคัญ: {sel.substances.join(", ")}</div>}
          <div style={{ marginTop: 10 }}>
            <a className="btn primary" href={selHref!} target="_blank" rel="noopener">ดูรายละเอียด ↗</a>
          </div>
        </div>}
      </div>
      {hover && !sel && <div className="tooltip" style={{ left: hover.x + 12, top: hover.y + 12 }}>
        <div style={{ fontWeight: 600 }}>{hover.d.label}</div>
        {hover.d.title && <div className="secondary">{hover.d.title}</div>}
        {hover.d.status && <div className="small">{STATUS_LABEL[hover.d.status]} · สิ้นอายุ {beDate(hover.d.expiry_date)}</div>}
      </div>}
    </div>
  );
}
