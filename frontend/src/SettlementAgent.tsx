import { useEffect, useRef, useState } from "react";
import { api, Field } from "./App";
import WintouchSettlementForm from "./WintouchSettlementForm";
import { StatusBadge } from "./components/BusinessUI";
import { ASSISTANTS } from "./components/AssistantIdentity";
type Row = Record<string, any>;
const money = (value: number) => new Intl.NumberFormat("pt-PT", { style: "currency", currency: "EUR" }).format(value / 100);
const today = () => new Intl.DateTimeFormat("en-CA", { timeZone: "Europe/Lisbon", year: "numeric", month: "2-digit", day: "2-digit" }).format(new Date());

export default function SettlementAgent({ boot, store, initialEntity, accounts, onClose, onUpdated }: { boot: Row; store: string; initialEntity: string; accounts: Row[]; onClose: () => void; onUpdated: () => Promise<void> }) {
  const dialog = useRef<HTMLDialogElement>(null);
  const [entity, setEntity] = useState(initialEntity), [entities, setEntities] = useState<string[]>([]);
  const [direction, setDirection] = useState(-1), [overdue, setOverdue] = useState(true), [amount, setAmount] = useState("");
  const [bank, setBank] = useState(""), [horizon, setHorizon] = useState("30"), [sustainability, setSustainability] = useState<Row | null>(null);
  const [proposal, setProposal] = useState<Row | null>(null), [lines, setLines] = useState<Row[]>([]);
  const [busy, setBusy] = useState(false), [error, setError] = useState(""), [notice, setNotice] = useState("");
  const [confirmed, setConfirmed] = useState(false), [paymentDate, setPaymentDate] = useState(today()), [reason, setReason] = useState("");
  const [erpLine, setErpLine] = useState<Row | null>(null);
  const completed = useRef(new Set<string>());
  useEffect(() => { if (!erpLine && !dialog.current?.open) dialog.current?.showModal(); }, [erpLine]);
  useEffect(() => {
    api("/liquidity/settlement-proposal", { entity: "", direction, overdue_only: overdue, store_id: accounts.find(a => a.key === bank)?.store_id || store }).then(r => setEntities(r.entities)).catch(e => setError(e.message));
  }, [direction, overdue, store, bank]);
  const selected = lines.filter(r => r.selected && !r.blocked);
  const total = selected.reduce((sum, r) => sum + r.proposed_cents, 0);
  const pending = selected.filter(r => !completed.current.has(r.key));
  const localValid = pending.filter(r => r.route === "local").every(r => r.target && r.category_id);
  function invalidate() { setProposal(null); setLines([]); setConfirmed(false); completed.current.clear(); setNotice(""); setSustainability(null); }
  async function propose() {
    setBusy(true); setError(""); setNotice("");
    try {
      const amountCents = amount ? Math.round(Number(amount.replace(",", ".")) * 100) : null;
      if (amount && (!Number.isFinite(amountCents) || amountCents! <= 0)) throw new Error("Indique um valor positivo.");
      const result = await api("/liquidity/settlement-proposal", { entity, direction, overdue_only: overdue, amount_cents: amountCents, store_id: accounts.find(a => a.key === bank)?.store_id || store });
      setProposal(result); setEntities(result.entities); setSustainability(null); setConfirmed(false); completed.current.clear();
      setLines(result.lines.map((r: Row) => ({ ...r, selected: !r.blocked, target: bank, category_id: boot.categories[0]?.id || "", request_key: crypto.randomUUID(), status: "Proposto" })));
    } catch (e) { setError((e as Error).message); } finally { setBusy(false); }
  }
  function change(key: string, field: string, value: any) { setLines(current => current.map(r => r.key === key ? { ...r, [field]: value } : r)); setConfirmed(false); setSustainability(null); }
  async function finish(key: string, message: string) {
    completed.current.add(key); setLines(current => current.map(r => r.key === key ? { ...r, status: "Confirmado" } : r));
    setNotice(message); setSustainability(null); await onUpdated();
  }
  async function validateBalance(manageBusy = true) {
    if (manageBusy) setBusy(true); setError("");
    try {
      const check = await api("/liquidity/settlement-sustainability", { target: bank, horizon_days: Number(horizon), reserved_cents: selected.filter(r => r.route === "erp" && r.direction === -1 && completed.current.has(r.key)).reduce((sum, r) => sum + r.proposed_cents, 0), lines: selected.filter(r => !completed.current.has(r.key)).map(r => ({ key: r.key, amount_cents: r.proposed_cents })) });
      setSustainability(check); return check.valid;
    } catch (e) { setError((e as Error).message); setSustainability(null); return false; } finally { if (manageBusy) setBusy(false); }
  }
  async function execute() {
    if (!confirmed || !localValid || reason.trim().length < 3 || !bank) return;
    if (!await validateBalance()) return;
    setBusy(true); setError("");
    try {
      for (const line of pending) {
        if (line.route === "erp") {
          const original = await api(`/liquidity/origin/${encodeURIComponent(line.key)}`);
          if (original.data.amount_cents < line.proposed_cents) throw new Error("O saldo ERP mudou. Gere novamente a proposta antes de enviar.");
          setErpLine({ ...original.data, key: line.key, id: line.key, remaining_cents: original.data.amount_cents, proposed_cents: line.proposed_cents, proposed_bank: accounts.find(a => a.key === bank)?.name });
          return;
        }
        if (!await validateBalance(false)) throw new Error("O saldo deixou de suportar os pagamentos pendentes.");
        await api(`/liquidity/settle/${encodeURIComponent(line.key)}`, { amount_cents: line.proposed_cents, target: line.target, category_id: line.category_id, payment_date: paymentDate, reason, request_key: line.request_key, confirmed: true });
        await finish(line.key, `Pagamento local confirmado: ${line.title} · ${money(line.proposed_cents)}.`);
      }
      setNotice("Todos os pagamentos selecionados foram confirmados.");
    } catch (e) { setError((e as Error).message + " Os pagamentos já confirmados ficam preservados. A repetição local usa a mesma referência."); } finally { setBusy(false); }
  }
  if (erpLine) return <WintouchSettlementForm row={erpLine} beforeSubmit={async () => { if (!await validateBalance(false)) throw new Error("O saldo do banco precisa de nova valida\u00e7\u00e3o antes do envio."); }} onCancel={() => setErpLine(null)} onDone={async result => {
    await finish(erpLine.key, result.message); setErpLine(null);
  }} />;
  return <dialog ref={dialog} className="doc-dialog liquidity-dialog" aria-label={`${ASSISTANTS.iris.name} · Proposta de liquidação`} onCancel={e => { e.preventDefault(); if (!busy) onClose(); }}>
    <div className="panel-title"><h2>{ASSISTANTS.iris.name} · Proposta de liquidação</h2><button aria-label="Fechar" disabled={busy} onClick={onClose}>×</button></div>
    <p>{ASSISTANTS.iris.mission}</p>
    <p>Propõe documentos reais por entidade, do vencimento mais antigo para o mais recente. Um valor indicado pode liquidar parcialmente o último documento.</p>
    {error && <p className="error" role="alert">{error}</p>}{notice && <p className="success" role="status">{notice}</p>}
    <form className="doc-form" onSubmit={e => { e.preventDefault(); propose(); }}><fieldset disabled={busy || completed.current.size > 0}>
      <Field label="Banco para a liquidação"><select required value={bank} onChange={e => { invalidate(); setBank(e.target.value); }}><option value="">Escolher banco</option>{accounts.filter(a => a.key.startsWith("bank:") && a.currency === "EUR" && (!store || a.store_id === store)).map(a => <option key={a.key} value={a.key}>{a.name}</option>)}</select></Field><Field label="Validar sustentabilidade nos próximos (dias)"><input type="number" min="1" max="365" required value={horizon} onChange={e => { invalidate(); setHorizon(e.target.value); }} /></Field>
      <Field label="Entidade da proposta"><select value={entity} onChange={e => { invalidate(); setEntity(e.target.value); }}><option value="">Todas as entidades</option>{entity && !entities.includes(entity) && <option value={entity}>{entity}</option>}{entities.map(name => <option key={name} value={name}>{name}</option>)}</select></Field>
      <Field label="Tipo de liquidação"><select value={direction} onChange={e => { invalidate(); setDirection(Number(e.target.value)); }}><option value="-1">Pagamentos a fornecedores</option><option value="1">Recebimentos de clientes</option></select></Field>
      <label className="doc-check"><input type="checkbox" checked={overdue} onChange={e => { invalidate(); setOverdue(e.target.checked); }} />Apenas documentos vencidos</label>
      <Field label="Valor total a liquidar (€), opcional"><input inputMode="decimal" value={amount} placeholder="Vazio: liquidar os documentos elegíveis" onChange={e => { invalidate(); setAmount(e.target.value); }} /></Field>
    </fieldset><button type="submit" disabled={busy || completed.current.size > 0}>Propor liquidação</button></form>
    {proposal && <><p>{proposal.rule}</p><div className="liquidity-selected-total"><span>{selected.length} documento(s)</span><span>Total proposto <strong>{money(total)}</strong></span>{proposal.unallocated_cents > 0 && <span>Valor sem documentos elegíveis <strong>{money(proposal.unallocated_cents)}</strong></span>}</div>
      <div className="table-wrap"><table><thead><tr>{["Selecionar", "Entidade / documento", "Vencimento", "Por liquidar", "Proposto", "Destino", "Conta / rubrica", "Estado"].map(label => <th key={label}>{label}</th>)}</tr></thead><tbody>{lines.map(line => <tr key={line.key}>
        <td><input type="checkbox" aria-label={`Selecionar ${line.title}`} checked={line.selected} disabled={busy || !!line.blocked || completed.current.has(line.key)} onChange={e => change(line.key, "selected", e.target.checked)} /></td><td>{line.title}<small>{line.entity}</small></td><td>{line.due_date?.split("-").reverse().join("/") || "—"}</td><td>{money(line.amount_cents)}</td><td>{money(line.proposed_cents)}</td><td>{line.route === "erp" ? "ERP · Wintouch" : "Local · GPGest"}</td>
        <td>{line.route === "local" && !line.blocked && <fieldset disabled={busy || completed.current.has(line.key)}><span>{accounts.find(a => a.key === bank)?.name || "Banco por selecionar"}</span><select aria-label={`Rubrica de ${line.title}`} value={line.category_id} onChange={e => change(line.key, "category_id", e.target.value)}><option value="">Escolher rubrica</option>{boot.categories.map((c: Row) => <option key={c.id} value={c.id}>{c.name}</option>)}</select></fieldset>}{line.route === "erp" && <small>Documento, série e meio confirmados no passo ERP.</small>}</td><td><StatusBadge tone={line.blocked ? "warning" : line.status === "Confirmado" ? "success" : "neutral"}>{line.blocked || line.status}</StatusBadge></td>
      </tr>)}</tbody></table>{!lines.length && <p>Sem documentos elegíveis para estes critérios.</p>}</div>
      {pending.length > 0 && <><button disabled={busy || !bank} onClick={() => validateBalance()}>Validar saldo do banco</button>{sustainability && <section className="panel" aria-label="Sustentabilidade do saldo"><StatusBadge tone={sustainability.valid ? "success" : "danger"}>{sustainability.message}</StatusBadge><div className="liquidity-selected-total">{[["Saldo utilizado", sustainability.balance_cents], ["Após esta liquidação", sustainability.after_cents], ["Outras saídas previstas", sustainability.other_outgoing_cents], ["Menor saldo previsto", sustainability.minimum_cents], ["Reserva mínima", sustainability.reserve_cents], ["Valor em falta", sustainability.shortfall_cents]].map(([label, value]) => <span key={String(label)}>{label}<strong>{value === null ? "Indisponível" : money(Number(value))}</strong></span>)}</div><p>{sustainability.assumption}</p><p>Saldo de {sustainability.balance_date} · Horizonte até {sustainability.end}{sustainability.estimated ? " · Saldo estimado" : ""}</p></section>}<Field label="Data dos pagamentos locais"><input type="date" max={today()} value={paymentDate} disabled={busy} onChange={e => { setPaymentDate(e.target.value); setConfirmed(false); }} /></Field><Field label="Justificação dos pagamentos"><textarea minLength={3} value={reason} disabled={busy} onChange={e => { setReason(e.target.value); setConfirmed(false); }} /></Field><p>Os pagamentos locais ficam por reconciliar com o banco. Pagamentos em caixa exigem uma folha aberta e numerário suficiente. Cada liquidação ERP exige as opções e a confirmação do conector; uma falha não cria um pagamento local.</p><label className="doc-check"><input type="checkbox" checked={confirmed} disabled={busy} onChange={e => setConfirmed(e.target.checked)} />Revi os documentos e valores. Confirmo os pagamentos locais realizados e pretendo preparar as liquidações ERP selecionadas.</label><button className="primary" disabled={busy || !confirmed || !localValid || reason.trim().length < 3 || !sustainability?.valid} onClick={execute}>{completed.current.size ? "Continuar pagamentos pendentes" : "Criar pagamentos propostos"}</button></>}
    </>}
  </dialog>;
}
