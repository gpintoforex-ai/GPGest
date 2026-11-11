import { useEffect, useState } from "react";
import { Car, MoreHorizontal, Plus } from "lucide-react";
import { StatusBadge, type BusinessTone } from "./components/BusinessUI";
import { ActionButton, AppDialog, Notice } from "./components/InteractionUI";
import { ActionMenu, DetailDrawer } from "./components/WorkspaceUI";
import { api } from "./lib/api";
import { centsInput, euroToCents, formatEuro } from "./utils/format";
import { ArchiveDocs } from "./ObligationDocs";

/**
 * Viaturas (Obrigações e Prazos): ficha com IUC, inspeção periódica pela categoria e idade, seguro e revisão.
 * As regras legais são modelos «a validar»; cada data pode ser corrigida na ocorrência (com a origem).
 */

type Row = Record<string, any>;
const STATE_TONE: Record<string, BusinessTone> = { vencido: "danger", urgente: "danger", a_aproximar: "warning", agendado: "info" };
const STATE_LABEL: Record<string, string> = { vencido: "Vencido", urgente: "Urgente", a_aproximar: "A aproximar-se", agendado: "Agendado" };
const ALERTS = [90, 60, 30, 15, 7, 0];
const MONEY_FIELDS = ["iuc_amount_cents", "inspection_cost_cents", "insurance_amount_cents", "service_cost_cents"];
const fullDate = (iso?: string | null) => iso ? iso.slice(0, 10).split("-").reverse().join("/") : "—";

function Due({ item, label }: { item?: Row; label?: string }) {
  if (!item) return <>—</>;
  return <>{fullDate(item.due_date)}{label && <small>{label}</small>}
    <small>{item.expected_cents != null ? formatEuro(item.expected_cents) + " · " : ""}{item.days < 0 ? `${-item.days} dia(s) em atraso` : `daqui a ${item.days} dia(s)`}</small>
    <span className="obl-badges"><StatusBadge tone={STATE_TONE[item.state] || "neutral"} compact>{STATE_LABEL[item.state] || item.state}</StatusBadge>
      {!item.validated && <StatusBadge tone="warning" compact>Data a validar</StatusBadge>}</span></>;
}

export default function Vehicles({ store, stores, categories }: { store: string; stores: Row[]; categories: Row[] }) {
  const [data, setData] = useState<Row | null>(null), [error, setError] = useState(""), [notice, setNotice] = useState("");
  const [editing, setEditing] = useState<Row | null>(null), [statusFor, setStatusFor] = useState<{ status: string; vehicle: Row } | null>(null);
  const [version, setVersion] = useState(0);
  const refresh = () => setVersion(v => v + 1);
  useEffect(() => {
    let active = true;
    api(`/vehicles?${new URLSearchParams({ store_id: store })}`).then(r => { if (active) setData(r); }).catch(e => { if (active) setError((e as Error).message); });
    return () => { active = false; };
  }, [store, version]);
  async function open(id: string) {
    try { setEditing(await api(`/vehicles/${encodeURIComponent(id)}`)); } catch (e) { setError((e as Error).message); }
  }
  return <section className="obl-card" aria-label="Viaturas">
    <div className="obl-contracts-head">
      <h2><Car size={18} aria-hidden="true" />Viaturas</h2>
      <ActionButton type="button" className="primary" onClick={() => setEditing({})}><Plus size={16} aria-hidden="true" />Nova viatura</ActionButton>
    </div>
    {error && <Notice tone="error" onDismiss={() => setError("")}>{error}</Notice>}
    {notice && <Notice tone="success" onDismiss={() => setNotice("")}>{notice}</Notice>}
    {!data ? !error && <p role="status">A carregar viaturas…</p> : !data.vehicles.length ? <p className="dash-muted">Sem viaturas registadas. Registe-as para ser avisado do IUC, da inspeção, do seguro e das revisões.</p>
      : <div className="table-wrap"><table className="obl-grid" aria-label="Lista de viaturas">
        <thead><tr><th>Viatura</th><th>IUC</th><th>Inspeção</th><th>Seguro</th><th>Estado</th><th>Ações</th></tr></thead>
        <tbody>{data.vehicles.map((v: Row) => <tr key={v.id}>
          <td><button type="button" className="obl-link" onClick={() => void open(v.id)}>{v.plate}</button>
            <small>{[v.make, v.model].filter(Boolean).join(" ")}{v.fuel ? ` · ${v.fuel}` : ""}</small><small>{v.category_label} · {v.age_years} ano(s)</small></td>
          <td><Due item={v.upcoming.iuc} /></td>
          <td><Due item={v.upcoming.inspection} /></td>
          <td><Due item={v.upcoming.insurance} label={v.insurance_entity || undefined} /></td>
          <td><StatusBadge tone={v.status === "ativa" ? "success" : "neutral"} compact>{v.status_label}</StatusBadge></td>
          <td><ActionMenu label={`Ações da viatura ${v.plate}`} icon={<MoreHorizontal size={18} aria-hidden="true" />}>
            <ActionButton type="button" onClick={() => void open(v.id)}>Abrir ficha</ActionButton>
            {v.status === "ativa" ? <><ActionButton type="button" onClick={() => setStatusFor({ status: "vendida", vehicle: v })}>Registar venda</ActionButton>
              <ActionButton type="button" onClick={() => setStatusFor({ status: "abatida", vehicle: v })}>Registar abate</ActionButton></>
              : <ActionButton type="button" onClick={() => setStatusFor({ status: "ativa", vehicle: v })}>Reativar</ActionButton>}
          </ActionMenu></td></tr>)}</tbody></table></div>}
    <ul className="obl-notes">{(data?.notes || []).map((n: string) => <li key={n}>{n}</li>)}</ul>
    {editing && data && <VehicleDrawer value={editing} meta={data} store={store} stores={stores} categories={categories} onClose={() => setEditing(null)}
      onSaved={message => { setEditing(null); setNotice(message); refresh(); }} />}
    {statusFor && <StatusDialog {...statusFor} onClose={() => setStatusFor(null)} onDone={message => { setStatusFor(null); setNotice(message); refresh(); }} />}
  </section>;
}

