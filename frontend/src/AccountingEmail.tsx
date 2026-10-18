import { useEffect, useRef, useState } from "react";
import { Mail, Eye, Send, RefreshCw, CheckCircle, Download } from "lucide-react";
import { api, Field } from "./App";
import { ActionButton, ActionLink, AppDialog, ManagedForm, Notice, confirmAction, confirmFormExit, markDialogSaved } from "./components/InteractionUI";
import { StatusBadge } from "./components/BusinessUI";

type Row = Record<string, any>;
export const emailStates: Record<string, string> = {
  prepared: "Preparado", sending: "Envio em curso", accepted: "Aceite pelo servidor SMTP",
  failed: "Falhado", received: "Receção confirmada",
};
const splitEmails = (text: string) => text.split(/[;,\n]+/).map(value => value.trim()).filter(Boolean);
const day = (value: string) => value?.slice(0, 10).split("-").reverse().join("/") || "—";
const bytes = (size: number) => new Intl.NumberFormat("pt-PT", { maximumFractionDigits: 2 }).format(size / (size < 1_000 ? 1 : size < 1_000_000 ? 1_000 : 1_000_000)) + (size < 1_000 ? " B" : size < 1_000_000 ? " KB" : " MB");
const categories = [["compras", "Faturas de compras e despesas"], ["notas_credito", "Notas de crédito"],
  ["outros", "Outros documentos e digitalizações"], ["fechos", "Fechos de caixa"], ["depositos", "Depósitos e comprovativos"],
  ["extratos", "Extratos importados (CSV)"], ["pagamentos", "Pagamentos e comprovativos disponíveis"], ["pendencias", "Relatórios de pendências e divergências"]];

export function EmailHistory({ onOpen, refreshKey = 0 }: { onOpen: (row: Row) => void; refreshKey?: number }) {
  const [rows, setRows] = useState<Row[]>([]), [error, setError] = useState("");
  useEffect(() => { let active = true; api("/emails").then(result => { if (active) { setRows(result); setError(""); } }).catch(e => { if (active) setError(e.message); }); return () => { active = false; }; }, [refreshKey]);
  return <section className="panel"><h3>Histórico de emails</h3><p className="footnote">Últimos 100 envios desta empresa. A aceitação SMTP não confirma a receção pelo contabilista.</p>
    {error && <Notice tone="error">{error}</Notice>}
    <div className="table-wrap"><table><thead><tr><th>Preparado em / utilizador</th><th>Período</th><th>Destinatários</th><th>Estado</th><th>Ações</th></tr></thead><tbody>{rows.map(row => <tr key={row.id}>
      <td>{day(row.created_at)}<br/><small>{row.created_by}</small></td><td>{day(row.start)} — {day(row.end)}</td>
      <td>{row.draft.to?.join(", ")} {!!row.draft.cc?.length && <small>CC: {row.draft.cc.join(", ")}</small>}</td>
      <td><StatusBadge tone={row.status === "received" ? "success" : row.status === "failed" ? "danger" : row.status === "accepted" ? "neutral" : "warning"}>{emailStates[row.status] || row.status}</StatusBadge>{row.uncertain && <small>Entrega incerta: confirme antes de repetir.</small>}</td>
      <td><ActionButton onClick={() => onOpen(row)}><Eye size={16}/>Consultar</ActionButton></td>
    </tr>)}</tbody></table></div>{!rows.length && !error && <p>Ainda não existem envios preparados nesta empresa.</p>}
  </section>;
}

