import { api } from "../api";
import { Graph } from "../components/Graph";
import { ExportLink, Loading, useAsOf, useData } from "../components/ui";
import { beDate } from "../viz";

export default function Matching() {
  const { asOf } = useAsOf();
  const graph = useData(() => api.graph(asOf), [asOf]);
  return (
    <>
      <div className="page-head">
        <div>
          <h1>การจับคู่ยา–สิทธิบัตร</h1>
          <div className="muted">ประเมิน ณ {beDate(asOf)} · แสดงความสัมพันธ์ระหว่าง ยา – ตัวยา – สิทธิบัตร</div>
        </div>
        <div className="row"><ExportLink name="drug_patent_matrix" label="Export ตารางยา–สิทธิบัตร" /><ExportLink name="assessments" label="Export ผลการประเมิน" /></div>
      </div>
      <div className="card">
        <h2>เครือข่าย ยา – ตัวยา – สิทธิบัตร</h2>
        {graph.data ? <Graph data={graph.data} /> : <Loading error={graph.error} />}
      </div>
    </>
  );
}