function StatusDialog({ status, vehicle, onClose, onDone }: { status: string; vehicle: Row; onClose: () => void; onDone: (message: string) => void }) {
  const [reason, setReason] = useState(""), [error, setError] = useState(""), [busy, setBusy] = useState(false);
  const title = status === "vendida" ? "Registar venda" : status === "abatida" ? "Registar abate" : "Reativar viatura";
  async function confirm() {
    setBusy(true); setError("");
    try { await api(`/vehicles/${encodeURIComponent(vehicle.id)}/status`, { revision: vehicle.revision, status, reason }); onDone(`${title}: registado.`); }
    catch (e) { setError((e as Error).message); } finally { setBusy(false); }
  }
  return <AppDialog className="obl-action-dialog" aria-label={title} busy={busy} onRequestClose={onClose}>
    <h2>{title}</h2><p><strong>{vehicle.plate}</strong> {[vehicle.make, vehicle.model].filter(Boolean).join(" ")}</p>
    {error && <Notice tone="error">{error}</Notice>}
    {status !== "ativa" && <Notice tone="info">Os próximos prazos (IUC, inspeção, seguro, revisão) deixam de ser gerados; o histórico mantém-se.</Notice>}
    <label>Motivo<textarea rows={3} value={reason} maxLength={2000} onChange={e => setReason(e.target.value)} /></label>
    <div className="ui-dialog-actions"><ActionButton type="button" onClick={onClose}>Cancelar</ActionButton>
      <ActionButton type="button" className="primary" disabled={busy || reason.trim().length < 3} onClick={() => void confirm()}>{title}</ActionButton></div>
  </AppDialog>;
}

