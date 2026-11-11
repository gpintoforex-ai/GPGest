import { useEffect, useMemo, useState, type ReactNode } from "react";
import { AlertTriangle, BadgeCheck, Car, CalendarCheck, CalendarClock, CalendarDays, CheckCircle2, ChevronLeft, ChevronRight, Database, FileText, Landmark, MoreHorizontal, Plus, ShieldCheck, Tag, Umbrella } from "lucide-react";
import { StatusBadge, type BusinessTone } from "./components/BusinessUI";
import { ActionButton, AppDialog, Notice } from "./components/InteractionUI";
import { ActionMenu, DetailDrawer, ModuleTabs, PageHeader, useWorkspaceTab } from "./components/WorkspaceUI";
import { api } from "./lib/api";
import { euroToCents, formatEuro } from "./utils/format";
import Contracts from "./Contracts";
import "./Obligations.css";

/**
 * Obrigações, contratos e prazos: painel (indicadores, próximos prazos, calendário, categorias), listas
 * por categoria e ficha da obrigação. As ocorrências só ficam concluídas por confirmação do utilizador;
 * datas legais indicam a origem e se foram validadas.
 */

type Row = Record<string, any>;
const CATEGORY_META: Record<string, [string, BusinessTone, typeof Tag]> = {
  fiscal: ["Fiscal", "suggestion", Landmark], contrato: ["Contrato", "success", FileText], veiculo: ["Veículo", "info", Car],
  inspecao: ["Inspeção", "warning", ShieldCheck], seguro: ["Seguro", "accent", Umbrella], licenca: ["Licença", "neutral", BadgeCheck], outro: ["Outro", "neutral", Tag],
};
const STATE_TONE: Record<string, BusinessTone> = {
  vencido: "danger", urgente: "danger", a_aproximar: "warning", agendado: "info", concluido: "success", cancelado: "neutral", nao_aplicavel: "neutral",
};
const STATE_LABEL: Record<string, string> = {
  agendado: "Agendado", a_aproximar: "A aproximar-se", urgente: "Urgente", vencido: "Vencido", concluido: "Concluído", cancelado: "Cancelado", nao_aplicavel: "Não aplicável",
};
const TABS = [
  { id: "visao", label: "Visão geral" }, { id: "prazos", label: "Todos os prazos" }, { id: "fiscal", label: "Fiscais" }, { id: "contrato", label: "Contratos" },
  { id: "veiculo", label: "Veículos" }, { id: "inspecao", label: "Inspeções" }, { id: "outros", label: "Outros prazos" }, { id: "calendario", label: "Calendário" },
] as const;
const OTHER = ["seguro", "licenca", "outro"];
const KINDS: Record<string, string[]> = {
  fiscal: ["IVA — declaração periódica", "IVA — pagamento", "IRC — Modelo 22", "IRC — pagamento por conta", "IES", "Retenções na fonte", "Declaração Mensal de Remunerações", "Segurança Social", "IMI", "IUC"],
  contrato: ["Telecomunicações", "Renda", "Software", "Energia", "Manutenção", "Leasing / renting"],
  veiculo: ["IUC", "Inspeção periódica", "Seguro automóvel", "Revisão"],
  inspecao: ["Extintores", "Segurança contra incêndios", "Equipamentos", "Certificação", "Manutenção obrigatória"],
  seguro: ["Multirriscos", "Responsabilidade civil", "Acidentes de trabalho", "Automóvel"],
  licenca: ["Licença de utilização", "Licença de atividade", "Alvará"], outro: [],
};
const ALERTS = [90, 60, 30, 15, 7, 0];
const MONTHS = ["janeiro", "fevereiro", "março", "abril", "maio", "junho", "julho", "agosto", "setembro", "outubro", "novembro", "dezembro"];
const short = (iso: string) => { const [, m, d] = iso.split("-"); return `${Number(d)} ${MONTHS[Number(m) - 1].slice(0, 3)}`; };
const fullDate = (iso?: string | null) => iso ? iso.slice(0, 10).split("-").reverse().join("/") : "—";
const daysText = (days: number) => days === 0 ? "Hoje" : days < 0 ? `${-days} dia(s) em atraso` : `${days} dia(s)`;

