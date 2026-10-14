import { Link } from "react-router-dom";
import type { Matrix as M } from "../api";
import { CATEGORY_LABEL, STATUS_BADGE, STATUS_LABEL, beDate, categoryColor } from "../viz";
import { VerdictBadge } from "./ui";

/** Products × claim categories. Each cell: worst status (icon + label) and the latest expiry of patents still in force. */
export function MatrixView({ m }: { m: M }) {
  return (
    <div className="table-wrap">
      <table className="matrix">
        <thead>
          <tr>
            <th>ผลิตภัณฑ์</th>
            {m.categories.map((c) => (
              <th key={c}><span className="row" style={{ gap: 6 }}><span className="swatch" style={{ background: categoryColor(c) }} />
                {CATEGORY_LABEL[c]}</span></th>
            ))}
            <th>ผลการประเมิน</th>
          </tr>
        </thead>
        <tbody>
          {m.rows.map((r) => (
            <tr key={r.product_id}>
              <td><Link to={`/drugs/${r.product_id}`}>{r.product}</Link></td>
              {m.categories.map((c) => {
                const cell = r.cells[c];
                if (!cell) return <td key={c} className="cell muted">—</td>;
                const b = STATUS_BADGE[cell.worst ?? ""] ?? STATUS_BADGE.not_published;
                return (
                  <td key={c} className="cell" title={`${cell.n} รายการ, ยังมีผล ${cell.n_active}`}>
                    <div className="badge"><span aria-hidden style={{ color: b.color }}>{b.icon}</span>
                      {STATUS_LABEL[cell.worst ?? ""] ?? cell.worst}</div>
                    <div className="n">{cell.n} รายการ{cell.latest_expiry ? ` · ถึง ${beDate(cell.latest_expiry)}` : ""}</div>
                  </td>
                );
              })}
              <td><VerdictBadge verdict={r.verdict} freeDate={r.free_date ? beDate(r.free_date) : null} /></td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
