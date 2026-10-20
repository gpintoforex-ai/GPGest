import { takePeriod } from "./utils/periodHandoff";
import DatePresets from "./components/DatePresets";
import { useEffect, useMemo, useState } from "react";
import { Link2, RefreshCw, Undo2, ShieldCheck, Search } from "lucide-react";
import { api } from "./lib/api";
import { Field } from "./components/Form";
import { ActionButton, Notice, confirmAction } from "./components/InteractionUI";
import { StatusBadge } from "./components/BusinessUI";
import { formatEuro as money } from "./utils/format";
import "./BillingConference.css";

type Row = Record<string, any>;
type Tab = "documentos" | "recebimentos" | "diario" | "sugestoes" | "falta" | "duplicados" | "historico";

const day = (value?: string) => value ? value.slice(0, 10).split("-").reverse().join("/") : "—";
const lisbonToday = () => new Intl.DateTimeFormat("en-CA", { timeZone: "Europe/Lisbon", year: "numeric", month: "2-digit", day: "2-digit" }).format(new Date());
const states: Record<string, [string, "neutral" | "success" | "warning" | "danger"]> = {
  justificado: ["Justificado", "success"],
  parcial: ["Parcial", "warning"],
  conferido_agregado: ["Conferido em total diário", "neutral"],
  por_conferir: ["Por conferir", "warning"],
  excluido: ["Excluído da faturação", "neutral"],
};
const origins: Record<string, string> = { fecho_local: "Fecho de caixa", manual: "Manual", banco: "Registo bancário", fecho_caixa: "Diferença de caixa" };
const euros = (cents: number) => (cents / 100).toFixed(2).replace(".", ",");
const cents = (value: string) => Math.round(Number(value.replace(",", ".")) * 100);