export const toCents = euroToCents;
const centsText = (cents: number | null | undefined) => cents == null ? "" : `${Math.floor(cents / 100)},${String(cents % 100).padStart(2, "0")}`;

function CategoryBadge({ category }: { category: string }) {
  const [label, tone] = CATEGORY_META[category] || CATEGORY_META.outro;
  return <StatusBadge tone={tone} compact>{label}</StatusBadge>;
}
function CategoryIcon({ category }: { category: string }) {
  const [, tone, Icon] = CATEGORY_META[category] || CATEGORY_META.outro;
  return <span className="obl-icon ui-tone" data-tone={tone} aria-hidden="true"><Icon size={18} /></span>;
}

export default function Obligations({ store, stores, categories }: { store: string; stores: Row[]; categories: Row[] }) {
  const [tab, setTab] = useWorkspaceTab("obrigacoes", "visao", TABS.map(t => t.id));
  const [month, setMonth] = useState("");
  const [data, setData] = useState<Row | null>(null);
  const [list, setList] = useState<Row[] | null>(null);
  const [filters, setFilters] = useState({ state: "", responsible: "", q: "" });
  const [error, setError] = useState(""), [notice, setNotice] = useState("");
  const [version, setVersion] = useState(0);
  const [editing, setEditing] = useState<Row | null>(null);
  const [acting, setActing] = useState<{ action: string; item: Row } | null>(null);
  const refresh = () => setVersion(v => v + 1);

  useEffect(() => {
    let active = true;
    setError("");
    api(`/obligations/overview?${new URLSearchParams({ store_id: store, month })}`).then(r => { if (active) setData(r); }).catch(e => { if (active) setError((e as Error).message); });
    return () => { active = false; };
  }, [store, month, version]);
  const category = tab === "outros" ? "" : ["fiscal", "contrato", "veiculo", "inspecao"].includes(tab) ? tab : "";
  useEffect(() => {
    if (["visao", "calendario"].includes(tab)) return;
    let active = true;
    setList(null);
    api(`/obligations/occurrences?${new URLSearchParams({ store_id: store, category })}`).then(r => { if (active) setList(r); }).catch(e => { if (active) setError((e as Error).message); });
    return () => { active = false; };
  }, [tab, store, category, version]);

  const drill = (state: string) => { setFilters({ state, responsible: "", q: "" }); setTab("prazos"); };
  async function openObligation(id: string) {
    try { setEditing(await api(`/obligations/${encodeURIComponent(id)}`)); } catch (e) { setError((e as Error).message); }
  }
  const actions = (item: Row) => <ActionMenu label={`Ações de ${item.title}`} icon={<MoreHorizontal size={18} aria-hidden="true" />}>
    {item.status === "pendente" && <>
      <ActionButton type="button" onClick={() => setActing({ action: "complete", item })}>Concluir</ActionButton>
      <ActionButton type="button" onClick={() => setActing({ action: "reschedule", item })}>Alterar data-limite</ActionButton>
      <ActionButton type="button" onClick={() => setActing({ action: "not-applicable", item })}>Não aplicável</ActionButton>
      <ActionButton type="button" onClick={() => setActing({ action: "cancel", item })}>Cancelar</ActionButton>
    </>}
    {item.status !== "pendente" && <ActionButton type="button" onClick={() => setActing({ action: "reopen", item })}>Reabrir</ActionButton>}
    {!item.read && <ActionButton type="button" onClick={() => void act("read", item, {})}>Confirmar leitura</ActionButton>}
    <ActionButton type="button" onClick={() => void openObligation(item.obligation_id)}>Abrir ficha</ActionButton>
  </ActionMenu>;
  async function act(action: string, item: Row, body: Row) {
    try {
      await api(`/obligations/occurrences/${encodeURIComponent(item.id)}/${action}`, { revision: item.revision, ...body });
      setActing(null);
      setNotice(action === "read" ? "Leitura confirmada." : "Prazo atualizado.");
      refresh();
    } catch (e) { setError((e as Error).message); }
  }

  const kpis = data?.kpis;
  const filtered = useMemo(() => (list || []).filter(item =>
    (tab !== "outros" || OTHER.includes(item.category))
    && (!filters.state || filters.state.split(",").includes(item.state))
    && (!filters.responsible || item.responsible_id === filters.responsible)
    && (!filters.q || `${item.title} ${item.entity_name} ${item.kind}`.toLowerCase().includes(filters.q.toLowerCase()))), [list, filters, tab]);

  return <div className="obl">
    <PageHeader title="Obrigações, Contratos e Prazos" description="Obrigações fiscais, renovações de contratos, IUC, inspeções, seguros, licenças e outros prazos da empresa."
      actions={<ActionButton type="button" className="primary" onClick={() => setEditing({})}><Plus size={16} aria-hidden="true" />Novo prazo</ActionButton>} />
    {error && <Notice tone="error">{error}</Notice>}
    {notice && <Notice tone="success">{notice}</Notice>}
    <div className="obl-kpis" role="group" aria-label="Indicadores de prazos">
      <Kpi tone="danger" icon={AlertTriangle} label="Vencidas" value={kpis?.overdue} onOpen={() => drill("vencido")} />
      <Kpi tone="danger" icon={CalendarClock} label="Prazos a expirar" note="≤ 7 dias" value={kpis?.next_7} onOpen={() => drill("urgente")} />
      <Kpi tone="warning" icon={CalendarDays} label="Próximos 30 dias" value={kpis?.next_30} onOpen={() => drill("urgente,a_aproximar")}
        note={kpis?.commitments_30_cents ? `${formatEuro(kpis.commitments_30_cents)} previstos` : undefined} />
      <Kpi tone="info" icon={CalendarCheck} label="Próximos 90 dias" value={kpis?.next_90} onOpen={() => drill("urgente,a_aproximar,agendado")}
        note={kpis?.commitments_90_cents ? `${formatEuro(kpis.commitments_90_cents)} previstos` : undefined} />
      <Kpi tone="success" icon={CheckCircle2} label="Em dia" value={kpis?.up_to_date} note={kpis ? `${kpis.done_year} concluída(s) este ano` : undefined} onOpen={() => drill("concluido")} />
      <Kpi tone="suggestion" icon={Database} label="Total de registos" value={kpis?.total} onOpen={() => drill("")} />
    </div>
    <ModuleTabs label="Áreas das obrigações" tabs={TABS} value={tab} onChange={setTab} />
    {!data ? !error && <p role="status">A carregar prazos…</p>
      : tab === "visao" ? <Overview data={data} actions={actions} onAll={() => drill("")} onMonth={setMonth} onOpen={openObligation} />
      : tab === "calendario" ? <section className="obl-card"><Calendar data={data} large onMonth={setMonth} /></section>
      : tab === "contrato" ? <Contracts store={store} stores={stores} categories={categories} />
      : <section className="obl-card" aria-label="Lista de prazos">
        <div className="obl-filters filter-bar" role="group" aria-label="Filtrar prazos">
          <label>Pesquisar<input type="search" value={filters.q} placeholder="Título, entidade ou tipo" onChange={e => setFilters({ ...filters, q: e.target.value })} /></label>
          <label>Estado<select value={filters.state} onChange={e => setFilters({ ...filters, state: e.target.value })}>
            <option value="">Todos</option>
            {filters.state.includes(",") && <option value={filters.state}>Selecionados no indicador</option>}
            {Object.entries(STATE_LABEL).map(([key, label]) => <option key={key} value={key}>{label}</option>)}</select></label>
          <label>Responsável<select value={filters.responsible} onChange={e => setFilters({ ...filters, responsible: e.target.value })}>
            <option value="">Todos</option>{data.users.map((u: Row) => <option key={u.id} value={u.id}>{u.name}</option>)}</select></label>
        </div>
        {!list ? <p role="status">A carregar…</p> : !filtered.length ? <p className="dash-muted">Sem prazos com estes filtros.</p>
          : <div className="table-wrap"><table className="obl-grid" aria-label="Prazos">
            <thead><tr><th>Data-limite</th><th>Prazo</th><th>Categoria</th><th>Periodicidade</th><th className="numeric">Previsto</th><th className="numeric">Efetivo</th><th>Responsável</th><th>Estado</th><th>Ações</th></tr></thead>
            <tbody>{filtered.map(item => <tr key={item.id}>
              <td>{fullDate(item.due_date)}<small>{item.status === "pendente" ? daysText(item.days) : ""}</small></td>
              <td><strong>{item.title}</strong><small>{[item.kind, item.entity_name].filter(Boolean).join(" · ")}</small>
                {!item.date_validated && <small className="obl-unvalidated">Data a validar</small>}{item.date_origin && <small>Data alterada: {item.date_origin}</small>}</td>
              <td><CategoryBadge category={item.category} /></td><td>{item.periodicity}</td>
              <td className="numeric">{item.expected_cents == null ? "—" : formatEuro(item.expected_cents)}</td>
              <td className="numeric">{item.actual_cents == null ? "—" : formatEuro(item.actual_cents)}</td>
              <td>{item.responsible || "—"}</td>
              <td><StatusBadge tone={STATE_TONE[item.state]} compact>{STATE_LABEL[item.state]}</StatusBadge></td>
              <td>{actions(item)}</td></tr>)}</tbody></table></div>}
      </section>}
    {editing && <ObligationDrawer value={editing} data={data} stores={stores} categories={categories} store={store} onClose={() => setEditing(null)}
      onSaved={message => { setEditing(null); setNotice(message); refresh(); }} actions={actions} />}
    {acting && <ActionDialog {...acting} onClose={() => setActing(null)} onConfirm={body => void act(acting.action, acting.item, body)} />}
  </div>;
}

