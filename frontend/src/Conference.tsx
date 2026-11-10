import { useEffect, useState } from "react";
import { AlertTriangle, Bot, CalendarClock, CheckCheck, FileDiff, FilePlus2, FileSearch, Landmark, Link2, PiggyBank, Tags, Wallet } from "lucide-react";
import { StatusBadge, type BusinessTone } from "./components/BusinessUI";
import { ActionButton, AppDialog, Notice } from "./components/InteractionUI";
import { ModuleTabs, PageHeader, useWorkspaceTab } from "./components/WorkspaceUI";
import { api } from "./lib/api";
import { formatEuro } from "./utils/format";
import "./Conference.css";

/**
 * Centro de Conferência: o que precisa de intervenção, de todos os módulos, com acesso direto ao módulo
 * onde se resolve; aprovação em lote só do que é seguro (as rotas de origem voltam a validar) e
 * automatizações autorizadas pelo administrador (por omissão desligadas, nunca financeiras).
 */

type Row = Record<string, any>;
const TABS = [{ id: "pendentes", label: "Por resolver" }, { id: "automatizacoes", label: "Automatizações" }] as const;
const ICONS: Record<string, typeof Tags> = {
  novos: FilePlus2, faltas: FileSearch, diferencas: FileDiff, propostas_documentos: Link2, rubricas: Tags, orcamento: PiggyBank,
  sem_orcamento: AlertTriangle, reconciliacao: Landmark, arquivo: Link2, prazos: CalendarClock,
};
const fullDate = (iso?: string | null) => iso ? iso.slice(0, 10).split("-").reverse().join("/") : "—";
const isoDay = (d: Date) => `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}-${String(d.getDate()).padStart(2, "0")}`;

export default function Conference({ stores, admin, navigate }: { stores: Row[]; admin: boolean; navigate: (page: string, params?: Record<string, string>) => void }) {
  const [tab, setTab] = useWorkspaceTab("conferencia", "pendentes", TABS.map(t => t.id));
  const today = new Date();
  const [filters, setFilters] = useState({ start: isoDay(new Date(today.getFullYear(), today.getMonth() - 2, today.getDate())), end: isoDay(today), store: "" });
  const [data, setData] = useState<Row | null>(null), [error, setError] = useState(""), [notice, setNotice] = useState(""), [busy, setBusy] = useState(false);
  const [approving, setApproving] = useState<Row | null>(null), [version, setVersion] = useState(0);
  useEffect(() => {
    let active = true;
    setData(null); setError("");
    api(`/conference/overview?${new URLSearchParams({ start: filters.start, end: filters.end, store_id: filters.store })}`)
      .then(r => { if (active) setData(r); }).catch(e => { if (active) setError((e as Error).message); });
    return () => { active = false; };
  }, [filters.start, filters.end, filters.store, version]);
  async function approve(section: Row) {
    setBusy(true); setError("");
    try {
      await api(section.safe.endpoint, section.safe.body);
      setApproving(null); setNotice(`${section.safe.count} aprovação(ões) aplicada(s) em «${section.title}».`); setVersion(v => v + 1);
    } catch (e) { setError((e as Error).message); } finally { setBusy(false); }
  }
  const set = (changes: Row) => setFilters(current => ({ ...current, ...changes }));
  return <div className="conf">
    <PageHeader title="Centro de Conferência" description="Tudo o que precisa de intervenção, de todos os módulos, num só lugar. Resolva no módulo de origem ou aprove em lote o que é seguro." />
    {error && <Notice tone="error" onDismiss={() => setError("")}>{error}</Notice>}
    {notice && <Notice tone="success" onDismiss={() => setNotice("")}>{notice}</Notice>}
    <ModuleTabs label="Áreas do centro de conferência" tabs={TABS} value={tab} onChange={setTab} />
    {tab === "pendentes" ? <>
      <div className="conf-filters filter-bar" role="group" aria-label="Período e estabelecimento">
        <label>De<input type="date" value={filters.start} onChange={e => set({ start: e.target.value })} /></label>
        <label>Até<input type="date" value={filters.end} onChange={e => set({ end: e.target.value })} /></label>
        <label>Estabelecimento<select value={filters.store} onChange={e => set({ store: e.target.value })}><option value="">Todos</option>
          {stores.map(s => <option key={s.id} value={s.id}>{s.name}</option>)}</select></label>
        {data && <StatusBadge tone={data.total ? "warning" : "success"}>{data.total ? `${data.total} situação(ões) por resolver` : "Nada por resolver"}</StatusBadge>}
      </div>
      {!data ? !error && <p role="status">A reunir o que precisa de intervenção…</p>
        : <div className="conf-grid">{data.sections.map((section: Row) => <SectionCard key={section.key} section={section}
          onOpen={() => navigate(section.page, section.params)} onApprove={() => setApproving(section)} />)}</div>}
      <ul className="conf-notes">{(data?.notes || []).map((n: string) => <li key={n}>{n}</li>)}</ul>
    </> : <Automation admin={admin} onChanged={message => { setNotice(message); setVersion(v => v + 1); }} onError={setError} />}
    {approving && <AppDialog className="conf-dialog" aria-label="Aprovar as seguras" busy={busy} onRequestClose={() => setApproving(null)}>
      <h2>Aprovar as seguras</h2>
      <p>«{approving.title}»: {approving.safe.count} proposta(s) com confiança igual ou superior a {approving.safe.threshold} %. Cada uma volta a ser validada; se alguma falhar, nenhuma é aplicada.</p>
      <div className="ui-dialog-actions"><ActionButton type="button" onClick={() => setApproving(null)}>Cancelar</ActionButton>
        <ActionButton type="button" className="primary" disabled={busy} onClick={() => void approve(approving)}><CheckCheck size={16} aria-hidden="true" />Aprovar {approving.safe.count}</ActionButton></div>
    </AppDialog>}
  </div>;
}