/** Faturação (SAF-T), recebimentos registados e conferência entre ambos, sempre separados. */
export default function BillingConference({ stores, store, manager }: { stores: Row[]; store: string; manager: boolean }) {
  const today = lisbonToday();
  const [handoff] = useState(() => takePeriod("Faturação e recebimentos"));
  const [target, setTarget] = useState(store), [start, setStart] = useState(handoff?.start || today.slice(0, 8) + "01"), [end, setEnd] = useState(handoff?.end || today);
  const [data, setData] = useState<Row | null>(null), [error, setError] = useState(""), [notice, setNotice] = useState("");
  const [busy, setBusy] = useState(false), [version, setVersion] = useState(0), [tab, setTab] = useState<Tab>("documentos");
  const [docs, setDocs] = useState<Record<string, string>>({}), [recs, setRecs] = useState<Record<string, string>>({}), [notes, setNotes] = useState("");
  const [missing, setMissing] = useState<Row | null>(null), [duplicates, setDuplicates] = useState<Row | null>(null);
  useEffect(() => { setTarget(store); }, [store]);
  useEffect(() => {
    let active = true; setError("");
    const query = new URLSearchParams({ start, end, store_id: target });
    api(`/conference?${query}`, undefined, { timeoutMs: 120000 }).then(result => { if (active) setData(result); }).catch(e => { if (active) setError((e as Error).message); });
    return () => { active = false; };
  }, [target, start, end, version]);
  useEffect(() => {
    if (tab === "falta" && manager) api(`/conference/missing-receipts?${new URLSearchParams({ start, end, store_id: target })}`).then(setMissing).catch(e => setError(e.message));
    if (tab === "duplicados" && manager) api(`/conference/duplicates?${new URLSearchParams({ store_id: target })}`).then(setDuplicates).catch(e => setError(e.message));
  }, [tab, target, start, end, version, manager]);
  const byDoc = useMemo(() => new Map((data?.documents || []).map((d: Row) => [d.id, d])), [data]);
  const byRec = useMemo(() => new Map((data?.receipts || []).map((r: Row) => [`${r.source}:${r.id}`, r])), [data]);
  const storeName = (id: string) => stores.find(s => s.id === id)?.name || "—";
  async function run(task: () => Promise<void>) {
    if (busy) return; setBusy(true); setError(""); setNotice("");
    try { await task(); setVersion(v => v + 1); } catch (e) { setError((e as Error).message); } finally { setBusy(false); }
  }
  const docTotal = Object.values(docs).reduce((sum, value) => sum + (cents(value) || 0), 0);
  const recTotal = Object.values(recs).reduce((sum, value) => sum + (cents(value) || 0), 0);
  function choose(suggestion: Row) {
    setDocs(Object.fromEntries(suggestion.documents.map((d: Row) => [d.record_id, euros(d.amount_cents)])));
    setRecs(Object.fromEntries(suggestion.receipts.map((r: Row) => [`${r.source}:${r.id}`, euros(r.amount_cents)])));
    setNotes("Sugestão revista: " + suggestion.reasons.join("; "));
    setTab("documentos");
  }
  async function associate() {
    const ok = await confirmAction({ title: "Confirmar conferência", tone: "primary", confirmLabel: "Confirmar associação",
      message: <>Associa {Object.keys(docs).length} documento(s) a {Object.keys(recs).length} recebimento(s), no valor de {money(docTotal)}. <strong>Não cria movimentos</strong>; o remanescente fica por justificar.</> });
    if (!ok) return;
    await run(async () => {
      await api("/conference/confirm", { kind: "individual", confirmed: true, notes,
        documents: Object.entries(docs).map(([record_id, value]) => ({ record_id, amount_cents: cents(value) })),
        receipts: Object.entries(recs).map(([key, value]) => { const [source, id] = key.split(":"); return { source, id, amount_cents: cents(value) }; }) });
      setDocs({}); setRecs({}); setNotes(""); setNotice("Conferência registada. Nenhum movimento foi criado.");
    });
  }
  async function aggregate(row: Row) {
    const difference = row.received_cents - (row.payment ? row.saft_payments_cents : row.billing_day_cents);
    let justification = "";
    if (difference) {
      justification = window.prompt(`Diferença de ${money(difference)}. Indique a justificação (ex.: venda a crédito, recebimento de dias anteriores):`) || "";
      if (justification.trim().length < 5) return;
    }
    const ok = await confirmAction({ title: "Confirmar conferência agregada", tone: "primary", confirmLabel: "Confirmar totais do dia",
      message: <>Confirma os totais de {day(row.date)} · {storeName(row.store_id)} · {row.payment_name}. Os documentos <strong>não</strong> ficam marcados individualmente como recebidos.</> });
    if (ok) await run(async () => { await api("/conference/confirm", { kind: "agregada", confirmed: true, store_id: row.store_id, day: row.date, payment: row.payment, notes: justification }); setNotice("Conferência agregada registada."); });
  }
  async function undo(id: string) {
    const reason = window.prompt("Motivo para desfazer a conferência:") || "";
    if (reason.trim().length >= 3) await run(async () => { await api(`/conference/${id}/undo`, { reason }); setNotice("Conferência desfeita; os movimentos não foram alterados."); });
  }
  async function registerMissing(item: Row, session: string, acknowledge: boolean) {
    const ok = await confirmAction({ title: "Registar recebimento em falta", tone: "warning", confirmLabel: "Registar recebimento",
      message: <>Cria um recebimento de {money(item.amount_cents)} em {item.method_name} na caixa escolhida, com a data de hoje, associado a {item.document}. A data de pagamento do SAF-T ({day(item.payment_date)}) fica nas notas.{item.warnings.length > 0 && <> Avisos revistos: {item.warnings.join(" ")}</>}</> });
    if (ok) await run(async () => { await api("/conference/missing-receipts/confirm", { record_id: item.record_id, payment_index: item.payment_index, session_id: session, confirmed: true, acknowledge_warnings: acknowledge, request_key: crypto.randomUUID() }); setNotice("Recebimento registado e associado ao documento."); });
  }
  async function correct(item: Row) {
    const reason = window.prompt("Descreva porque se trata de um duplicado (mín. 5 caracteres):") || "";
    if (reason.trim().length < 5) return;
    const ok = await confirmAction({ title: "Corrigir duplicado por estorno", tone: "danger", confirmLabel: "Estornar registo", message: <>O registo indicado é estornado com o motivo indicado. O original e o estorno ficam no histórico; nada é apagado.</> });
    if (ok) await run(async () => { await api("/conference/duplicates/correct", { ...item.correct, reason, confirmed: true }); setNotice("Duplicado corrigido por estorno."); });
  }
  const indicators = data?.indicators;
  const tabs: [Tab, string][] = [["documentos", "Documentos"], ["recebimentos", "Recebimentos"], ["diario", "Totais diários"], ["sugestoes", `Sugestões (${data?.suggestions.length || 0})`], ...(manager ? [["falta", "Recebimentos em falta"], ["duplicados", "Possíveis duplicados"]] as [Tab, string][] : []), ["historico", "Histórico"]];
  return <div className="billing-conference">
    <div className="page-intro"><div><h1>Faturação e recebimentos</h1><p>A faturação (SAF-T) e os recebimentos registados são medidas distintas. A conferência associa-os sem criar movimentos.</p></div></div>
    {error && <Notice tone="error">{error}</Notice>}{notice && <Notice tone="success" onDismiss={() => setNotice("")}>{notice}</Notice>}
    <section className="panel"><div className="form-grid">
      <Field label="Estabelecimento"><select value={target} onChange={e => setTarget(e.target.value)}><option value="">Todos os autorizados</option>{stores.map(s => <option key={s.id} value={s.id}>{s.name}</option>)}</select></Field>
      <DatePresets from={start} to={end} untilToday onSelect={(from, to) => { setStart(from); setEnd(to); }} />
      <Field label="Desde"><input type="date" value={start} max={end} onChange={e => setStart(e.target.value)} /></Field>
      <Field label="Até"><input type="date" value={end} min={start} max={today} onChange={e => setEnd(e.target.value)} /></Field>
    </div><ActionButton disabled={busy} onClick={() => setVersion(v => v + 1)}><RefreshCw size={16} />Atualizar</ActionButton></section>
    {!data && !error && <p role="status">A carregar faturação e recebimentos…</p>}
    {indicators && <BillingIndicators indicators={indicators} />}
    {data && !data.billing_available && <Notice tone="warning">Sem NIF da empresa configurado, não há faturação SAF-T para conferir. Os recebimentos continuam disponíveis.</Notice>}
    {data && <section className="panel">
      <div className="conference-tabs" role="group" aria-label="Vistas da conferência">{tabs.map(([key, label]) => <ActionButton key={key} type="button" aria-pressed={tab === key} className={tab === key ? "active" : ""} onClick={() => setTab(key)}>{label}</ActionButton>)}</div>
      {(tab === "documentos" || tab === "recebimentos") && manager && (Object.keys(docs).length > 0 || Object.keys(recs).length > 0) && <div className="conference-selection">
        <h3><Link2 size={17} />Associação em preparação</h3>
        <p>Documentos: <strong>{money(docTotal)}</strong> · Recebimentos: <strong>{money(recTotal)}</strong> · {docTotal === recTotal ? "Valores iguais" : <span className="negative">Diferença {money(recTotal - docTotal)}: ajuste os valores (o remanescente fica por justificar).</span>}</p>
        <Field label="Evidência / notas (obrigatório quando só o valor coincide)"><textarea value={notes} maxLength={2000} onChange={e => setNotes(e.target.value)} /></Field>
        <ActionButton className="primary" disabled={busy || !docTotal || docTotal !== recTotal} onClick={associate}><ShieldCheck size={16} />Confirmar associação</ActionButton> <ActionButton disabled={busy} onClick={() => { setDocs({}); setRecs({}); setNotes(""); }}>Limpar seleção</ActionButton>
      </div>}
      {tab === "documentos" && <div className="table-wrap"><table><thead><tr>{manager && <th>Associar</th>}<th>Documento</th><th>Data</th><th>Estab.</th><th className="numeric">Total c/ IVA</th><th className="numeric">Justificado</th><th className="numeric">Remanescente</th><th>Estado</th><th>Pagamento indicado no SAF-T (apoio)</th></tr></thead><tbody>
        {data.documents.map((d: Row) => <tr key={d.id}>
          {manager && <td>{d.remaining_cents > 0 && <input type="text" inputMode="decimal" aria-label={`Valor a justificar de ${d.document}`} className="conference-amount" placeholder="—" value={docs[d.id] || ""} onChange={e => setDocs(v => { const next = { ...v }; if (e.target.value) next[d.id] = e.target.value; else delete next[d.id]; return next; })} onFocus={() => !docs[d.id] && setDocs(v => ({ ...v, [d.id]: euros(d.remaining_cents) }))} />}</td>}
          <td><strong>{d.document}</strong><small>{d.type}{d.direction < 0 ? " · nota de crédito" : ""}{d.status !== "N" ? ` · ${d.status_label}` : ""}</small></td><td>{day(d.date)}</td><td>{storeName(d.store_id)}</td>
          <td className="numeric">{d.direction < 0 ? "−" : ""}{money(d.gross_cents)}<small>Líq. {money(d.net_cents)}</small></td><td className="numeric">{money(d.justified_cents)}</td><td className="numeric">{money(d.remaining_cents)}</td>
          <td><StatusBadge tone={states[d.state][1]}>{states[d.state][0]}</StatusBadge></td>
          <td>{d.saft_payments.length ? d.saft_payments.map((p: Row) => <small key={p.index}>{p.method_name} ({p.code}) {money(p.amount_cents)} · {p.date ? day(p.date) : "sem data"}</small>) : <small>Sem informação de pagamento</small>}</td>
        </tr>)}</tbody></table>{!data.documents.length && <p>Sem documentos SAF-T neste período.</p>}</div>}
      {tab === "recebimentos" && <div className="table-wrap"><table><thead><tr>{manager && <th>Associar</th>}<th>Data</th><th>Caixa / banco</th><th>Meio</th><th>Origem</th><th>Referência</th><th className="numeric">Valor</th><th className="numeric">Remanescente</th></tr></thead><tbody>
        {data.receipts.map((r: Row) => { const key = `${r.source}:${r.id}`; return <tr key={key}>
          {manager && <td>{r.remaining_cents > 0 && <input type="text" inputMode="decimal" aria-label="Valor a associar do recebimento" className="conference-amount" placeholder="—" value={recs[key] || ""} onChange={e => setRecs(v => { const next = { ...v }; if (e.target.value) next[key] = e.target.value; else delete next[key]; return next; })} onFocus={() => !recs[key] && setRecs(v => ({ ...v, [key]: euros(r.remaining_cents) }))} />}</td>}
          <td>{day(r.date)}</td><td>{r.register_name || storeName(r.store_id)}</td><td>{r.payment}</td><td>{origins[r.origin] || r.origin}{r.aggregate && <small>Total diário agregado</small>}</td>
          <td><small>{r.document || r.reference || r.notes || "—"}</small></td><td className={"numeric " + (r.direction > 0 ? "positive" : "negative")}>{r.direction > 0 ? "+" : "−"}{money(r.amount_cents)}</td><td className="numeric">{money(r.remaining_cents)}</td>
        </tr>; })}</tbody></table>{!data.receipts.length && <p>Sem recebimentos registados neste período.</p>}</div>}
      {tab === "diario" && <><p className="footnote">Pagamentos indicados no SAF-T (apoio) comparados com os recebimentos registados, por dia, estabelecimento e meio. Uma coincidência confirma uma conferência agregada, não o pagamento de cada documento.</p>
        <div className="table-wrap"><table><thead><tr><th>Dia</th><th>Estab.</th><th>Meio</th><th className="numeric">Faturado no dia</th><th className="numeric">Pagamentos SAF-T</th><th className="numeric">Recebido registado</th><th className="numeric">Por justificar/conferir</th><th>Ações</th></tr></thead><tbody>
          {data.daily.map((row: Row) => <tr key={`${row.date}-${row.store_id}-${row.payment}`}><td>{day(row.date)}</td><td>{storeName(row.store_id)}</td><td>{row.payment_name}</td><td className="numeric">{money(row.billing_day_cents)}</td><td className="numeric">{money(row.saft_payments_cents)}</td><td className="numeric">{money(row.received_cents)}</td><td className="numeric">{money(row.difference_cents)}</td>
            <td>{row.conference_id ? <StatusBadge tone="success">Conferido</StatusBadge> : manager && row.payment && <ActionButton disabled={busy} onClick={() => aggregate(row)}>Confirmar totais</ActionButton>}</td></tr>)}
        </tbody></table>{!data.daily.length && <p>Sem totais no período.</p>}</div></>}
      {tab === "sugestoes" && <><p className="footnote">Propostas com os critérios usados. Nada é associado automaticamente; o valor coincidente, sozinho, não é proposto.</p>
        {data.suggestions.map((s: Row, i: number) => <div key={i} className="conference-suggestion"><div><strong>{s.documents.map((d: Row) => d.document).join(", ")}</strong> → {s.receipts.map((r: Row) => { const rec = byRec.get(`${r.source}:${r.id}`) as Row | undefined; return rec ? `${day(rec.date)} · ${rec.register_name || ""} · ${rec.payment}` : r.id; }).join(", ")} · {money(s.receipts.reduce((sum: number, r: Row) => sum + r.amount_cents, 0))}
          <ul>{s.reasons.map((reason: string) => <li key={reason}>{reason}</li>)}</ul><small>Confiança: {s.confidence}</small></div>{manager && <ActionButton onClick={() => choose(s)}><Search size={16} />Rever e associar</ActionButton>}</div>)}
        {!data.suggestions.length && <p>Sem sugestões com evidência suficiente.</p>}</>}
      {tab === "falta" && <MissingReceipts data={missing} busy={busy} storeName={storeName} onRegister={registerMissing} />}
      {tab === "duplicados" && <Duplicates data={duplicates} busy={busy} onCorrect={correct} />}
      {tab === "historico" && <div className="table-wrap"><table><thead><tr><th>Data</th><th>Tipo</th><th>Estab.</th><th>Documentos / recebimentos</th><th className="numeric">Valor</th><th>Critérios</th><th>Ações</th></tr></thead><tbody>
        {data.conferences.map((c: Row) => <tr key={c.id}><td>{day(c.created_at)}</td><td>{c.kind === "agregada" ? `Agregada · ${day(c.day)}${c.payment ? " · " + c.payment : ""}` : "Individual"}</td><td>{storeName(c.store_id)}</td>
          <td><small>{c.lines.filter((l: Row) => l.side === "document").map((l: Row) => (byDoc.get(l.source_id) as Row | undefined)?.document || "documento").join(", ")}</small><small>{c.lines.filter((l: Row) => l.side === "receipt").length} recebimento(s)</small></td>
          <td className="numeric">{money(c.billed_cents)}{c.kind === "agregada" && <small>Recebido {money(c.received_cents)}</small>}</td><td><small>{c.criteria.join("; ")}</small>{c.notes && <small>Notas: {c.notes}</small>}</td>
          <td>{manager && <ActionButton disabled={busy} onClick={() => undo(c.id)}><Undo2 size={16} />Desfazer</ActionButton>}</td></tr>)}
      </tbody></table>{!data.conferences.length && <p>Sem conferências registadas.</p>}</div>}
      <ul className="footnote">{data.rules.map((rule: string) => <li key={rule}>{rule}</li>)}</ul>
    </section>}
  </div>;
}

