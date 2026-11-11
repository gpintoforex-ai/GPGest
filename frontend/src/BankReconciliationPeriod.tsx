import { useEffect, useState } from "react";
import { Download, LockKeyhole, Plus, Settings, Trash2, UnlockKeyhole } from "lucide-react";
import { centsInput, euroToCents } from "./utils/format";
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
      <RulesForm rules={rules} onChange={setRules} />
      <p>As regras geram sugestões. A confirmação financeira e qualquer lançamento de comissão continuam a exigir aprovação explícita; um movimento de valor parecido nunca marca uma fatura como paga.</p>
      <ActionButton disabled={busy || !!rulesProblem(rules)} onClick={() => act(async () => {
        await api(`/reconciliation/accounts/${account}/rules`, rulesBody(rules), { method: "PUT" }); setVersion(v => v + 1); setRules(null);
        window.dispatchEvent(new Event("iazela-reconciliation-rules-changed"));
      })}>Guardar regras</ActionButton>
      {rulesProblem(rules) && <small className="reconciliation-conflict">{rulesProblem(rules)}</small>}
    </AppDialog>}
  </RecordGroup>;
}

/** Valor em euros escrito pelo utilizador (texto) ou, à primeira, o valor guardado em cêntimos. */
const euroText = (text: string | undefined, cents: number | null | undefined) => text ?? (cents == null ? "" : centsInput(cents));
const integer = (value: unknown, min: number, max: number) => { const n = Number(value); return Number.isInteger(n) && n >= min && n <= max; };

export function rulesProblem(rules: Row): string {
  if (!integer(rules.date_tolerance_days, 0, 90)) return "A tolerância de datas vai de 0 a 90 dias.";
  const cents = euroToCents(euroText(rules.amount_text, rules.amount_tolerance_cents));
  if (cents != null && (Number.isNaN(cents) || cents > 10000)) return "A tolerância de valor vai até 100,00 €.";
  const percent = Number(String(rules.amount_tolerance_percent ?? "0").replace(",", "."));
  if (!(percent >= 0 && percent <= 10)) return "A tolerância percentual vai de 0 a 10 %.";
  if (!integer(rules.min_score, 30, 95) || !integer(rules.safe_score, 50, 100) || !integer(rules.safe_gap, 0, 60)) return "Reveja os limites de confiança.";
  if (Number(rules.safe_score) < Number(rules.min_score)) return "A confiança para aceitar em lote não pode ser inferior à confiança mínima.";
  for (const s of rules.suppliers || []) {
    if ((s.match || "").trim().length < 3) return "Cada exceção por fornecedor precisa de pelo menos 3 letras.";
    if (s.date_tolerance_days !== "" && s.date_tolerance_days != null && !integer(s.date_tolerance_days, 0, 366)) return "A tolerância de datas do fornecedor vai de 0 a 366 dias.";
    const own = euroToCents(euroText(s.amount_text, s.amount_tolerance_cents));
    if (own != null && (Number.isNaN(own) || own > 10000)) return "A tolerância de valor do fornecedor vai até 100,00 €.";
  }
  return "";
}

export function rulesBody(rules: Row) {
  return {
    date_tolerance_days: Number(rules.date_tolerance_days), grouped_proposals: !!rules.grouped_proposals,
    amount_tolerance_cents: euroToCents(euroText(rules.amount_text, rules.amount_tolerance_cents)) || 0,
    amount_tolerance_percent: String(rules.amount_tolerance_percent ?? "0").replace(",", ".") || "0",
    min_score: Number(rules.min_score), safe_score: Number(rules.safe_score), safe_gap: Number(rules.safe_gap),
    suppliers: (rules.suppliers || []).map((s: Row) => ({
      match: s.match.trim(), note: s.note || "",
      date_tolerance_days: s.date_tolerance_days === "" || s.date_tolerance_days == null ? null : Number(s.date_tolerance_days),
      amount_tolerance_cents: euroToCents(euroText(s.amount_text, s.amount_tolerance_cents)),
    })),
  };
}

