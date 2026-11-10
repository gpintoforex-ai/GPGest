import { useEffect, useMemo, useState } from "react";
import { CheckCheck, ListChecks, Plus, Sparkles, Tags, Trash2, Wand2 } from "lucide-react";
import { StatusBadge, type BusinessTone } from "./components/BusinessUI";
import { ActionButton, AppDialog, Notice } from "./components/InteractionUI";
import { ModuleTabs, PageHeader, useWorkspaceTab } from "./components/WorkspaceUI";
import { api } from "./lib/api";
import { centsInput, euroToCents, formatEuro } from "./utils/format";
import "./Classification.css";

/**
 * Classificação inteligente de rubricas (financeira): sugestões por regras aprovadas, histórico do
 * fornecedor e ficha do fornecedor; repartição por valor ou percentagem que soma o total do documento;
 * aceitação individual ou em lote (só as sugestões selecionadas) e regras para utilizações futuras.
 */

type Row = Record<string, any>;
const TABS = [{ id: "pendentes", label: "Por classificar" }, { id: "regras", label: "Regras aprovadas" }] as const;
const STATE: Record<string, [string, BusinessTone]> = {
  por_classificar: ["Por classificar", "warning"], fornecedor: ["Rubrica do fornecedor", "info"], confirmada: ["Classificada", "success"], linhas: ["Nas linhas da compra", "neutral"],
};
const SOURCE: Record<string, string> = { regra: "Regra", historico: "Histórico", fornecedor: "Fornecedor" };
const fullDate = (iso?: string | null) => iso ? iso.slice(0, 10).split("-").reverse().join("/") : "—";
const isoDay = (d: Date) => `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}-${String(d.getDate()).padStart(2, "0")}`;
const docLabel = (type: string, number: string) => number?.toUpperCase().startsWith(`${type} `.toUpperCase()) ? number : `${type} ${number}`;
const percentText = (value: string) => `${Number(value).toLocaleString("pt-PT", { maximumFractionDigits: 2 })} %`;
export const confidenceTone = (value: number, safe = 85): BusinessTone => value >= safe ? "success" : value >= 70 ? "info" : "warning";
/** «70,5» → «70.5»; vazio ou inválido → null. */
export const decimalText = (text: string) => { const v = text.trim().replace(",", "."); return /^\d+(\.\d{1,2})?$/.test(v) ? v : null; };

function SuggestionChips({ suggestion }: { suggestion: Row }) {
  return <span className="cls-chips">{suggestion.shares.map((s: Row) => <span key={s.category_id} className="cls-chip">{s.category_name}{suggestion.shares.length > 1 ? ` ${percentText(s.percent)}` : ""}</span>)}</span>;
}