/** Indicadores do mês corrente no painel: faturação, recebimentos e saldos lado a lado. */
export function DashboardBilling({ store, onOpen }: { store: string; onOpen: () => void }) {
  const [indicators, setIndicators] = useState<Row | null>(null);
  useEffect(() => {
    let active = true;
    const today = lisbonToday();
    api(`/conference/indicators?${new URLSearchParams({ start: today.slice(0, 8) + "01", end: today, store_id: store })}`, undefined, { timeoutMs: 60000 })
      .then(result => { if (active && result?.billing && result?.receipts) setIndicators(result); }).catch(() => { /* indicadores informativos */ });
    return () => { active = false; };
  }, [store]);
  if (!indicators) return null;
  return <div className="billing-conference"><div className="page-intro"><div><span className="eyebrow">FATURAÇÃO E TESOURARIA · MÊS ATUAL</span></div><ActionButton onClick={onOpen}>Abrir conferência</ActionButton></div><BillingIndicators indicators={indicators} /></div>;
}

/** Indicadores separados; reutilizado no painel. Faturação e recebimentos nunca são somados. */
export function BillingIndicators({ indicators }: { indicators: Row }) {
  const b = indicators.billing, r = indicators.receipts, s = indicators.balances, c = indicators.conference;
  const cards: [string, number | null, string][] = [
    ["Faturação líquida de IVA", b.available ? b.net_cents : null, `${b.documents} documento(s); notas de crédito subtraem`],
    ["Total faturado com IVA", b.available ? b.gross_cents : null, b.excluded_documents ? `${b.excluded_documents} documento(s) anulados/excluídos` : "Documentos normais do SAF-T"],
    ["Recebimentos do período", r.net_cents, `Recebido ${money(r.received_cents)} · reembolsos ${money(r.refunds_cents)}`],
    ["Pagamentos do período", indicators.payments_cents, "Fornecedores, despesas e encargos bancários"],
    ["Saldo das caixas abertas", s.cash_open_sessions_cents, "Numerário em sessões abertas"],
    ["Saldo dos bancos (registos)", s.bank_book_cents, s.bank_accounts_without_opening ? `${s.bank_accounts_without_opening} conta(s) sem saldo inicial` : "Pelos registos da aplicação"],
    ["TPA / MB WAY por liquidar", s.card_pending_settlement_cents, `${s.card_pending_settlement_count} recebimento(s) a aguardar o prestador`],
    ["Em conferência", c.pending_cents, `${c.pending_documents} documento(s) por justificar · justificado ${money(c.justified_cents)}`],
  ];
  return <section className="panel billing-indicators" aria-label="Indicadores de faturação e tesouraria">
    <div className="billing-cards">{cards.map(([label, value, hint]) => <div key={label} className="billing-card"><span>{label}</span><strong>{value === null ? "Sem informação" : money(value)}</strong><small>{hint}</small></div>)}</div>
    {indicators.to_justify_cents !== null && <p className="billing-difference"><strong>Por justificar/conferir: {money(indicators.to_justify_cents)}</strong> (total faturado com IVA − recebimentos líquidos). <small>{indicators.to_justify_note}</small></p>}
  </section>;
}

