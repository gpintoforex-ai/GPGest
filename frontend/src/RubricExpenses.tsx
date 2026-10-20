import { takePeriod } from "./utils/periodHandoff";
import { useEffect, useMemo, useState } from "react";
import { RefreshCw } from "lucide-react";
import { api } from "./lib/api";
import { ActionButton, Notice, confirmAction } from "./components/InteractionUI";
import { Field } from "./components/Form";
import { StatusBadge } from "./components/BusinessUI";
import DatePresets from "./components/DatePresets";
import { formatEuro } from "./utils/format";
import "./RubricExpenses.css";

type Row = Record<string, any>;
const day = (value: string) => value?.slice(0, 10).split("-").reverse().join("/") || "—";
const lisbonToday = () => new Intl.DateTimeFormat("en-CA", { timeZone: "Europe/Lisbon", year: "numeric", month: "2-digit", day: "2-digit" }).format(new Date());
const SOURCES: Row = { fornecedor: "Rubrica do fornecedor", documento: "Definida no documento", linhas: "Linhas da compra", "": "Por classificar" };

/** Despesas por rubrica, pela data das faturas de compra (ERP e Compras). */
export default function RubricExpenses({ stores, store, categories, admin, integrated }: {
  stores: Row[]; store: string; categories: Row[]; admin: boolean; integrated: boolean;
}) {
  const today = lisbonToday();
  const [handoff] = useState(() => takePeriod("Despesas por rubrica"));
  const [start, setStart] = useState(handoff?.start || today.slice(0, 8) + "01"), [end, setEnd] = useState(handoff?.end || today);
  const [data, setData] = useState<Row | null>(null), [loading, setLoading] = useState(false);
  const [busy, setBusy] = useState(false), [error, setError] = useState(""), [notice, setNotice] = useState("");
  const [rubricFilter, setRubricFilter] = useState<string | null>(null), [search, setSearch] = useState("");
  const [editing, setEditing] = useState<Row | null>(null), [shares, setShares] = useState<Row[]>([]);
  const valid = !!start && !!end && start <= end;

  async function load() {
    if (!valid) return;
    setLoading(true);
    try { setData(await api(`/rubric-expenses?start=${start}&end=${end}&store_id=${encodeURIComponent(store)}`)); }
    finally { setLoading(false); }
  }
  useEffect(() => { setError(""); load().catch(e => setError((e as Error).message)); }, [start, end, store]);
  async function run(task: () => Promise<void>) {
    setBusy(true); setError(""); setNotice("");
    try { await task(); } catch (e) { setError((e as Error).message); } finally { setBusy(false); }
  }

  const documents: Row[] = useMemo(() => (data?.documents || []).filter((doc: Row) => {
    if (rubricFilter !== null && !doc.allocations.some((a: Row) => (a.category_id || "") === rubricFilter)) return false;
    const text = search.trim().toLowerCase();
    return !text || `${doc.supplier_name} ${doc.supplier_nif} ${doc.document}`.toLowerCase().includes(text);
  }), [data, rubricFilter, search]);
  const positive = (data?.rubrics || []).reduce((sum: number, item: Row) => sum + Math.max(0, item.total_cents), 0);

  function startEdit(doc: Row) {
    setEditing(doc);
    setShares(doc.rubric_source === "documento"
      ? doc.allocations.map((a: Row) => ({ category_id: a.category_id, amount: (Math.abs(a.amount_cents) / 100).toFixed(2).replace(".", ",") }))
      : [{ category_id: doc.allocations[0]?.category_id || "", amount: (Math.abs(doc.amount_cents) / 100).toFixed(2).replace(".", ",") }]);
  }
  const shareTotal = shares.reduce((sum, share) => sum + Math.round(Number(String(share.amount).replace(",", ".")) * 100 || 0), 0);

  return <>
    <div className="page-intro"><div><h1>Despesas por rubrica</h1><p>Faturas de compra pela data do documento: compras do ERP e compras confirmadas na IAZELA. As notas de crédito subtraem. Cada documento conta uma vez.</p></div></div>
    {error && <Notice className="error" role="alert">{error}</Notice>}
    {notice && <Notice tone="success" onDismiss={() => setNotice("")}>{notice}</Notice>}
    <section className="panel rubric-expenses-filters">
      <DatePresets from={start} to={end} onSelect={(from, to) => { setStart(from); setEnd(to); }} />
      <Field label="Desde"><input type="date" value={start} onChange={e => setStart(e.target.value)} /></Field>
      <Field label="Até"><input type="date" value={end} min={start} onChange={e => setEnd(e.target.value)} /></Field>
      {integrated && admin && <ActionButton type="button" disabled={busy || !valid} onClick={() => void run(async () => {
        const targets = store ? [store] : stores.map(s => s.id);
        if (!(await confirmAction({ title: "Atualizar compras do ERP?", message: `Consulta as compras do Wintouch de ${day(start)} a ${day(end)} (${targets.length} loja(s)). Apenas leitura; o ERP não é alterado.`, confirmLabel: "Consultar ERP" }))) return;
        let count = 0;
        for (const store_id of targets) count += (await api("/documents/erp-sync", { store_id, start, end, confirm_demo: true }, { timeoutMs: 180_000 })).count || 0;
        setNotice(`${count} documento(s) de compra consultados no ERP.`); await load();
      })}><RefreshCw size={16} />Atualizar compras do ERP</ActionButton>}
    </section>
    {loading && <p role="status">A carregar despesas…</p>}
    {data && <section className="panel">
      <div className="panel-title"><h3>Total do período · {formatEuro(data.total_cents)}</h3>{!!data.unclassified && <StatusBadge tone="warning">{data.unclassified} documento(s) por classificar</StatusBadge>}</div>
      {!!data.pending_efatura?.count && <Notice tone="warning">{data.pending_efatura.count} fatura(s) do e-Fatura neste período ({formatEuro(data.pending_efatura.total_cents)}) ainda não foram lançadas e não entram nestes totais. {integrated ? "Confirme o lançamento no ERP e atualize as compras do ERP." : "Lance-as na Conferência documental → Rever propostas."}</Notice>}
      {integrated && !data.erp_documents && <Notice tone="info">Não há compras do ERP neste período na IAZELA. {admin ? "Use «Atualizar compras do ERP» para as consultar." : "Peça a um administrador para atualizar as compras do ERP."}</Notice>}
      <div className="table-wrap"><table className="rubric-expenses-table"><thead><tr><th>Rubrica</th><th className="numeric">Total</th><th className="numeric">Documentos</th><th>Peso</th></tr></thead><tbody>
        {data.rubrics.map((item: Row) => { const key = item.category_id || ""; const share = positive ? Math.max(0, item.total_cents) / positive : 0; return <tr key={key} className={rubricFilter === key ? "selected" : ""}>
          <td><ActionButton type="button" className="rubric-expenses-link" onClick={() => setRubricFilter(rubricFilter === key ? null : key)}>{item.category_name}</ActionButton></td>
          <td className="numeric">{formatEuro(item.total_cents)}</td><td className="numeric">{item.documents}</td>
          <td><div className="rubric-expenses-bar" aria-label={`${Math.round(share * 100)}%`}><span style={{ width: `${share * 100}%` }} /></div><small>{Math.round(share * 100)}%</small></td>
        </tr>; })}
      </tbody></table></div>
      {!data.rubrics.length && <p>Sem faturas de compra neste período.</p>}
    </section>}
    {data && !!data.documents.length && <section className="panel">
      <div className="panel-title"><h3>Documentos{rubricFilter !== null && ` · ${data.rubrics.find((r: Row) => (r.category_id || "") === rubricFilter)?.category_name}`}</h3>
        {rubricFilter !== null && <ActionButton type="button" onClick={() => setRubricFilter(null)}>Ver todas as rubricas</ActionButton>}</div>
      <Field label="Fornecedor ou documento"><input value={search} onChange={e => setSearch(e.target.value)} placeholder="Nome, NIF ou número" /></Field>
      <div className="table-wrap"><table><thead><tr><th>Data</th><th>Fornecedor</th><th>Documento</th><th>Origem</th><th className="numeric">Total</th><th>Rubrica</th><th>Ações</th></tr></thead><tbody>
        {documents.slice(0, 200).map(doc => <tr key={doc.id}>
          <td>{day(doc.document_date)}</td><td>{doc.supplier_name}<br /><small>{doc.supplier_nif}</small></td><td>{doc.document_type} {doc.document}</td><td>{doc.source}<br /><small>{doc.store}</small></td>
          <td className="numeric">{formatEuro(doc.amount_cents)}</td>
          <td>{doc.allocations.map((a: Row) => <div key={a.category_id || "none"}>{a.category_name || <StatusBadge tone="warning">Por classificar</StatusBadge>}{doc.allocations.length > 1 && <small> · {formatEuro(a.amount_cents)}</small>}</div>)}<small>{SOURCES[doc.rubric_source]}</small></td>
          <td>{doc.source === "ERP" && <ActionButton type="button" className="rubric-expenses-link" disabled={busy} onClick={() => startEdit(doc)}>Definir rubrica</ActionButton>}</td>
        </tr>)}
      </tbody></table></div>
      {documents.length > 200 && <p className="footnote">A mostrar 200 de {documents.length} documentos; use os filtros para restringir.</p>}
      <p className="footnote">A rubrica de uma compra do ERP é a do fornecedor (Tabelas → Fornecedores), salvo se for definida no documento. Definir aqui a rubrica não altera o ERP. As compras da IAZELA usam a rubrica de cada linha.</p>
    </section>}
    {editing && <section className="panel rubric-expenses-edit" aria-label="Rubrica do documento">
      <div className="panel-title"><h4>{editing.supplier_name} · {editing.document_type} {editing.document} · {formatEuro(editing.amount_cents)}</h4><ActionButton type="button" onClick={() => setEditing(null)}>Fechar</ActionButton></div>
      <p>Escolha a rubrica do documento ou reparta o valor por várias rubricas (por exemplo, mercadorias e serviços na mesma fatura).</p>
      {shares.map((share, index) => <div className="form-grid" key={index}>
        <Field label="Rubrica"><select value={share.category_id} onChange={e => setShares(shares.map((s, i) => i === index ? { ...s, category_id: e.target.value } : s))}><option value="">Escolher rubrica</option>{categories.map(c => <option key={c.id} value={c.id}>{c.name}</option>)}</select></Field>
        <Field label="Valor (€)"><input inputMode="decimal" value={share.amount} onChange={e => setShares(shares.map((s, i) => i === index ? { ...s, amount: e.target.value } : s))} /></Field>
        {shares.length > 1 && <ActionButton type="button" onClick={() => setShares(shares.filter((_, i) => i !== index))}>Remover</ActionButton>}
      </div>)}
      <ActionButton type="button" onClick={() => setShares([...shares, { category_id: "", amount: "" }])}>Repartir por outra rubrica</ActionButton>
      <p className={shareTotal !== Math.abs(editing.amount_cents) ? "workflow-warning" : ""}>Repartido: {formatEuro(shareTotal)} de {formatEuro(Math.abs(editing.amount_cents))}</p>
      <div className="rubric-expenses-actions">
        <ActionButton type="button" className="primary" disabled={busy || shareTotal !== Math.abs(editing.amount_cents) || shares.some(s => !s.category_id)} onClick={() => void run(async () => {
          await api(`/rubric-expenses/erp/${editing.id.slice(4)}`, { allocations: shares.map(s => ({ category_id: s.category_id, amount: String(s.amount) })) });
          setEditing(null); setNotice("Rubrica do documento guardada."); await load();
        })}>Guardar rubrica</ActionButton>
        {editing.rubric_source === "documento" && <ActionButton type="button" disabled={busy} onClick={() => void run(async () => {
          await api(`/rubric-expenses/erp/${editing.id.slice(4)}`, { allocations: [] });
          setEditing(null); setNotice("O documento volta a usar a rubrica do fornecedor."); await load();
        })}>Usar a rubrica do fornecedor</ActionButton>}
      </div>
    </section>}
  </>;
}