export function AccountingEmailDialog({ company, stores, store = "", initial, period, onClose, onChanged }: {
  company: Row; stores: Row[]; store?: string; initial?: Row; period?: Row; onClose: () => void; onChanged: () => void;
}) {
  const today = new Intl.DateTimeFormat("en-CA", { timeZone: "Europe/Lisbon", year: "numeric", month: "2-digit", day: "2-digit" }).format(new Date());
  const [start, setStart] = useState(period?.start || today.slice(0, 8) + "01"), [end, setEnd] = useState(period?.end || today);
  const [selectedStore, setSelectedStore] = useState(store), [selectedCategories, setSelectedCategories] = useState(categories.map(([key]) => key));
  const [row, setRow] = useState<Row | null>(initial || null), [draft, setDraft] = useState<Row>(initial?.draft || {});
  const [to, setTo] = useState(initial?.draft.to?.join("; ") || ""), [cc, setCc] = useState(initial?.draft.cc?.join("; ") || "");
  const [busy, setBusy] = useState(false), [error, setError] = useState(""), [notice, setNotice] = useState("");
  const [approved, setApproved] = useState(false), [receiptNote, setReceiptNote] = useState("");
  const [config, setConfig] = useState<Row | null>(null), [historyTick, setHistoryTick] = useState(0);
  const running = useRef(false), prepareKey = useRef(crypto.randomUUID()), attemptKey = useRef(crypto.randomUUID());
  const dialog = useRef<HTMLDialogElement>(null), periodTouched = useRef(false);
  const baseline = useRef("");
  useEffect(() => { markDialogSaved(dialog.current); }, [row?.id, row?.revision, row?.status]);
  useEffect(() => { let active = true; api("/settings/email").then(value => {
    if (!active) return; setConfig(value);
    if (!initial && !period && !periodTouched.current && value.periodicity === "trimestral") { const month = Number(today.slice(5, 7)); setStart(today.slice(0, 5) + String(Math.floor((month - 1) / 3) * 3 + 1).padStart(2, "0") + "-01"); }
  }).catch(e => { if (active) setError(e.message); }); return () => { active = false; }; }, []);
  function adopt(value: Row) { setRow(value); setDraft(value.draft); setTo(value.draft.to?.join("; ") || ""); setCc(value.draft.cc?.join("; ") || ""); setApproved(false); baseline.current = ""; attemptKey.current = crypto.randomUUID(); }
  async function run(task: () => Promise<void>) {
    if (running.current) return; running.current = true; setBusy(true); setError(""); setNotice("");
    try { await task(); } catch (e) { setError((e as Error).message); } finally { running.current = false; setBusy(false); }
  }
  const editable = row?.status === "prepared";
  const currentDraft = { ...draft, to: splitEmails(to), cc: splitEmails(cc) };
  const stamp = JSON.stringify(currentDraft), plan = row?.manifest.plan;
  const previewValid = !!row && editable && baseline.current === stamp && !!plan;
  function changeDraft(next: Row) { setDraft(next); setApproved(false); }
  const selectedFiles: Row[] = row?.manifest.files.filter((file: Row) => draft.selected_keys?.includes(file.key)) || [];
  return <AppDialog ref={dialog} busy={busy} className="doc-dialog" aria-label="Enviar à contabilidade" onRequestClose={onClose}>
    <h2>Enviar à contabilidade</h2>
    <p><strong>Empresa: {company.name || row?.manifest.company.name || "Empresa selecionada"}</strong></p>
    {error && <Notice tone="error">{error}</Notice>}{notice && <Notice tone="success">{notice}</Notice>}
    {busy && <p role="status">A processar… A preparação pode consultar anexos privados no Drive. Não repita a operação.</p>}
    {!row ? <ManagedForm onSubmit={e => { e.preventDefault(); void run(async () => {
      adopt(await api("/accounting/email/prepare", { request_key: prepareKey.current, start, end,
        store_ids: selectedStore ? [selectedStore] : [], categories: selectedCategories })); onChanged();
    }); }}><fieldset disabled={busy} className="ui-form-fields">
      <Field label="Estabelecimento"><select value={selectedStore} onChange={e => setSelectedStore(e.target.value)}><option value="">Todos os estabelecimentos</option>{stores.map(item => <option key={item.id} value={item.id}>{item.name}</option>)}</select></Field>
      <div className="form-grid"><Field label="Data início"><input type="date" required value={start} onChange={e => { periodTouched.current = true; setStart(e.target.value); }}/></Field><Field label="Data fim"><input type="date" required min={start} value={end} onChange={e => { periodTouched.current = true; setEnd(e.target.value); }}/></Field></div>
      <p>Periodicidade habitual: {config?.periodicity || "mensal"}. Envio manual; não encerra o período.</p>{config?.custom_period_note && <p>{config.custom_period_note}</p>}
      <fieldset><legend>Documentação a preparar</legend>{categories.map(([key, label]) => <label className="doc-check" key={key}><input type="checkbox" checked={selectedCategories.includes(key)} onChange={e => setSelectedCategories(e.target.checked ? [...selectedCategories, key] : selectedCategories.filter(item => item !== key))}/>{label}</label>)}</fieldset>
      <p className="footnote">Os extratos são exportados dos movimentos importados. Os originais bancários só são incluídos quando estão guardados como anexos. Os relatórios identificam a documentação em falta.</p>
      <div className="ui-dialog-actions"><ActionButton type="button" onClick={onClose}>Cancelar</ActionButton><ActionButton className="primary" disabled={!selectedCategories.length || busy}><Mail size={16}/>Preparar dossiê e email</ActionButton></div>
    </fieldset></ManagedForm> : <>
      <StatusBadge tone={row.status === "failed" ? "danger" : row.status === "received" ? "success" : "neutral"}>{emailStates[row.status]}</StatusBadge>
      <p>{day(row.start)} — {day(row.end)} · {row.manifest.files.length} ficheiros disponíveis · {selectedFiles.length} selecionados · {bytes(selectedFiles.reduce((total, file) => total + file.size_bytes, 0))} antes da codificação do email</p>
      {row.error && <Notice tone="warning">{row.error}</Notice>}
      {row.uncertain && <Notice tone="warning">A entrega é incerta. Confirme com o servidor e o destinatário antes de voltar a enviar.</Notice>}
      <ActionButton disabled={busy} onClick={async () => { if (await confirmFormExit(dialog.current)) await run(async () => adopt(await api(`/emails/${row.id}`))); }}>Consultar estado guardado</ActionButton>
      <ManagedForm onSubmit={e => { e.preventDefault(); void run(async () => {
        const result = await api(`/emails/${row.id}/preview`, { ...currentDraft, revision: row.revision });
        adopt(result); baseline.current = JSON.stringify(result.draft); setNotice("Pré-visualização guardada. Reveja a mensagem e confirme o envio."); onChanged();
      }); }}><fieldset disabled={busy || !editable} className="ui-form-fields">
        <Field label="Destinatários (Para)"><input required value={to} onChange={e => { setTo(e.target.value); setApproved(false); }} placeholder="contabilidade@exemplo.pt; outro@exemplo.pt"/></Field>
        <Field label="CC (opcional)"><input value={cc} onChange={e => { setCc(e.target.value); setApproved(false); }}/></Field>
        <Field label="Assunto"><input required maxLength={500} value={draft.subject || ""} onChange={e => changeDraft({ ...draft, subject: e.target.value })}/></Field>
        <Field label="Mensagem e assinatura"><textarea rows={12} required maxLength={30000} value={draft.message || ""} onChange={e => changeDraft({ ...draft, message: e.target.value })}/></Field>
        <Field label="Formato dos anexos"><select value={draft.format || "zip"} onChange={e => changeDraft({ ...draft, format: e.target.value })}><option value="zip">ZIP organizado por período e tipo</option><option value="individual">Anexos individuais com índice</option></select></Field>
        <label className="doc-check"><input type="checkbox" checked={!!draft.allow_split} onChange={e => changeDraft({ ...draft, allow_split: e.target.checked })}/>Autorizo dividir em mensagens numeradas se o limite SMTP for excedido.</label>
        {!!row.manifest.pending.length && <details open><summary>{row.manifest.pending.length} documentos ou situações pendentes</summary><p>Pode enviar a documentação disponível. As pendências identificadas no relatório são mantidas.</p><ul>{row.manifest.pending.slice(0, 50).map((item: Row, i: number) => <li key={i}><strong>{item.document || item.id}</strong> · {item.reason}</li>)}</ul></details>}
        <div className="actions"><ActionButton type="button" onClick={() => changeDraft({ ...draft, selected_keys: row.manifest.files.filter((file: Row) => !file.previous_dispatch).map((file: Row) => file.key) })}>Selecionar novos / alterados</ActionButton><ActionButton type="button" onClick={() => changeDraft({ ...draft, selected_keys: row.manifest.files.map((file: Row) => file.key) })}>Selecionar todos, incluindo anteriores</ActionButton></div>
        <div className="table-wrap"><table><thead><tr><th>Incluir</th><th>Documento / ficheiro</th><th>Tamanho</th><th>Envio anterior / versão</th></tr></thead><tbody>{row.manifest.files.map((file: Row) => <tr key={file.key}>
          <td><input type="checkbox" aria-label={`Incluir ${file.key}`} checked={draft.selected_keys?.includes(file.key) || false} onChange={e => changeDraft({ ...draft, selected_keys: e.target.checked ? [...draft.selected_keys, file.key] : draft.selected_keys.filter((key: string) => key !== file.key) })}/></td>
          <td><strong>{file.document || file.original_name}</strong><br/><small>{file.supplier} {file.nif} · {file.key}</small><br/><ActionLink href={`/api/emails/${row.id}/files/${file.key}`}><Download size={15}/>Consultar ficheiro preparado</ActionLink>{file.note && <p>{file.note}</p>}</td><td>{bytes(file.size_bytes)}</td><td>{file.previous_dispatch ? "Já aceite pelo SMTP para estes destinatários" : "Novo / alterado"}<br/><small title={file.sha256}>SHA-256: {file.sha256.slice(0, 12)}…</small></td>
        </tr>)}</tbody></table></div>
        {editable && <ActionButton className="primary" disabled={busy || !selectedFiles.length}><Eye size={16}/>Atualizar pré-visualização</ActionButton>}
      </fieldset></ManagedForm>
      {plan && <Notice tone={plan.requires_split_confirmation ? "warning" : "info"}>Plano: {plan.messages} mensagem(ns) · {bytes(plan.total_bytes)} com codificação MIME · limite por mensagem: {bytes(plan.limit_bytes)}. {plan.requires_split_confirmation ? "Autorize a divisão e atualize a pré-visualização." : "O limite anunciado pelo servidor será verificado novamente antes do envio."}</Notice>}
      {editable && <><label className="doc-check"><input type="checkbox" disabled={!previewValid || busy} checked={approved} onChange={e => setApproved(e.target.checked)}/>Revi destinatários, mensagem, ficheiros, versões anteriores e pendências. Confirmo «Enviar à contabilidade».</label>
        <ActionButton className="primary" disabled={busy || !previewValid || !approved || plan?.requires_split_confirmation} onClick={() => run(async () => {
          const result = await api(`/emails/${row.id}/send`, { revision: row.revision, request_key: attemptKey.current, confirmed: true });
          adopt(result); onChanged(); setHistoryTick(value => value + 1);
          setNotice(result.status === "accepted" ? "Aceite pelo servidor SMTP. A receção pelo contabilista ainda não está confirmada." : "");
        })}><Send size={16}/>Enviar à contabilidade</ActionButton></>}
      {row.status === "failed" && <ActionButton disabled={busy} onClick={() => run(async () => {
        let acknowledged = false;
        if (row.uncertain) { acknowledged = await confirmAction({ title: "Preparar nova tentativa com entrega incerta?", message: "Confirme primeiro no servidor e com o destinatário. Repetir mensagens de entrega incerta pode duplicar a documentação.", confirmLabel: "Reconheço o risco e quero preparar" }); if (!acknowledged) return; }
        adopt(await api(`/emails/${row.id}/retry`, { request_key: crypto.randomUUID(), acknowledge_uncertain: acknowledged })); onChanged();
      })}><RefreshCw size={16}/>Preparar nova tentativa das mensagens não aceites</ActionButton>}
      {row.status === "sending" && <><p>O envio pode continuar no servidor. Não repita o pedido. Pode atualizar a consulta do estado.</p><ActionButton disabled={busy} onClick={() => run(async () => adopt(await api(`/emails/${row.id}`)))}>Consultar estado</ActionButton><ActionButton disabled={busy} onClick={() => run(async () => {
        if (!(await confirmAction({ title: "Recuperar estado do envio?", message: "Use apenas se a tentativa estiver interrompida há pelo menos 20 minutos. O estado ficará como entrega incerta; confirme antes de reenviar.", confirmLabel: "Recuperar estado" }))) return;
        adopt(await api(`/emails/${row.id}/recover`, { confirmed: true, note: "Estado revisto manualmente após interrupção prolongada." })); onChanged();
      })}>Recuperar tentativa interrompida</ActionButton></>}
      {row.status === "accepted" && <ManagedForm onSubmit={e => { e.preventDefault(); void run(async () => { adopt(await api(`/emails/${row.id}/receipt`, { confirmed: true, note: receiptNote })); onChanged(); }); }}><Field label="Confirmação de receção: como e por quem"><textarea required minLength={3} value={receiptNote} onChange={e => setReceiptNote(e.target.value)}/></Field><ActionButton disabled={busy || receiptNote.trim().length < 3}><CheckCircle size={16}/>Registar receção confirmada</ActionButton></ManagedForm>}
      {row.received_at && <p>Receção registada em {day(row.received_at)} por {row.received_by}: {row.receipt_note}</p>}
      {!!row.parts.length && <details open><summary>Resultado de cada mensagem</summary>{row.parts.map((part: Row) => <p key={part.id}>Mensagem {part.number}: {emailStates[part.status] || part.status} · {bytes(part.size_bytes)} · {part.message_id} {part.error}</p>)}</details>}
      {!editable && <details><summary>Consultar os ficheiros preparados</summary>{row.manifest.files.filter((file: Row) => draft.selected_keys?.includes(file.key)).map((file: Row) => <p key={file.key}><ActionLink href={`/api/emails/${row.id}/files/${file.key}`}><Download size={15}/>{file.original_name}</ActionLink> · SHA-256: {file.sha256}</p>)}</details>}
      <ActionButton disabled={busy} onClick={onClose}>Fechar</ActionButton>
      <p className="footnote">Este envio não marca documentos como contabilizados, reconciliados ou completos. Os ficheiros e versões preparados ficam no histórico privado da empresa.</p>
      <details><summary>Histórico desta empresa</summary><EmailHistory refreshKey={historyTick} onOpen={value => { void (async () => { if (!busy && await confirmFormExit(dialog.current)) adopt(value); })(); }}/></details>
    </>}
  </AppDialog>;
}