function MissingReceipts({ data, busy, storeName, onRegister }: { data: Row | null; busy: boolean; storeName: (id: string) => string; onRegister: (item: Row, session: string, acknowledge: boolean) => void }) {
  const [sessions, setSessions] = useState<Record<string, string>>({}), [acks, setAcks] = useState<Record<string, boolean>>({});
  if (!data) return <p role="status">A rever documentos com pagamento indicado no SAF-T…</p>;
  return <><p className="footnote">{data.note}</p>
    {data.items.map((item: Row) => { const key = `${item.record_id}:${item.payment_index}`; const session = sessions[key] || item.destinations[0]?.id || ""; return <div key={key} className="conference-suggestion">
      <div><strong>{item.document}</strong> · {storeName(item.store_id)} · documento de {day(item.document_date)} · pagamento SAF-T {item.payment_date ? day(item.payment_date) : "sem data"} · {item.method_name} ({item.code}) · <strong>{money(item.amount_cents)}</strong>
        {item.missing.map((m: string) => <Notice key={m} tone="error">{m}</Notice>)}{item.warnings.map((w: string) => <Notice key={w} tone="warning">{w}</Notice>)}</div>
      {item.proposable && (item.destinations.length ? <div className="conference-actions">
        <Field label="Caixa de destino (aberta)"><select value={session} onChange={e => setSessions(v => ({ ...v, [key]: e.target.value }))}>{item.destinations.map((d: Row) => <option key={d.id} value={d.id}>{d.register} · folha de {day(d.day)}</option>)}</select></Field>
        {item.warnings.length > 0 && <label><input type="checkbox" checked={!!acks[key]} onChange={e => setAcks(v => ({ ...v, [key]: e.target.checked }))} /> Revi os avisos e confirmo que é um recebimento distinto</label>}
        <ActionButton disabled={busy || !session || (item.warnings.length > 0 && !acks[key])} onClick={() => onRegister(item, session, !!acks[key])}>Registar recebimento</ActionButton>
      </div> : <small>Abra uma caixa deste estabelecimento para registar o recebimento.</small>)}
    </div>; })}
    {!data.items.length && <p>Não há pagamentos indicados no SAF-T por rever neste período.</p>}</>;
}

