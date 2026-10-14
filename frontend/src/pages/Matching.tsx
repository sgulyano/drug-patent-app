import { api } from "../api";
import { Graph } from "../components/Graph";
import { MatrixView } from "../components/Matrix";
import { ExportLink, Loading, useAsOf, useData } from "../components/ui";
import { beDate } from "../viz";

export default function Matching() {
  const { asOf } = useAsOf();
  const matrix = useData(() => api.matrix(asOf), [asOf]);
  const graph = useData(() => api.graph(asOf), [asOf]);
  return (
    <>
      <div className="page-head">
        <div>
          <h1>การจับคู่ยา–สิทธิบัตร</h1>
          <div className="muted">ประเมิน ณ {beDate(asOf)} · ทุกลิงก์ที่ระบบพบ (ยกเว้นที่ปฏิเสธ)</div>
        </div>
        <div className="row"><ExportLink name="drug_patent_matrix" label="Export ตารางยา–สิทธิบัตร" /><ExportLink name="assessments" label="Export ผลการประเมิน" /></div>
      </div>
      <div className="card">
        <h2>ภาพรวม: ผลิตภัณฑ์ × ประเภทข้อถือสิทธิ</h2>
        <p className="small muted" style={{ marginTop: -6 }}>แต่ละช่องแสดงสถานะที่ “แรงที่สุด” ของสิทธิบัตรในประเภทนั้น และวันสิ้นอายุล่าสุดของรายการที่ยังมีผล</p>
        {matrix.data ? <MatrixView m={matrix.data} /> : <Loading error={matrix.error} />}
      </div>
      <div className="card" style={{ marginTop: 16 }}>
        <h2>เครือข่าย ยา – ตัวยา – สิทธิบัตร</h2>
        {graph.data ? <Graph data={graph.data} /> : <Loading error={graph.error} />}
      </div>
    </>
  );
}
