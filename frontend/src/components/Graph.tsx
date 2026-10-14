import cytoscape from "cytoscape";
import { useEffect, useRef, useState } from "react";
import { useNavigate } from "react-router-dom";
import type { GraphData } from "../api";
import { CATEGORIES, CATEGORY_LABEL, STATUS_LABEL, beDate } from "../viz";

function cssVar(name: string) {
  return getComputedStyle(document.documentElement).getPropertyValue(name).trim() || "#888";
}

/** Product → substance → patent network. Edge colour = claim category; patent node fill = in force or not. */
export function Graph({ data }: { data: GraphData }) {
  const ref = useRef<HTMLDivElement>(null);
  const nav = useNavigate();
  const [hover, setHover] = useState<{ x: number; y: number; d: any } | null>(null);

  useEffect(() => {
    if (!ref.current) return;
    const catColor = (c?: string) => {
      const i = CATEGORIES.indexOf((c ?? "") as any);
      return i >= 0 ? cssVar(`--cat-${i + 1}`) : cssVar("--inactive");
    };
    const active = new Set(["granted_active", "pending_examining", "pending_unexamined"]);
    const cy = cytoscape({
      container: ref.current,
      elements: [
        ...data.nodes.map((n) => ({ data: { ...n, active: n.status ? active.has(n.status) : undefined } })),
        ...data.edges.map((e, i) => ({ data: { id: `e${i}`, ...e, color: e.type === "link" ? catColor(e.category) : cssVar("--border") } })),
      ],
      style: [
        { selector: "node", style: { label: "data(label)", "font-size": 10, color: cssVar("--text-primary"),
          "text-valign": "bottom", "text-margin-y": 4, "font-family": "IBM Plex Sans Thai, sans-serif" } },
        { selector: 'node[type="product"]', style: { shape: "round-rectangle", width: 18, height: 18,
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
    cy.on("tap", 'node[type="patent"]', (e) => nav(`/patents/${e.target.data("application_no")}`));
    cy.on("tap", 'node[type="product"]', (e) => nav(`/drugs/${String(e.target.id()).slice(1)}`));
    cy.on("mouseover", "node", (e) => {
      const p = e.renderedPosition; const box = ref.current!.getBoundingClientRect();
      setHover({ x: box.left + p.x, y: box.top + p.y, d: e.target.data() });
    });
    cy.on("mouseout", "node", () => setHover(null));
    return () => cy.destroy();
  }, [data, nav]);

  return (
    <div style={{ position: "relative" }}>
      <div className="viz-legend">
        {CATEGORIES.map((c, i) => <span key={c} className="item"><span className="swatch" style={{ background: `var(--cat-${i + 1})` }} />{CATEGORY_LABEL[c]}</span>)}
      </div>
      <div className="viz-legend">
        <span className="item"><span className="dot" style={{ background: "var(--st-critical)" }} />สิทธิบัตร/คำขอที่ยังมีผล</span>
        <span className="item"><span className="dot" style={{ border: "2px solid var(--text-secondary)" }} />สิ้นผลแล้ว</span>
        <span className="item">เส้นหนา = คุ้มครองตัวยาสำคัญ · เส้นประ = ยังไม่ได้ตรวจทาน</span>
      </div>
      <div ref={ref} className="graph-box" />
      {hover && <div className="tooltip" style={{ left: hover.x + 12, top: hover.y + 12 }}>
        <div style={{ fontWeight: 600 }}>{hover.d.label}</div>
        {hover.d.title && <div className="secondary">{hover.d.title}</div>}
        {hover.d.status && <div className="small">{STATUS_LABEL[hover.d.status]} · สิ้นอายุ {beDate(hover.d.expiry_date)}</div>}
      </div>}
    </div>
  );
}