function Kpi({ tone, icon: Icon, label, value, note, onOpen }: { tone: BusinessTone; icon: typeof Tag; label: string; value?: number; note?: string; onOpen: () => void }) {
  return <ActionButton type="button" className="obl-kpi ui-tone" data-tone={tone} inferAction={false} onClick={onOpen} aria-label={`${label}: ${value ?? "—"}. Ver prazos`}>
    <span className="obl-kpi-icon" aria-hidden="true"><Icon size={22} /></span>
    <span className="obl-kpi-text"><span>{label}</span><strong>{value ?? "—"}</strong>{note && <small>{note}</small>}</span>
  </ActionButton>;
}

function Overview({ data, actions, onAll, onMonth, onOpen }: { data: Row; actions: (item: Row) => ReactNode; onAll: () => void; onMonth: (m: string) => void; onOpen: (id: string) => void }) {
  const total = data.categories.reduce((sum: number, c: Row) => sum + c.count, 0);
  const maxMonth = Math.max(1, ...data.per_month.map((m: Row) => m.count));
  return <div className="obl-overview">
    <section className="obl-card obl-upcoming" aria-labelledby="obl-upcoming">
      <h2 id="obl-upcoming"><CalendarClock size={20} aria-hidden="true" />Próximos prazos</h2>
      {!data.upcoming.length ? <p className="dash-muted">Sem prazos nos próximos 90 dias.</p> : <ul>{data.upcoming.slice(0, 8).map((item: Row) => <li key={item.id}>
        <span className="obl-date ui-tone" data-tone={STATE_TONE[item.state]}><b>{short(item.due_date)}</b><small>{daysText(item.days)}</small></span>
        <CategoryIcon category={item.category} />
        <span className="obl-item"><strong>{item.title}</strong><small>{[item.entity_name || item.kind, item.expected_cents != null ? formatEuro(item.expected_cents) : ""].filter(Boolean).join(" · ")}</small></span>
        <CategoryBadge category={item.category} />{actions(item)}</li>)}</ul>}
      <ActionButton type="button" className="dash-link" onClick={onAll}>Ver todos os prazos</ActionButton>
    </section>
    <section className="obl-card" aria-label="Calendário"><Calendar data={data} onMonth={onMonth} /></section>
    <section className="obl-card obl-categories" aria-labelledby="obl-categories">
      <h2 id="obl-categories"><Tag size={20} aria-hidden="true" />Categorias</h2>
      <Donut parts={data.categories} total={total} />
      <ul>{data.categories.filter((c: Row) => c.count).map((c: Row) => <li key={c.key}><span className="obl-dot ui-tone" data-tone={CATEGORY_META[c.key][1]} />{c.label}<b>{c.count}</b></li>)}</ul>
    </section>
    <section className="obl-card" aria-labelledby="obl-months">
      <h2 id="obl-months"><CalendarDays size={20} aria-hidden="true" />Prazos por mês <small>(próximos 6 meses)</small></h2>
      <ol className="obl-bars" aria-label="Prazos por mês">{data.per_month.map((m: Row) => <li key={m.month} aria-label={`${MONTHS[Number(m.month.slice(5)) - 1]}: ${m.count} prazo(s)`}>
        <b>{m.count}</b><span style={{ height: `${Math.max(4, m.count / maxMonth * 100)}%` }} /><small>{MONTHS[Number(m.month.slice(5)) - 1].slice(0, 3)}</small></li>)}</ol>
    </section>
    <section className="obl-card" aria-labelledby="obl-fiscal">
      <h2 id="obl-fiscal"><Landmark size={20} aria-hidden="true" />Obrigações fiscais principais</h2>
      {!data.fiscal.length ? <p className="dash-muted">Sem obrigações fiscais registadas. Use «Novo prazo» com a categoria Fiscal.</p>
        : <ul className="obl-simple">{data.fiscal.map((row: Row) => <li key={row.id}>
          {row.next && ["vencido", "urgente"].includes(row.next.state) ? <AlertTriangle size={18} className="obl-warn" aria-hidden="true" /> : <CheckCircle2 size={18} className="obl-ok" aria-hidden="true" />}
          <ActionButton type="button" className="dash-link" onClick={() => onOpen(row.id)}>{row.title} ({row.periodicity_label.toLowerCase()})</ActionButton>
          <small>{row.next ? `vence em ${fullDate(row.next.due_date)}` : "sem prazo pendente"}{!row.date_validated && " · data a validar"}</small></li>)}</ul>}
    </section>
    <section className="obl-card" aria-labelledby="obl-contracts">
      <h2 id="obl-contracts"><FileText size={20} aria-hidden="true" />Contratos em renovação <small>(próximos 90 dias)</small></h2>
      {!data.contracts.length ? <p className="dash-muted">Sem contratos a renovar nos próximos 90 dias.</p>
        : <ul className="obl-simple">{data.contracts.map((item: Row) => <li key={item.id}><FileText size={18} aria-hidden="true" />
          <ActionButton type="button" className="dash-link" onClick={() => onOpen(item.obligation_id)}>{item.title}</ActionButton><small><CalendarDays size={14} aria-hidden="true" /> {short(item.due_date)}</small></li>)}</ul>}
    </section>
  </div>;
}