export default function Classification({ stores }: { stores: Row[] }) {
  const [tab, setTab] = useWorkspaceTab("classificacao", "pendentes", TABS.map(t => t.id));
  const today = new Date();
  const [filters, setFilters] = useState({ start: isoDay(new Date(today.getFullYear(), today.getMonth() - 2, 1)), end: isoDay(today), store: "", q: "" });
  const [data, setData] = useState<Row | null>(null), [rules, setRules] = useState<Row[] | null>(null);
  const [error, setError] = useState(""), [notice, setNotice] = useState(""), [busy, setBusy] = useState(false);
  const [selected, setSelected] = useState<Set<string>>(new Set());
  const [dialog, setDialog] = useState<Row | null>(null), [ruleDialog, setRuleDialog] = useState<Row | null>(null);
  const [version, setVersion] = useState(0);
  const refresh = () => setVersion(v => v + 1);
  useEffect(() => {
    let active = true;
    setError(""); setData(null);
    api(`/classification/pending?${new URLSearchParams({ start: filters.start, end: filters.end, store_id: filters.store })}`)
      .then(r => { if (active) { setData(r); setSelected(new Set()); } }).catch(e => { if (active) setError((e as Error).message); });
    return () => { active = false; };
  }, [filters.start, filters.end, filters.store, version]);
  useEffect(() => {
    if (tab !== "regras") return;
    let active = true;
    api("/classification/rules").then(r => { if (active) setRules(r); }).catch(e => { if (active) setError((e as Error).message); });
    return () => { active = false; };
  }, [tab, version]);
  const rows: Row[] = useMemo(() => (data?.rows || []).filter((r: Row) => !filters.q || `${r.supplier_name} ${r.supplier_nif} ${r.document}`.toLowerCase().includes(filters.q.toLowerCase())), [data, filters.q]);
  const keyOf = (r: Row) => `${r.kind}:${r.id}`;
  const payload = (r: Row) => { const s = r.suggestions[0]; return { kind: r.kind, id: r.id, mode: "percent", rule_id: s.rule_id || null, shares: s.shares.map((x: Row) => ({ category_id: x.category_id, value: x.percent })) }; };
  async function accept(items: Row[]) {
    setBusy(true); setError("");
    try {
      if (items.length === 1) await api("/classification/apply", payload(items[0]));
      else await api("/classification/batch", { items: items.map(payload) });
      setNotice(items.length === 1 ? "Classificação aplicada." : `${items.length} classificações aplicadas.`);
      refresh();
    } catch (e) { setError((e as Error).message); } finally { setBusy(false); }
  }
  const chosen = rows.filter(r => selected.has(keyOf(r)) && r.suggestions.length);
  const set = (changes: Row) => setFilters(current => ({ ...current, ...changes }));

  return <div className="cls">
    <PageHeader title="Classificação de rubricas" description="Sugestões por regras aprovadas, histórico do fornecedor e ficha do fornecedor. Nada é aplicado sem a sua confirmação; as regras ficam para as próximas faturas." />
    {error && <Notice tone="error" onDismiss={() => setError("")}>{error}</Notice>}
    {notice && <Notice tone="success" onDismiss={() => setNotice("")}>{notice}</Notice>}
    {data && <div className="cls-kpis" role="group" aria-label="Resumo da classificação">
      <Kpi tone="warning" icon={Tags} label="Por classificar" value={data.summary.por_classificar} />
      <Kpi tone="info" icon={ListChecks} label="Com a rubrica do fornecedor" value={data.summary.fornecedor} note="Por confirmar" />
      <Kpi tone="success" icon={Sparkles} label="Sugestões seguras" value={data.summary.seguras} note={`Confiança ≥ ${data.safe_threshold} %`} />
      <Kpi tone="suggestion" icon={Wand2} label="Regras ativas" value={data.summary.regras} onOpen={() => setTab("regras")} />
    </div>}
    <ModuleTabs label="Áreas da classificação" tabs={TABS} value={tab} onChange={setTab} />
    {tab === "pendentes" ? <section className="cls-card" aria-label="Documentos por classificar">
      <div className="cls-filters filter-bar" role="group" aria-label="Filtrar documentos">
        <label>De<input type="date" value={filters.start} onChange={e => set({ start: e.target.value })} /></label>
        <label>Até<input type="date" value={filters.end} onChange={e => set({ end: e.target.value })} /></label>
        <label>Estabelecimento<select value={filters.store} onChange={e => set({ store: e.target.value })}><option value="">Todos</option>
          {stores.map(s => <option key={s.id} value={s.id}>{s.name}</option>)}</select></label>
        <label>Pesquisar<input type="search" value={filters.q} placeholder="Fornecedor, NIF ou número" onChange={e => set({ q: e.target.value })} /></label>
      </div>
      {data && rows.some(r => r.suggestions.length) && <div className="cls-batch" role="group" aria-label="Aceitar em lote">
        <ActionButton type="button" onClick={() => setSelected(new Set(rows.filter(r => r.safe).map(keyOf)))}><CheckCheck size={16} aria-hidden="true" />Selecionar as seguras</ActionButton>
        <ActionButton type="button" onClick={() => setSelected(new Set())} disabled={!selected.size}>Limpar seleção</ActionButton>
        <ActionButton type="button" className="primary" disabled={busy || !chosen.length} onClick={() => void accept(chosen)}>Aceitar {chosen.length} sugestão(ões) selecionada(s)</ActionButton>
      </div>}
      {!data ? !error && <p role="status">A carregar documentos…</p> : !rows.length ? <p className="dash-muted">Nada por classificar neste período.</p>
        : <div className="table-wrap"><table className="cls-grid" aria-label="Documentos a classificar">
          <thead><tr><th><span className="sr-only">Selecionar</span></th><th>Data</th><th>Documento</th><th className="numeric">Total</th><th>Situação</th><th>Sugestão</th><th>Ações</th></tr></thead>
          <tbody>{rows.map(r => {
            const top = r.suggestions[0];
            const [label, tone] = STATE[r.state];
            return <tr key={keyOf(r)}>
              <td>{top && <input type="checkbox" aria-label={`Selecionar ${docLabel(r.document_type, r.document)}`} checked={selected.has(keyOf(r))}
                onChange={e => { const next = new Set(selected); if (e.target.checked) next.add(keyOf(r)); else next.delete(keyOf(r)); setSelected(next); }} />}</td>
              <td>{fullDate(r.document_date)}</td>
              <td><strong>{r.supplier_name}</strong><small>{docLabel(r.document_type, r.document)}{r.kind === "erp" ? " · ERP" : ""}{r.store ? ` · ${r.store}` : ""}</small></td>
              <td className="numeric">{formatEuro(r.amount_cents)}</td>
              <td><StatusBadge tone={tone} compact>{label}</StatusBadge>{r.current.length > 0 && <small>{r.current.map((c: Row) => c.category_name).join(", ")}</small>}</td>
              <td>{top ? <><SuggestionChips suggestion={top} /><StatusBadge tone={confidenceTone(top.confidence, data.safe_threshold)} compact>{`${SOURCE[top.source]} · ${top.confidence} %`}</StatusBadge>
                <small>{top.reasons[0]}</small></> : <small>{r.state === "linhas" ? "Classifique nas linhas, em Compras." : "Sem sugestão: classifique manualmente."}</small>}</td>
              <td><div className="cls-actions">
                {top && <ActionButton type="button" disabled={busy} onClick={() => void accept([r])} aria-label={`Aceitar a sugestão para ${docLabel(r.document_type, r.document)}`}>Aceitar</ActionButton>}
                {r.state !== "linhas" && <ActionButton type="button" onClick={() => setDialog(r)} aria-label={`Classificar ${docLabel(r.document_type, r.document)}`}>Classificar…</ActionButton>}
              </div></td></tr>;
          })}</tbody></table></div>}
      <ul className="cls-notes">{(data?.notes || []).map((n: string) => <li key={n}>{n}</li>)}</ul>
    </section>
      : <Rules rules={rules} onEdit={setRuleDialog} />}
    {dialog && <ClassifyDialog kind={dialog.kind} id={dialog.id} onClose={() => setDialog(null)} onSaved={message => { setDialog(null); setNotice(message); refresh(); }} />}
    {ruleDialog && data && <RuleDialog rule={ruleDialog} categories={data.categories} stores={stores} onClose={() => setRuleDialog(null)}
      onSaved={message => { setRuleDialog(null); setNotice(message); refresh(); }} />}
  </div>;
}

