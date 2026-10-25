import MovementIdentity from "./components/MovementIdentity";
import { ActionLink } from "./components/InteractionUI";
import { ActionButton, AppDialog, ManagedForm, Notice } from "./components/InteractionUI";
import { ScanButton } from "./DocumentScanner";
import { useEffect, useRef, useState } from "react";
import { api } from "./lib/api";
import { Field } from "./components/Form";
import RecordList, { calendarDay } from "./RecordList";
import { MetricCard } from "./components/BusinessUI";
import "./Deposits.css";

type Row = Record<string, any>;
// «Entregue» = registado no banco e ainda não reconciliado com o extrato.
const status: Record<string, string> = { rascunho: "Rascunho", preparado: "Preparado", em_transito: "Em trânsito", entregue: "Por reconciliar", parcialmente_reconciliado: "Parcialmente reconciliado", reconciliado: "Reconciliado", cancelado: "Cancelado" };
const pending = ["rascunho", "preparado", "em_transito"];
const fmt = (value: string | number) => new Intl.NumberFormat("pt-PT", { style: "currency", currency: "EUR" }).format(Number(value));
const today = () => calendarDay(new Date().toISOString());
const dateText = (value?: string) => value ? calendarDay(value).split("-").reverse().join("/") : "—";
const totalCents = (value: string) => Math.round(Number(value.replace(",", ".")) * 100) || 0;