function Donut({ parts, total }: { parts: Row[]; total: number }) {
  let offset = 0;
  const radius = 42, length = 2 * Math.PI * radius;
  return <div className="obl-donut">
    <svg viewBox="0 0 100 100" role="img" aria-label={`${total} prazos por categoria`}>
      <circle cx="50" cy="50" r={radius} className="obl-donut-track" />
      {total > 0 && parts.filter(p => p.count).map(p => {
        const share = p.count / total * length;
        const segment = <circle key={p.key} cx="50" cy="50" r={radius} className="obl-donut-part ui-tone" data-tone={CATEGORY_META[p.key][1]}
          strokeDasharray={`${share} ${length - share}`} strokeDashoffset={-offset} />;
        offset += share;
        return segment;
      })}
    </svg>
    <span><strong>{total}</strong><small>prazos</small></span>
  </div>;
}

function Calendar({ data, onMonth, large = false }: { data: Row; onMonth: (m: string) => void; large?: boolean }) {
  const [year, month] = data.calendar.month.split("-").map(Number);
  const first = new Date(Date.UTC(year, month - 1, 1));
  const days = new Date(Date.UTC(year, month, 0)).getUTCDate();
  const lead = (first.getUTCDay() + 6) % 7;
  const shift = (delta: number) => { const d = new Date(Date.UTC(year, month - 1 + delta, 1)); onMonth(d.toISOString().slice(0, 7)); };
  const cells = [...Array.from({ length: lead }, () => null), ...Array.from({ length: days }, (_, i) => i + 1)];
  return <div className={`obl-calendar${large ? " is-large" : ""}`}>
    <div className="obl-calendar-head">
      <ActionButton type="button" aria-label="Mês anterior" onClick={() => shift(-1)}><ChevronLeft size={18} aria-hidden="true" /></ActionButton>
      <h2>{MONTHS[month - 1][0].toUpperCase() + MONTHS[month - 1].slice(1)} {year}</h2>
      <ActionButton type="button" aria-label="Mês seguinte" onClick={() => shift(1)}><ChevronRight size={18} aria-hidden="true" /></ActionButton>
    </div>
    <div className="obl-calendar-grid" role="grid" aria-label={`Prazos de ${MONTHS[month - 1]} de ${year}`}>
      {["Seg", "Ter", "Qua", "Qui", "Sex", "Sáb", "Dom"].map(d => <span key={d} className="obl-weekday" role="columnheader">{d}</span>)}
      {cells.map((dayNumber, index) => {
        if (!dayNumber) return <span key={`e${index}`} />;
        const iso = `${data.calendar.month}-${String(dayNumber).padStart(2, "0")}`;
        const items: Row[] = data.calendar.days[iso] || [];
        const worst = items.find(i => ["vencido", "urgente"].includes(i.state)) || items.find(i => i.state === "a_aproximar") || items[0];
        return <span key={iso} role="gridcell" className={`obl-day${iso === data.today ? " is-today" : ""}${items.length ? " has-items" : ""}`}
          data-tone={worst ? STATE_TONE[worst.state] : undefined} aria-label={`${dayNumber}${items.length ? `: ${items.map(i => i.title).join(", ")}` : ""}`}
          title={items.map(i => `${i.title} (${STATE_LABEL[i.state]})`).join("\n") || undefined}>
          <b>{dayNumber}</b>{large ? items.map(i => <small key={i.id} className="ui-tone" data-tone={CATEGORY_META[i.category][1]}>{i.title}</small>) : items.length > 0 && <i aria-hidden="true" />}
        </span>;
      })}
    </div>
  </div>;
}

