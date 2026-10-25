import { useEffect, useLayoutEffect, useRef, useState, type CSSProperties } from "react";
import {
  AlignLeft, Building2, CalendarClock, CalendarDays, CheckCircle2, ChevronLeft, ChevronRight, CircleAlert, CreditCard, Hash,
  Info, ListChecks, Mail, Mailbox, MapPin, Palette, Percent, Phone, Shapes, Store, Tag, Type, User,
  type LucideIcon,
} from "lucide-react";
import { api } from "../lib/api";
import { ActionButton, AppDialog, Notice, confirmFormExit, markDialogSaved } from "../components/InteractionUI";
import { Field, Form } from "../components/Form";
import RubricSelect from "../components/RubricSelect";
import RubricBadge from "../components/RubricBadge";
import { ModuleTabs } from "../components/WorkspaceUI";
import { ActiveBadge, OriginBadge, SyncBadge } from "./Badges";
import Processing from "./Processing";
import { displayValue, formatDateTime, gendered, type FieldSpec, type RecordDetails, type TableContext, type TableInfo } from "./types";

type Mode = "create" | "edit" | "view";

/** Campos seguidos da mesma secção, pela ordem da tabela. */
function groups(fields: FieldSpec[]) {
  const result: [string, FieldSpec[]][] = [];
  for (const field of fields) {
    const group = field.group || "";
    const last = result[result.length - 1];
    if (last && last[0] === group) last[1].push(field);
    else result.push([group, [field]]);
  }
  return result;
}

/** Largura numa grelha de 6 colunas: definida pela tabela ou, por omissão, meia linha. */
function span(field: FieldSpec) {
  if (field.span) return field.span;
  return field.type === "textarea" || field.type === "category" ? 6 : 3;
}
type Term = { id: string; name: string; active: boolean };
type NifState = { tone: "valid" | "invalid" | "info" | "busy"; text: string };

/** Verificação local do NIF português (9 dígitos e dígito de controlo); estrangeiros não são verificados. */
export function checkNif(raw: string): NifState | null {
  const value = raw.trim().toUpperCase().replace(/[\s.-]/g, "");
  if (!value) return null;
  if (/^[A-Z]{2}/.test(value) && !value.startsWith("PT")) return { tone: "info", text: "NIF estrangeiro: sem verificação local." };
  const digits = value.replace(/^PT/, "");
  if (!/^\d{9}$/.test(digits)) return { tone: "invalid", text: "O NIF português tem 9 dígitos." };
  const sum = digits.slice(0, 8).split("").reduce((total, digit, index) => total + Number(digit) * (9 - index), 0);
  const control = 11 - (sum % 11);
  return (control >= 10 ? 0 : control) === Number(digits[8])
    ? { tone: "valid", text: "NIF válido." }
    : { tone: "invalid", text: "NIF inválido: o dígito de controlo não corresponde." };
}

// Ícone ilustrativo de cada campo (pelo nome; senão pelo tipo).
const FIELD_ICONS: Record<string, LucideIcon> = {
  tax_number: Hash, code: Hash, name: User, email: Mail, phone: Phone, address: MapPin, postal_code: Mailbox,
  locality: Building2, category_id: Tag, payment_term_id: CalendarClock, payment_method_id: CreditCard,
  store_id: Store, days: CalendarDays, discount_percent: Percent, term_type: CalendarClock, icon: Shapes,
  color: Palette, vat_deduction_percent: Percent,
};
const TYPE_ICONS: Partial<Record<FieldSpec["type"], LucideIcon>> = {
  text: Type, textarea: AlignLeft, number: Hash, decimal: Percent, select: ListChecks, email: Mail, nif: Hash,
  store: Store, category: Tag, payment_term: CalendarClock, payment_method: CreditCard, color: Palette, icon: Shapes,
};

function FieldLabel({ field, suffix }: { field: FieldSpec; suffix: string }) {
  const Icon = FIELD_ICONS[field.name] || TYPE_ICONS[field.type] || Type;
  return <span className="table-field-label"><span className="table-field-icon" aria-hidden="true"><Icon size={15} /></span>{field.label}{suffix}</span>;
}

