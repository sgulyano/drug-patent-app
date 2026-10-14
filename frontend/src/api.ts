export interface PatentLinkBrief { substance: string; substance_id: number; category: string | null; review_state: string }

export interface Patent {
  id: number; application_no: string; filing_date: string | null; filing_date_be: string | null;
  publication_no: string | null; publication_date: string | null; publication_date_be: string | null;
  patent_no: string | null; grant_date: string | null; grant_date_be: string | null;
  expiry_date: string | null; expiry_date_be: string | null; expiry_source: string | null;
  kind: string | null; status_raw: string | null; status_norm: string | null; status_th: string | null;
  title: string | null; main_claim: string | null; summary?: string | null; ipc: string | null;
  applicant: string | null; source_url: string | null; industry: string | null;
  status_as_of?: string; status_as_of_th?: string;
  all_claims?: string | null; inventors?: string | null; discovered_via?: string[];
  links?: PatentLinkBrief[] | Link[];
}

export interface Link {
  id: number; patent_id: number; substance_id: number; drug_product_id: number | null;
  claim_category: string | null; category_th: string | null; blocking_level: string | null;
  design_around_note: string | null; match_method: string | null;
  matched_terms: { terms?: string[]; sections?: string[] }; confidence: number | null;
  evidence_snippet: string | null; rationale: string | null; review_state: string;
  reviewed_at: string | null; thai_patentability_flag: string | null;
  substance?: string; patent?: Patent;
}

export interface Substance {
  id: number; inn_name: string; salt_form: string | null; pubchem_cid: number | null; parent_cid: number | null;
  iupac_name: string | null; molecular_formula: string | null; molecular_weight: number | null;
  smiles: string | null; inchikey: string | null; structure_img_url: string | null; description: string | null;
  indication: string | null; mechanism: string | null; atc_code: string | null;
  name_variants_th: { observed: { spelling: string; docs: number; source: string }[]; probes: string[] } | null;
  synonyms_en: string[]; pubchem_url: string | null;
}

export interface AvoidRow {
  application_no: string; patent_no: string | null; title: string | null; category: string | null;
  category_th: string | null; blocking_level: string | null; status: string; status_th: string;
  expiry_date: string | null; expiry_date_be: string | null; note: string | null; substance: string;
  review_state: string;
}

export interface Assessment {
  verdict: string; verdict_th: string; free_date: string | null; free_date_be: string | null;
  fully_clear_date: string | null; fully_clear_date_be?: string | null;
  blocking: AvoidRow[]; avoid: AvoidRow[]; warnings: AvoidRow[]; counts: Record<string, number>;
  summary_th: string; as_of: string; mode: string; analyst_override: string | null; analyst_note: string | null;
}

export interface DrugSummary {
  id: number; name: string; dosage_form: string | null; orange_book_note: string | null;
  substances: Substance[]; n_links: number; n_avoid: number; n_blocking: number; review: ReviewStatus;
  assessment: Pick<Assessment, "verdict" | "verdict_th" | "free_date" | "free_date_be" | "fully_clear_date" | "counts" | "analyst_override">;
}

export interface DrugDetail {
  id: number; name: string; dosage_form: string | null; orange_book_note: string | null;
  th_generic_registered: boolean | null; th_generic_note: string | null;
  substances: Substance[]; links: (Link & { patent: Patent })[];
  orange_book: { jurisdiction: string; patent_no: string; claim_type: string; use_code: string | null;
    expiry: string | null; product_desc: string; applicant: string }[];
  assessment: Assessment; review: ReviewStatus;
}

export interface ReviewStatus { total: number; pending: number; confirmed: boolean }

export interface GraphData {
  nodes: { id: string; type: "product" | "substance" | "patent"; label: string; title?: string; status?: string;
    application_no?: string; expiry_date?: string }[];
  edges: { source: string; target: string; type: string; category?: string; blocking_level?: string; review_state?: string }[];
}

async function get<T>(url: string): Promise<T> {
  const r = await fetch(url);
  if (!r.ok) throw new Error(`${r.status} ${url}`);
  return r.json();
}
async function send<T>(url: string, method: string, body: unknown): Promise<T> {
  const r = await fetch(url, { method, headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });
  if (!r.ok) throw new Error(`${r.status} ${url}`);
  return r.json();
}
const qs = (o: Record<string, unknown>) =>
  new URLSearchParams(Object.entries(o).filter(([, v]) => v !== undefined && v !== null && v !== "" && v !== false)
    .map(([k, v]) => [k, String(v)])).toString();

export const api = {
  stats: () => get<Record<string, number | string>>("/api/stats"),
  patents: (p: Record<string, unknown>) => get<{ total: number; rows: Patent[] }>(`/api/patents?${qs(p)}`),
  patent: (appno: string) => get<Patent & { links: Link[] }>(`/api/patents/${appno}`),
  drugs: (asOf: string) => get<DrugSummary[]>(`/api/drugs?${qs({ as_of: asOf })}`),
  drug: (id: number | string, asOf: string) => get<DrugDetail>(`/api/drugs/${id}?${qs({ as_of: asOf })}`),
  override: (id: number, body: unknown) => send(`/api/drugs/${id}/override`, "POST", body),
  updateDrug: (id: number, body: unknown) => send(`/api/drugs/${id}`, "PATCH", body),
  graph: (asOf: string) => get<GraphData>(`/api/matching/graph?${qs({ as_of: asOf })}`),
  links: (p: Record<string, unknown>) => get<{ total: number; rows: Link[] }>(`/api/links?${qs(p)}`),
  updateLink: (id: number, body: unknown) => send<Link>(`/api/links/${id}`, "PATCH", body),
  addLink: (body: unknown) => send<Link>(`/api/links`, "POST", body),
  evalLatest: () => get<any>("/api/eval/latest"),
  exportUrl: (name: string, p: Record<string, unknown> = {}) => `/api/export/${name}.csv?${qs(p)}`,
};