function ActionDialog({ action, item, onClose, onConfirm }: { action: string; item: Row; onClose: () => void; onConfirm: (body: Row) => void }) {
  const [reason, setReason] = useState(""), [amount, setAmount] = useState(centsText(item.expected_cents)), [due, setDue] = useState(item.due_date);
  const titles: Record<string, string> = { complete: "Concluir prazo", reschedule: "Alterar data-limite", "not-applicable": "Marcar como não aplicável", cancel: "Cancelar prazo", reopen: "Reabrir prazo" };
  const cents = toCents(amount);
  const needsReason = action !== "complete";
  const invalid = (needsReason && reason.trim().length < 3) || (action === "complete" && Number.isNaN(cents)) || (action === "reschedule" && !due);
  return <AppDialog className="obl-action-dialog" aria-label={titles[action]} guardChanges={false} onRequestClose={onClose}>
    <h2>{titles[action]}</h2>
    <p><strong>{item.title}</strong> · data-limite {fullDate(item.due_date)}</p>
    {action === "complete" && <><Notice tone="info">Confirme só depois de cumprida a obrigação (por exemplo, declaração entregue ou pagamento feito). Um movimento financeiro semelhante não conclui o prazo.</Notice>
      <label>Valor efetivo (€)<input inputMode="decimal" value={amount} onChange={e => setAmount(e.target.value)} placeholder="Opcional" /></label></>}
    {action === "reschedule" && <label>Nova data-limite<input type="date" value={due} onChange={e => setDue(e.target.value)} /></label>}
    <label>{action === "complete" ? "Observação (opcional)" : action === "reschedule" ? "Origem da alteração (por exemplo, prorrogação publicada)" : "Motivo"}
      <textarea value={reason} rows={3} maxLength={2000} onChange={e => setReason(e.target.value)} /></label>
    <div className="ui-dialog-actions"><ActionButton type="button" onClick={onClose}>Voltar</ActionButton>
      <ActionButton type="button" className="primary" disabled={invalid} onClick={() => onConfirm({ reason, ...(action === "complete" && cents !== null ? { actual_cents: cents } : {}), ...(action === "reschedule" ? { due_date: due } : {}) })}>{titles[action]}</ActionButton></div>
  </AppDialog>;
}