function Kpi({ tone, icon: Icon, label, value, note, onOpen }: { tone: BusinessTone; icon: typeof Tags; label: string; value: number; note?: string; onOpen?: () => void }) {
  const body = <><span className="cls-kpi-icon" aria-hidden="true"><Icon size={20} /></span><span className="cls-kpi-text"><span>{label}</span><strong>{value}</strong>{note && <small>{note}</small>}</span></>;
  return onOpen ? <ActionButton type="button" className="cls-kpi ui-tone" data-tone={tone} inferAction={false} onClick={onOpen} aria-label={`${label}: ${value}. Ver`}>{body}</ActionButton>
    : <div className="cls-kpi ui-tone" data-tone={tone}>{body}</div>;
}

type Share = { category_id: string; value: string };

function ShareEditor({ shares, mode, total, categories, onChange }: { shares: Share[]; mode: "amount" | "percent"; total?: number; categories: Row[]; onChange: (s: Share[]) => void }) {
  const parsed = shares.map(s => mode === "amount" ? euroToCents(s.value) : decimalText(s.value) === null ? null : Number(decimalText(s.value)));
  const sum = parsed.reduce((acc: number, v) => acc + (typeof v === "number" && !Number.isNaN(v) ? v : 0), 0);
  const target = mode === "amount" ? total ?? 0 : 100;
  const left = Math.round((target - sum) * 100) / 100;
  return <fieldset className="cls-shares"><legend>Repartição por rubrica</legend>
    {shares.map((share, index) => <div key={index} className="cls-share">
      <label>Rubrica<select value={share.category_id} onChange={e => onChange(shares.map((s, i) => i === index ? { ...s, category_id: e.target.value } : s))}>
        <option value="">Escolha</option>{categories.map(c => <option key={c.id} value={c.id}>{c.name}</option>)}</select></label>
      <label>{mode === "amount" ? "Valor (€)" : "Percentagem (%)"}<input inputMode="decimal" value={share.value} onChange={e => onChange(shares.map((s, i) => i === index ? { ...s, value: e.target.value } : s))} /></label>
      {shares.length > 1 && <ActionButton type="button" aria-label="Remover rubrica" onClick={() => onChange(shares.filter((_, i) => i !== index))}><Trash2 size={15} aria-hidden="true" /></ActionButton>}
    </div>)}
    <div className="cls-share-foot">
      <ActionButton type="button" onClick={() => onChange([...shares, { category_id: "", value: mode === "amount" && left > 0 ? centsInput(left) : left > 0 ? String(left).replace(".", ",") : "" }])}><Plus size={15} aria-hidden="true" />Acrescentar rubrica</ActionButton>
      <span role="status" className={left === 0 ? "cls-ok" : "cls-warn"}>{left === 0 ? "Repartição completa" : mode === "amount" ? `Falta repartir ${formatEuro(left)}` : `Falta repartir ${String(left).replace(".", ",")} %`}</span>
    </div>
  </fieldset>;
}

