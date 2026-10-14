// Visual encoding shared by every chart.
// Colour = claim category (validated categorical palette, fixed order — never cycled).
// Legal status is encoded by FILL STYLE + text label, never by colour, so the two channels never collide.

export const CATEGORIES = [
  "drug_substance", "solid_form", "process", "formulation", "device", "use", "combination",
] as const;
export type Category = (typeof CATEGORIES)[number];

export const CATEGORY_LABEL: Record<string, string> = {
  drug_substance: "ตัวยาสำคัญ (Substance)",
  solid_form: "รูปแบบสาร/ผลึก (Solid form)",
  process: "กระบวนการผลิต (Process)",
  formulation: "สูตรตำรับ (Formulation)",
  device: "อุปกรณ์ (Device)",
  use: "การใช้ (Use)",
  combination: "ยาผสม (Combination)",
};
export const CATEGORY_SHORT: Record<string, string> = {
  drug_substance: "Substance", solid_form: "Solid form", process: "Process", formulation: "Formulation",
  device: "Device", use: "Use", combination: "Combination",
};

export function categoryColor(cat?: string | null): string {
  const i = CATEGORIES.indexOf((cat ?? "") as Category);
  return i >= 0 ? `var(--cat-${i + 1})` : "var(--inactive)";
}

export type StatusKey = "granted_active" | "pending_unexamined" | "pending_examining" | "likely_abandoned" |
  "expired" | "abandoned" | "revoked" | "not_published";

export const STATUS_LABEL: Record<string, string> = {
  granted_active: "มีผลคุ้มครอง",
  pending_unexamined: "รอตรวจสอบ (ยังไม่ยื่นตรวจ)",
  pending_examining: "อยู่ระหว่างตรวจสอบ",
  likely_abandoned: "น่าจะถูกละทิ้ง",
  expired: "สิ้นอายุ",
  abandoned: "ละทิ้ง/ยกคำขอ",
  revoked: "เพิกถอน",
  not_published: "ยังไม่ประกาศโฆษณา",
};

// Fill style per status, used by the timeline. 'solid' = in force; 'hatch' = pending (not yet granted);
// 'faded' = ended normally (expired); 'outline' = never in force (abandoned/revoked/likely abandoned).
export type FillStyle = "solid" | "hatch" | "faded" | "outline";
export function statusFill(status?: string | null): FillStyle {
  switch (status) {
    case "granted_active": return "solid";
    case "pending_unexamined":
    case "pending_examining": return "hatch";
    case "expired": return "faded";
    default: return "outline";
  }
}

// Status palette (reserved roles) + icon, for badges and matrix cells. Always rendered with its label.
export const STATUS_BADGE: Record<string, { color: string; icon: string; rank: number }> = {
  granted_active: { color: "var(--st-critical)", icon: "●", rank: 5 },
  pending_examining: { color: "var(--st-serious)", icon: "◐", rank: 4 },
  pending_unexamined: { color: "var(--st-serious)", icon: "◐", rank: 4 },
  likely_abandoned: { color: "var(--st-warning)", icon: "◌", rank: 3 },
  expired: { color: "var(--st-neutral)", icon: "○", rank: 2 },
  abandoned: { color: "var(--st-neutral)", icon: "✕", rank: 1 },
  revoked: { color: "var(--st-neutral)", icon: "✕", rank: 1 },
  not_published: { color: "var(--st-neutral)", icon: "·", rank: 0 },
};

// Per-link blocking level, styled like the drug verdict it feeds into.
export const BLOCKING: Record<string, { label: string; color: string; icon: string }> = {
  blocking: { label: "ขวางการผลิต", color: "var(--st-critical)", icon: "✕" },
  avoidable: { label: "หลีกเลี่ยงได้", color: "var(--st-warning)", icon: "!" },
  not_relevant: { label: "ไม่เกี่ยวข้อง", color: "var(--st-good)", icon: "✓" },
};

export const VERDICT: Record<string, { label: string; color: string; icon: string }> = {
  free: { label: "ผลิตได้", color: "var(--st-good)", icon: "✓" },
  free_with_avoidance: { label: "ผลิตได้โดยหลีกเลี่ยง", color: "var(--st-warning)", icon: "!" },
  blocked_until: { label: "ยังผลิตเพื่อจำหน่ายไม่ได้", color: "var(--st-critical)", icon: "✕" },
  pending_review: { label: "รอตรวจทานสิทธิบัตร", color: "var(--st-neutral)", icon: "⏳" },
  unknown: { label: "ไม่ทราบ", color: "var(--st-neutral)", icon: "?" },
};

export function beDate(iso?: string | null): string {
  if (!iso) return "—";
  const [y, m, d] = iso.slice(0, 10).split("-");
  return `${d}/${m}/${Number(y) + 543}`;
}
