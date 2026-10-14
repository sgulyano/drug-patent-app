import { useState } from "react";
import { Link } from "react-router-dom";
import { api } from "../api";
import { ExportLink, Loading, Picker, ReviewBadge, VerdictBadge, useAsOf, useData, verdictOption } from "../components/ui";
import { VERDICT } from "../viz";

export default function Drugs() {
  const { asOf } = useAsOf();
  const { data, error } = useData(() => api.drugs(asOf), [asOf]);
  const [verdict, setVerdict] = useState("");
  // the badge shows the analyst override when present, so filter on the same effective verdict
  const effective = (d: NonNullable<typeof data>[number]) => d.assessment.analyst_override || d.assessment.verdict;
  const counts = (data ?? []).reduce<Record<string, number>>((m, d) => ({ ...m, [effective(d)]: (m[effective(d)] ?? 0) + 1 }), {});
  const shown = (data ?? []).filter((d) => !verdict || effective(d) === verdict);
  return (
    <>
      <div className="page-head">
        <div>
          <h1>ยาเป้าหมาย</h1>
          <div className="muted">บัญชียามุ่งเป้า · คลิกที่ยาเพื่อดูรายละเอียดสิทธิบัตรที่เกี่ยวข้อง</div>
        </div>
        <ExportLink name="drugs" />
      </div>
      {!data ? <Loading error={error} /> : (
        <>
          <div className="row" style={{ marginBottom: 12 }}>
            <Picker label="กรองตามสถานะ" value={verdict} onChange={setVerdict} options={[
              { value: "", label: `ทุกสถานะ (${data.length})`, marker: <span className="ic" aria-hidden /> },
              ...Object.keys(VERDICT).filter((k) => counts[k]).map((k) => verdictOption(k, ` (${counts[k]})`))]} />
            {verdict && <span className="muted small">แสดง {shown.length} จาก {data.length} รายการ</span>}
          </div>
          <div className="grid cards">
            {shown.map((d) => {
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
        </>
      )}
    </>
  );
}
