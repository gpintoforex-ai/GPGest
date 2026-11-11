import { useEffect, useMemo, useRef, useState } from "react";
import { AlertTriangle, Download, FlaskConical, History, Inbox, Lightbulb, ShieldCheck, Upload } from "lucide-react";
import { StatusBadge } from "./components/BusinessUI";
import { ActionButton, Notice } from "./components/InteractionUI";
import FiscalPayments from "./FiscalPayments";
import ReasonField, { NATURE_REASONS, REJECT_REASONS } from "./ReasonField";
import { api } from "./lib/api";
import { formatEuro } from "./utils/format";
import "./RubricNature.css";

/**
 * Natureza financeira das rubricas: em que análises entra cada rubrica (custos operacionais, compras e
 * investimentos, saídas de tesouraria, obrigações fiscais, orçamento de pagamentos). Qualquer alteração é
 * simulada (antes/depois nos últimos 12 meses e registos afetados) e só um administrador a aplica, com motivo;
 * o gestor pede aprovação. Histórico por aplicação (com reposição simulada) e exportação/importação da
 * configuração entre empresas. Os documentos e movimentos nunca são alterados.
 */

type Row = Record<string, any>;
const FLAGS = ["in_operating_costs", "in_purchases", "in_treasury", "in_fiscal", "in_payment_budget"] as const;
type Flag = typeof FLAGS[number];
type Draft = { nature: string } & Record<Flag, boolean>;
const SHORT: Record<Flag, string> = {
  in_operating_costs: "Custos operacionais", in_purchases: "Compras e investimentos", in_treasury: "Saídas de tesouraria",
  in_fiscal: "Obrigações fiscais", in_payment_budget: "Orçamento de pagamentos",
};
const FORMAT = "iazela-natureza-rubricas";
const fullDate = (iso?: string | null) => iso ? iso.slice(0, 10).split("-").reverse().join("/") : "—";
const pick = (row: Row): Draft => ({ nature: row.nature, ...Object.fromEntries(FLAGS.map(f => [f, !!row[f]])) } as Draft);
const same = (a: Draft, b: Draft) => a.nature === b.nature && FLAGS.every(f => a[f] === b[f]);
const fold = (text: string) => text.normalize("NFD").replace(/[̀-ͯ]/g, "").toLowerCase().trim();
const signed = (cents: number) => cents ? `${cents > 0 ? "+" : "−"}${formatEuro(Math.abs(cents))}` : "Sem alteração";
const STATUS_TONE: Record<string, "warning" | "success" | "danger" | "neutral"> = { pendente: "warning", aprovado: "success", recusado: "danger", cancelado: "neutral" };

