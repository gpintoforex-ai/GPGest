import { useEffect, useState } from "react";
import { FileSignature, MoreHorizontal, Plus } from "lucide-react";
import { StatusBadge, type BusinessTone } from "./components/BusinessUI";
import { ActionButton, AppDialog, Notice } from "./components/InteractionUI";
import { ActionMenu, DetailDrawer } from "./components/WorkspaceUI";
import { api } from "./lib/api";
import { centsInput, euroToCents, formatEuro } from "./utils/format";
import { ArchiveDocs } from "./ObligationDocs";

/**
 * Contratos (Obrigações e Prazos): ficha com fidelização, renovação automática e pré-aviso de denúncia.
 * Os alertas contam a partir do prazo de denúncia; nada é renovado, denunciado ou terminado sem decisão.
 */

type Row = Record<string, any>;
const STATE_TONE: Record<string, BusinessTone> = { vencido: "danger", urgente: "danger", a_aproximar: "warning", agendado: "info", concluido: "neutral" };
const STATE_LABEL: Record<string, string> = { vencido: "Prazo passado", urgente: "Urgente", a_aproximar: "A aproximar-se", agendado: "Agendado", concluido: "Decidido" };
const STATUS_TONE: Record<string, BusinessTone> = { ativo: "success", denunciado: "warning", terminado: "neutral" };
const ACTIONS: Record<string, string> = { denunciar: "Registar denúncia", renovar: "Renovar", terminar: "Terminar", reativar: "Reativar" };
const ALERTS = [90, 60, 30, 15, 7, 0];
const fullDate = (iso?: string | null) => iso ? iso.slice(0, 10).split("-").reverse().join("/") : "—";

export default function Contracts({ store, stores, categories }: { store: string; stores: Row[]; categories: Row[] }) {
  const [data, setData] = useState<Row | null>(null), [error, setError] = useState(""), [notice, setNotice] = useState("");
  const [editing, setEditing] = useState<Row | null>(null), [deciding, setDeciding] = useState<{ action: string; contract: Row } | null>(null);
  const [version, setVersion] = useState(0);
  const refresh = () => setVersion(v => v + 1);
  useEffect(() => {
    let active = true;
    api(`/contracts?${new URLSearchParams({ store_id: store })}`).then(r => { if (active) setData(r); }).catch(e => { if (active) setError((e as Error).message); });
    return () => { active = false; };
  }, [store, version]);
  async function open(id: string) {
    try { setEditing(await api(`/contracts/${encodeURIComponent(id)}`)); } catch (e) { setError((e as Error).message); }
  }
  const actions = (c: Row) => <ActionMenu label={`Ações do contrato ${c.entity_name}`} icon={<MoreHorizontal size={18} aria-hidden="true" />}>
    <ActionButton type="button" onClick={() => void open(c.id)}>Abrir ficha</ActionButton>
    {c.status === "ativo" && c.end_date && <ActionButton type="button" onClick={() => setDeciding({ action: "denunciar", contract: c })}>Registar denúncia</ActionButton>}
    {c.status === "ativo" && !c.auto_renewal && <ActionButton type="button" onClick={() => setDeciding({ action: "renovar", contract: c })}>Renovar</ActionButton>}
    {c.status !== "terminado" && <ActionButton type="button" onClick={() => setDeciding({ action: "terminar", contract: c })}>Terminar</ActionButton>}
    {c.status !== "ativo" && <ActionButton type="button" onClick={() => setDeciding({ action: "reativar", contract: c })}>Reativar</ActionButton>}
  </ActionMenu>;
  return <section className="obl-card" aria-label="Contratos">
    <div className="obl-contracts-head">
      <h2><FileSignature size={18} aria-hidden="true" />Contratos</h2>
      <ActionButton type="button" className="primary" onClick={() => setEditing({})}><Plus size={16} aria-hidden="true" />Novo contrato</ActionButton>
    </div>
    {error && <Notice tone="error" onDismiss={() => setError("")}>{error}</Notice>}
    {notice && <Notice tone="success" onDismiss={() => setNotice("")}>{notice}</Notice>}
    {data && <p className="obl-contracts-summary">{data.summary.active} ativo(s) · {formatEuro(data.summary.annual_cents)} por ano · {data.summary.notice_30} com prazo de denúncia nos próximos 30 dias · {data.summary.fidelized} fidelizado(s)</p>}
    {!data ? !error && <p role="status">A carregar contratos…</p> : !data.contracts.length ? <p className="dash-muted">Sem contratos registados. Registe telecomunicações, rendas, software, energia, leasing… para ser avisado antes do prazo de denúncia.</p>
      : <div className="table-wrap"><table className="obl-grid" aria-label="Lista de contratos">
        <thead><tr><th>Contrato</th><th className="numeric">Valor</th><th>Próxima renovação</th><th>Prazo de denúncia</th><th>Fidelização</th><th>Estado</th><th>Ações</th></tr></thead>
        <tbody>{data.contracts.map((c: Row) => <tr key={c.id}>
          <td><button type="button" className="obl-link" onClick={() => void open(c.id)}>{c.entity_name}</button>
            <small>{[c.contract_type, c.number && `n.º ${c.number}`].filter(Boolean).join(" · ")}</small></td>
          <td className="numeric">{c.amount_cents ? <>{formatEuro(c.amount_cents)}<small>{c.payment_periodicity_label} · {formatEuro(c.annual_cents)}/ano</small></> : "—"}</td>
          <td>{fullDate(c.next_renewal)}<small>{c.auto_renewal ? `Renovação automática (${c.renewal_months} meses)` : c.end_date ? "Sem renovação automática" : "Sem termo"}</small></td>
          <td>{c.notice_deadline ? <>{fullDate(c.notice_deadline)}<small>{c.status === "ativo" ? `${c.notice_days} dias de pré-aviso · ${c.days_to_notice < 0 ? `${-c.days_to_notice} dia(s) atrás` : `daqui a ${c.days_to_notice} dia(s)`}` : "Decisão registada"}</small>
            {c.notice_state && <StatusBadge tone={STATE_TONE[c.notice_state]} compact>{STATE_LABEL[c.notice_state]}</StatusBadge>}</> : "—"}</td>
          <td>{c.fidelization_end ? <>{fullDate(c.fidelization_end)}<small>{c.fidelized ? "Fidelizado" : "Terminada"}</small></> : "—"}</td>
          <td><StatusBadge tone={STATUS_TONE[c.status]} compact>{c.status === "denunciado" ? "Denunciado" : c.status === "ativo" ? "Ativo" : "Terminado"}</StatusBadge></td>
          <td>{actions(c)}</td></tr>)}</tbody></table></div>}
    <p className="dash-muted">Os alertas contam a partir do prazo de denúncia (renovação − pré-aviso). Nada é renovado, denunciado ou terminado automaticamente.</p>
    {editing && data && <ContractDrawer value={editing} meta={data} store={store} stores={stores} categories={categories} onClose={() => setEditing(null)}
      onSaved={message => { setEditing(null); setNotice(message); refresh(); }} />}
    {deciding && <DecisionDialog {...deciding} onClose={() => setDeciding(null)} onDone={message => { setDeciding(null); setNotice(message); refresh(); }} />}
  </section>;
}