function Duplicates({ data, busy, onCorrect }: { data: Row | null; busy: boolean; onCorrect: (item: Row) => void }) {
  if (!data) return <p role="status">A procurar possíveis duplicações…</p>;
  return <><p className="footnote">{data.note}</p>
    {data.checks.map((check: Row) => <Notice key={check.name} tone={check.ok ? "success" : "warning"}>{check.name}: {check.count}. {check.detail}</Notice>)}
    {data.items.map((item: Row, i: number) => <div key={i} className="conference-suggestion"><div><strong>{item.reason}</strong>
      <div className="table-wrap"><table><thead><tr><th>Origem</th><th>Data</th><th>Descrição / documento</th><th className="numeric">Valor</th><th>Estado</th></tr></thead><tbody>
        {item.records.map((r: Row) => <tr key={r.source + r.id} className={item.correct.id === r.id ? "conference-target" : ""}><td>{r.origin}</td><td>{day(r.date)}</td><td><small>{r.document || r.description}</small></td><td className="numeric">{money(r.amount_cents)}</td>
          <td><small>{[r.reconciled && "Reconciliado", r.conference_cents && "Em conferência", r.closed && "Caixa fechada", item.correct.id === r.id && "Proposto para estorno"].filter(Boolean).join(" · ") || "—"}</small></td></tr>)}
      </tbody></table></div></div><ActionButton className="danger" disabled={busy} onClick={() => onCorrect(item)}>Corrigir por estorno</ActionButton></div>)}
    {!data.items.length && <p>Não foram encontradas possíveis duplicações.</p>}</>;
}
