import { useCallback, useEffect, useState } from "react";
import { CircleMinus, Eye, Pencil, RotateCcw, Trash2 } from "lucide-react";
import { api } from "../lib/api";
import { ActionButton, Notice, confirmAction } from "../components/InteractionUI";
import { FilterBar, SearchInput } from "../components/DataTable";
import RubricBadge from "../components/RubricBadge";
import { ActiveBadge, OriginBadge, SyncBadge } from "./Badges";
import Processing from "./Processing";
import RecordDialog from "./RecordDialog";
import RemoveDialog from "./RemoveDialog";
import SyncDialog from "./SyncDialog";
import { DIRECTION_LABELS, displayValue, formatDateTime, gendered, type ConflictPolicy, type Direction, type TableContext, type TableInfo, type TableRow } from "./types";
import "./TablePage.css";

type Listing = { rows: TableRow[]; total: number; total_all: number; page: number; pages: number; size: number };
type Dialog =
  | { kind: "record"; mode: "create" | "edit" | "view"; id?: string }
  | { kind: "remove"; row: TableRow }
  | { kind: "sync"; direction: Direction; ids: string[] }
  | null;

const SYNC_FILTERS: [string, string][] = [["all", "Todos"], ["apenas_local", "Apenas local"], ["sincronizado", "Sincronizado"], ["pendente", "Alterações pendentes"], ["conflito", "Conflito"], ["erro", "Erro"]];

/**
 * Página comum a todas as tabelas: mesmas ações, posições, mensagens e confirmações.
 * A configuração (campos, permissões, capacidades do ERP) vem de /api/tables/{key}.
 */