function NifStatus({ state }: { state: NifState | null }) {
  if (!state) return null;
  if (state.tone === "busy") return <Processing label={state.text} inline />;
  const Icon = state.tone === "valid" ? CheckCircle2 : state.tone === "invalid" ? CircleAlert : Info;
  return <small className={`table-nif-status is-${state.tone}`} role="status"><Icon size={14} aria-hidden="true" />{state.text}</small>;
}

function initial(table: TableInfo, details: RecordDetails | null) {
  const values: Record<string, any> = {};
  for (const field of table.fields) {
    const value = details?.values[field.name];
    values[field.name] = field.type === "boolean" ? !!value : value ?? "";
  }
  if (!details && table.fields.some(f => f.name === "color")) values.color = values.color || "#245fc5";
  if (!details && table.fields.some(f => f.name === "icon")) values.icon = values.icon || "tag";
  return values;
}

function payload(table: TableInfo, values: Record<string, any>) {
  const result: Record<string, unknown> = {};
  for (const field of table.fields) {
    const value = values[field.name];
    if (field.type === "boolean") result[field.name] = !!value;
    else if (field.type === "number") result[field.name] = value === "" || value === null ? null : Number(value);
    else result[field.name] = value === "" ? null : value;
  }
  return result;
}

export default function RecordDialog({ table, context, mode: startMode, recordId, recordIds = [], onNavigate, onClose, onSaved }: {
  table: TableInfo;
  context: TableContext;
  mode: Mode;
  recordId?: string;
  // Registos da lista atual, para as setas «anterior / seguinte».
  recordIds?: string[];
  onNavigate?: (id: string) => void;
  onClose: () => void;
  onSaved: (message: string, warning?: string) => void;
}) {
  const dialogRef = useRef<HTMLDialogElement>(null);
  const lastLookup = useRef("");
  const [mode, setMode] = useState<Mode>(startMode);
  const [details, setDetails] = useState<RecordDetails | null>(null);
  const [values, setValues] = useState<Record<string, any>>(() => initial(table, null));
  const [terms, setTerms] = useState<Term[]>([]);
  const [error, setError] = useState("");
  const [lookup, setLookup] = useState<NifState | null>(null);
  const singular = table.singular;
  // Separadores da ficha (ex.: «Dados gerais» e «Condições comerciais»), pela ordem dos campos.
  const visible = table.fields.filter(f => !f.hidden);
  const tabs = [...new Set(visible.map(f => f.tab || "").filter(Boolean))];
  const [tab, setTab] = useState(tabs[0] || "");
  const tabOf = (name: string) => table.fields.find(f => f.name === name)?.tab || "";

  useEffect(() => {
    if (!recordId) return;
    setError("");
    setLookup(null);
    lastLookup.current = "";
    api(`/tables/${table.key}/records/${encodeURIComponent(recordId)}/details`)
      .then((data: RecordDetails) => {
        setDetails(data);
        setValues(initial(table, data));
      })
      .catch(e => setError(e.message));
  }, [recordId, table]);

  // O registo acabado de mostrar passa a ser a referência para «alterações por guardar»,
  // no mesmo commit em que os valores aparecem (antes de ser possível escrever).
  useLayoutEffect(() => {
    if (details) markDialogSaved(dialogRef.current?.querySelector("form") || null);
  }, [details]);

  const position = recordId ? recordIds.indexOf(recordId) : -1;
  async function go(step: number) {
    const next = recordIds[position + step];
    if (!next || !onNavigate) return;
    if (!(await confirmFormExit(dialogRef.current))) return;
    onNavigate(next);
  }
  useEffect(() => {
    if (table.fields.some(f => f.type === "payment_term")) api("/payment-terms").then(setTerms).catch(() => setTerms([]));
  }, [table]);

  const managed = new Set(details?.managed_fields || []);
  const set = (name: string, value: unknown) => setValues(current => ({ ...current, [name]: value }));

  /** Ao sair do NIF: se for um NIF português válido e novo, consulta o VIES e preenche só os campos vazios. */
  async function lookupNif(raw: string) {
    const nif = raw.trim().toUpperCase().replace(/[\s.-]/g, "").replace(/^PT/, "");
    if (checkNif(nif)?.tone !== "valid" || nif === lastLookup.current || nif === String(details?.values.tax_number || "")) return;
    lastLookup.current = nif;
    setLookup({ tone: "busy", text: "A procurar no VIES" });
    try {
      const found = await api(`/entities/lookup?tax_number=${encodeURIComponent(nif)}`) as { is_valid: boolean; name: string; address: string; locality: string; postal_code: string };
      if (!found.is_valid) {
        setLookup({ tone: "info", text: "NIF válido; não consta no VIES." });
        return;
      }
      setValues(current => ({
        ...current,
        name: current.name || found.name,
        address: current.address || found.address,
        locality: current.locality || found.locality,
        postal_code: current.postal_code || found.postal_code,
      }));
      setLookup({ tone: "valid", text: "Confirmado no VIES." });
    } catch {
      setLookup({ tone: "info", text: "NIF válido; VIES indisponível." });
    }
  }

  function input(field: FieldSpec) {
    const locked = mode === "edit" && (managed.has(field.name) || field.create_only);
    const required = field.required || (mode === "create" && !!field.create_required);
    const common = { id: undefined, name: field.name, required, disabled: locked, title: locked ? (field.create_only ? "Só pode ser definido na criação." : "Gerido pelo ERP.") : undefined };
    const value = values[field.name];
    switch (field.type) {
      case "boolean":
        return <label className="checkbox table-checkbox"><input type="checkbox" {...common} checked={!!value} onChange={e => set(field.name, e.target.checked)} /> {field.label}</label>;
      case "textarea":
        return <textarea {...common} rows={2} maxLength={field.max_length || undefined} minLength={field.min_length || undefined} value={value} onChange={e => set(field.name, e.target.value)} />;
      case "number":
        return <input type="number" step="1" {...common} min={field.minimum ?? undefined} max={field.maximum ?? undefined} value={value ?? ""} onChange={e => set(field.name, e.target.value)} />;
      case "decimal":
        return <input inputMode="decimal" pattern="\d+([.,]\d{1,2})?" {...common} value={String(value ?? "").replace(".", ",")} onChange={e => set(field.name, e.target.value.replace(",", "."))} />;
      case "select":
      case "icon":
        return <select {...common} value={value ?? ""} onChange={e => set(field.name, e.target.value)}>{!field.required && <option value="">Por indicar</option>}{field.options.map(o => <option key={o.value} value={o.value}>{o.label}</option>)}</select>;
      case "store":
        return <select {...common} value={value ?? ""} onChange={e => set(field.name, e.target.value)}><option value="">Escolha…</option>{context.stores.map(s => <option key={s.id} value={s.id}>{s.name}</option>)}</select>;
      case "payment_term":
        return <select {...common} value={value ?? ""} onChange={e => set(field.name, e.target.value)}><option value="">{details?.origin === "erp" && !details.values[field.name] && details.labels[field.name] ? `Igual ao ERP (${details.labels[field.name]})` : mode === "create" ? "Escolha…" : "Por indicar"}</option>{terms.filter(t => t.active || t.id === details?.values[field.name]).map(t => <option key={t.id} value={t.id}>{t.name}{t.active ? "" : " (inativo)"}</option>)}</select>;
      case "payment_method": {
        const current = details?.values[field.name];
        const followsErp = details?.origin === "erp" && !current && details.labels[field.name];
        return <select {...common} value={value ?? ""} onChange={e => set(field.name, e.target.value)}>
          <option value="">{followsErp ? `Igual ao ERP (${details!.labels[field.name]})` : mode === "create" ? "Escolha…" : "Por indicar"}</option>
          {field.options.filter(o => o.active !== false || o.value === current).map(o => <option key={o.value} value={o.value}>{o.label}{o.active === false ? " (inativo)" : ""}</option>)}
        </select>;
      }
      case "category":
        return <RubricSelect categories={context.categories.filter(c => c.active !== false || c.id === details?.values[field.name]).map(c => ({ id: c.id, name: c.name }))} value={value || ""} onChange={next => set(field.name, next)} disabled={locked} />;
      case "color":
        return <input type="color" {...common} value={value || "#245fc5"} onChange={e => set(field.name, e.target.value)} />;
      case "nif": {
        const state = checkNif(String(value || ""));
        return <input {...common} inputMode="text" autoComplete="off" maxLength={field.max_length || undefined} minLength={field.min_length || undefined} value={value} aria-invalid={state?.tone === "invalid" || undefined} onChange={e => { set(field.name, e.target.value); setLookup(null); }} onBlur={e => { if (!locked) void lookupNif(e.currentTarget.value); }} />;
      }
      default:
        return <input type={field.type === "email" ? "email" : "text"} {...common} maxLength={field.max_length || undefined} minLength={field.min_length || undefined} value={value ?? ""} onChange={e => set(field.name, e.target.value)} />;
    }
  }

  const title = mode === "create" ? `${gendered(table, "Novo")} ${singular}` : mode === "edit" ? `Editar ${singular}` : `Detalhes ${gendered(table, "do")} ${singular}`;
  const preview = table.fields.some(f => f.name === "icon") && table.fields.some(f => f.name === "color");

  return <AppDialog ref={dialogRef} autoOpen className="modal table-dialog" onRequestClose={onClose} aria-labelledby="table-record-title">
    <div className="panel-title table-dialog-title">
      <h2 id="table-record-title">{title}</h2>
      {mode !== "create" && position >= 0 && recordIds.length > 1 && onNavigate && <div className="table-record-nav" role="group" aria-label="Navegar entre registos">
        <ActionButton type="button" aria-label="Registo anterior" title="Registo anterior" disabled={position <= 0} onClick={() => go(-1)}><ChevronLeft size={20} aria-hidden="true" /><span>Anterior</span></ActionButton>
        <span className="table-record-position" aria-live="polite">{position + 1} de {recordIds.length}</span>
        <ActionButton type="button" aria-label="Registo seguinte" title="Registo seguinte" disabled={position >= recordIds.length - 1} onClick={() => go(1)}><span>Seguinte</span><ChevronRight size={20} aria-hidden="true" /></ActionButton>
      </div>}
    </div>
    {error && <Notice className="error" role="alert">{error}</Notice>}
    {mode === "view" ? <>
      {details ? <>
        <dl className="table-details">
          {visible.map(f => <div key={f.name}><dt>{f.label}</dt><dd>{displayValue(f, details.values[f.name], details.labels[f.name])}</dd></div>)}
          {table.extra_columns.map(c => <div key={c.name}><dt>{c.label}</dt><dd>{details.labels[c.name] || "—"}</dd></div>)}
          {details.labels.erp_payment_method !== undefined && <div><dt>Meio de pagamento (ERP)</dt><dd>{details.labels.erp_payment_method || "—"}</dd></div>}
          {table.has_active && <div><dt>Estado</dt><dd><ActiveBadge active={details.active} /></dd></div>}
        </dl>
        {table.erp.integrated && <section className="table-erp-details" aria-label="Integração com o ERP">
          <h3>Integração com o ERP</h3>
          <dl className="table-details">
            <div><dt>Origem</dt><dd><OriginBadge origin={details.origin} /></dd></div>
            <div><dt>Código no ERP</dt><dd>{details.erp_code || "—"}</dd></div>
            <div><dt>Identificador no ERP</dt><dd className="table-mono">{details.erp_id || "—"}</dd></div>
            <div><dt>Sincronização</dt><dd><SyncBadge state={details.sync_state} label={details.sync_label} /></dd></div>
            <div><dt>Última sincronização</dt><dd>{formatDateTime(details.last_sync_at)}</dd></div>
          </dl>
          {details.last_error && <Notice className="error" role="alert"><b>Último erro:</b> {details.last_error}</Notice>}
          {!!details.managed_fields.length && <p className="footnote">Campos geridos pelo ERP: {table.fields.filter(f => details.managed_fields.includes(f.name)).map(f => f.label).join(", ")}.</p>}
        </section>}
        <section aria-label="Registos dependentes"><h3>Utilização</h3>{details.dependencies.length ? <ul>{details.dependencies.map(d => <li key={d.label}>{d.count} {d.label}</li>)}</ul> : <p>Sem registos dependentes.</p>}</section>
      </> : !error && <Processing label="A carregar" />}
      <div className="ui-dialog-actions">
        <ActionButton type="button" onClick={onClose}>Fechar</ActionButton>
        {details && table.permissions.edit && <ActionButton type="button" className="primary" onClick={() => setMode("edit")}>Editar</ActionButton>}
      </div>
    </> : (mode === "edit" && !details) ? (!error && <Processing label="A carregar" />) : <Form key={recordId || "novo"} submitLabel={mode === "create" ? `Criar ${singular}` : "Guardar alterações"} onCancel={onClose} onSubmit={async () => {
      const body = { values: payload(table, values) };
      if (mode === "create") {
        // Obrigatórios só ao criar (ex.: rubrica, que não é um campo nativo): indica o separador certo.
        const missing = table.fields.find(f => f.create_required && (values[f.name] === "" || values[f.name] == null));
        if (missing) {
          if (tabs.length > 1) setTab(tabOf(missing.name));
          throw new Error(`Preencha o campo «${missing.label}» para criar o registo.`);
        }
        // Em modo integrado, o servidor grava logo no ERP e devolve o resultado.
        const created = await api(`/tables/${table.key}/records`, body) as { erp?: { ok: boolean; message: string } | null };
        const saved = `${singular.charAt(0).toUpperCase() + singular.slice(1)} ${gendered(table, "criado")}.`;
        if (created.erp && !created.erp.ok) onSaved(saved, created.erp.message);
        else onSaved(created.erp ? `${saved} ${created.erp.message}` : saved);
      } else {
        await api(`/tables/${table.key}/records/${encodeURIComponent(recordId!)}`, body);
        onSaved("Alterações guardadas.");
      }
    }}>
      {tabs.length > 1 && <ModuleTabs label={`Separadores da ficha de ${singular}`} id="table-record" panels tabs={tabs.map(name => ({ id: name, label: name }))} value={tab} onChange={setTab} />}
      <div onInvalidCapture={event => {
        // Campo obrigatório num separador escondido: mostra esse separador e o campo.
        const panel = (event.target as HTMLElement).closest<HTMLElement>("[data-tab]");
        if (panel?.dataset.tab && panel.dataset.tab !== tab) {
          setTab(panel.dataset.tab);
          const target = event.target as HTMLElement;
          window.setTimeout(() => target.focus(), 0);
        }
      }}>
      <div className={tabs.length > 1 ? "table-tab-panels" : undefined}>
      {(tabs.length > 1 ? tabs : [""]).map(current => <div key={current || "geral"} data-tab={current || undefined} role={tabs.length > 1 ? "tabpanel" : undefined} id={tabs.length > 1 ? `table-record-panel-${encodeURIComponent(current)}` : undefined} aria-labelledby={tabs.length > 1 ? `table-record-tab-${encodeURIComponent(current)}` : undefined} className={tabs.length > 1 && current !== tab ? "is-inactive" : undefined} inert={tabs.length > 1 && current !== tab} aria-hidden={tabs.length > 1 && current !== tab ? true : undefined}>
      {groups(visible.filter(f => tabs.length <= 1 || (f.tab || "") === current)).map(([group, fields]) => {
        const flags = fields.filter(f => f.type === "boolean");
        const grid = <div className="table-form-grid">
          {fields.filter(f => f.type !== "boolean").map(field => <div key={field.name} style={{ "--span": span(field) } as CSSProperties}>
            <Field label={<FieldLabel field={field} suffix={managed.has(field.name) && mode === "edit" ? " (ERP)" : ""} />}>{input(field)}</Field>
            {field.type === "nif" && <NifStatus state={lookup || checkNif(String(values[field.name] || ""))} />}
            {mode === "create" && field.create_required && field.type === "category" && !values[field.name] && <small>Obrigatório para criar.</small>}
            {field.help && <small>{field.help}</small>}
          </div>)}
          {flags.length > 0 && <div className="table-form-flags" style={{ "--span": 6 } as CSSProperties}>
            {flags.map(field => <span key={field.name}>{input(field)}{field.help && <small>{field.help}</small>}</span>)}
          </div>}
        </div>;
        return group && group !== current ? <fieldset key={group} className="table-form-group"><legend>{group}</legend>{grid}</fieldset> : <div key={group || "geral"}>{grid}</div>;
      })}
      </div>)}
      </div>
      </div>
      {preview && <div className="table-preview"><RubricBadge name={values.name || "Pré-visualização"} icon={values.icon} color={values.color} /></div>}
      {mode === "edit" && managed.size > 0 && <p className="footnote">Os campos marcados com «(ERP)» são geridos pelo ERP e só podem ser alterados na origem.</p>}
    </Form>}
  </AppDialog>;
}