function ObligationDrawer({ value, data, stores, categories, store, onClose, onSaved, actions }: {
  value: Row; data: Row | null; stores: Row[]; categories: Row[]; store: string; onClose: () => void; onSaved: (message: string) => void; actions: (item: Row) => ReactNode;
}) {
  const editing = !!value.id;
  const [form, setForm] = useState<Row>(() => editing ? { ...value, amount: centsText(value.amount_cents), vat_rate: value.vat_rate ?? "" }
    : { category: "outro", kind: "", title: "", description: "", store_id: store || null, entity_name: "", category_id: "", responsible_id: "", periodicity: "anual", every_months: "",
      start_date: "", end_date: "", alert_days: [90, 60, 30, 15, 7, 0], amount: "", vat_rate: "", date_source: "manual", date_note: "", date_validated: true, revision: 0 });
  const [error, setError] = useState(""), [busy, setBusy] = useState(false);
  const set = (changes: Row) => setForm(current => ({ ...current, ...changes }));
  const cents = toCents(form.amount || "");
  async function save() {
    setBusy(true); setError("");
    try {
      const body = {
        revision: form.revision || 0, store_id: form.store_id || null, category: form.category, kind: form.kind, title: form.title, description: form.description || "",
        entity_id: form.entity_id || null, entity_name: form.entity_name || "", category_id: form.category_id || null, responsible_id: form.responsible_id || null,
        periodicity: form.periodicity, every_months: form.periodicity === "personalizada" ? Number(form.every_months) || null : null,
        start_date: form.start_date, end_date: form.end_date || null, alert_days: form.alert_days, amount_cents: cents, vat_rate: form.vat_rate === "" ? null : form.vat_rate,
        date_source: form.date_source, date_note: form.date_note || "", date_validated: !!form.date_validated, data: form.data || {},
      };
      await api(editing ? `/obligations/${encodeURIComponent(value.id)}` : "/obligations", body, editing ? { method: "PUT" } : undefined);
      onSaved(editing ? "Obrigação atualizada." : "Prazo criado.");
    } catch (e) { setError((e as Error).message); } finally { setBusy(false); }
  }
  const legal = form.category === "fiscal" || ["IUC", "Inspeção periódica"].includes(form.kind);
  return <DetailDrawer className="obl-drawer" aria-label={editing ? "Ficha da obrigação" : "Novo prazo"} busy={busy} onRequestClose={onClose}>
    <h2>{editing ? form.title : "Novo prazo"}</h2>
    {error && <Notice tone="error">{error}</Notice>}
    {value.contract_id && <Notice tone="info">Prazo gerido pelo contrato: altere as datas, o valor e os alertas na ficha do contrato (separador Contratos). Aqui pode tratar cada ocorrência.</Notice>}
    <div className="obl-form">
      <label>Categoria<select value={form.category} onChange={e => set({ category: e.target.value, kind: "", ...(e.target.value === "fiscal" && !editing ? { date_source: "regra_legal", date_validated: false } : {}) })}>
        {Object.entries(CATEGORY_META).map(([key, [label]]) => <option key={key} value={key}>{label}</option>)}</select></label>
      <label>Tipo<input list="obl-kinds" value={form.kind} maxLength={60} onChange={e => set({ kind: e.target.value })} />
        <datalist id="obl-kinds">{(KINDS[form.category] || []).map(kind => <option key={kind} value={kind} />)}</datalist></label>
      <label className="obl-wide">Título<input value={form.title} maxLength={200} onChange={e => set({ title: e.target.value })} placeholder="Por exemplo, IUC — 00-AA-00 ou Renovação MEO" /></label>
      <label>Âmbito<select value={form.store_id || ""} onChange={e => set({ store_id: e.target.value || null })}><option value="">Empresa</option>
        {stores.map(s => <option key={s.id} value={s.id}>{s.name}</option>)}</select></label>
      <label>Entidade<input value={form.entity_name} maxLength={200} onChange={e => set({ entity_name: e.target.value })} placeholder="Fornecedor, AT, Segurança Social…" /></label>
      <label>Responsável<select value={form.responsible_id || ""} onChange={e => set({ responsible_id: e.target.value })}><option value="">Sem responsável</option>
        {(data?.users || []).map((u: Row) => <option key={u.id} value={u.id}>{u.name}</option>)}</select></label>
      <label>Rubrica<select value={form.category_id || ""} onChange={e => set({ category_id: e.target.value })}><option value="">Sem rubrica</option>
        {categories.filter(c => c.active !== false).map(c => <option key={c.id} value={c.id}>{c.name}</option>)}</select></label>
      <label>Periodicidade<select value={form.periodicity} onChange={e => set({ periodicity: e.target.value })}>
        {Object.entries(data?.labels?.periodicities || { anual: "Anual" }).map(([key, label]) => <option key={key} value={key}>{String(label)}</option>)}</select></label>
      {form.periodicity === "personalizada" && <label>De quantos em quantos meses<input type="number" min="1" max="120" value={form.every_months || ""} onChange={e => set({ every_months: e.target.value })} /></label>}
      <label>{form.periodicity === "unica" ? "Data-limite" : "Primeira data-limite"}<input type="date" value={form.start_date} onChange={e => set({ start_date: e.target.value })} /></label>
      {form.periodicity !== "unica" && <label>Termina em (opcional)<input type="date" value={form.end_date || ""} onChange={e => set({ end_date: e.target.value })} /></label>}
      <label>Valor previsto (€)<input inputMode="decimal" value={form.amount} onChange={e => set({ amount: e.target.value })} placeholder="Vazio se não tiver valor" /></label>
      <label>Taxa de IVA (%)<input type="number" min="0" max="100" step="0.01" value={form.vat_rate} onChange={e => set({ vat_rate: e.target.value })} placeholder="Se aplicável" /></label>
      <fieldset className="obl-wide"><legend>Alertas (dias antes)</legend><div className="obl-chips">
        {ALERTS.map(days => <label key={days} className="checkbox"><input type="checkbox" checked={form.alert_days.includes(days)}
          onChange={e => set({ alert_days: e.target.checked ? [...form.alert_days, days] : form.alert_days.filter((d: number) => d !== days) })} />{days ? `${days} dias` : "No dia"}</label>)}
      </div></fieldset>
      <fieldset className="obl-wide"><legend>Origem da data-limite</legend>
        {legal && <Notice tone="warning">As datas legais mudam (calendário fiscal, regime, prorrogações). Indique a origem e valide a data com o calendário oficial ou com o contabilista.</Notice>}
        <div className="obl-form">
          <label>Origem<select value={form.date_source} onChange={e => set({ date_source: e.target.value })}>
            {Object.entries(data?.labels?.date_sources || { manual: "Indicada manualmente" }).map(([key, label]) => <option key={key} value={key}>{String(label)}</option>)}</select></label>
          <label className="checkbox"><input type="checkbox" checked={!!form.date_validated} onChange={e => set({ date_validated: e.target.checked })} />Data validada</label>
          <label className="obl-wide">Referência ou nota<input value={form.date_note} maxLength={2000} onChange={e => set({ date_note: e.target.value })} placeholder="Por exemplo, artigo, despacho ou calendário fiscal consultado" /></label>
        </div>
      </fieldset>
      <label className="obl-wide">Observações<textarea rows={3} value={form.description} maxLength={4000} onChange={e => set({ description: e.target.value })} /></label>
    </div>
    {editing && value.occurrences && <section className="obl-history" aria-label="Histórico de ocorrências">
      <h3>Ocorrências e histórico</h3>
      <div className="table-wrap"><table><thead><tr><th>Data-limite</th><th>Estado</th><th className="numeric">Previsto</th><th className="numeric">Efetivo</th><th>Concluído por</th><th>Ações</th></tr></thead>
        <tbody>{value.occurrences.slice(0, 24).map((o: Row) => <tr key={o.id}><td>{fullDate(o.due_date)}{o.date_origin && <small>{o.date_origin}</small>}</td>
          <td><StatusBadge tone={STATE_TONE[o.state]} compact>{STATE_LABEL[o.state]}</StatusBadge></td>
          <td className="numeric">{o.expected_cents == null ? "—" : formatEuro(o.expected_cents)}</td><td className="numeric">{o.actual_cents == null ? "—" : formatEuro(o.actual_cents)}</td>
          <td>{o.completed_by ? `${o.completed_by} · ${fullDate(o.completed_at)}` : "—"}</td><td>{actions(o)}</td></tr>)}</tbody></table></div>
    </section>}
    <div className="ui-dialog-actions"><ActionButton type="button" onClick={onClose}>Fechar</ActionButton>
      <ActionButton type="button" className="primary" disabled={busy || !!value.contract_id || form.title.trim().length < 2 || !form.start_date || Number.isNaN(cents)} onClick={() => void save()}>
        {editing ? "Guardar alterações" : "Criar prazo"}</ActionButton></div>
  </DetailDrawer>;
}