export function ClassifyDialog({ kind, id, onClose, onSaved }: { kind: string; id: string; onClose: () => void; onSaved: (message: string) => void }) {
  const [data, setData] = useState<Row | null>(null), [categories, setCategories] = useState<Row[]>([]);
  const [error, setError] = useState(""), [busy, setBusy] = useState(false);
  const [mode, setMode] = useState<"amount" | "percent">("percent");
  const [shares, setShares] = useState<Share[]>([{ category_id: "", value: "100" }]);
  const [ruleId, setRuleId] = useState<string | null>(null);
  const [saveRule, setSaveRule] = useState(false), [criteria, setCriteria] = useState({ supplier: true, document_type: false, store: false, keyword: "" }), [ruleName, setRuleName] = useState("");
  useEffect(() => {
    let active = true;
    api(`/classification/suggest?${new URLSearchParams({ kind, id })}`)
      .then(suggest => {
        if (!active) return;
        setData(suggest); setCategories(suggest.categories || []);
        const top = suggest.suggestions[0];
        if (suggest.current.length) { setMode("amount"); setShares(suggest.current.map((c: Row) => ({ category_id: c.category_id, value: centsInput(c.amount_cents) }))); }
        else if (top) { setShares(top.shares.map((s: Row) => ({ category_id: s.category_id, value: s.percent.replace(".", ",") }))); setRuleId(top.rule_id); }
        setRuleName(suggest.supplier_name || "");
      }).catch(e => { if (active) setError((e as Error).message); });
    return () => { active = false; };
  }, [kind, id]);
  function use(suggestion: Row) { setMode("percent"); setRuleId(suggestion.rule_id); setShares(suggestion.shares.map((s: Row) => ({ category_id: s.category_id, value: s.percent.replace(".", ",") }))); }
  function switchMode(next: "amount" | "percent") {
    if (!data || next === mode) return;
    setShares(shares.map(s => {
      if (next === "amount") { const p = Number(decimalText(s.value) || 0); return { ...s, value: centsInput(Math.round(data.total_cents * p / 100)) }; }
      const c = euroToCents(s.value) || 0; return { ...s, value: String(Math.round(c * 10000 / (data.total_cents || 1)) / 100).replace(".", ",") };
    }));
    setMode(next); setRuleId(null);
  }
  const values = shares.map(s => mode === "amount" ? (() => { const c = euroToCents(s.value); return c == null || Number.isNaN(c) ? null : (c / 100).toFixed(2); })() : decimalText(s.value));
  const invalid = shares.some((s, i) => !s.category_id || !values[i]) || (saveRule && !criteria.supplier && !criteria.document_type && !criteria.store && !criteria.keyword.trim());
  async function save() {
    setBusy(true); setError("");
    try {
      await api("/classification/apply", { kind, id, mode, rule_id: ruleId, shares: shares.map((s, i) => ({ category_id: s.category_id, value: values[i] })),
        ...(saveRule ? { save_rule: criteria, rule_name: ruleName } : {}) });
      onSaved(saveRule ? "Classificação aplicada e regra guardada." : "Classificação aplicada.");
    } catch (e) { setError((e as Error).message); } finally { setBusy(false); }
  }
  return <AppDialog className="cls-dialog" aria-label="Classificar documento" busy={busy} onRequestClose={onClose}>
    <h2>Classificar documento</h2>
    {error && <Notice tone="error">{error}</Notice>}
    {!data ? !error && <p role="status">A carregar sugestões…</p> : <>
      <p className="cls-doc"><strong>{data.supplier_name}</strong> · {docLabel(data.document_type, data.document)} · {fullDate(data.document_date)} · total {formatEuro(data.total_cents)}
        {data.base_cents != null && <small>Base {formatEuro(Math.abs(data.base_cents))} · IVA {formatEuro(Math.abs(data.tax_cents || 0))}</small>}</p>
      {data.target === "lines" ? <Notice tone="info">Esta compra tem linhas lançadas: classifique as linhas em Compras.</Notice> : <>
        {data.target === "erp" && <Notice tone="info">A classificação fica no documento do ERP ligado (o ERP não é alterado).</Notice>}
        {data.suggestions.length > 0 && <section aria-label="Sugestões"><h3>Sugestões</h3><ul className="cls-suggestions">
          {data.suggestions.map((s: Row) => <li key={s.key}><div><SuggestionChips suggestion={s} />
            <StatusBadge tone={confidenceTone(s.confidence, data.safe_threshold)} compact>{`${SOURCE[s.source]} · ${s.confidence} %`}</StatusBadge>
            <small>{s.reasons.join(" · ")}</small></div>
            <ActionButton type="button" onClick={() => use(s)}>Usar</ActionButton></li>)}</ul></section>}
        <div className="cls-mode" role="group" aria-label="Repartir por">
          <ActionButton type="button" aria-pressed={mode === "percent"} onClick={() => switchMode("percent")}>Percentagem</ActionButton>
          <ActionButton type="button" aria-pressed={mode === "amount"} onClick={() => switchMode("amount")}>Valor</ActionButton>
        </div>
        <ShareEditor shares={shares} mode={mode} total={data.total_cents} categories={categories} onChange={next => { setShares(next); setRuleId(null); }} />
        <fieldset className="cls-rule"><legend>Para as próximas faturas</legend>
          <label className="checkbox"><input type="checkbox" checked={saveRule} onChange={e => setSaveRule(e.target.checked)} />Guardar como regra aprovada</label>
          {saveRule && <div className="cls-rule-fields">
            <label>Nome da regra<input value={ruleName} maxLength={120} onChange={e => setRuleName(e.target.value)} /></label>
            <label className="checkbox"><input type="checkbox" checked={criteria.supplier} onChange={e => setCriteria({ ...criteria, supplier: e.target.checked })} />Mesmo fornecedor ({data.supplier_nif})</label>
            <label className="checkbox"><input type="checkbox" checked={criteria.document_type} onChange={e => setCriteria({ ...criteria, document_type: e.target.checked })} />Mesmo tipo de documento ({data.document_type})</label>
            <label className="checkbox"><input type="checkbox" checked={criteria.store} onChange={e => setCriteria({ ...criteria, store: e.target.checked })} />Mesmo estabelecimento</label>
            <label>Palavra-chave (opcional)<input value={criteria.keyword} maxLength={80} placeholder="Por exemplo, gasóleo" onChange={e => setCriteria({ ...criteria, keyword: e.target.value })} /></label>
          </div>}
        </fieldset>
      </>}
    </>}
    <div className="ui-dialog-actions"><ActionButton type="button" onClick={onClose}>Cancelar</ActionButton>
      {data && data.target !== "lines" && <ActionButton type="button" className="primary" disabled={busy || invalid} onClick={() => void save()}>Aplicar classificação</ActionButton>}</div>
  </AppDialog>;
}

