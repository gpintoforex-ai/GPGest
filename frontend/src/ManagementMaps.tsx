import { currentMonthRange } from "./components/DatePresets";
import { ModuleTabs, useWorkspaceTab } from "./components/WorkspaceUI";
import { takePeriod } from "./utils/periodHandoff";
import { useEffect, useState } from "react";
import { api } from "./lib/api";
import { Notice } from "./components/InteractionUI";
import { Field } from "./components/Form";
import DatePresets from "./components/DatePresets";
import { formatEuro } from "./utils/format";
import ManagementCosts from "./ManagementCosts";
import "./ManagementMaps.css";

type Row = Record<string, any>;

/** Mapas de gestão indicativos: proveitos, custos e margens por rubrica (por período) e mapa de IVA. */
export default function ManagementMaps({ store, mode = "local", navigate }: { store: string; mode?: string; navigate?: (page: string, params?: Record<string, string>) => void }) {
  const [tab, setTab] = useWorkspaceTab("mapas", "costs", ["costs", "vat"]);
  const [handoff] = useState(() => takePeriod("Mapas de gestão"));
  const [start, setStart] = useState(handoff?.start || currentMonthRange().from), [end, setEnd] = useState(handoff?.end || currentMonthRange().to);
  const [group, setGroup] = useState("month");
  const [data, setData] = useState<Row | null>(null), [error, setError] = useState(""), [loading, setLoading] = useState(false);
  // Recarrega depois de uma correção de rubrica ou de alterar os limites dos alertas.
  const [version, setVersion] = useState(0);
  const valid = !!start && !!end && start <= end;
  useEffect(() => {
    if (!valid) return;
    let active = true;
    setLoading(true); setError("");
    // O ano anterior vem sempre: as variações dos indicadores e a comparação usam-no.
    const path = tab === "costs" ? `/management/costs-revenues?start=${start}&end=${end}&group=${group}&compare=true` : `/management/vat-map?start=${start}&end=${end}`;
    api(path + `&store_id=${encodeURIComponent(store)}`, undefined, { timeoutMs: 90_000 }).then(result => { if (active) setData(result); })
      .catch(e => { if (active) setError((e as Error).message); }).finally(() => { if (active) setLoading(false); });
    return () => { active = false; };
  }, [tab, start, end, group, store, version]);
  return <>
    <div className="page-intro mc-no-print"><div><h1>Mapas de gestão</h1><p>Proveitos, custos e margens por rubrica e mapa de IVA, a partir das vendas e dos documentos de compra. Valores indicativos para gestão; não substituem a contabilidade.</p></div></div>
    <ModuleTabs label="Mapas disponíveis" value={tab} tabs={[{ id: "costs", label: "Proveitos, custos e margens" }, { id: "vat", label: "Mapa de IVA" }]} onChange={value => { setData(null); setTab(value); }} />

    <section className="panel maps-filters filter-bar mc-no-print">
      <DatePresets from={start} to={end} onSelect={(from, to) => { setStart(from); setEnd(to); }} />
      <Field label="Desde"><input type="date" value={start} onChange={e => setStart(e.target.value)} /></Field>
      <Field label="Até"><input type="date" value={end} min={start} onChange={e => setEnd(e.target.value)} /></Field>
      {tab === "costs" && <Field label="Agrupar por"><select value={group} onChange={e => setGroup(e.target.value)}><option value="month">Mês</option><option value="quarter">Trimestre</option></select></Field>}
    </section>
    {error && <Notice className="error" role="alert">{error}</Notice>}
    {loading && <p role="status" className="mc-no-print">A calcular…</p>}
    {data && tab === "costs" && data.periods && <ManagementCosts data={data} start={start} end={end} store={store} mode={mode} navigate={navigate}
      onPeriod={(from, to) => { setStart(from); setEnd(to); }} onChanged={() => { setVersion(v => v + 1); window.dispatchEvent(new Event("iazela-expenses-changed")); }} />}
    {data && tab === "vat" && <VatMap data={data} />}
  </>;
}

function VatMap({ data }: { data: Row }) {
  if (!data.available) return <section className="panel"><Notice tone="info">{data.message}</Notice></section>;
  return <section className="panel">
    <p>Periodicidade: <b>{data.period === "mensal" ? "mensal" : "trimestral"}</b>, conforme o regime de IVA configurado.{data.estimated_deduction && " Há rubricas com dedução parcial: o IVA dedutível é uma estimativa."}</p>
    <div className="table-wrap"><table className="maps-table"><thead><tr><th>Período</th><th className="numeric">IVA liquidado</th><th className="numeric">IVA suportado</th><th className="numeric">IVA dedutível (estim.)</th><th className="numeric">Saldo estimado</th><th className="numeric">Documentos</th></tr></thead><tbody>
      {data.periods.map((row: Row) => <tr key={row.key}><td>{row.label}</td><td className="numeric">{formatEuro(row.output_cents)}</td><td className="numeric">{formatEuro(row.input_cents)}</td><td className="numeric">{formatEuro(row.deductible_cents)}</td>
        <td className="numeric"><b>{formatEuro(Math.abs(row.balance_cents))}</b> <small>{row.balance_cents >= 0 ? "a entregar" : "a recuperar"}</small></td><td className="numeric"><small>{row.sales_documents} vendas · {row.purchase_documents} compras</small></td></tr>)}
    </tbody></table></div>
    {!data.periods.length && <p>Sem documentos neste período.</p>}
    {!!data.rubrics.length && <details><summary>IVA suportado por rubrica</summary><div className="table-wrap"><table><thead><tr><th>Rubrica</th><th className="numeric">Dedução</th><th className="numeric">IVA suportado</th><th className="numeric">IVA dedutível (estim.)</th></tr></thead><tbody>
      {data.rubrics.map((row: Row) => <tr key={row.category_id || "none"}><td>{row.category_name}</td><td className="numeric">{row.vat_deduction_percent == null ? "100%" : `${row.vat_deduction_percent}%`}</td><td className="numeric">{formatEuro(row.input_cents)}</td><td className="numeric">{formatEuro(row.deductible_cents)}</td></tr>)}
    </tbody></table></div><p className="footnote">A percentagem de dedução define-se em Tabelas → Rubricas.</p></details>}
    {data.notes.filter(Boolean).map((note: string) => <p key={note} className="footnote">{note}</p>)}
  </section>;
}