export default function AccountingEmail({ company, stores, store = "", admin }: { company: Row; stores: Row[]; store?: string; admin: boolean }) {
  const [opened, setOpened] = useState(false), [initial, setInitial] = useState<Row | undefined>(), [tick, setTick] = useState(0);
  const [period, setPeriod] = useState<Row | undefined>();
  if (!admin) return null;
  return <section className="panel"><h2>Envio de documentação</h2><p>Prepare o dossiê disponível para o período, reveja as pendências e envie manualmente por SMTP.</p>
    <ActionButton className="primary" onClick={() => { setInitial(undefined); setPeriod(undefined); setOpened(true); }}><Mail size={17}/>Enviar à contabilidade</ActionButton>
    <AccountingEmailPeriods refreshKey={tick} onPeriod={value => { setInitial(undefined); setPeriod(value); setOpened(true); }}/>
    <details><summary>Histórico de envios</summary><EmailHistory refreshKey={tick} onOpen={row => { setInitial(row); setOpened(true); }}/></details>
    {opened && <AccountingEmailDialog key={initial?.id || "new"} company={company} stores={stores} store={store} initial={initial} period={period} onClose={() => { setOpened(false); setTick(value => value + 1); }} onChanged={() => setTick(value => value + 1)}/>}
  </section>;
}