function Rules({ rules, onEdit }: { rules: Row[] | null; onEdit: (rule: Row) => void }) {
  return <section className="cls-card" aria-label="Regras aprovadas">
    <div className="cls-batch"><ActionButton type="button" className="primary" onClick={() => onEdit({})}><Plus size={16} aria-hidden="true" />Nova regra</ActionButton></div>
    {!rules ? <p role="status">A carregar regras…</p> : !rules.length ? <p className="dash-muted">Ainda sem regras. Guarde uma ao classificar um documento ou crie-a aqui.</p>
      : <div className="table-wrap"><table className="cls-grid" aria-label="Lista de regras">
        <thead><tr><th>Regra</th><th>Critérios</th><th>Repartição</th><th className="numeric">Utilizações</th><th>Estado</th><th>Ações</th></tr></thead>
        <tbody>{rules.map(r => <tr key={r.id}><td><strong>{r.name}</strong></td>
          <td>{[r.supplier_nif && `Fornecedor ${r.supplier_name || r.supplier_nif}`, r.document_type && `Tipo ${r.document_type}`, r.keyword && `«${r.keyword}»`, r.store && r.store].filter(Boolean).join(" · ")}</td>
          <td>{r.shares.map((s: Row) => `${s.category_name} ${percentText(s.percent)}`).join(" · ")}</td>
          <td className="numeric">{r.hits}<small>{r.last_used_at ? `última ${fullDate(r.last_used_at)}` : ""}</small></td>
          <td><StatusBadge tone={r.active ? "success" : "neutral"} compact>{r.active ? "Ativa" : "Inativa"}</StatusBadge></td>
          <td><ActionButton type="button" onClick={() => onEdit(r)} aria-label={`Editar a regra ${r.name}`}>Editar</ActionButton></td></tr>)}</tbody></table></div>}
  </section>;
}