export default function Deposits({ boot, store, reload }: { boot: Row; store: string; reload: () => Promise<void> }) {
  const [rows, setRows] = useState<Row[]>([]), [selected, setSelected] = useState<Row | null>(null);
  const [modal, setModal] = useState(""), [tab, setTab] = useState("Dados gerais"), [error, setError] = useState(""), [notice, setNotice] = useState(""), [busy, setBusy] = useState(false);
  const [draft, setDraft] = useState<Row>({}), [candidates, setCandidates] = useState<Row[]>([]);
  const [command, setCommand] = useState(""), [action, setAction] = useState<Row>({}), [preview, setPreview] = useState("");
  const [filter, setFilter] = useState<Row>({ register: "", bank: "", type: "", status: "" });
  const dialog = useRef<HTMLDialogElement>(null), intent = useRef<"register" | "draft">("register");
  const [cashBalance, setCashBalance] = useState<Row | null>(null);
  useEffect(() => {
    let active = true;
    setCashBalance(null);
    if (modal !== "edit" || !draft.register_id || !draft.preparation_date) return;
    api(`/registers/${draft.register_id}/deposit-balance?selected_date=${draft.preparation_date}${selected ? `&exclude_deposit=${selected.id}` : ""}`).then(result => {
      if (!active) return;
      setCashBalance(result);
      if (result.available) setDraft(previous => ({ ...previous, cash_amount: selected && previous.register_id === selected.register_id && previous.preparation_date === selected.preparation_date ? previous.cash_amount : String(Math.max(0, Number(result.cash_amount))), cash_mode: "total", cheques: result.cheques || [] }));
    }).catch(e => { if (active) setCashBalance({ available: false, message: e.message }); });
    return () => { active = false; };
  }, [modal, draft.register_id, draft.preparation_date, selected?.id]);
  const write = boot.user.role !== "consulta", manager = ["administrador", "gestor"].includes(boot.user.role);
  const load = async () => setRows(await api("/deposits"));
  useEffect(() => { load().catch(e => setError(e.message)); }, []);
  useEffect(() => { if (modal && !dialog.current?.open) dialog.current?.showModal(); }, [modal]);
  async function run(task: () => Promise<void>) { setBusy(true); setError(""); setNotice(""); try { await task(); } catch (e) { setError((e as Error).message); } finally { setBusy(false); } }
  async function refresh(id: string) { const [saved] = await Promise.all([api(`/deposits/${id}`), load(), reload()]); setSelected(saved); window.dispatchEvent(new CustomEvent("gpgest-sales-updated", { detail: { appRefreshed: true } })); }
  function edit(row?: Row) {

    setSelected(row || null); setError(""); setNotice(""); setCommand("");
    setDraft(row ? { ...row, request_key: row.request_key, cash_mode: row.details.cash_mode, counts: row.details.counts, cheques: row.cheques.filter((c: Row) => c.active).map((c: Row) => ({ number: c.number, bank: c.bank, amount: c.amount, cheque_date: c.cheque_date, issuer: c.issuer, receipt_id: c.receipt_id, source_receipt: c.source_receipt, notes: c.notes })) } : { register_id: "", account_id: "", preparation_date: today(), cash_mode: "total", cash_amount: "0", counts: {}, cheques: [], notes: "", reference: "", request_key: crypto.randomUUID() });
    setModal("edit");
  }
  function start(actionName: string) { setCommand(actionName); setAction({ action_date: today(), reason: "", request_key: crypto.randomUUID(), amount: "0", transaction_id: "", returned: false, allow_insufficient: false, cheque_id: null }); setError(""); if (["reconcile", "fee", "cheque-return"].includes(actionName)) run(async () => setCandidates(await api(`/deposits/${selected!.id}/candidates`))); }
  const cash = draft.cash_mode === "denominations" ? Object.entries(draft.counts || {}).reduce((sum, [key, value]) => sum + Number(key) * Number(value), 0) : totalCents(draft.cash_amount || "0");
  const chequeTotal = (draft.cheques || []).reduce((sum: number, cheque: Row) => sum + totalCents(cheque.amount), 0);
  const register = boot.registers.find((r: Row) => r.id === draft.register_id);
  const filtered = rows.filter(r => (!store || r.store_id === store) && (!filter.register || r.register_id === filter.register) && (!filter.bank || r.account_id === filter.bank) && (!filter.type || r.type === filter.type) && (!filter.status || r.status === filter.status));
  const visible = rows.filter(r => !store || r.store_id === store);
  const close = () => { if (!busy) { setModal(""); setCommand(""); setPreview(""); } };
  return <>
    <div className="page-intro"><div><h1>Depósitos bancários</h1><p>Registe o depósito feito no banco: sai da caixa e entra na conta. Mais tarde reconcilie-o com o extrato em Bancos › Reconciliação.</p></div>{write && <ActionButton className="primary" onClick={() => edit()}>Registar depósito</ActionButton>}</div>
    {error && !modal && <Notice className="error" role="alert">{error}</Notice>}{notice && !modal && <Notice onDismiss={() => setNotice("")} className="success" role="status">{notice}</Notice>}
    <div className="deposit-metrics">{[["Por registar no banco", pending, "Rascunhos de depósito"], ["Por reconciliar", ["entregue", "parcialmente_reconciliado"], "Aguardam o crédito no extrato"]].map(([label, values, description]) => <MetricCard key={String(label)} label={String(label)} value={visible.filter(r => (values as string[]).includes(r.status)).length} description={String(description)} />)}<MetricCard label="Sem comprovativo validado" value={visible.filter(r => r.status !== "cancelado" && r.proof_status !== "validado").length} description="Pendências documentais" tone="warning" /></div>
    <section className="panel"><div className="deposit-filters filter-bar">
      <Field label="Caixa"><select value={filter.register} onChange={e => setFilter({ ...filter, register: e.target.value })}><option value="">Todas</option>{boot.registers.map((r: Row) => <option key={r.id} value={r.id}>{r.name}</option>)}</select></Field>
      <Field label="Banco e conta"><select value={filter.bank} onChange={e => setFilter({ ...filter, bank: e.target.value })}><option value="">Todos</option>{boot.accounts.map((a: Row) => <option key={a.id} value={a.id}>{a.bank} · {a.name}</option>)}</select></Field>
      <Field label="Tipo"><select value={filter.type} onChange={e => setFilter({ ...filter, type: e.target.value })}><option value="">Todos</option><option value="numerario">Numerário</option><option value="cheques">Cheques</option><option value="misto">Misto</option></select></Field>
      <Field label="Estado"><select value={filter.status} onChange={e => setFilter({ ...filter, status: e.target.value })}><option value="">Todos</option>{Object.entries(status).map(([key, label]) => <option key={key} value={key}>{label}</option>)}</select></Field>
    </div><RecordList rows={filtered} dateField="preparation_date" resetKey={JSON.stringify(filter) + store}>{list => <div className="table-wrap"><table><thead><tr>{["Número", "Preparação / saída / entrega", "Caixa", "Banco e conta", "Total", "Responsável", "Estado", "Comprovativo", "Ações"].map(label => <th key={label}>{label}</th>)}</tr></thead><tbody>{list.map(r => <tr key={r.id}><td><MovementIdentity>{r.number}</MovementIdentity></td><td>{dateText(r.preparation_date)}<br />{dateText(r.exit_date)} / {dateText(r.delivery_date)}</td><td>{r.register_name}</td><td>{r.bank} · {r.account_name}</td><td>{fmt(r.total)}</td><td>{r.responsible}</td><td><span className={`status-badge deposit-status-${r.status}`}>{status[r.status]}</span></td><td>{r.proof_status === "validado" ? "Validado" : r.proof_status === "em_falta" ? "Em falta" : "Por validar"}</td><td><ActionButton onClick={() => run(async () => { setSelected(await api(`/deposits/${r.id}`)); setTab("Dados gerais"); setCommand(""); setModal("detail"); })}>Abrir</ActionButton></td></tr>)}</tbody></table>{!list.length && <p>Não existem depósitos para estes filtros.</p>}</div>}</RecordList></section>
    {modal && <AppDialog busy={busy} className="doc-dialog deposit-dialog" ref={dialog} aria-label="Depósito bancário" dirty={modal === "detail" ? !!command : undefined} onRequestClose={() => {  close(); }}><div className="panel-title"><h2>{modal === "edit" ? selected ? "Editar depósito" : "Novo depósito" : <MovementIdentity>{selected?.number}</MovementIdentity>}</h2></div>
      {error && <Notice className="error" role="alert">{error}</Notice>}{notice && <Notice onDismiss={() => setNotice("")} className="success" role="status">{notice}</Notice>}
      {modal === "edit" ? <ManagedForm className="doc-form" onSubmit={e => { e.preventDefault(); run(async () => {
        const body = { from_real_movements: true, use_cash_amount: true, register_id: draft.register_id, account_id: draft.account_id, preparation_date: draft.preparation_date, cash_mode: "total", cash_amount: draft.cash_amount, counts: draft.counts, cheques: draft.cheques, reference: draft.reference, notes: draft.notes, request_key: draft.request_key };
        const result = await api(selected ? `/deposits/${selected.id}/save` : "/deposits", body);
        if (intent.current === "register") {
          // Saída da caixa e entrada na conta numa só operação; se falhar (ex.: saldo), fica em rascunho.
          try { await api(`/deposits/${result.id}/register`, { request_key: crypto.randomUUID(), action_date: draft.preparation_date }); }
          catch (problem) { await refresh(result.id); setModal("detail"); setTab("Dados gerais"); throw new Error(`Guardado em rascunho, mas não foi registado: ${(problem as Error).message}`); }
          await refresh(result.id); setModal("detail"); setTab("Dados gerais");
          setNotice("Depósito registado: saiu da caixa e entrou na conta bancária. Reconcilie-o com o extrato em Bancos › Reconciliação.");
          return;
        }
        await refresh(result.id); setModal("detail"); setTab("Dados gerais"); setNotice("Depósito guardado em rascunho. Os saldos não foram alterados.");
      }); }}>
        <Field label="Caixa de origem"><select required value={draft.register_id} onChange={e => { setDraft({ ...draft, register_id: e.target.value, account_id: "", cash_amount: selected ? draft.cash_amount : "0" }); }}><option value="">Escolher caixa</option>{boot.registers.filter((r: Row) => !store || r.store_id === store).map((r: Row) => <option key={r.id} value={r.id}>{r.name}</option>)}</select></Field>
        <Field label="Conta bancária de destino"><select required value={draft.account_id} onChange={e => setDraft({ ...draft, account_id: e.target.value })}><option value="">Escolher conta</option>{boot.accounts.filter((a: Row) => a.store_id === register?.store_id).map((a: Row) => <option key={a.id} value={a.id}>{a.bank} · {a.name}</option>)}</select></Field>
        <Field label="Data do depósito no banco"><input type="date" required max={today()} value={draft.preparation_date} onChange={e => { setDraft({ ...draft, preparation_date: e.target.value }); }} /></Field>
        <Field label="Referência bancária"><input maxLength={200} value={draft.reference} onChange={e => setDraft({ ...draft, reference: e.target.value })} /></Field>
        <h3>Valores provenientes dos movimentos reais</h3>
        {draft.register_id && <div className="deposit-total" role="status">{cashBalance ? <><b>{cashBalance.available ? `Numerário acumulado: ${fmt(cashBalance.cash_amount)}` : "Saldo indisponível"}</b><span>{cashBalance.message}</span><span>Caixa de origem: {register?.name || "—"} · Até {dateText(draft.preparation_date)}</span></> : "A consultar os movimentos reais da caixa…"}</div>}
        <div className="deposit-entry-summary">
          <Field label="Valor em numerário (€)"><input type="number" inputMode="decimal" min="0" step="0.01" max={cashBalance?.available ? cashBalance.cash_amount : undefined} required disabled={busy || !cashBalance?.available} value={draft.cash_amount ?? "0"} onChange={e => setDraft({ ...draft, cash_amount: e.target.value })} /></Field>
          <div className="deposit-total">Numerário: {fmt(cash / 100)} · Cheques: {fmt(chequeTotal / 100)}<strong>Total: {fmt((cash + chequeTotal) / 100)}</strong></div>
          <ActionButton className="primary" disabled={busy || !cashBalance?.available || cash + chequeTotal <= 0} onClick={() => { intent.current = "register"; }}>Registar depósito</ActionButton>
          <ActionButton disabled={busy || !cashBalance?.available || cash + chequeTotal <= 0} onClick={() => { intent.current = "draft"; }}>Guardar rascunho</ActionButton>
        </div>
        <details className="deposit-source-history">
          <summary>Histórico de documentos e movimentos ({cashBalance?.movements?.length || 0})</summary>
          <div className="table-wrap"><table><thead><tr><th>Data</th><th>Documento / movimento de origem</th><th>Numerário</th></tr></thead><tbody>{(cashBalance?.movements || []).map((row: Row) => <tr key={row.source + row.id}><td>{dateText(row.date)}</td><td>{row.document}{row.description && <small className={`deposit-origin-description${Number(row.amount) < 0 ? " is-outgoing" : ""}`}>{row.description}</small>}</td><td>{fmt(row.amount)}</td></tr>)}</tbody></table></div>
        </details>
        <h3>Recebimentos por cheque da caixa</h3>
        {(draft.cheques || []).map((cheque: Row, index: number) => <div className="deposit-total" key={cheque.source_receipt || cheque.receipt_id || index}><b>{cheque.number} · {fmt(cheque.amount)}</b><span>{cheque.issuer || "Emitente não indicado"} · {dateText(cheque.cheque_date)}</span><small>{cheque.notes}</small></div>)}
        {!draft.cheques?.length && <p>Não existem recebimentos por cheque disponíveis nesta caixa e data.</p>}
        <Field label="Observações"><textarea value={draft.notes} maxLength={2000} onChange={e => setDraft({ ...draft, notes: e.target.value })} /></Field>

      </ManagedForm> : selected && <>
        <div className="actions deposit-actions"><span className={`status-badge deposit-status-${selected.status}`}>{status[selected.status]}</span><ActionLink className="button" href={`/api/deposits/${selected.id}/pdf`} target="_blank" rel="noreferrer">Talão interno / imprimir</ActionLink>
          {write && pending.includes(selected.status) && <><ActionButton className="primary" onClick={() => start("register")}>Registar no banco</ActionButton>{selected.status !== "em_transito" && <ActionButton onClick={() => edit(selected)}>{selected.status === "rascunho" ? "Editar" : "Atualizar movimentos de origem"}</ActionButton>}</>}
          {["entregue", "parcialmente_reconciliado"].includes(selected.status) && <span className="deposit-next">Próximo passo: reconciliar com o crédito do extrato em <b>Bancos › Reconciliação</b> (sugestão automática pelo valor e pela referência {selected.number}).</span>}
          {manager && ["entregue", "parcialmente_reconciliado", "reconciliado"].includes(selected.status) && <>{selected.cheques.length > 0 && <ActionButton onClick={() => start("cheque-return")}>Registar cheque devolvido</ActionButton>}{selected.credits.some((c: Row) => !c.undone_at) && <ActionButton className="danger" onClick={() => start("undo")}>Desfazer associações</ActionButton>}</>}
          {write && selected.status !== "cancelado" && <ActionButton className="danger" onClick={() => start("cancel")}>Cancelar / devolver à caixa</ActionButton>}
        </div>
        <nav className="deposit-tabs" aria-label="Ficha do depósito">{["Dados gerais", "Numerário", "Cheques", "Anexos", "Movimentos associados", "Reconciliação", "Histórico"].map(name => <ActionButton key={name} aria-pressed={tab === name} onClick={() => { setTab(name); setPreview(""); }}>{name}</ActionButton>)}</nav>
        {tab === "Dados gerais" && <div><p>{selected.store_name} · {selected.register_name} → {selected.bank} · {selected.account_name}</p><p>Preparação: {dateText(selected.preparation_date)} · Saída: {dateText(selected.exit_date)} · Entrega: {dateText(selected.delivery_date)}</p><p>Responsável: {selected.responsible} · Referência: {selected.reference || "—"}</p><p>{selected.notes || "Sem observações."}</p><div className="deposit-total"><strong>{fmt(selected.total)}</strong><span>Transferência entre caixa e banco. Sem receita ou venda.</span></div></div>}
        {tab === "Numerário" && <div><h3>Numerário: {fmt(selected.cash_amount)}</h3>{Object.entries(selected.details.counts || {}).filter(([, quantity]) => Number(quantity) > 0).map(([key, quantity]) => <p key={key}>{String(quantity)} × {fmt(Number(key) / 100)} = {fmt(Number(key) * Number(quantity) / 100)}</p>)}</div>}
        {tab === "Cheques" && <div>{selected.cheques.map((cheque: Row) => <p key={cheque.id}>{cheque.number} · {cheque.bank} · {fmt(cheque.amount)} · {dateText(cheque.cheque_date)} · {cheque.issuer || "Emitente não indicado"} {cheque.returned_at ? "· Devolvido" : ""}<br />{cheque.notes}</p>)}<p>A entrega não confirma a disponibilização dos fundos. Confira a data-valor no extrato.</p></div>}
        {tab === "Anexos" && <div>{manager && <ScanButton deposit={selected} onSaved={async () => { await refresh(selected.id); }} />}<p>Talão interno disponível. {selected.proof_status === "validado" ? "Comprovativo bancário validado." : selected.proof_status === "em_falta" ? "Comprovativo bancário em falta — pendência documental." : "Comprovativo por validar."}</p>{write && <Field label="Carregar comprovativo (PDF, PNG ou JPG, até 5 MB)"><input type="file" accept="application/pdf,image/png,image/jpeg" disabled={busy} onChange={e => { const file = e.target.files?.[0]; if (file) run(async () => { const form = new FormData(); form.append("file", file); const response = await fetch(`/api/deposits/${selected.id}/attachments`, { method: "POST", body: form }); const result = await response.json(); if (!response.ok) throw new Error(typeof result.detail === "string" ? result.detail : "Não foi possível carregar o comprovativo."); await refresh(selected.id); setNotice("Comprovativo anexado. Aguarda validação."); }); e.target.value = ""; }} /></Field>}{selected.attachments.map((attachment: Row) => <div className="setting-row" key={attachment.id}><ActionButton onClick={() => setPreview(attachment.view_url || `/api/deposits/${selected.id}/attachments/${attachment.id}`)}>{attachment.name}</ActionButton><span>{attachment.validated ? "Validado" : "Por validar"}</span>{manager && !attachment.validated && <ActionButton disabled={busy} onClick={() => run(async () => { await api(attachment.scan_id ? `/scans/${attachment.scan_id}/validate` : `/deposits/${selected.id}/attachments/${attachment.id}/validate`, attachment.scan_id ? { confirmed: true } : {}); await refresh(selected.id); setNotice("Comprovativo validado."); })}>Validar comprovativo</ActionButton>}</div>)}{preview && <iframe title="Pré-visualização do comprovativo" src={preview} className="deposit-preview" />}</div>}
        {tab === "Movimentos associados" && <div><p>Folha de caixa de origem: {selected.details.session_id || "Não registada nos depósitos anteriores"}</p>{(selected.details.sources || []).map((source: Row) => <p key={source.source + source.id}>{dateText(source.date)} · {source.document} · {fmt(source.amount)}</p>)}<p>Saída de caixa: {selected.movement_id || "Ainda não confirmada"}</p><p>Reversão: {selected.reversal_id || "Não existe"}</p>{selected.credits.map((link: Row) => <p key={link.id}>{link.kind} · {fmt(link.amount)} · Extrato {link.transaction_id} {link.undone_at ? "· Associação desfeita" : ""}</p>)}</div>}
        {tab === "Reconciliação" && <div className="deposit-total"><span>Depositado: {fmt(selected.total)}</span><span>Associado no extrato: {fmt(selected.matched)}</span><span>Diferença (associado − depositado): {fmt(selected.difference)}</span><strong>Por reconciliar: {fmt(selected.remaining)}</strong><span>Comissões: {fmt(selected.fees || "0")}</span><span>Cheques devolvidos: {fmt(selected.returned_amount || "0")}</span><span>Líquido no extrato: {fmt(selected.bank_net ?? selected.matched)}</span><p>Comissões e cheques devolvidos são apresentados em movimentos separados.</p></div>}
        {tab === "Histórico" && <div>{selected.history.map((event: Row, index: number) => <p key={index}>{dateText(event.date)} · {event.user} · {event.action}<br />{event.detail.reason || ""}</p>)}</div>}
        {command && <ManagedForm className="deposit-command doc-form" onSubmit={e => { e.preventDefault(); run(async () => { await api(`/deposits/${selected.id}/${command}`, action); await refresh(selected.id); setCommand(""); setNotice("Operação confirmada com sucesso."); }); }}><h3>{({ register: "Registar depósito no banco (sai da caixa e entra na conta)", exit: "Confirmar saída física", deliver: "Registar entrega ao banco", cancel: "Cancelar depósito / confirmar regresso", reconcile: "Associar crédito do extrato", undo: "Desfazer associações", fee: "Associar comissão bancária", "cheque-return": "Associar devolução de cheque" } as Row)[command]}</h3>
          <Field label="Data da operação"><input type="date" required value={action.action_date} max={today()} onChange={e => setAction({ ...action, action_date: e.target.value })} /></Field>
          {["reconcile", "fee", "cheque-return"].includes(command) && <><Field label="Movimento do extrato"><select required value={action.transaction_id} onChange={e => { const candidate = candidates.find(c => c.id === e.target.value); setAction({ ...action, transaction_id: e.target.value, amount: candidate ? String(Math.min(Number(candidate.available), command === "reconcile" ? Number(selected.remaining) : Number(candidate.available))) : "0" }); }}><option value="">Escolher movimento — confirme a correspondência</option>{candidates.filter(c => (Number(c.amount) > 0) === (command === "reconcile")).map(c => <option key={c.id} value={c.id}>{dateText(c.date)} · {c.description} · {fmt(c.available)} · {c.reasons.join(", ")}</option>)}</select></Field><Field label="Montante a associar (€)"><input required inputMode="decimal" value={action.amount} onChange={e => setAction({ ...action, amount: e.target.value })} /></Field></>}
          {command === "cheque-return" && <Field label="Cheque devolvido"><select required value={action.cheque_id || ""} onChange={e => setAction({ ...action, cheque_id: e.target.value, amount: selected.cheques.find((c: Row) => c.id === e.target.value)?.amount || "0" })}><option value="">Escolher cheque</option>{selected.cheques.filter((c: Row) => !c.returned_at).map((c: Row) => <option key={c.id} value={c.id}>{c.number} · {c.bank} · {fmt(c.amount)}</option>)}</select></Field>}
          <Field label="Justificação / observações"><textarea required={["cancel", "undo", "fee", "cheque-return"].includes(command) || action.allow_insufficient} minLength={5} maxLength={2000} value={action.reason} onChange={e => setAction({ ...action, reason: e.target.value })} /></Field>
          {command === "cancel" && selected.movement_id && <label className="doc-check"><input type="checkbox" required checked={action.returned} onChange={e => setAction({ ...action, returned: e.target.checked })} />Confirmo que todos os valores regressaram fisicamente à caixa.</label>}
          {["exit", "register"].includes(command) && boot.user.role === "administrador" && <label className="doc-check"><input type="checkbox" checked={action.allow_insufficient} onChange={e => setAction({ ...action, allow_insufficient: e.target.checked })} />Autorizar exceção justificada ao saldo disponível.</label>}
          <div className="actions"><ActionButton className={command === "cancel" || command === "undo" ? "danger" : "primary"} disabled={busy}>Confirmar operação</ActionButton><ActionButton type="button" disabled={busy} onClick={() => setCommand("")}>Voltar</ActionButton></div>
        </ManagedForm>}
      </>}
    </AppDialog>}
  </>;
}