export default function RubricNature() {
  const [data, setData] = useState<Row | null>(null), [drafts, setDrafts] = useState<Record<string, Draft>>({});
  const [simulation, setSimulation] = useState<Row | null>(null), [reason, setReason] = useState("");
  const [error, setError] = useState(""), [notice, setNotice] = useState(""), [busy, setBusy] = useState(false), [version, setVersion] = useState(0);
  const [showInactive, setShowInactive] = useState(false);
  const [requests, setRequests] = useState<Row | null>(null);
  const [review, setReview] = useState<Row | null>(null), [reclassReview, setReclassReview] = useState<Row | null>(null);
  const [rejecting, setRejecting] = useState<{ id: string; reason: string } | null>(null);
  const [importNote, setImportNote] = useState("");
  const file = useRef<HTMLInputElement>(null);
  useEffect(() => {
    let active = true;
    api("/rubric-nature").then(r => { if (active) { setData(r); setDrafts({}); setSimulation(null); setReview(null); } }).catch(e => { if (active) setError((e as Error).message); });
    api("/rubric-nature/requests").then(r => { if (active) setRequests(r); }).catch(() => { if (active) setRequests(null); });
    return () => { active = false; };
  }, [version]);
  const rubrics: Row[] = useMemo(() => (data?.rubrics || []).filter((r: Row) => showInactive || r.active), [data, showInactive]);
  const changes = useMemo(() => (data?.rubrics || []).filter((r: Row) => drafts[r.id] && !same(drafts[r.id], pick(r)))
    .map((r: Row) => ({ category_id: r.id, ...drafts[r.id] })), [data, drafts]);
  const suggestions = (data?.rubrics || []).filter((r: Row) => r.suggestion && r.active);
  const presets: Record<string, Row> = Object.fromEntries((data?.natures || []).map((n: Row) => [n.key, n.flags]));
  const labels: Record<string, string> = Object.fromEntries((data?.natures || []).map((n: Row) => [n.key, n.label]));
  const byId: Record<string, Row> = Object.fromEntries((data?.rubrics || []).map((r: Row) => [r.id, r]));
  const current = (r: Row) => drafts[r.id] || pick(r);
  const edit = (r: Row, next: Draft) => { setDrafts(d => ({ ...d, [r.id]: next })); setSimulation(null); setNotice(""); };
  const applySuggestion = (r: Row) => { const { reason: _reason, ...values } = r.suggestion; edit(r, values as Draft); };
  const load = (next: Record<string, Draft>) => { setDrafts(next); setSimulation(null); setNotice(""); setError(""); };

  async function run(action: () => Promise<void>) {
    setBusy(true); setError(""); setNotice("");
    try { await action(); } catch (e) { setError((e as Error).message); } finally { setBusy(false); }
  }
  const simulate = (list: Row[] = changes) => run(async () => { setSimulation(await api("/rubric-nature/simulate", { changes: list })); });
  const apply = () => run(async () => {
    const result = await api("/rubric-nature/apply", { changes, fingerprint: simulation!.fingerprint, reason: reason.trim(), ...(review ? { request_id: review.id } : {}) });
    setReason(""); setVersion(v => v + 1);
    setNotice(`Natureza aplicada a ${result.applied} rubrica(s)${review ? ` (pedido de ${review.requested_by} aprovado)` : ""}. Os documentos e movimentos não foram alterados; as análises passam a usar a nova configuração.`);
  });
  const ask = () => run(async () => {
    await api("/rubric-nature/requests", { kind: "nature", changes, reason: reason.trim() });
    setReason(""); setVersion(v => v + 1);
    setNotice("Pedido enviado ao administrador. Nada muda até ser aprovado; o administrador vê o aviso na barra do menu.");
  });
  const reviewRequest = (request: Row) => {
    if (request.kind === "reclassification") { setReclassReview({ ...request }); return; }
    const next: Record<string, Draft> = {};
    for (const change of request.payload) { const { category_id, ...values } = change; next[category_id] = values as Draft; }
    load(next); setReview(request); setReason("");
    void simulate(request.payload);
  };
  const reject = () => run(async () => {
    await api(`/rubric-nature/requests/${rejecting!.id}/reject`, { reason: rejecting!.reason.trim() });
    setRejecting(null); setVersion(v => v + 1); setNotice("Pedido recusado. Quem pediu vê o motivo no histórico de pedidos.");
  });
  const cancel = (id: string) => run(async () => {
    await api(`/rubric-nature/requests/${id}/cancel`, {}); setVersion(v => v + 1); setNotice("Pedido cancelado.");
  });
  const restore = (batch: Row) => {
    const next: Record<string, Draft> = { ...drafts };
    for (const change of batch.changes) if (byId[change.category_id] && change.before && "nature" in change.before) next[change.category_id] = pick(change.before);
    load(next); setReview(null);
    setNotice(`Configuração anterior a ${fullDate(batch.date)} carregada na tabela. Simule para ver o efeito antes de aplicar.`);
  };
  const exportConfig = () => {
    const payload = { format: FORMAT, version: 1, exported_at: new Date().toISOString(),
      rubrics: (data?.rubrics || []).map((r: Row) => ({ name: r.name, ...pick(r) })) };
    const link = document.createElement("a");
    link.href = URL.createObjectURL(new Blob([JSON.stringify(payload, null, 2)], { type: "application/json" }));
    link.download = "natureza-das-rubricas.json";
    link.click();
    URL.revokeObjectURL(link.href);
  };
  async function importConfig(chosen?: File | null) {
    if (!chosen) return;
    setError(""); setImportNote("");
    try {
      const parsed = JSON.parse(await chosen.text());
      if (parsed?.format !== FORMAT || !Array.isArray(parsed.rubrics)) throw new Error("O ficheiro não é uma configuração de natureza das rubricas exportada pela IAZELA.");
      const names: Record<string, Row> = Object.fromEntries((data?.rubrics || []).map((r: Row) => [fold(r.name), r]));
      const next: Record<string, Draft> = { ...drafts }, missing: string[] = [];
      let matched = 0;
      for (const item of parsed.rubrics) {
        const rubric = names[fold(String(item.name || ""))];
        if (!rubric) { missing.push(String(item.name)); continue; }
        const value = pick(item);
        if (!labels[value.nature] && value.nature) continue;
        matched += 1;
        if (!same(value, pick(rubric))) next[rubric.id] = value;
      }
      load(next); setReview(null);
      const changed = Object.keys(next).filter(id => byId[id] && !same(next[id], pick(byId[id]))).length;
      setImportNote(`${matched} rubrica(s) com o mesmo nome; ${changed} com configuração diferente carregada${changed ? " na tabela (simule antes de aplicar)" : ""}.`
        + (missing.length ? ` Sem correspondência nesta empresa: ${missing.join(", ")}.` : ""));
    } catch (e) { setError((e as Error).message); } finally { if (file.current) file.current.value = ""; }
  }

  if (!data) return error ? <Notice tone="error">{error}</Notice> : <p role="status">A carregar a natureza das rubricas…</p>;
  const requestList: Row[] = requests?.requests || [];
  const history: Row[] = data.history || [];
  return <div className="rn">
    <p className="rn-intro">Diga em que análises entra cada rubrica. Por exemplo, um pagamento de IVA ao Estado é uma saída de tesouraria e uma obrigação fiscal, mas não é um custo operacional.
      Nada muda nos documentos nem nos movimentos: só muda onde o valor aparece.</p>
    {error && <Notice tone="error">{error}</Notice>}
    {notice && <Notice tone="success">{notice}</Notice>}
    {!!requestList.length && <section className="rn-card" aria-label="Pedidos de aprovação">
      <h3><Inbox size={16} aria-hidden /> Pedidos de aprovação</h3>
      <ul className="rn-requests">{requestList.map(r => <li key={r.id}>
        <div className="rn-request-head"><strong>{r.title}</strong><StatusBadge tone={STATUS_TONE[r.status]} compact>{r.status_label}</StatusBadge></div>
        <small>Pedido por {r.requested_by} em {fullDate(r.created_at)}: «{r.reason}»{r.decided_at && r.status !== "pendente" ? ` · ${r.status_label} por ${r.decided_by} em ${fullDate(r.decided_at)}: «${r.decision_reason}»` : ""}</small>
        {r.status === "pendente" && <p className="rn-hint">Na simulação do pedido: {(r.summary.views || []).filter((v: Row) => v.difference_cents).map((v: Row) => `${v.label} ${signed(v.difference_cents)}`).join("; ") || "sem diferenças nos totais"}.
          {(r.summary.warnings || []).length ? ` Avisos: ${r.summary.warnings.join(" ")}` : ""}</p>}
        {r.status === "pendente" && <div className="rn-row-actions">
          {requests?.can_decide && <ActionButton type="button" className="primary" onClick={() => reviewRequest(r)} disabled={busy}>Rever e aprovar</ActionButton>}
          {requests?.can_decide && (rejecting && rejecting.id === r.id ? <div className="rn-inline rn-reject">
            <ReasonField label="Motivo da recusa" value={rejecting.reason} onChange={value => setRejecting({ id: r.id, reason: value })} options={REJECT_REASONS} />
            <ActionButton type="button" onClick={() => void reject()} disabled={busy || rejecting.reason.trim().length < 5}>Confirmar recusa</ActionButton>
            <ActionButton type="button" onClick={() => setRejecting(null)}>Cancelar</ActionButton>
          </div> : <ActionButton type="button" onClick={() => setRejecting({ id: r.id, reason: "" })}>Recusar</ActionButton>)}
          {r.mine && <ActionButton type="button" onClick={() => void cancel(r.id)} disabled={busy}>Cancelar pedido</ActionButton>}
        </div>}
      </li>)}</ul>
    </section>}
    {!!suggestions.length && <section className="rn-card" aria-label="Sugestões">
      <h3><Lightbulb size={16} aria-hidden /> Sugestões (só aplicadas com a sua aprovação)</h3>
      <ul className="rn-suggestions">{suggestions.map((r: Row) => <li key={r.id}>
        <div><strong>{r.name}</strong> → {labels[r.suggestion.nature]}<small>{r.suggestion.reason}</small></div>
        <ActionButton type="button" onClick={() => applySuggestion(r)} disabled={drafts[r.id] && same(drafts[r.id], { ...r.suggestion })}>Usar sugestão em {r.name}</ActionButton>
      </li>)}</ul>
    </section>}
    <FiscalPayments onRubricCreated={() => setVersion(v => v + 1)} review={reclassReview} onReviewDone={() => { setReclassReview(null); setVersion(v => v + 1); }} />
    <section className="rn-card" aria-label="Natureza das rubricas">
      <div className="rn-head">
        <h3>Natureza e análises</h3>
        <div className="rn-row-actions">
          <ActionButton type="button" onClick={exportConfig}><Download size={15} aria-hidden /> Exportar configuração</ActionButton>
          <ActionButton type="button" onClick={() => file.current?.click()}><Upload size={15} aria-hidden /> Importar de outra empresa</ActionButton>
          <input ref={file} type="file" accept="application/json,.json" hidden aria-label="Ficheiro da configuração a importar" onChange={e => void importConfig(e.target.files?.[0])} />
          <label className="checkbox"><input type="checkbox" checked={showInactive} onChange={e => setShowInactive(e.target.checked)} /> Mostrar rubricas inativas</label>
        </div>
      </div>
      {importNote && <Notice tone="info">{importNote}</Notice>}
      {review && <Notice tone="info">A rever o pedido de {review.requested_by}: {review.title}. Pode ajustar antes de simular; ao aplicar, o pedido fica aprovado.</Notice>}
      <div className="table-wrap"><table className="rn-grid" aria-label="Natureza e análises de cada rubrica">
        <thead><tr><th>Rubrica</th><th>Natureza</th>{FLAGS.map(f => <th key={f}>{SHORT[f]}</th>)}</tr></thead>
        <tbody>{rubrics.map((r: Row) => {
          const value = current(r), changed = !same(value, pick(r));
          return <tr key={r.id} data-changed={changed || undefined}>
            <td><strong>{r.name}</strong>{!r.active && <small>Inativa</small>}{changed && <StatusBadge tone="info" compact>{simulation ? "Alterada (simulada)" : "Alterada (por simular)"}</StatusBadge>}</td>
            <td><select aria-label={`Natureza de ${r.name}`} value={value.nature}
              onChange={e => edit(r, { ...(presets[e.target.value] as Draft), nature: e.target.value })}>
              {data.natures.map((n: Row) => <option key={n.key} value={n.key}>{n.label}</option>)}</select></td>
            {FLAGS.map(f => <td key={f} className="rn-flag"><input type="checkbox" aria-label={`${r.name}: ${SHORT[f]}`} checked={value[f]}
              onChange={e => edit(r, { ...value, [f]: e.target.checked })} /></td>)}
          </tr>;
        })}</tbody></table></div>
      <p className="rn-hint">Escolher a natureza propõe as análises habituais; pode ajustar cada uma. «Por definir» mantém o comportamento anterior (entra nos custos, na tesouraria e no orçamento).
        A previsão de liquidez, os extratos, a reconciliação e o Mapa de IVA não dependem desta configuração. A exportação leva a configuração para outra empresa (pelas rubricas com o mesmo nome).</p>
      <div className="rn-actions">
        <ActionButton type="button" onClick={() => { setDrafts({}); setSimulation(null); setReview(null); setImportNote(""); }} disabled={!changes.length || busy}>Descartar alterações</ActionButton>
        <ActionButton type="button" className="primary" onClick={() => void simulate()} disabled={!changes.length || busy}>
          Simular {changes.length} alteração(ões)</ActionButton>
      </div>
    </section>
    {simulation && <section className="rn-card" aria-label="Simulação">
      <h3><FlaskConical size={16} aria-hidden /> Simulação: {fullDate(simulation.start)} a {fullDate(simulation.end)}</h3>
      <ul className="rn-changes">{simulation.changes.map((c: Row) => <li key={c.category_id}><strong>{c.name}</strong>: {c.before.nature === c.after.nature ? `${labels[c.after.nature]} (natureza mantém-se)` : `${labels[c.before.nature]} → ${labels[c.after.nature]}`}
        {FLAGS.filter(f => c.before[f] !== c.after[f]).map(f => <span key={f} className="rn-chip">{c.after[f] ? "Entra em" : "Sai de"} {SHORT[f].toLowerCase()}</span>)}</li>)}</ul>
      <div className="table-wrap"><table className="rn-grid" aria-label="Antes e depois por vista">
        <thead><tr><th>Vista</th><th>Antes</th><th>Depois</th><th>Diferença</th></tr></thead>
        <tbody>{simulation.views.map((v: Row) => <tr key={v.key}><td>{v.label}</td><td>{formatEuro(v.before_cents)}</td><td>{formatEuro(v.after_cents)}</td>
          <td className={v.difference_cents ? "rn-diff" : ""}>{signed(v.difference_cents)}</td></tr>)}</tbody>
      </table></div>
      {!!simulation.warnings?.length && <div className="rn-warnings" role="note" aria-label="Avisos da simulação">
        <strong><AlertTriangle size={15} aria-hidden /> Confirme antes de aplicar</strong>
        <ul>{simulation.warnings.map((w: Row, i: number) => <li key={i}>{w.text}</li>)}</ul>
      </div>}
      <h4>{simulation.affected.count} registo(s) afetado(s){simulation.affected.count > simulation.affected.rows.length ? ` (os ${simulation.affected.rows.length} mais recentes)` : ""}</h4>
      {simulation.affected.rows.length ? <div className="table-wrap"><table className="rn-grid" aria-label="Registos afetados">
        <thead><tr><th>Data</th><th>Documento ou movimento</th><th>Entidade</th><th>Origem</th><th>Rubrica</th><th>Valor</th></tr></thead>
        <tbody>{simulation.affected.rows.map((r: Row) => <tr key={r.id}><td>{fullDate(r.date)}</td><td>{r.label}</td><td>{r.entity}</td><td>{r.source}</td>
          <td>{r.categories.join(", ")}</td><td>{formatEuro(r.amount_cents)}</td></tr>)}</tbody></table></div>
        : <p className="rn-hint">Nenhum documento ou movimento nos últimos 12 meses usa estas rubricas.</p>}
      <p className="rn-preserved"><ShieldCheck size={15} aria-hidden /> Não muda: {simulation.preserved.join("; ")}.</p>
      <div className="rn-apply">
        <ReasonField label={data.can_apply ? "Motivo da alteração" : "Motivo do pedido"} value={reason} onChange={setReason} options={NATURE_REASONS}
          placeholder="Ex.: separar pagamentos de IVA ao Estado dos custos operacionais" />
        {data.can_apply
          ? <ActionButton type="button" className="primary" onClick={() => void apply()} disabled={busy || reason.trim().length < 5}>{review ? "Aprovar e aplicar" : "Aplicar alterações"}</ActionButton>
          : <ActionButton type="button" className="primary" onClick={() => void ask()} disabled={busy || reason.trim().length < 5}>Pedir aprovação ao administrador</ActionButton>}
      </div>
      {!data.can_apply && <p className="rn-hint">Só um administrador aplica. O pedido guarda esta simulação; o administrador volta a simular antes de aprovar.</p>}
    </section>}
    {!!history.length && <details className="rn-card rn-history">
      <summary><History size={15} aria-hidden /> Histórico de alterações da natureza ({history.length})</summary>
      <ul className="rn-batches">{history.map(batch => <li key={batch.id}>
        <div className="rn-request-head"><strong>{fullDate(batch.date)} · {batch.user}</strong>
          <ActionButton type="button" onClick={() => restore(batch)}>Repor a configuração anterior a {fullDate(batch.date)}</ActionButton></div>
        <small>Motivo: «{batch.reason}»{batch.request_id ? " · aprovado a partir de um pedido" : ""}</small>
        <ul className="rn-changes">{batch.changes.map((c: Row) => <li key={c.category_id}>{c.name}: {c.before.nature === c.after.nature ? `${labels[c.after.nature] ?? c.after.nature} (natureza mantém-se)` : `${labels[c.before.nature] ?? c.before.nature} → ${labels[c.after.nature] ?? c.after.nature}`}
          {FLAGS.filter(f => c.before[f] !== c.after[f]).map(f => <span key={f} className="rn-chip">{c.after[f] ? "Entrou em" : "Saiu de"} {SHORT[f].toLowerCase()}</span>)}</li>)}</ul>
      </li>)}</ul>
    </details>}
  </div>;
}