function RuleDialog({ rule, categories, stores, onClose, onSaved }: { rule: Row; categories: Row[]; stores: Row[]; onClose: () => void; onSaved: (message: string) => void }) {
  const editing = !!rule.id;
  const [form, setForm] = useState({ name: rule.name || "", supplier_nif: rule.supplier_nif || "", document_type: rule.document_type || "", keyword: rule.keyword || "", store_id: rule.store_id || "", active: rule.active ?? true });
  const [shares, setShares] = useState<Share[]>(editing ? rule.shares.map((s: Row) => ({ category_id: s.category_id, value: s.percent.replace(".", ",") })) : [{ category_id: "", value: "100" }]);
  const [error, setError] = useState(""), [busy, setBusy] = useState(false);
  const values = shares.map(s => decimalText(s.value));
  async function save() {
    setBusy(true); setError("");
    try {
      const body = { ...form, store_id: form.store_id || null, revision: rule.revision || 0, shares: shares.map((s, i) => ({ category_id: s.category_id, value: values[i] })) };
      await api(editing ? `/classification/rules/${encodeURIComponent(rule.id)}` : "/classification/rules", body, editing ? { method: "PUT" } : undefined);
      onSaved(editing ? "Regra atualizada." : "Regra criada.");
    } catch (e) { setError((e as Error).message); } finally { setBusy(false); }
  }
  return <AppDialog className="cls-dialog" aria-label={editing ? "Editar regra" : "Nova regra"} busy={busy} onRequestClose={onClose}>
    <h2>{editing ? "Editar regra" : "Nova regra"}</h2>
    {error && <Notice tone="error">{error}</Notice>}
    <div className="cls-rule-fields">
      <label>Nome<input value={form.name} maxLength={120} onChange={e => setForm({ ...form, name: e.target.value })} /></label>
      <label>NIF do fornecedor<input value={form.supplier_nif} maxLength={30} onChange={e => setForm({ ...form, supplier_nif: e.target.value })} /></label>
      <label>Tipo de documento<input value={form.document_type} maxLength={10} placeholder="FT, FS, FR…" onChange={e => setForm({ ...form, document_type: e.target.value })} /></label>
      <label>Palavra-chave<input value={form.keyword} maxLength={80} onChange={e => setForm({ ...form, keyword: e.target.value })} /></label>
      <label>Estabelecimento<select value={form.store_id} onChange={e => setForm({ ...form, store_id: e.target.value })}><option value="">Todos</option>{stores.map(s => <option key={s.id} value={s.id}>{s.name}</option>)}</select></label>
      <label className="checkbox"><input type="checkbox" checked={form.active} onChange={e => setForm({ ...form, active: e.target.checked })} />Regra ativa</label>
    </div>
    <ShareEditor shares={shares} mode="percent" categories={categories} onChange={setShares} />
    <div className="ui-dialog-actions"><ActionButton type="button" onClick={onClose}>Cancelar</ActionButton>
      <ActionButton type="button" className="primary" disabled={busy || form.name.trim().length < 2 || shares.some((s, i) => !s.category_id || !values[i])} onClick={() => void save()}>{editing ? "Guardar" : "Criar regra"}</ActionButton></div>
  </AppDialog>;
}