function DecisionDialog({ action, contract, onClose, onDone }: { action: string; contract: Row; onClose: () => void; onDone: (message: string) => void }) {
  const [reason, setReason] = useState(""), [when, setWhen] = useState(""), [penalty, setPenalty] = useState(false);
  const [error, setError] = useState(""), [busy, setBusy] = useState(false);
  const fidelized = action === "denunciar" && contract.fidelization_end && contract.next_renewal && contract.fidelization_end > contract.next_renewal;
  async function confirm() {
    setBusy(true); setError("");
    try {
      await api(`/contracts/${encodeURIComponent(contract.id)}/decision`, { revision: contract.revision, action, reason, effective_date: when || null, accept_fidelization_penalty: penalty });
      onDone(`${ACTIONS[action]}: decisão registada.`);
    } catch (e) { setError((e as Error).message); } finally { setBusy(false); }
  }
  return <AppDialog className="obl-action-dialog" aria-label={ACTIONS[action]} busy={busy} onRequestClose={onClose}>
    <h2>{ACTIONS[action]}</h2>
    <p><strong>{contract.entity_name}</strong>{contract.contract_type ? ` · ${contract.contract_type}` : ""}</p>
    {error && <Notice tone="error">{error}</Notice>}
    {action === "denunciar" && <Notice tone="info">O contrato termina na próxima renovação ({fullDate(contract.next_renewal)}); os pagamentos previstos param aí. Envie a carta ou o pedido de denúncia ao fornecedor até {fullDate(contract.notice_deadline)}.</Notice>}
    {fidelized && <><Notice tone="warning">Fidelizado até {fullDate(contract.fidelization_end)}: a denúncia antes desse dia pode ter penalização.</Notice>
      <label className="checkbox"><input type="checkbox" checked={penalty} onChange={e => setPenalty(e.target.checked)} />Aceito a eventual penalização</label></>}
    {action === "renovar" && <label>Nova data de fim (vazio = mais {contract.renewal_months} meses)<input type="date" value={when} onChange={e => setWhen(e.target.value)} /></label>}
    {action === "terminar" && <label>Data efetiva (vazio = hoje)<input type="date" value={when} onChange={e => setWhen(e.target.value)} /></label>}
    <label>Motivo<textarea rows={3} value={reason} maxLength={2000} onChange={e => setReason(e.target.value)} /></label>
    <div className="ui-dialog-actions"><ActionButton type="button" onClick={onClose}>Cancelar</ActionButton>
      <ActionButton type="button" className="primary" disabled={busy || reason.trim().length < 3 || (!!fidelized && !penalty)} onClick={() => void confirm()}>{ACTIONS[action]}</ActionButton></div>
  </AppDialog>;
}

