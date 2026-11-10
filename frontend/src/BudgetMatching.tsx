import { useEffect, useState } from "react";
import { CheckCheck, Link2, Undo2, X } from "lucide-react";
import { StatusBadge, type BusinessTone } from "./components/BusinessUI";
import { ActionButton, AppDialog, Notice } from "./components/InteractionUI";
import { api } from "./lib/api";
import { centsInput, euroToCents, formatEuro } from "./utils/format";

/**
 * Associação do realizado ao orçamento: propostas (rubrica, fornecedor, data, valor), confirmação individual
 * ou em lote (só as selecionadas), escolha de outra ocorrência ou valor parcial, rejeição e anulação.
 * Nada é associado sem confirmação; o orçamento aprovado não é alterado.
 */

type Row = Record<string, any>;
const STRENGTH: Record<string, [string, BusinessTone]> = {
  forte: ["Forte", "success"], provavel: ["Provável", "info"], possivel: ["Possível", "warning"], varias: ["Várias possibilidades", "warning"],
};
const fullDate = (iso?: string | null) => iso ? iso.slice(0, 10).split("-").reverse().join("/") : "—";
const keyOf = (p: Row) => `${p.source_kind}:${p.source_id}:${p.category_id}`;
const payload = (p: Row, c: Row, amount?: number) => ({ source_kind: p.source_kind, source_id: p.source_id, category_id: p.category_id, line_id: c.line_id,
  rule_date: c.rule_date, confidence: c.confidence, reasons: c.reasons, ...(amount ? { amount_cents: amount } : {}) });

