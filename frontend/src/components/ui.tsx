import { createContext, useContext, useEffect, useState, type ReactNode } from "react";
import { CATEGORY_LABEL, STATUS_BADGE, STATUS_LABEL, VERDICT, categoryColor } from "../viz";

// ---------------------------------------------------------------- global as-of / mode
interface Ctx { asOf: string; setAsOf: (s: string) => void }
const today = () => new Date().toISOString().slice(0, 10);
const AsOfCtx = createContext<Ctx>({ asOf: today(), setAsOf: () => {} });

function stored(key: string, fallback: string) {
  try { return localStorage.getItem(key) || fallback; } catch { return fallback; }
}
export function AsOfProvider({ children }: { children: ReactNode }) {
  const [asOf, setAsOfS] = useState(() => stored("asOf", today()));
  const setAsOf = (s: string) => { setAsOfS(s); try { localStorage.setItem("asOf", s); } catch { /* ignore */ } };
  return <AsOfCtx.Provider value={{ asOf, setAsOf }}>{children}</AsOfCtx.Provider>;
}
export const useAsOf = () => useContext(AsOfCtx);

export function AsOfControls() {
  const { asOf, setAsOf } = useAsOf();
  return (
    <div className="controls">
      <label className="ctl" title="ประเมินสถานะสิทธิบัตร ณ วันที่">ณ วันที่
        <input type="date" value={asOf} onChange={(e) => e.target.value && setAsOf(e.target.value)} />
      </label>
      <button onClick={() => setAsOf(today())}>วันนี้</button>
      <ThemeToggle />
    </div>
  );
}

function ThemeToggle() {
  const [theme, setTheme] = useState(() => stored("theme", ""));
  useEffect(() => {
    if (theme) document.documentElement.setAttribute("data-theme", theme);
    else document.documentElement.removeAttribute("data-theme");
    try { localStorage.setItem("theme", theme); } catch { /* ignore */ }
  }, [theme]);
  const next = theme === "" ? "dark" : theme === "dark" ? "light" : "";
  return <button title="ธีม: อัตโนมัติ / มืด / สว่าง" onClick={() => setTheme(next)}>
    {theme === "dark" ? "🌙" : theme === "light" ? "☀️" : "🌓"}
  </button>;
}

// ---------------------------------------------------------------- data hook
export function useData<T>(fn: () => Promise<T>, deps: unknown[]): { data: T | null; error: string | null; loading: boolean; reload: () => void } {
  const [data, setData] = useState<T | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [tick, setTick] = useState(0);
  useEffect(() => {
    let alive = true;
    setLoading(true);
    fn().then((d) => { if (alive) { setData(d); setError(null); } })
      .catch((e) => alive && setError(String(e)))
      .finally(() => alive && setLoading(false));
    return () => { alive = false; };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [...deps, tick]);
  return { data, error, loading, reload: () => setTick((t) => t + 1) };
}

export function Loading({ error }: { error?: string | null }) {
  return <div className="empty">{error ? `เกิดข้อผิดพลาด: ${error}` : "กำลังโหลด…"}</div>;
}

// ---------------------------------------------------------------- badges
export function CategoryChip({ cat }: { cat?: string | null }) {
  return <span className="chip"><span className="swatch" style={{ background: categoryColor(cat) }} />
    {cat ? CATEGORY_LABEL[cat] ?? cat : "ไม่ระบุ"}</span>;
}

export function StatusBadge({ status, raw }: { status?: string | null; raw?: string | null }) {
  const b = STATUS_BADGE[status ?? ""] ?? STATUS_BADGE.not_published;
  return <span className="badge" title={raw ?? undefined}>
    <span aria-hidden style={{ color: b.color }}>{b.icon}</span>{STATUS_LABEL[status ?? ""] ?? status ?? "—"}
  </span>;
}

export function ReviewBadge({ review }: { review: { total: number; pending: number; confirmed: boolean } }) {
  if (review.confirmed) return <span className="badge" title="ผู้เชี่ยวชาญตรวจทานสิทธิบัตรที่เกี่ยวข้องครบทุกรายการแล้ว">✓ ยืนยันแล้ว</span>;
  if (!review.total) return <span className="badge muted" title="ยังไม่มีสิทธิบัตรที่เชื่อมโยง">ไม่มีสิทธิบัตรให้ตรวจทาน</span>;
  return <span className="badge" title="ยังมีสิทธิบัตรที่ผู้เชี่ยวชาญยังไม่ได้ตรวจทาน">⏳ รอตรวจทาน {review.pending}/{review.total}</span>;
}

export function VerdictBadge({ verdict, freeDate, override }: { verdict: string; freeDate?: string | null; override?: string | null }) {
  const v = VERDICT[override || verdict] ?? VERDICT.unknown;
  return <span className="verdict">
    <span className="ic" aria-hidden style={{ color: v.color }}>{v.icon}</span>
    {v.label}{(override || verdict) === "blocked_until" && freeDate ? ` (ถึง ${freeDate})` : ""}
    {override && <span className="muted small">· แก้ไขโดยผู้วิเคราะห์</span>}
  </span>;
}

export function ExportLink({ name, params, label }: { name: string; params?: Record<string, unknown>; label?: string }) {
  const q = new URLSearchParams(Object.entries(params ?? {}).filter(([, v]) => v !== undefined && v !== "" && v !== false)
    .map(([k, v]) => [k, String(v)])).toString();
  return <a className="btn" href={`/api/export/${name}.csv${q ? `?${q}` : ""}`} download>⭳ {label ?? "Export CSV"}</a>;
}

export function Highlight({ text, terms }: { text: string; terms: string[] }) {
  if (!terms.length) return <>{text}</>;
  const esc = terms.filter(Boolean).map((t) => t.replace(/[.*+?^${}()|[\]\\]/g, "\\$&"));
  const parts = text.split(new RegExp(`(${esc.join("|")})`, "gi"));
  return <>{parts.map((p, i) => (esc.some((t) => new RegExp(`^${t}$`, "i").test(p)) ? <mark key={i}>{p}</mark> : p))}</>;
}