function ContractDrawer({ value, meta, store, stores, categories, onClose, onSaved }: {
  value: Row; meta: Row; store: string; stores: Row[]; categories: Row[]; onClose: () => void; onSaved: (message: string) => void;
}) {
  const editing = !!value.id;
  const [form, setForm] = useState<Row>(() => editing ? { ...value, amount: centsInput(value.amount_cents), vat_rate: value.vat_rate ?? "", end_date: value.end_date || "",
    fidelization_end: value.fidelization_end || "", first_payment_date: value.first_payment_date || "", payment_every_months: value.payment_every_months || "" }
    : { entity_name: "", contract_type: "", number: "", description: "", store_id: store || null, start_date: "", end_date: "", fidelization_end: "", auto_renewal: true,
      renewal_months: 12, notice_days: 30, amount: "", vat_rate: "", payment_periodicity: "mensal", payment_every_months: "", first_payment_date: "", category_id: "",
      responsible_id: "", alert_days: [90, 60, 30, 15, 7, 0], notes: "", revision: 0 });
  const [error, setError] = useState(""), [busy, setBusy] = useState(false);
  const set = (changes: Row) => setForm(current => ({ ...current, ...changes }));
  const cents = euroToCents(form.amount || "");
  async function save() {
    setBusy(true); setError("");
    try {
      const body = {
        revision: form.revision || 0, store_id: form.store_id || null, entity_name: form.entity_name, contract_type: form.contract_type, description: form.description || "",
        number: form.number || "", start_date: form.start_date, end_date: form.end_date || null, fidelization_end: form.fidelization_end || null, auto_renewal: !!form.auto_renewal,
        renewal_months: Number(form.renewal_months) || 12, notice_days: Number(form.notice_days) || 0, amount_cents: cents, vat_rate: form.vat_rate === "" ? null : form.vat_rate,
        payment_periodicity: form.payment_periodicity, payment_every_months: form.payment_periodicity === "personalizada" ? Number(form.payment_every_months) || null : null,
        first_payment_date: form.first_payment_date || null, category_id: form.category_id || null, responsible_id: form.responsible_id || null, alert_days: form.alert_days,
        notes: form.notes || "",
      };
      await api(editing ? `/contracts/${encodeURIComponent(value.id)}` : "/contracts", body, editing ? { method: "PUT" } : undefined);
      onSaved(editing ? "Contrato atualizado." : "Contrato criado; os prazos de denúncia e os pagamentos foram gerados.");
    } catch (e) { setError((e as Error).message); } finally { setBusy(false); }
  }
  const notice = form.end_date && Number(form.notice_days) >= 0 ? new Date(new Date(form.end_date).getTime() - Number(form.notice_days) * 86400000).toISOString().slice(0, 10) : "";
  return <DetailDrawer className="obl-drawer" aria-label={editing ? "Ficha do contrato" : "Novo contrato"} busy={busy} onRequestClose={onClose}>
    <h2>{editing ? form.entity_name : "Novo contrato"}</h2>
    {error && <Notice tone="error">{error}</Notice>}
    <div className="obl-form">
      <label>Fornecedor ou entidade<input value={form.entity_name} maxLength={200} onChange={e => set({ entity_name: e.target.value })} /></label>
      <label>Tipo de contrato<input list="contract-types" value={form.contract_type} maxLength={60} onChange={e => set({ contract_type: e.target.value })} />
        <datalist id="contract-types">{meta.types.map((t: string) => <option key={t} value={t} />)}</datalist></label>
      <label>Número do contrato<input value={form.number} maxLength={80} onChange={e => set({ number: e.target.value })} /></label>
      <label>Âmbito<select value={form.store_id || ""} onChange={e => set({ store_id: e.target.value || null })}><option value="">Empresa</option>
        {stores.map(s => <option key={s.id} value={s.id}>{s.name}</option>)}</select></label>
      <label>Data de início<input type="date" value={form.start_date} onChange={e => set({ start_date: e.target.value })} /></label>
      <label>Fim do período em curso / renovação<input type="date" value={form.end_date} onChange={e => set({ end_date: e.target.value })} /></label>
      <label>Fim da fidelização (opcional)<input type="date" value={form.fidelization_end} onChange={e => set({ fidelization_end: e.target.value })} /></label>
      <label className="checkbox"><input type="checkbox" checked={!!form.auto_renewal} onChange={e => set({ auto_renewal: e.target.checked })} />Renovação automática</label>
      {form.auto_renewal && <label>Renova por (meses)<input type="number" min="1" max="120" value={form.renewal_months} onChange={e => set({ renewal_months: e.target.value })} /></label>}
      <label>Pré-aviso para denúncia (dias)<input type="number" min="0" max="365" value={form.notice_days} onChange={e => set({ notice_days: e.target.value })} /></label>
      {notice && <p className="obl-wide obl-hint">Prazo de denúncia: <strong>{fullDate(notice)}</strong>{form.auto_renewal ? ", e o mesmo dia em cada renovação" : ""}. Os alertas contam a partir deste dia.</p>}
      <label>Valor de cada pagamento (€, com IVA)<input inputMode="decimal" value={form.amount} onChange={e => set({ amount: e.target.value })} placeholder="Vazio se não tiver valor" /></label>
      <label>Taxa de IVA (%)<input type="number" min="0" max="100" step="0.01" value={form.vat_rate} onChange={e => set({ vat_rate: e.target.value })} /></label>
      <label>Periodicidade dos pagamentos<select value={form.payment_periodicity} onChange={e => set({ payment_periodicity: e.target.value })}>
        {Object.entries(meta.periodicities).map(([k, label]) => <option key={k} value={k}>{String(label)}</option>)}</select></label>
      {form.payment_periodicity === "personalizada" && <label>De quantos em quantos meses<input type="number" min="1" max="120" value={form.payment_every_months} onChange={e => set({ payment_every_months: e.target.value })} /></label>}
      <label>Primeiro pagamento (vazio = início)<input type="date" value={form.first_payment_date} onChange={e => set({ first_payment_date: e.target.value })} /></label>
      <label>Rubrica financeira<select value={form.category_id || ""} onChange={e => set({ category_id: e.target.value })}><option value="">Sem rubrica</option>
        {categories.filter(c => c.active !== false).map(c => <option key={c.id} value={c.id}>{c.name}</option>)}</select></label>
      <label>Responsável<select value={form.responsible_id || ""} onChange={e => set({ responsible_id: e.target.value })}><option value="">Sem responsável</option>
        {meta.users.map((u: Row) => <option key={u.id} value={u.id}>{u.name}</option>)}</select></label>
      <fieldset className="obl-wide"><legend>Alertas antes do prazo de denúncia</legend><div className="obl-chips">
        {ALERTS.map(days => <label key={days} className="checkbox"><input type="checkbox" checked={form.alert_days.includes(days)}
          onChange={e => set({ alert_days: e.target.checked ? [...form.alert_days, days] : form.alert_days.filter((d: number) => d !== days) })} />{days ? `${days} dias` : "No dia"}</label>)}
      </div></fieldset>
      <label className="obl-wide">Descrição e observações<textarea rows={3} value={form.notes} maxLength={4000} onChange={e => set({ notes: e.target.value })} /></label>
    </div>
    {editing && <ArchiveDocs kind="contract" id={value.id} title="Contrato, adendas e comunicações" />}
    {editing && value.occurrences?.length > 0 && <section className="obl-history" aria-label="Prazos e pagamentos do contrato">
      <h3>Prazos e pagamentos</h3>
      <div className="table-wrap"><table><thead><tr><th>Data</th><th>Tipo</th><th className="numeric">Valor</th><th>Estado</th></tr></thead>
        <tbody>{value.occurrences.slice(0, 18).map((o: Row) => <tr key={o.id}><td>{fullDate(o.due_date)}</td><td>{o.role === "notice" ? "Prazo de denúncia / fim" : "Pagamento"}</td>
          <td className="numeric">{o.expected_cents == null ? "—" : formatEuro(o.expected_cents)}</td><td>{o.status === "pendente" ? (o.days < 0 ? "Vencido" : `Daqui a ${o.days} dia(s)`) : o.status}</td></tr>)}</tbody></table></div>
    </section>}
    <div className="ui-dialog-actions"><ActionButton type="button" onClick={onClose}>Fechar</ActionButton>
      <ActionButton type="button" className="primary" disabled={busy || form.entity_name.trim().length < 2 || !form.start_date || Number.isNaN(cents) || (form.auto_renewal && !form.end_date)} onClick={() => void save()}>
        {editing ? "Guardar alterações" : "Criar contrato"}</ActionButton></div>
  </DetailDrawer>;
}