export function Matching({ budgetId, onChanged }: { budgetId: string; onChanged: (message: string) => void }) {
  const [data, setData] = useState<Row | null>(null), [error, setError] = useState(""), [busy, setBusy] = useState(false);
  const [selected, setSelected] = useState<Set<string>>(new Set()), [choosing, setChoosing] = useState<Row | null>(null);
  const [version, setVersion] = useState(0);
  useEffect(() => {
    let active = true;
    setData(null); setError("");
    api(`/budgets/${encodeURIComponent(budgetId)}/matching`).then(r => { if (active) { setData(r); setSelected(new Set()); } }).catch(e => { if (active) setError((e as Error).message); });
    return () => { active = false; };
  }, [budgetId, version]);
  async function run(action: () => Promise<unknown>, message: string) {
    setBusy(true); setError("");
    try { await action(); setChoosing(null); setVersion(v => v + 1); onChanged(message); } catch (e) { setError((e as Error).message); } finally { setBusy(false); }
  }
  const base = `/budgets/${encodeURIComponent(budgetId)}`;
  const chosen = (data?.pending || []).filter((p: Row) => selected.has(keyOf(p)));
  if (!data) return <section className="bud-card" aria-label="Associação ao realizado">{error ? <Notice tone="error">{error}</Notice> : <p role="status">A procurar correspondências…</p>}</section>;
  return <section className="bud-card bud-matching" aria-label="Associação ao realizado">
    {error && <Notice tone="error" onDismiss={() => setError("")}>{error}</Notice>}
    {!data.editable && <Notice tone="info">Esta versão não está em vigor: consulte as associações na versão em vigor.</Notice>}
    <div className="bud-match-summary" role="group" aria-label="Resumo das associações">
      <span><b>{data.summary.pending}</b> por associar</span><span><b>{data.summary.strong}</b> fortes</span>
      <span><b>{data.summary.ambiguous}</b> com várias possibilidades</span>
      <span><b>{data.summary.unbudgeted}</b> sem previsão ({formatEuro(data.summary.unbudgeted_cents)})</span><span><b>{data.summary.linked}</b> associados</span>
    </div>
    {data.editable && data.pending.length > 0 && <div className="bud-batch" role="group" aria-label="Associar em lote">
      <ActionButton type="button" onClick={() => setSelected(new Set(data.pending.filter((p: Row) => p.strength === "forte").map(keyOf)))}><CheckCheck size={16} aria-hidden="true" />Selecionar as fortes</ActionButton>
      <ActionButton type="button" disabled={!selected.size} onClick={() => setSelected(new Set())}>Limpar seleção</ActionButton>
      <ActionButton type="button" className="primary" disabled={busy || !chosen.length}
        onClick={() => void run(() => api(`${base}/links/batch`, { items: chosen.map((p: Row) => payload(p, p.candidates[0])) }), `${chosen.length} associação(ões) confirmada(s).`)}>
        Associar {chosen.length} selecionada(s)</ActionButton>
    </div>}
    <h3>Propostas</h3>
    {!data.pending.length ? <p className="dash-muted">Nada por associar.</p> : <div className="table-wrap"><table className="bud-grid" aria-label="Propostas de associação">
      <thead><tr><th><span className="bud-sr">Selecionar</span></th><th>Documento</th><th className="numeric">Por associar</th><th>Despesa prevista</th><th>Confiança</th><th>Ações</th></tr></thead>
      <tbody>{data.pending.map((p: Row) => {
        const best = p.candidates[0];
        const [label, tone] = STRENGTH[p.strength];
        return <tr key={keyOf(p)}>
          <td>{data.editable && <input type="checkbox" aria-label={`Selecionar ${p.label}`} checked={selected.has(keyOf(p))}
            onChange={e => { const next = new Set(selected); if (e.target.checked) next.add(keyOf(p)); else next.delete(keyOf(p)); setSelected(next); }} />}</td>
          <td><strong>{p.supplier_name}</strong><small>{p.label} · {fullDate(p.date)} · {p.category_name} · {p.origin}</small></td>
          <td className="numeric">{formatEuro(p.remaining_cents)}{p.linked_cents > 0 && <small>de {formatEuro(p.cost_cents)}</small>}</td>
          <td><strong>{best.description}</strong><small>{fullDate(best.rule_date)} · previsto {formatEuro(best.expected_cents)}{best.occurrence_linked_cents ? ` · já associado ${formatEuro(best.occurrence_linked_cents)}` : ""}</small>
            <small>{best.reasons.join(" · ")}</small></td>
          <td><StatusBadge tone={tone} compact>{`${label} · ${best.confidence} %`}</StatusBadge></td>
          <td>{data.editable && <div className="bud-actions">
            <ActionButton type="button" disabled={busy} aria-label={`Associar ${p.label}`} onClick={() => void run(() => api(`${base}/links`, payload(p, best)), "Associação confirmada.")}><Link2 size={15} aria-hidden="true" />Associar</ActionButton>
            <ActionButton type="button" aria-label={`Escolher outra para ${p.label}`} onClick={() => setChoosing(p)}>Escolher…</ActionButton>
            <ActionButton type="button" disabled={busy} aria-label={`Rejeitar a proposta para ${p.label}`} onClick={() => void run(() => api(`${base}/links/reject`, payload(p, best)), "Proposta rejeitada.")}><X size={15} aria-hidden="true" />Rejeitar</ActionButton>
          </div>}</td></tr>;
      })}</tbody></table></div>}
    {data.unbudgeted.length > 0 && <><h3>Despesas sem previsão</h3><div className="table-wrap"><table className="bud-grid" aria-label="Despesas sem previsão">
      <thead><tr><th>Documento</th><th>Rubrica</th><th className="numeric">Valor</th><th>Motivo</th></tr></thead>
      <tbody>{data.unbudgeted.map((u: Row) => <tr key={keyOf(u)}><td><strong>{u.supplier_name}</strong><small>{u.label} · {fullDate(u.date)}</small></td><td>{u.category_name}</td>
        <td className="numeric">{formatEuro(u.remaining_cents)}</td><td>{u.reason}</td></tr>)}</tbody></table></div></>}
    <ul className="bud-notes">{data.notes.map((n: string) => <li key={n}>{n}</li>)}</ul>
    {choosing && <ChooseDialog item={choosing} busy={busy} onClose={() => setChoosing(null)}
      onConfirm={(candidate, amount) => void run(() => api(`${base}/links`, payload(choosing, candidate, amount)), "Associação confirmada.")} />}
  </section>;
}