function SectionCard({ section, onOpen, onApprove }: { section: Row; onOpen: () => void; onApprove: () => void }) {
  const Icon = ICONS[section.key] || Wallet;
  return <section className="conf-card" aria-label={section.title}><div className="conf-inner ui-tone" data-tone={section.tone as BusinessTone}>
    <div className="conf-head">
      <span className="conf-icon" aria-hidden="true"><Icon size={20} /></span>
      <div><h2>{section.title}</h2>{section.note && <small>{section.note}</small>}</div>
      <strong className="conf-count">{section.count}</strong>
    </div>
    {section.amount_cents ? <p className="conf-amount">{formatEuro(section.amount_cents)}</p> : null}
    {section.items.length > 0 && <ul className="conf-items">{section.items.map((item: Row, index: number) => <li key={index}>
      <span>{item.label}<small>{[fullDate(item.date), item.detail].filter(Boolean).join(" · ")}</small></span>
      {item.amount_cents != null && <b>{formatEuro(item.amount_cents)}</b>}
    </li>)}</ul>}
    {!section.count && <p className="conf-ok">Nada por resolver.</p>}
    <div className="conf-actions">
      <ActionButton type="button" onClick={onOpen} aria-label={`Abrir ${section.title}`}>Abrir</ActionButton>
      {section.safe && <ActionButton type="button" className="primary" onClick={onApprove} aria-label={`Aprovar as seguras: ${section.title}`}><CheckCheck size={15} aria-hidden="true" />Aprovar {section.safe.count} segura(s)</ActionButton>}
    </div>
  </div></section>;
}

