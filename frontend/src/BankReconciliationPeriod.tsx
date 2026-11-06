import { useEffect, useState } from "react";
import { Download, LockKeyhole, Settings, UnlockKeyhole } from "lucide-react";
import { api } from "./lib/api";
import { ActionButton, ActionLink, AppDialog, Notice, confirmAction } from "./components/InteractionUI";
import { Field } from "./components/Form";
import { RecordGroup } from "./components/RecordSheet";

type Row = Record<string, any>;
const money = (n: number | null | undefined, currency: string) => n == null ? "Não disponível" : new Intl.NumberFormat("pt-PT", { style: "currency", currency }).format(n / 100);

export default function BankReconciliationPeriod({ account, from, to, revision, manager, admin, onSummary }: {
  account: string; from: string; to: string; revision: unknown; manager: boolean; admin: boolean; onSummary?: (data: Row | null) => void;
}) {
  const [data, setData] = useState<Row | null>(null), [error, setError] = useState(""), [busy, setBusy] = useState(false);
  const [reason, setReason] = useState(""), [exception, setException] = useState(false), [version, setVersion] = useState(0);
  const [rules, setRules] = useState<Row | null>(null);
  const [reportOrder, setReportOrder] = useState("date:asc"), [auditAnnex, setAuditAnnex] = useState(false);
  useEffect(() => {
    let current = true;
    setData(null); setError(""); setReason(""); setException(false); setRules(null);
    onSummary?.(null);
    if (account && from && to) api(`/reconciliation/accounts/${encodeURIComponent(account)}/period?${new URLSearchParams({ start: from, end: to })}`)
      .then(next => { if (current) { setData(next); onSummary?.(next); } }).catch(e => { if (current) setError(e.message); });
    return () => { current = false; };
  }, [account, from, to, revision, version]);
  async function act(action: () => Promise<void>) {
    if (busy) return;
    setBusy(true); setError("");
    try { await action(); } catch (e) { setError((e as Error).message); } finally { setBusy(false); }
  }
  if (!account) return <Notice>Selecione uma conta para consultar os saldos, regras e fecho do período. As moedas não são somadas entre contas.</Notice>;
  const base = `/api/reconciliation/accounts/${encodeURIComponent(account)}/report?${new URLSearchParams({ start: from, end: to, sort_by: reportOrder.split(":")[0], descending: String(reportOrder.endsWith(":desc")), audit: String(auditAnnex) })}`;
  const activeClose = data?.closings?.find((c: Row) => !c.reopened_at);
  return <RecordGroup title="Fecho e relatório da reconciliação" icon={LockKeyhole}>
    {error && <Notice tone="error">{error}</Notice>}
    {!data && !error && <p role="status">A consultar saldos e fechos…</p>}
    {data && <>
      <dl className="reconciliation-period-values">{[
        ["Saldo inicial inferido do extrato", data.statement_opening_cents], ["Entradas", data.statement_incoming_cents],
        ["Saídas", data.statement_outgoing_cents], ["Saldo final do extrato", data.bank_balance_cents],
        ["Saldo pelos registos da aplicação", data.book_balance_cents], ["Diferenças justificadas", data.justified_difference_cents],
        ["Diferenças por resolver", data.unresolved_difference_cents],
      ].map(([label, amount]) => <div key={String(label)}><dt>{label}</dt><dd>{money(amount as number | null, data.currency)}</dd></div>)}</dl>
      <p>{data.pending_bank?.count ?? "—"} movimentos bancários pendentes · {data.pending_application?.count ?? "—"} registos da aplicação em trânsito.</p>
      <p className="footnote">O saldo inicial do extrato é inferido apenas quando as linhas importadas têm saldos contínuos. O saldo da aplicação usa os registos com conta identificada e a referência inicial configurada. Não representa um saldo contabilístico oficial do ERP. Os saldos só são comparados na mesma conta, moeda e data.</p>
      {activeClose ? <Notice tone={activeClose.stale ? "warning" : "success"}>{activeClose.stale ? "Os dados mudaram após o fecho. O relatório original mantém-se preservado; reabra e confira o período." : "Período fechado e registado na auditoria."}</Notice> : data.closing_blockers?.length > 0 && <Notice tone="warning">{data.closing_blockers.join(" ")}</Notice>}
      <div className="reconciliation-report-options"><Field label="Ordenar relatório PDF"><select value={reportOrder} onChange={e => setReportOrder(e.target.value)}><option value="date:asc">Data - mais antigos primeiro</option><option value="date:desc">Data - mais recentes primeiro</option><option value="amount:desc">Valor absoluto - maiores primeiro</option><option value="amount:asc">Valor absoluto - menores primeiro</option></select></Field><label className="checkbox"><input type="checkbox" checked={auditAnnex} onChange={e => setAuditAnnex(e.target.checked)} />Incluir anexo técnico no PDF</label></div>
      <div className="actions">
        <ActionLink href={base + "&format=pdf"} target="_blank" rel="noopener noreferrer"><Download size={16} />PDF de consulta</ActionLink>
        <ActionLink href={base + "&format=csv"}><Download size={16} />CSV de consulta</ActionLink>
        {manager && <ActionButton disabled={busy} onClick={() => act(async () => setRules(await api(`/reconciliation/accounts/${account}/rules`)))}><Settings size={16} />Regras de correspondência</ActionButton>}
      </div>
      {manager && <>
        <Field label={activeClose ? "Motivo da reabertura" : "Observações do fecho"}><textarea maxLength={2000} value={reason} disabled={busy} onChange={e => setReason(e.target.value)} /></Field>
        {!activeClose && admin && data.closing_blockers?.length > 0 && <label className="checkbox"><input type="checkbox" checked={exception} disabled={busy} onChange={e => setException(e.target.checked)} />Autorizo o fecho excecional com pendências, com justificação detalhada na auditoria.</label>}
        {activeClose ? <ActionButton disabled={busy || reason.trim().length < 3} onClick={() => act(async () => {
          if (!await confirmAction({ title: "Reabrir reconciliação?", message: "A reabertura permite corrigir associações neste período. O fecho e o motivo ficam no histórico.", confirmLabel: "Reabrir período" })) return;
          await api(`/reconciliation/accounts/${account}/periods/${activeClose.id}/reopen`, { reason }); setVersion(v => v + 1);
        })}><UnlockKeyhole size={16} />Reabrir período</ActionButton> : <ActionButton variant="primary" disabled={busy || !data.comparable || (data.closing_blockers?.length > 0 && !(admin && exception && reason.trim().length >= 10))} onClick={() => act(async () => {
          if (!await confirmAction({ title: "Fechar reconciliação bancária?", message: `Conta: ${data.name}. Período: ${from} a ${to}. O resumo ficará registado e as associações deste período ficarão protegidas.${exception ? " Será registada uma exceção autorizada com pendências." : ""}`, confirmLabel: "Confirmar fecho" })) return;
          await api(`/reconciliation/accounts/${account}/close`, { start: from, end: to, signature: data.signature, confirmed: true, authorized_exception: exception, reason }); setVersion(v => v + 1);
        })}><LockKeyhole size={16} />Fechar período</ActionButton>}
      </>}
      {data.closings?.length > 0 && <details><summary>Histórico de fechos ({data.closings.length})</summary>{data.closings.map((c: Row) => <div className="setting-row" key={c.id}><div><strong>{c.reopened_at ? "Reaberto" : c.stale ? "Dados alterados após o fecho" : "Fechado"}</strong><p>{new Date(c.created_at).toLocaleString("pt-PT")} · {c.created_by} · {c.reason}</p>{c.reopened_at && <p>Reaberto por {c.reopened_by || "utilizador registado"} / {c.reopen_reason}</p>}<ActionLink href={base + `&format=pdf&closing_id=${encodeURIComponent(c.id)}`} target="_blank" rel="noopener noreferrer">PDF do fecho preservado</ActionLink></div></div>)}</details>}
    </>}
    {rules && <AppDialog aria-label="Regras de correspondência" busy={busy} onRequestClose={() => setRules(null)}><h2>Regras de correspondência</h2>{error && <Notice tone="error">{error}</Notice>}
      <Field label="Tolerância de datas (dias)"><input type="number" min="0" max="90" step="1" value={rules.date_tolerance_days} onChange={e => setRules({ ...rules, date_tolerance_days: e.target.value })} /></Field>
      <label className="checkbox"><input type="checkbox" checked={rules.grouped_proposals} onChange={e => setRules({ ...rules, grouped_proposals: e.target.checked })} />Gerar propostas de agrupamento</label>
      <p>As regras geram sugestões. A confirmação financeira e qualquer lançamento de comissão continuam a exigir aprovação explícita.</p>
      <ActionButton disabled={busy || !Number.isInteger(Number(rules.date_tolerance_days)) || Number(rules.date_tolerance_days) < 0 || Number(rules.date_tolerance_days) > 90} onClick={() => act(async () => {
        await api(`/reconciliation/accounts/${account}/rules`, { date_tolerance_days: Number(rules.date_tolerance_days), grouped_proposals: rules.grouped_proposals }, { method: "PUT" }); setVersion(v => v + 1); setRules(null);
        window.dispatchEvent(new Event("iazela-reconciliation-rules-changed"));
      })}>Guardar regras</ActionButton>
    </AppDialog>}
  </RecordGroup>;
}