function ChooseDialog({ item, busy, onClose, onConfirm }: { item: Row; busy: boolean; onClose: () => void; onConfirm: (candidate: Row, amount?: number) => void }) {
  const [index, setIndex] = useState(0), [amount, setAmount] = useState(centsInput(item.remaining_cents));
  const cents = euroToCents(amount);
  const invalid = cents == null || Number.isNaN(cents) || cents <= 0 || cents > item.remaining_cents;
  return <AppDialog className="bud-dialog" aria-label="Escolher despesa prevista" busy={busy} onRequestClose={onClose}>
    <h2>Escolher despesa prevista</h2>
    <p><strong>{item.supplier_name}</strong> · {item.label} · {fullDate(item.date)} · {item.category_name} · por associar {formatEuro(item.remaining_cents)}</p>
    <fieldset className="bud-choices"><legend>Ocorrência</legend>
      {item.candidates.map((c: Row, i: number) => <label key={c.line_id + c.rule_date} className="bud-choice">
        <input type="radio" name="bud-choice" checked={index === i} onChange={() => setIndex(i)} />
        <span><b>{c.description}</b> · {fullDate(c.rule_date)} · previsto {formatEuro(c.expected_cents)} · {c.confidence} %<small>{c.reasons.join(" · ")}</small></span>
      </label>)}
    </fieldset>
    <label>Valor a associar (€)<input inputMode="decimal" value={amount} onChange={e => setAmount(e.target.value)} /></label>
    <p className="bud-hint">Pode associar só parte do documento (por exemplo, uma fatura que cobre dois meses); o resto fica por associar.</p>
    <div className="ui-dialog-actions"><ActionButton type="button" onClick={onClose}>Cancelar</ActionButton>
      <ActionButton type="button" className="primary" disabled={busy || invalid} onClick={() => onConfirm(item.candidates[index], cents === item.remaining_cents ? undefined : cents!)}>Associar</ActionButton></div>
  </AppDialog>;
}

export function LineLinks({ lineId, editable, onChanged }: { lineId: string; editable: boolean; onChanged: () => void }) {
  const [links, setLinks] = useState<Row[] | null>(null), [error, setError] = useState(""), [version, setVersion] = useState(0);
  const [undoing, setUndoing] = useState<Row | null>(null), [reason, setReason] = useState("");
  useEffect(() => {
    let active = true;
    api(`/budget-lines/${encodeURIComponent(lineId)}/links`).then(r => { if (active) setLinks(r.links); }).catch(e => { if (active) setError((e as Error).message); });
    return () => { active = false; };
  }, [lineId, version]);
  async function undo() {
    try { await api(`/budget-links/${encodeURIComponent(undoing!.id)}/undo`, { reason: reason.trim() }); setUndoing(null); setReason(""); setVersion(v => v + 1); onChanged(); }
    catch (e) { setError((e as Error).message); }
  }
  const visible = (links || []).filter(l => l.status !== "rejeitada");
  return <section className="bud-history" aria-label="Documentos associados">
    <h3>Documentos e movimentos associados</h3>
    {error && <Notice tone="error">{error}</Notice>}
    {!links ? <p role="status">A carregar…</p> : !visible.length ? <p className="dash-muted">Ainda sem documentos associados. Use o separador «Associações».</p>
      : <div className="table-wrap"><table aria-label="Associações da despesa">
        <thead><tr><th>Ocorrência</th><th>Documento</th><th className="numeric">Valor</th><th>Pagamento</th><th>Estado</th><th>Ações</th></tr></thead>
        <tbody>{visible.map(l => <tr key={l.id}><td>{fullDate(l.rule_date)}</td><td>{l.supplier_name}<small>{l.label} · {l.origin}</small></td>
          <td className="numeric">{formatEuro(l.amount_cents)}</td>
          <td>{l.payment === "pago" ? "Pago" : l.payment === "parcial" ? "Parcial" : l.payment === "por_pagar" ? "Por pagar" : "—"}</td>
          <td><StatusBadge tone={l.status === "confirmada" ? "success" : "neutral"} compact>{l.status === "confirmada" ? "Confirmada" : "Anulada"}</StatusBadge>
            <small>{l.status === "anulada" ? l.undo_reason : `${l.decided_by || ""} · ${fullDate(l.decided_at)}${l.method === "revisao" ? " · passada da versão anterior" : ""}`}</small></td>
          <td>{editable && l.status === "confirmada" && <ActionButton type="button" onClick={() => setUndoing(l)} aria-label={`Anular a associação de ${l.label}`}><Undo2 size={15} aria-hidden="true" />Anular</ActionButton>}</td></tr>)}</tbody>
      </table></div>}
    {undoing && <AppDialog className="bud-dialog" aria-label="Anular associação" onRequestClose={() => setUndoing(null)}>
      <h2>Anular associação</h2>
      <p>{undoing.supplier_name} · {undoing.label} · {formatEuro(undoing.amount_cents)}. O documento volta a ficar por associar; a anulação fica no histórico.</p>
      <label>Motivo<textarea rows={3} value={reason} maxLength={2000} onChange={e => setReason(e.target.value)} /></label>
      <div className="ui-dialog-actions"><ActionButton type="button" onClick={() => setUndoing(null)}>Cancelar</ActionButton>
        <ActionButton type="button" className="primary" disabled={reason.trim().length < 3} onClick={() => void undo()}>Anular associação</ActionButton></div>
    </AppDialog>}
  </section>;
}