function Automation({ admin, onChanged, onError }: { admin: boolean; onChanged: (message: string) => void; onError: (message: string) => void }) {
  const [form, setForm] = useState<Row | null>(null), [busy, setBusy] = useState(false);
  useEffect(() => { api("/conference/automation").then(setForm).catch(e => onError((e as Error).message)); }, []);
  if (!form) return <p role="status">A carregar…</p>;
  const set = (changes: Row) => setForm({ ...form, ...changes });
  const number = (value: unknown) => Number(value);
  async function save() {
    setBusy(true);
    try {
      const saved = await api("/conference/automation", { revision: form!.revision, frequency: form!.frequency,
        classification: { enabled: !!form!.classification.enabled, min_confidence: number(form!.classification.min_confidence) },
        budget_links: { enabled: !!form!.budget_links.enabled, min_confidence: number(form!.budget_links.min_confidence) },
        batch_classification: number(form!.batch_classification), batch_budget_links: number(form!.batch_budget_links) }, { method: "PUT" });
      setForm(saved); onChanged("Automatizações guardadas.");
    } catch (e) { onError((e as Error).message); } finally { setBusy(false); }
  }
  async function run() {
    setBusy(true);
    try {
      const result = await api("/conference/automation/run", {});
      onChanged(`Automatizações executadas: ${result.classified} classificação(ões) e ${result.linked} associação(ões) ao orçamento.${result.errors.length ? ` ${result.errors.length} não aplicada(s).` : ""}`);
      setForm(await api("/conference/automation"));
    } catch (e) { onError((e as Error).message); } finally { setBusy(false); }
  }
  const last = form.last_result || {};
  return <section className="conf-card conf-automation" aria-label="Automatizações">
    <h2><Bot size={20} aria-hidden="true" />Automatizações</h2>
    <p>Só operações inequívocas e autorizadas. Ficam na auditoria em nome de quem as ativou, marcadas como automáticas. {!admin && "Só o administrador pode alterar."}</p>
    <fieldset disabled={!admin || busy}>
      <label className="checkbox"><input type="checkbox" checked={!!form.classification.enabled} onChange={e => set({ classification: { ...form.classification, enabled: e.target.checked } })} />
        Aplicar a classificação de rubricas sugerida por uma regra aprovada</label>
      <label>Confiança mínima (85–99 %)<input type="number" min="85" max="99" value={form.classification.min_confidence} onChange={e => set({ classification: { ...form.classification, min_confidence: e.target.value } })} /></label>
      <label className="checkbox"><input type="checkbox" checked={!!form.budget_links.enabled} onChange={e => set({ budget_links: { ...form.budget_links, enabled: e.target.checked } })} />
        Associar ao orçamento as despesas com proposta forte e sem alternativa</label>
      <label>Confiança mínima (85–99 %)<input type="number" min="85" max="99" value={form.budget_links.min_confidence} onChange={e => set({ budget_links: { ...form.budget_links, min_confidence: e.target.value } })} /></label>
      <label>Execução<select value={form.frequency} onChange={e => set({ frequency: e.target.value })}><option value="manual">Só quando pedir («Executar agora»)</option><option value="diaria">Uma vez por dia</option></select></label>
      <label>Aprovação em lote · classificação (confiança mínima)<input type="number" min="70" max="99" value={form.batch_classification} onChange={e => set({ batch_classification: e.target.value })} /></label>
      <label>Aprovação em lote · orçamento (confiança mínima)<input type="number" min="70" max="99" value={form.batch_budget_links} onChange={e => set({ batch_budget_links: e.target.value })} /></label>
    </fieldset>
    <div className="conf-never"><h3>Nunca automático</h3><ul>
      <li>Pagamentos e liquidações</li><li>Reconciliação bancária (as regras da conta só propõem)</li><li>Lançamentos de compras e alterações de documentos</li>
      <li>Escritas no ERP (preços, documentos, pagamentos)</li><li>Conclusão de prazos das Obrigações</li></ul>
      <small>As relações do arquivo confirmadas por QR têm a sua própria opção em Configurações do arquivo.</small></div>
    <p className="conf-last">{form.last_run_at ? `Última execução: ${new Date(form.last_run_at).toLocaleString("pt-PT", { timeZone: "Europe/Lisbon" })} · ${last.classified || 0} classificação(ões), ${last.linked || 0} associação(ões)${last.errors?.length ? ` · ${last.errors.length} não aplicada(s)` : ""}` : "Ainda não executadas."}
      {form.authorized_by && ` Autorizadas por ${form.authorized_by}.`}</p>
    {admin && <div className="ui-dialog-actions">
      <ActionButton type="button" disabled={busy || !(form.classification.enabled || form.budget_links.enabled)} onClick={() => void run()}>Executar agora</ActionButton>
      <ActionButton type="button" className="primary" disabled={busy} onClick={() => void save()}>Guardar</ActionButton></div>}
  </section>;
}