function AccountingEmailPeriods({ refreshKey, onPeriod }: { refreshKey: number; onPeriod: (period: Row) => void }) {
  const [year, setYear] = useState(Number(new Intl.DateTimeFormat("en", { year: "numeric", timeZone: "Europe/Lisbon" }).format(new Date())));
  const [data, setData] = useState<Row | null>(null), [error, setError] = useState("");
  useEffect(() => { let active = true; api(`/accounting/email/periods?year=${year}`).then(value => { if (active) { setData(value); setError(""); } }).catch(e => { if (active) setError(e.message); }); return () => { active = false; }; }, [year, refreshKey]);
  return <details><summary>Organizar períodos e lembretes de envio</summary>
    <label>Ano dos envios<input type="number" min={2000} max={2100} value={year} onChange={e => { const value = Number(e.target.value); if (value >= 2000 && value <= 2100) setYear(value); }}/></label>
    {error && <Notice tone="error">{error}</Notice>}
    {data && <><p>Periodicidade: {data.periodicity}. {data.note} Os lembretes não enviam emails automaticamente e não certificam a conclusão contabilística.</p>
      {data.periods.some((period: Row) => period.calendar_ended && !period.accepted_dispatches) && <Notice tone="warning">Existem períodos terminados sem um envio integral aceite pelo servidor SMTP. Reveja o histórico e prepare a documentação disponível.</Notice>}
      <div className="table-wrap"><table><thead><tr><th>Período</th><th>Envios SMTP aceites</th><th>Ação</th></tr></thead><tbody>{data.periods.map((period: Row) => <tr key={period.start}><td>{day(period.start)} — {day(period.end)}</td><td>{period.accepted_dispatches}</td><td><ActionButton onClick={() => onPeriod(period)}>Preparar este período</ActionButton></td></tr>)}</tbody></table></div>
    </>}
  </details>;
}