function RulesForm({ rules, onChange }: { rules: Row; onChange: (rules: Row) => void }) {
  const set = (changes: Row) => onChange({ ...rules, ...changes });
  const suppliers: Row[] = rules.suppliers || [];
  const setSupplier = (index: number, changes: Row) => set({ suppliers: suppliers.map((s, i) => i === index ? { ...s, ...changes } : s) });
  return <div className="reconciliation-rules">
    <fieldset><legend>Datas e valores</legend>
      <Field label="Tolerância de datas (dias)"><input type="number" min="0" max="90" step="1" value={rules.date_tolerance_days} onChange={e => set({ date_tolerance_days: e.target.value })} /></Field>
      <Field label="Tolerância de valor (€)"><input inputMode="decimal" value={euroText(rules.amount_text, rules.amount_tolerance_cents)} onChange={e => set({ amount_text: e.target.value })} /></Field>
      <Field label="Tolerância de valor (%)"><input inputMode="decimal" value={String(rules.amount_tolerance_percent ?? "0").replace(".", ",")} onChange={e => set({ amount_tolerance_percent: e.target.value })} /></Field>
      <small>Usa-se o maior dos dois limites. Serve para comissões e arredondamentos: a proposta é sempre do valor menor e a diferença fica visível.</small>
      <label className="checkbox"><input type="checkbox" checked={rules.grouped_proposals} onChange={e => set({ grouped_proposals: e.target.checked })} />Gerar propostas de agrupamento</label>
    </fieldset>
    <fieldset><legend>Confiança</legend>
      <Field label="Confiança mínima para propor (30–95)"><input type="number" min="30" max="95" value={rules.min_score} onChange={e => set({ min_score: e.target.value })} /></Field>
      <Field label="Confiança para aceitar em lote (50–100)"><input type="number" min="50" max="100" value={rules.safe_score} onChange={e => set({ safe_score: e.target.value })} /></Field>
      <Field label="Distância mínima à alternativa seguinte (pontos)"><input type="number" min="0" max="60" value={rules.safe_gap} onChange={e => set({ safe_gap: e.target.value })} /></Field>
    </fieldset>
    <fieldset><legend>Exceções por fornecedor</legend>
      <small>Por exemplo, débitos diretos pagos 30 a 40 dias depois da fatura. Aplica-se quando o nome ou a referência do registo contém o texto indicado.</small>
      {suppliers.map((s, index) => <div className="reconciliation-rule-row" key={index}>
        <Field label="Fornecedor (nome ou referência)"><input value={s.match || ""} maxLength={80} onChange={e => setSupplier(index, { match: e.target.value })} /></Field>
        <Field label="Datas (dias)"><input type="number" min="0" max="366" value={s.date_tolerance_days ?? ""} placeholder="Da conta" onChange={e => setSupplier(index, { date_tolerance_days: e.target.value })} /></Field>
        <Field label="Valor (€)"><input inputMode="decimal" value={euroText(s.amount_text, s.amount_tolerance_cents)} placeholder="Da conta" onChange={e => setSupplier(index, { amount_text: e.target.value })} /></Field>
        <Field label="Nota"><input value={s.note || ""} maxLength={200} onChange={e => setSupplier(index, { note: e.target.value })} /></Field>
        <ActionButton type="button" aria-label={`Remover a exceção ${s.match || ""}`} onClick={() => set({ suppliers: suppliers.filter((_, i) => i !== index) })}><Trash2 size={15} aria-hidden="true" /></ActionButton>
      </div>)}
      <ActionButton type="button" onClick={() => set({ suppliers: [...suppliers, { match: "", date_tolerance_days: "", amount_tolerance_cents: null, note: "" }] })}><Plus size={15} aria-hidden="true" />Acrescentar exceção</ActionButton>
    </fieldset>
  </div>;
}