export default function TablePage({ tableKey, context, active = true, store = "", onChanged }: {
  tableKey: string;
  context: TableContext;
  active?: boolean;
  store?: string;
  onChanged?: () => Promise<void> | void;
}) {
  const [table, setTable] = useState<TableInfo | null>(null);
  const [listing, setListing] = useState<Listing | null>(null);
  const [query, setQuery] = useState("");
  const [status, setStatus] = useState<"active" | "inactive" | "all">("active");
  const [fieldFilters, setFieldFilters] = useState<Record<string, string>>({});
  const [origin, setOrigin] = useState("all");
  const [sync, setSync] = useState("all");
  const [sort, setSort] = useState<{ id: string; direction: "asc" | "desc" }>({ id: "description", direction: "asc" });
  const [page, setPage] = useState(1);
  const [size, setSize] = useState(25);
  const [dialog, setDialog] = useState<Dialog>(null);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const [warning, setWarning] = useState("");
  const [busy, setBusy] = useState("");

  const load = useCallback(async () => {
    const params = new URLSearchParams({ q: query, status, origin, sync, store, sort: sort.id, direction: sort.direction, page: String(page), size: String(size) });
    for (const [name, value] of Object.entries(fieldFilters)) if (value) params.set("f_" + name, value);
    const [info, data] = await Promise.all([api(`/tables/${tableKey}`), api(`/tables/${tableKey}/records?${params}`)]);
    if (!Array.isArray(info?.fields) || !info?.erp || !Array.isArray(data?.rows)) {
      throw new Error("O servidor devolveu a tabela num formato inesperado. Atualize a página; se persistir, contacte o suporte.");
    }
    setTable(info);
    setListing(data);
  }, [tableKey, query, status, fieldFilters, origin, sync, store, sort, page, size]);

  useEffect(() => { if (active) load().then(() => setError("")).catch(e => setError(e.message)); }, [active, load]);
  useEffect(() => { setPage(1); setDialog(null); }, [tableKey]);

  async function changed(message: string, warning = "") {
    setDialog(null);
    setNotice(message);
    setWarning(warning);
    try { await load(); await onChanged?.(); }
    catch { setError("A operação foi guardada, mas não foi possível atualizar os dados do ecrã. Atualize a página para consultar o registo."); }
  }

  async function setActive(row: TableRow, value: boolean) {
    const label = row.code ? `${row.code} — ${row.description}` : row.description;
    if (!value && !(await confirmAction({
      title: `Desativar ${table!.singular}?`,
      message: <>«{label}» deixa de poder ser {gendered(table!, "usado")} em novos registos. O histórico e os documentos existentes mantêm-n{gendered(table!, "o")}. {row.erp_id ? "O ERP não é alterado." : ""}</>,
      confirmLabel: "Desativar",
      tone: "warning",
    }))) return;
    setBusy(row.id); setError(""); setNotice("");
    try {
      await api(`/tables/${tableKey}/records/${encodeURIComponent(row.id)}/status`, { active: value });
      await changed(`«${label}» ${gendered(table!, value ? "ativado" : "desativado")} na IAZELA.`);
    } catch (e) { setError((e as Error).message); }
    finally { setBusy(""); }
  }

  async function setPolicy(policy: ConflictPolicy) {
    try {
      await api(`/tables/${tableKey}/settings`, { conflict_policy: policy });
      await load();
      setNotice("Regra para conflitos guardada.");
    } catch (e) { setError((e as Error).message); }
  }

  if (!table || !listing) return <>{error ? <Notice className="error" role="alert">{error}</Notice> : <Processing label="A carregar" />}</>;

  const erp = table.erp;
  const integrated = erp.integrated;
  const permissions = table.permissions;
  const fieldColumns = table.fields.filter(f => f.column && f.name !== table.code_field && f.name !== table.description_field && f.name !== "icon" && f.name !== "color");
  const rows = listing.rows;
  const sortButton = (id: string, label: string) => <ActionButton type="button" aria-label={`Ordenar por ${label}`} onClick={() => { setSort(s => ({ id, direction: s.id === id && s.direction === "asc" ? "desc" : "asc" })); setPage(1); }}>{label}{sort.id === id ? (sort.direction === "asc" ? " ▲" : " ▼") : ""}</ActionButton>;
  const ariaSort = (id: string) => sort.id === id ? (sort.direction === "asc" ? "ascending" : "descending") : "none";
  const syncReason = (direction: Direction) => {
    if (!erp.available) return erp.reason;
    if (!erp.operations.read?.supported) return erp.operations.read?.reason || "O conector não permite consultar esta tabela.";
    if (direction === "export" ? !permissions.export : !permissions.import) return "O seu perfil não permite esta operação.";
    if (direction === "export" && !(["create", "update", "activate", "deactivate"] as const).some(op => erp.operations[op]?.supported)) {
      return erp.operations.create?.reason || erp.operations.update?.reason || "O conector não permite escrever nesta tabela.";
    }
    return "";
  };
  const showSync = (direction: Direction) => setDialog({ kind: "sync", direction, ids: [] });
  const label = (row: TableRow) => row.code ? `${row.code} — ${row.description}` : row.description;

  return <>
    <div className="page-intro">
      <div><h1>{table.title}</h1><p>{table.description}</p></div>
      {table.allow_create && permissions.create && <div className="table-page-actions">
        <ActionButton className="primary" onClick={() => setDialog({ kind: "record", mode: "create" })}>{gendered(table, "Novo")} {table.singular}</ActionButton>
      </div>}
    </div>
    {error && <Notice className="error" role="alert">{error}</Notice>}
    {notice && <Notice onDismiss={() => setNotice("")} className="success" role="status">{notice}</Notice>}
    {warning && <Notice className="warning" role="status">{warning}</Notice>}
    {integrated && <section className="panel table-erp-bar" aria-label="Integração com o ERP">
      <div className="table-erp-heading">
        <h2>Integração com o ERP{erp.provider ? ` (${erp.provider === "wintouch" ? "Wintouch" : erp.provider})` : ""}</h2>
        <p>Recebe as alterações do ERP e envia as da IAZELA; mostra primeiro o que muda e só executa depois de confirmar. Os registos novos são gravados no ERP ao criar.</p>
      </div>
      <div className="table-erp-actions">
        {(["sync"] as Direction[]).map(direction => {
          const reason = syncReason(direction);
          return <span key={direction} className="table-erp-action">
            <ActionButton type="button" disabled={!!reason || !!busy} title={reason || DIRECTION_LABELS[direction].destination} onClick={() => showSync(direction)}>{DIRECTION_LABELS[direction].button}</ActionButton>
            {reason && <small>{reason}</small>}
          </span>;
        })}
        {erp.available && <label className="table-policy">Em conflito
          <select aria-label="Regra para conflitos" value={erp.conflict_policy || "perguntar"} disabled={!permissions.erp_remove} title={permissions.erp_remove ? undefined : "Só um administrador pode alterar esta regra."} onChange={e => setPolicy(e.target.value as ConflictPolicy)}>
            <option value="perguntar">Perguntar em cada registo</option>
            <option value="erp">Prevalece o ERP</option>
            <option value="local">Prevalece a IAZELA</option>
          </select>
        </label>}
      </div>
      <details className="table-capabilities">
        <summary>Operações suportadas pelo conector</summary>
        <ul>{Object.entries(erp.operations).map(([key, op]) => op && <li key={key} className={op.supported ? "is-supported" : "is-unsupported"}>
          <b>{op.label}:</b> {op.supported ? "disponível" : `indisponível — ${op.reason}`}
        </li>)}</ul>
      </details>
    </section>}
    <section className="panel">
      <FilterBar label={`Filtros de ${table.title.toLowerCase()}`}>
        <SearchInput label="Pesquisar" value={query} onChange={e => { setQuery(e.target.value); setPage(1); }} />
        {table.has_active && <label>Estado<select aria-label="Estado" value={status} onChange={e => { setStatus(e.target.value as typeof status); setPage(1); }}><option value="active">Ativos</option><option value="inactive">Inativos</option><option value="all">Todos</option></select></label>}
        {(table.filters || []).map(name => {
          const field = table.fields.find(f => f.name === name);
          return field && <label key={name}>{field.label}<select aria-label={field.label} value={fieldFilters[name] || ""} onChange={e => { setFieldFilters({ ...fieldFilters, [name]: e.target.value }); setPage(1); }}>
            <option value="">Todas</option>{field.options.map(o => <option key={o.value} value={o.value}>{o.label}</option>)}
          </select></label>;
        })}
        {integrated && <label>Origem<select aria-label="Origem" value={origin} onChange={e => { setOrigin(e.target.value); setPage(1); }}><option value="all">Todas</option><option value="local">IAZELA</option><option value="erp">ERP</option></select></label>}
        {integrated && <label>Sincronização<select aria-label="Sincronização" value={sync} onChange={e => { setSync(e.target.value); setPage(1); }}>{SYNC_FILTERS.map(([v, l]) => <option key={v} value={v}>{l}</option>)}</select></label>}
        <label>Por página<select aria-label="Registos por página" value={size} onChange={e => { setSize(Number(e.target.value)); setPage(1); }}><option>10</option><option>25</option><option>50</option><option>100</option></select></label>
      </FilterBar>
      <div className="table-wrap">
        <table className="responsive-data-table table-page" aria-label={table.title}>
          <thead><tr>
            {table.code_field && <th aria-sort={ariaSort("code")}>{sortButton("code", "Código")}</th>}
            <th aria-sort={ariaSort("description")}>{sortButton("description", table.fields.find(f => f.name === table.description_field)?.label || "Descrição")}</th>
            {fieldColumns.map(f => <th key={f.name} aria-sort={ariaSort(f.name)}>{sortButton(f.name, f.label)}</th>)}
            {table.extra_columns.map(c => <th key={c.name} aria-sort={ariaSort(c.name)}>{sortButton(c.name, c.label)}</th>)}
            {integrated && <>
              <th aria-sort={ariaSort("origin")}>{sortButton("origin", "Origem")}</th>
              <th aria-sort={ariaSort("erp_code")}>{sortButton("erp_code", "Código no ERP")}</th>
              <th aria-sort={ariaSort("sync_state")}>{sortButton("sync_state", "Sincronização")}</th>
              <th aria-sort={ariaSort("last_sync_at")}>{sortButton("last_sync_at", "Última sincronização")}</th>
            </>}
            <th>Ações</th>
          </tr></thead>
          <tbody>{rows.map(row => <tr key={row.id} className={row.active ? undefined : "is-inactive"}>
            {table.code_field && <td>{row.code || "—"}</td>}
            <td><span className="table-description">{row.values.icon && row.values.color ? <RubricBadge name={row.description} icon={row.values.icon} color={row.values.color} /> : <span>{row.description || "—"}</span>}{!row.active && <ActiveBadge active={false} />}</span></td>
            {fieldColumns.map(f => <td key={f.name}>{displayValue(f, row.values[f.name], row.labels[f.name])}</td>)}
            {table.extra_columns.map(c => <td key={c.name}>{row.labels[c.name] || "—"}</td>)}
            {integrated && <>
              <td><OriginBadge origin={row.origin} /></td>
              <td>{row.erp_code || "—"}</td>
              <td><SyncBadge state={row.sync_state} label={row.sync_label} />{row.last_error && <ActionButton type="button" className="table-error-link" onClick={() => setDialog({ kind: "record", mode: "view", id: row.id })}>Ver erro</ActionButton>}</td>
              <td>{formatDateTime(row.last_sync_at)}</td>
            </>}
            <td><div className="table-row-actions">
              {/* Quem pode editar abre a ficha pelo lápis; a consulta só aparece a quem não pode. */}
              {!permissions.edit && <ActionButton type="button" aria-label={`Consultar ${label(row)}`} title="Consultar detalhes" onClick={() => setDialog({ kind: "record", mode: "view", id: row.id })}><Eye size={17} aria-hidden="true" /></ActionButton>}
              {permissions.edit && <ActionButton type="button" aria-label={`Editar ${label(row)}`} title="Editar" disabled={!!busy} onClick={() => setDialog({ kind: "record", mode: "edit", id: row.id })}><Pencil size={17} aria-hidden="true" /></ActionButton>}
              {table.has_active && permissions.status && (row.active
                ? <ActionButton type="button" aria-label={`Desativar ${label(row)}`} title="Desativar" disabled={!!busy} onClick={() => setActive(row, false)}><CircleMinus size={17} aria-hidden="true" /></ActionButton>
                : <ActionButton type="button" aria-label={`Ativar ${label(row)}`} title="Ativar" disabled={!!busy} onClick={() => setActive(row, true)}><RotateCcw size={17} aria-hidden="true" /></ActionButton>)}
              {(permissions.remove || permissions.erp_remove) && (table.allow_remove || row.erp_id) && <ActionButton type="button" className="danger" aria-label={`Remover ${label(row)}`} title="Remover" disabled={!!busy} onClick={() => setDialog({ kind: "remove", row })}><Trash2 size={17} aria-hidden="true" /></ActionButton>}
            </div></td>
          </tr>)}</tbody>
        </table>
      </div>
      {!rows.length && <p>{listing.total_all ? "Nenhum registo corresponde aos filtros." : `Ainda não existem registos em ${table.title.toLowerCase()}.`}</p>}
      <div className="table-pagination" role="group" aria-label="Paginação">
        <span>{listing.total} registo(s){listing.total !== listing.total_all ? ` de ${listing.total_all}` : ""} · Página {listing.page} de {listing.pages}</span>
        <ActionButton type="button" disabled={listing.page <= 1} onClick={() => setPage(listing.page - 1)}>Anterior</ActionButton>
        <ActionButton type="button" disabled={listing.page >= listing.pages} onClick={() => setPage(listing.page + 1)}>Seguinte</ActionButton>
      </div>
      {!integrated && table.local_only_reason && erp.reason !== "A empresa está em modo local." && <p className="footnote">{table.local_only_reason}</p>}
    </section>
    {dialog?.kind === "record" && <RecordDialog table={table} context={context} mode={dialog.mode} recordId={dialog.id} recordIds={rows.map(r => r.id)} onNavigate={id => setDialog({ ...dialog, id })} onClose={() => setDialog(null)} onSaved={changed} />}
    {dialog?.kind === "remove" && <RemoveDialog table={table} row={dialog.row} onClose={() => setDialog(null)} onDone={changed} onDeactivate={row => { setDialog(null); void setActive(row, false); }} />}
    {dialog?.kind === "sync" && <SyncDialog table={table} context={context} direction={dialog.direction} ids={dialog.ids} onClose={() => setDialog(null)} onDone={() => { void load().catch(() => undefined); void onChanged?.(); }} />}
  </>;
}