function VehicleDrawer({ value, meta, store, stores, categories, onClose, onSaved }: {
  value: Row; meta: Row; store: string; stores: Row[]; categories: Row[]; onClose: () => void; onSaved: (message: string) => void;
}) {
  const editing = !!value.id;
  const [form, setForm] = useState<Row>(() => {
    const base: Row = editing ? { ...value } : { plate: "", make: "", model: "", first_registration: "", category: "ligeiro_passageiros", fuel: "", store_id: store || null,
      responsible_id: "", iuc_validated: false, inspection_validated: false, last_inspection_date: "", inspection_manual_next: "", inspection_manual_years: "",
      insurance_entity: "", insurance_policy: "", insurance_renewal: "", insurance_periodicity: "anual", next_service_date: "", contract_id: "", category_id: "",
      alert_days: [60, 30, 15, 7, 0], notes: "", revision: 0 };
    for (const key of MONEY_FIELDS) base[key + "_text"] = centsInput(base[key]);
    for (const key of ["last_inspection_date", "inspection_manual_next", "insurance_renewal", "next_service_date", "contract_id", "category_id", "responsible_id"]) base[key] = base[key] || "";
    base.inspection_manual_years = base.inspection_manual_years || "";
    return base;
  });
  const [error, setError] = useState(""), [busy, setBusy] = useState(false);
  const set = (changes: Row) => setForm(current => ({ ...current, ...changes }));
  const manual = ["motociclo", "reboque", "outro"].includes(form.category);
  const money = Object.fromEntries(MONEY_FIELDS.map(key => [key, euroToCents(form[key + "_text"] || "")]));
  const invalidMoney = Object.values(money).some(v => Number.isNaN(v));
  async function save() {
    setBusy(true); setError("");
    try {
      const body = {
        revision: form.revision || 0, store_id: form.store_id || null, plate: form.plate, make: form.make, model: form.model, first_registration: form.first_registration,
        category: form.category, fuel: form.fuel, responsible_id: form.responsible_id || null, ...money, iuc_validated: !!form.iuc_validated,
        last_inspection_date: form.last_inspection_date || null, inspection_manual_next: manual ? form.inspection_manual_next || null : null,
        inspection_manual_years: manual && form.inspection_manual_years ? Number(form.inspection_manual_years) : null, inspection_validated: !!form.inspection_validated,
        insurance_entity: form.insurance_entity, insurance_policy: form.insurance_policy, insurance_renewal: form.insurance_renewal || null,
        insurance_periodicity: form.insurance_periodicity, next_service_date: form.next_service_date || null, contract_id: form.contract_id || null,
        category_id: form.category_id || null, alert_days: form.alert_days, notes: form.notes || "",
      };
      await api(editing ? `/vehicles/${encodeURIComponent(value.id)}` : "/vehicles", body, editing ? { method: "PUT" } : undefined);
      onSaved(editing ? "Viatura atualizada." : "Viatura criada; IUC, inspeção e restantes prazos foram gerados.");
    } catch (e) { setError((e as Error).message); } finally { setBusy(false); }
  }
  return <DetailDrawer className="obl-drawer" aria-label={editing ? "Ficha da viatura" : "Nova viatura"} busy={busy} onRequestClose={onClose}>
    <h2>{editing ? form.plate : "Nova viatura"}</h2>
    {error && <Notice tone="error">{error}</Notice>}
    <div className="obl-form">
      <label>Matrícula<input value={form.plate} maxLength={20} placeholder="AA-00-BB" onChange={e => set({ plate: e.target.value })} /></label>
      <label>Data da primeira matrícula<input type="date" value={form.first_registration} onChange={e => set({ first_registration: e.target.value })} /></label>
      <label>Marca<input value={form.make} maxLength={60} onChange={e => set({ make: e.target.value })} /></label>
      <label>Modelo<input value={form.model} maxLength={80} onChange={e => set({ model: e.target.value })} /></label>
      <label>Categoria<select value={form.category} onChange={e => set({ category: e.target.value })}>
        {Object.entries(meta.categories).map(([k, label]) => <option key={k} value={k}>{String(label)}</option>)}</select></label>
      <label>Combustível<input list="vehicle-fuels" value={form.fuel} maxLength={30} onChange={e => set({ fuel: e.target.value })} />
        <datalist id="vehicle-fuels">{meta.fuels.map((f: string) => <option key={f} value={f} />)}</datalist></label>
      <label>Âmbito<select value={form.store_id || ""} onChange={e => set({ store_id: e.target.value || null })}><option value="">Empresa</option>
        {stores.map(s => <option key={s.id} value={s.id}>{s.name}</option>)}</select></label>
      <label>Responsável<select value={form.responsible_id} onChange={e => set({ responsible_id: e.target.value })}><option value="">Sem responsável</option>
        {meta.users.map((u: Row) => <option key={u.id} value={u.id}>{u.name}</option>)}</select></label>
      <fieldset className="obl-wide"><legend>IUC</legend><div className="obl-form">
        <label>Valor previsto (€)<input inputMode="decimal" value={form.iuc_amount_cents_text} onChange={e => set({ iuc_amount_cents_text: e.target.value })} /></label>
        <label className="checkbox"><input type="checkbox" checked={!!form.iuc_validated} onChange={e => set({ iuc_validated: e.target.checked })} />Data validada (Portal das Finanças)</label>
        <p className="obl-wide obl-hint">Modelo: até ao último dia do mês da matrícula, todos os anos.</p></div></fieldset>
      <fieldset className="obl-wide"><legend>Inspeção periódica</legend><div className="obl-form">
        {editing && <p className="obl-wide obl-hint">{value.inspection_rule}</p>}
        {manual ? <><label>Próxima inspeção<input type="date" value={form.inspection_manual_next} onChange={e => set({ inspection_manual_next: e.target.value })} /></label>
          <label>Depois, de quantos em quantos anos (vazio = única)<input type="number" min="1" max="10" value={form.inspection_manual_years} onChange={e => set({ inspection_manual_years: e.target.value })} /></label></>
          : <label>Última inspeção (opcional)<input type="date" value={form.last_inspection_date} onChange={e => set({ last_inspection_date: e.target.value })} /></label>}
        <label>Custo previsto (€)<input inputMode="decimal" value={form.inspection_cost_cents_text} onChange={e => set({ inspection_cost_cents_text: e.target.value })} /></label>
        {!manual && <label className="checkbox"><input type="checkbox" checked={!!form.inspection_validated} onChange={e => set({ inspection_validated: e.target.checked })} />Data validada (IMT / centro de inspeção)</label>}
      </div></fieldset>
      <fieldset className="obl-wide"><legend>Seguro, revisão e contrato</legend><div className="obl-form">
        <label>Seguradora<input value={form.insurance_entity} maxLength={200} onChange={e => set({ insurance_entity: e.target.value })} /></label>
        <label>Apólice<input value={form.insurance_policy} maxLength={80} onChange={e => set({ insurance_policy: e.target.value })} /></label>
        <label>Renovação do seguro<input type="date" value={form.insurance_renewal} onChange={e => set({ insurance_renewal: e.target.value })} /></label>
        <label>Prémio (€)<input inputMode="decimal" value={form.insurance_amount_cents_text} onChange={e => set({ insurance_amount_cents_text: e.target.value })} /></label>
        <label>Periodicidade do prémio<select value={form.insurance_periodicity} onChange={e => set({ insurance_periodicity: e.target.value })}>
          {[["anual", "Anual"], ["semestral", "Semestral"], ["trimestral", "Trimestral"], ["mensal", "Mensal"]].map(([k, l]) => <option key={k} value={k}>{l}</option>)}</select></label>
        <label>Próxima revisão<input type="date" value={form.next_service_date} onChange={e => set({ next_service_date: e.target.value })} /></label>
        <label>Custo da revisão (€)<input inputMode="decimal" value={form.service_cost_cents_text} onChange={e => set({ service_cost_cents_text: e.target.value })} /></label>
        <label>Leasing / renting (contrato)<select value={form.contract_id} onChange={e => set({ contract_id: e.target.value })}><option value="">Sem contrato</option>
          {meta.contracts.map((c: Row) => <option key={c.id} value={c.id}>{c.label}</option>)}</select></label>
        <label>Rubrica financeira<select value={form.category_id} onChange={e => set({ category_id: e.target.value })}><option value="">Sem rubrica</option>
          {categories.filter(c => c.active !== false).map(c => <option key={c.id} value={c.id}>{c.name}</option>)}</select></label>
      </div></fieldset>
      <fieldset className="obl-wide"><legend>Alertas (dias antes)</legend><div className="obl-chips">
        {ALERTS.map(days => <label key={days} className="checkbox"><input type="checkbox" checked={form.alert_days.includes(days)}
          onChange={e => set({ alert_days: e.target.checked ? [...form.alert_days, days] : form.alert_days.filter((d: number) => d !== days) })} />{days ? `${days} dias` : "No dia"}</label>)}
      </div></fieldset>
      <label className="obl-wide">Observações<textarea rows={2} value={form.notes} maxLength={4000} onChange={e => set({ notes: e.target.value })} /></label>
    </div>
    {editing && <ArchiveDocs kind="vehicle" id={value.id} title="Documentos da viatura (DUA, apólice, certificados)" />}
    {editing && value.occurrences?.length > 0 && <section className="obl-history" aria-label="Prazos da viatura">
      <h3>Prazos e histórico</h3>
      <div className="table-wrap"><table><thead><tr><th>Data</th><th>Prazo</th><th className="numeric">Previsto</th><th className="numeric">Efetivo</th><th>Estado</th></tr></thead>
        <tbody>{value.occurrences.slice(0, 20).map((o: Row) => <tr key={o.id}><td>{fullDate(o.due_date)}</td><td>{o.kind}</td>
          <td className="numeric">{o.expected_cents == null ? "—" : formatEuro(o.expected_cents)}</td><td className="numeric">{o.actual_cents == null ? "—" : formatEuro(o.actual_cents)}</td>
          <td>{o.status === "pendente" ? (STATE_LABEL[o.state] || o.state) : o.status === "concluida" ? "Concluído" : o.status}</td></tr>)}</tbody></table></div>
      <p className="obl-hint">Conclua cada prazo (com o valor pago) na lista de prazos ou na Visão geral; concluir a inspeção cria a seguinte.</p>
    </section>}
    <div className="ui-dialog-actions"><ActionButton type="button" onClick={onClose}>Fechar</ActionButton>
      <ActionButton type="button" className="primary" disabled={busy || form.plate.trim().length < 4 || !form.first_registration || invalidMoney || (manual && !form.inspection_manual_next)} onClick={() => void save()}>
        {editing ? "Guardar alterações" : "Criar viatura"}</ActionButton></div>
  </DetailDrawer>;
}
