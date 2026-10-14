import { Link } from "react-router-dom";
import { api } from "../api";
import { ExportLink, Loading, ReviewBadge, VerdictBadge, useAsOf, useData } from "../components/ui";

export default function Drugs() {
  const { asOf } = useAsOf();
  const { data, error } = useData(() => api.drugs(asOf), [asOf]);
  return (
    <>
      <div className="page-head">
        <div>
          <h1>ยาเป้าหมาย</h1>
          <div className="muted">บัญชียามุ่งเป้า · ข้อมูลเคมีจาก PubChem · ผลการประเมินสิทธิบัตร ณ วันที่เลือก</div>
        </div>
        <ExportLink name="drugs" />
      </div>
      {!data ? <Loading error={error} /> : (
        <div className="grid cards">
          {data.map((d) => {
            const s = d.substances[0];
            return (
              <Link key={d.id} to={`/drugs/${d.id}`} className="card drug-card" style={{ color: "inherit", textDecoration: "none" }}>
                {s?.structure_img_url && <img src={s.structure_img_url} alt={`โครงสร้างทางเคมีของ ${s.inn_name}`} loading="lazy" />}
                <div>
                  <h3 style={{ margin: 0 }}>{d.name}</h3>
                  <div className="muted small">{d.substances.map((x) => `${x.inn_name}${x.salt_form ? ` ${x.salt_form}` : ""}`).join(" + ")}
                    {s?.molecular_formula ? ` · ${s.molecular_formula}` : ""}</div>
                </div>
                <VerdictBadge verdict={d.assessment.verdict} freeDate={d.assessment.free_date_be} override={d.assessment.analyst_override} />
                <div><ReviewBadge review={d.review} /></div>
                <div className="small secondary">
                  สิทธิบัตรที่เชื่อมโยง {d.n_links} รายการ
                  {d.n_avoid ? ` · ต้องหลีกเลี่ยง ${d.n_avoid}` : ""}{d.n_blocking ? ` · ขวางการผลิต ${d.n_blocking}` : ""}
                </div>
                {s?.indication && <div className="small muted clip-3">{s.indication}</div>}
              </Link>
            );
          })}
        </div>
      )}
    </>
  );
}
