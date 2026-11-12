import { useEffect, useMemo, useState } from "react";
import { Download, FileSearch, History, Link2, PencilLine, Search, X } from "lucide-react";
import { StatusBadge } from "./components/BusinessUI";
import { ActionButton, AppDialog, Notice } from "./components/InteractionUI";
import ReasonField from "./ReasonField";
import { api } from "./lib/api";
import { downloadCsv } from "./lib/csv";
import { formatEuro } from "./utils/format";
import "./RubricMovements.css";

/**
 * Movimentos de uma rubrica: os mesmos registos e valores do gráfico «Despesas por rubrica», com pesquisa, filtros
 * e correção da classificação (individual ou em lote) pelo circuito próprio de cada origem. Valores, IVA, datas,
 * documentos e tesouraria nunca mudam; cada alteração fica na auditoria e pode ser revertida.
 */

type Row = Record<string, any>;
const day = (iso?: string | null) => iso ? iso.slice(0, 10).split("-").reverse().join("/") : "—";
const STATES: Record<string, [string, "success" | "warning" | "info" | "neutral"]> = {
  pago: ["Pago", "success"], parcial: ["Pago em parte", "warning"], por_pagar: ["Por pagar", "warning"], sem_ligacao: ["Sem pagamento ligado", "neutral"],
  reconciliado: ["Reconciliado", "success"], por_reconciliar: ["Por reconciliar", "warning"], caixa: ["Caixa", "info"],
};
const REASONS = ["Rubrica corrigida após conferência do documento", "Seguro estava em outras despesas", "Pagamento de IVA ao Estado (obrigação fiscal)",
  "Corrigir classificação do movimento bancário"];
const euros = (cents: number) => (cents / 100).toFixed(2).replace(".", ",");
const amountOf = (text: string) => {
  const value = text.trim().replace(/[\s€]/g, "").replace(/^[-+]/, "").replace(/\.(?=\d{3}(?:\D|$))/g, "").replace(",", ".");
  return /^\d+(?:\.\d{1,2})?$/.test(value) ? Math.round(Number(value) * 100) : null;
};

export type MovementsContext = { company: string; store: string; start: string; end: string; view: string; viewLabel?: string };

/** Restrição de origem (indicador ou alerta): só estas rubricas ou estes registos. */
export type MovementsScope = { categories?: string[]; ids?: string[]; label: string };

export default function RubricMovements({ context, category, categoryName, only, origin: auditOrigin, onClose, onChanged, onConsult, onReconcile }: {
  context: MovementsContext; category?: string | null; categoryName?: string; only?: MovementsScope; origin?: string;
  onClose: () => void; onChanged: () => void; onConsult: (row: Row) => void; onReconcile: (row: Row) => void;
}) {
  const [data, setData] = useState<Row | null>(null), [error, setError] = useState(""), [notice, setNotice] = useState("");
  const [busy, setBusy] = useState(false), [version, setVersion] = useState(0);
  const [search, setSearch] = useState(""), [origin, setOrigin] = useState(""), [state, setState] = useState(""), [account, setAccount] = useState("");
  const [rubric, setRubric] = useState(category === null ? "none" : category && category !== "outras" ? category : "");
  const [from, setFrom] = useState(context.start), [to, setTo] = useState(context.end), [limit, setLimit] = useState(60);
  const [selected, setSelected] = useState<string[]>([]), [target, setTarget] = useState("");
  const [preview, setPreview] = useState<Row | null>(null), [reason, setReason] = useState("");
  const [editing, setEditing] = useState<Row | null>(null), [history, setHistory] = useState<Row[] | null>(null), [revertReason, setRevertReason] = useState("");
  useEffect(() => {
    let active = true;
    setError("");
    api(`/rubric-movements?${new URLSearchParams({ start: context.start, end: context.end, store_id: context.store, view: context.view })}`, undefined, { timeoutMs: 60_000 })
      .then(r => { if (active) { setData(r); setSelected(s => s.filter(id => r.rows.some((row: Row) => row.id === id && row.editable))); } })
      .catch(e => { if (active) setError((e as Error).message); });
    return () => { active = false; };
  }, [context.start, context.end, context.store, context.view, version]);

  const amount = amountOf(search);
  // A rubrica escolhida inclui as suas sub-rubricas.
  const family = useMemo(() => new Set([rubric, ...(data?.categories || []).filter((c: Row) => c.parent_id === rubric).map((c: Row) => c.id)]), [data, rubric]);
  const matches = (share: Row) => rubric === "none" ? !share.category_id : family.has(share.category_id);
  const scoped = (share: Row) => !only?.categories || only.categories.includes(share.category_id || "");
  const rows: Row[] = useMemo(() => (data?.rows || []).filter((row: Row) =>
    (!only?.ids || only.ids.includes(row.id)) && row.shares.some(scoped)
    && (!rubric || row.shares.some(matches))
    && (!origin || row.origin === origin) && (!state || row.state === state) && (!account || row.account_id === account)
    && (!from || row.date >= from) && (!to || row.date <= to)
    && (!search.trim() || amount != null && [row.amount_cents, row.base_cents + row.tax_cents, row.category_amount_cents].includes(amount)
      || `${row.label} ${row.entity} ${row.account} ${row.annotation?.notes || ""}`.toLocaleLowerCase("pt-PT").includes(search.trim().toLocaleLowerCase("pt-PT")))),
  [data, rubric, family, origin, state, account, from, to, search, amount, only]);
  const inRubric = (row: Row) => row.shares.filter((s: Row) => (!rubric || matches(s)) && scoped(s))
    .reduce((n: number, s: Row) => n + s.amount_cents, 0);
  const total = rows.reduce((n, row) => n + inRubric(row), 0);
  const editable = rows.filter(row => row.editable);
  const categories: Row[] = data?.categories || [];
  const color = (id?: string | null) => categories.find(c => c.id === id)?.color || "var(--color-muted)";
  const title = rubric === "none" ? "Por classificar" : categories.find(c => c.id === rubric)?.name || categoryName || only?.label || "Todas as rubricas";
  const exportRows = () => downloadCsv(`movimentos-${title.toLocaleLowerCase("pt-PT").replace(/[^a-z0-9à-ú]+/gi, "-")}-${context.start}-${context.end}.csv`, [
    ["Data", "Pagamento", "Documento ou movimento", "Entidade", "Conta", "Origem", "Rubrica", "Base", "IVA", "Valor na rubrica", "Valor do registo", "Estado", "Observações"],
    ...rows.map(row => [day(row.date), row.payment_date ? day(row.payment_date) : "", row.label, row.entity, row.account, row.origin_label,
      row.shares.map((s: Row) => s.name).join(" + "), euros(row.base_cents), euros(row.tax_cents), euros(inRubric(row)), euros(row.amount_cents),
      (STATES[row.state] || [""])[0], row.annotation?.notes || ""]),
    ["Total", "", "", "", "", "", "", "", "", euros(total)],
  ]);

  async function run(action: () => Promise<void>) {
    setBusy(true); setError(""); setNotice("");
    try { await action(); } catch (e) { setError((e as Error).message); } finally { setBusy(false); }
  }
  const showPreview = (ids: string[], categoryId: string) => run(async () => {
    setPreview(await api("/rubric-movements/preview", { ids, category_id: categoryId })); setReason("");
  });
  const apply = () => run(async () => {
    const result = await api("/rubric-movements/apply", { ids: preview!.items.map((i: Row) => i.id), category_id: target, fingerprint: preview!.fingerprint, reason: reason.trim(), ...(auditOrigin ? { origin: auditOrigin } : {}) });
    setPreview(null); setSelected([]); setEditing(null); setVersion(v => v + 1); onChanged();
    setNotice(`${result.applied} movimento(s) passaram para «${preview!.target}»${result.skipped ? ` (${result.skipped} com restrições ficaram como estavam)` : ""}. Gráfico, totais e orçamento atualizados; valores e tesouraria não mudaram.`);
  });
  const askApproval = () => run(async () => {
    await api("/rubric-nature/requests", { kind: "reclassification", items: preview!.approval, category_id: target, reason: reason.trim() });
    setNotice("Pedido enviado ao administrador para os pagamentos do banco e da caixa. Nada muda até ser aprovado.");
  });
  const openEdit = (row: Row) => run(async () => {
    setEditing({ ...row, cost_center_id: row.annotation.cost_center_id || "", notes: row.annotation.notes || "" });
    setTarget(row.shares[0]?.category_id || ""); setPreview(null); setRevertReason("");
    setHistory((await api(`/rubric-movements/history?${new URLSearchParams({ id: row.id })}`)).entries);
  });
  const saveAnnotation = () => run(async () => {
    await api("/rubric-movements/annotation", { id: editing!.id, cost_center_id: editing!.cost_center_id || null, notes: editing!.notes, revision: editing!.annotation.revision }, { method: "PUT" });
    setVersion(v => v + 1); setEditing(null); setNotice("Centro de custo e observações guardados.");
  });
  const revert = (entry: Row) => run(async () => {
    await api("/rubric-movements/revert", { id: editing!.id, audit_id: entry.id, reason: revertReason.trim(), ...(auditOrigin ? { origin: auditOrigin } : {}) });
    setVersion(v => v + 1); setEditing(null); onChanged(); setNotice("Classificação revertida. Gráfico, totais e orçamento atualizados.");
  });

  return <AppDialog className="drilldown-dialog rubric-movements" aria-label="Movimentos da rubrica" guardChanges={false} onRequestClose={onClose}>
    <header className="drilldown-header">
      <h2>Movimentos · {title}</h2>
      <p>{context.company} · {context.viewLabel || "Custos e despesas operacionais"} · {day(context.start)} a {day(context.end)}</p>
    </header>
    {error && <Notice tone="error">{error}</Notice>}
    {notice && <Notice tone="success">{notice}</Notice>}
    {!data ? !error && <p role="status">A carregar os movimentos…</p> : <>
      <div className="rm-summary" role="status"><strong>{formatEuro(total)}</strong> em {rows.length} registo(s){rows.length !== data.rows.length && ` de ${data.rows.length}`}
        <small>Cada documento conta uma vez, pela data do documento; um pagamento de uma fatura não é uma segunda despesa.</small>
        <ActionButton type="button" className="rm-export" disabled={!rows.length} onClick={exportRows}><Download size={15} aria-hidden="true" />Exportar movimentos</ActionButton></div>
      <div className="rm-filters" role="group" aria-label="Pesquisar e filtrar movimentos">
        <label className="rm-search"><Search size={15} aria-hidden="true" /><input aria-label="Pesquisar descrição, fornecedor, documento ou valor" placeholder="Descrição, fornecedor, documento ou valor…" value={search} onChange={e => { setSearch(e.target.value); setLimit(60); }} /></label>
        <label>Rubrica<select value={rubric} onChange={e => { setRubric(e.target.value); setLimit(60); }}>
          <option value="">Todas</option><option value="none">Por classificar</option>
          {categories.map(c => <option key={c.id} value={c.id}>{c.name}</option>)}</select></label>
        <label>Origem<select value={origin} onChange={e => setOrigin(e.target.value)}>
          <option value="">Todas</option><option value="erp">ERP</option><option value="purchase">Compra</option><option value="efatura">e-Fatura</option>
          <option value="bank">Banco</option><option value="move">Caixa</option></select></label>
        <label>Estado<select value={state} onChange={e => setState(e.target.value)}>
          <option value="">Todos</option>{Object.entries(STATES).map(([key, [label]]) => <option key={key} value={key}>{label}</option>)}</select></label>
        {!!data.accounts.length && <label>Conta<select value={account} onChange={e => setAccount(e.target.value)}>
          <option value="">Todas</option>{data.accounts.map((a: Row) => <option key={a.id} value={a.id}>{a.name}</option>)}</select></label>}
        <label>Desde<input type="date" value={from} min={context.start} max={context.end} onChange={e => setFrom(e.target.value)} /></label>
        <label>Até<input type="date" value={to} min={context.start} max={context.end} onChange={e => setTo(e.target.value)} /></label>
      </div>
      {selected.length > 0 && <div className="rm-batch" role="region" aria-label="Alterar rubrica dos selecionados">
        <span><strong>{selected.length}</strong> selecionado(s)</span>
        <label>Nova rubrica<select value={target} onChange={e => { setTarget(e.target.value); setPreview(null); }}>
          <option value="">Escolher…</option>{categories.map(c => <option key={c.id} value={c.id}>{c.name}</option>)}</select></label>
        <ActionButton type="button" className="primary" disabled={busy || !target} onClick={() => void showPreview(selected, target)}>Pré-visualizar</ActionButton>
        <ActionButton type="button" onClick={() => { setSelected([]); setPreview(null); }}>Limpar seleção</ActionButton>
      </div>}
      {preview && <section className="rm-preview" aria-label="Pré-visualização da alteração de rubrica">
        <h3>Passar {preview.count} movimento(s) ({formatEuro(preview.total_cents)}) para «{preview.target}»</h3>
        <div className="table-wrap"><table aria-label="Movimentos a alterar"><thead><tr><th>Movimento</th><th>Rubrica anterior</th><th>Nova rubrica</th><th>Valor</th><th>Situação</th></tr></thead>
          <tbody>{preview.items.map((i: Row) => <tr key={i.id} className={i.blocked ? "is-blocked" : undefined}>
            <td>{i.label}<small>{i.entity} · {i.source}</small></td><td>{i.before_names.join(", ") || "—"}</td><td>{i.blocked ? "—" : preview.target}</td>
            <td className="numeric">{formatEuro(i.amount_cents)}</td><td>{i.blocked ? <small className="rm-blocked">{i.blocked}</small> : <StatusBadge tone="success" compact>Pronto</StatusBadge>}</td>
          </tr>)}</tbody></table></div>
        <p className="rm-hint">Não muda: {preview.preserved.join("; ")}.</p>
        {preview.count > 0 && <div className="rm-apply">
          <ReasonField label="Motivo da alteração" value={reason} onChange={setReason} options={REASONS} />
          <ActionButton type="button" className="primary" disabled={busy || reason.trim().length < 5} onClick={() => void apply()}>Aplicar a {preview.count} movimento(s)</ActionButton>
        </div>}
        {preview.approval?.length > 0 && <div className="rm-apply">
          {preview.count === 0 && <ReasonField label="Motivo do pedido" value={reason} onChange={setReason} options={REASONS} />}
          <ActionButton type="button" disabled={busy || reason.trim().length < 5} onClick={() => void askApproval()}>Pedir aprovação para {preview.approval.length} pagamento(s) do banco ou da caixa</ActionButton>
        </div>}
      </section>}
      <div className="table-wrap rm-table"><table aria-label="Movimentos da rubrica">
        <thead><tr><th className="rm-check"><input type="checkbox" aria-label="Selecionar movimentos editáveis visíveis" disabled={!editable.length}
          checked={!!editable.length && editable.slice(0, limit).every(r => selected.includes(r.id))}
          onChange={e => setSelected(e.target.checked ? [...new Set([...selected, ...editable.slice(0, limit).map(r => r.id)])] : selected.filter(id => !editable.some(r => r.id === id)))} /></th>
          <th>Data</th><th>Documento ou movimento</th><th>Origem</th><th>Rubrica</th><th className="numeric">Base</th><th className="numeric">IVA</th><th className="numeric">Valor</th><th>Estado</th><th><span className="sr-only">Ações</span></th></tr></thead>
        <tbody>{rows.slice(0, limit).map(row => { const [stateLabel, tone] = STATES[row.state] || ["—", "neutral"]; return <tr key={row.id} data-selected={selected.includes(row.id) || undefined}>
          <td className="rm-check"><input type="checkbox" aria-label={`Selecionar ${row.label}`} disabled={!row.editable} title={row.blocked || undefined}
            checked={selected.includes(row.id)} onChange={e => setSelected(e.target.checked ? [...selected, row.id] : selected.filter(id => id !== row.id))} /></td>
          <td>{day(row.date)}{row.payment_date && row.payment_date !== row.date && <small>Pago {day(row.payment_date)}</small>}</td>
          <td><strong>{row.label}</strong><small>{row.entity}{row.account && ` · ${row.account}`}</small>{row.annotation?.notes && <small className="rm-note">{row.annotation.notes}</small>}</td>
          <td><StatusBadge tone="neutral" compact>{row.origin_label}</StatusBadge></td>
          <td>{row.shares.map((s: Row, i: number) => <span key={i} className="rm-rubric"><i style={{ background: color(s.category_id) }} aria-hidden="true" />{s.name}</span>)}</td>
          <td className="numeric">{formatEuro(row.base_cents)}</td><td className="numeric">{formatEuro(row.tax_cents)}</td>
          <td className="numeric"><strong>{formatEuro(inRubric(row))}</strong>{inRubric(row) !== row.amount_cents && <small>de {formatEuro(row.amount_cents)}</small>}</td>
          <td><StatusBadge tone={tone} compact>{stateLabel}</StatusBadge></td>
          <td><div className="rm-actions">
            <ActionButton type="button" aria-label={`Consultar ${row.label}`} title="Consultar o documento e os originais" onClick={() => onConsult(row)}><FileSearch size={15} /></ActionButton>
            <ActionButton type="button" aria-label={`Editar ${row.label}`} title="Rubrica, centro de custo, observações e histórico" onClick={() => void openEdit(row)}><PencilLine size={15} /></ActionButton>
            {row.origin !== "erp" && row.origin !== "purchase" && row.origin !== "efatura" && row.state === "por_reconciliar" &&
              <ActionButton type="button" aria-label={`Associar ${row.label} a um documento`} title="Associar a um documento na Reconciliação" onClick={() => onReconcile(row)}><Link2 size={15} /></ActionButton>}
          </div></td>
        </tr>; })}</tbody></table></div>
      {!rows.length && <p className="rm-hint">Sem movimentos para estes filtros.</p>}
      {rows.length > limit && <ActionButton type="button" onClick={() => setLimit(l => l + 60)}>Mostrar mais movimentos</ActionButton>}
    </>}
    {editing && <AppDialog className="rm-edit" aria-label={`Editar ${editing.label}`} guardChanges={false} onRequestClose={() => setEditing(null)}>
      <header className="drilldown-header"><h2>{editing.label}</h2><p>{editing.entity} · {editing.origin_label} · {day(editing.date)} · {formatEuro(editing.amount_cents)}</p></header>
      <ActionButton type="button" className="rm-close" aria-label="Fechar" onClick={() => setEditing(null)}><X size={16} /></ActionButton>
      <section aria-label="Rubrica do movimento" className="rm-edit-block">
        <h3>Rubrica</h3>
        {editing.blocked ? <Notice tone="info">{editing.blocked}</Notice> : <div className="rm-batch">
          <label>Nova rubrica<select value={target} onChange={e => { setTarget(e.target.value); setPreview(null); }}>
            {categories.map(c => <option key={c.id} value={c.id}>{c.name}</option>)}</select></label>
          <ActionButton type="button" className="primary" disabled={busy || !target} onClick={() => { setSelected([editing.id]); setEditing(null); void showPreview([editing.id], target); }}>Pré-visualizar alteração</ActionButton>
        </div>}
      </section>
      <section aria-label="Classificação de gestão" className="rm-edit-block">
        <h3>Centro de custo e observações</h3>
        <label>Centro de custo<select value={editing.cost_center_id} onChange={e => setEditing({ ...editing, cost_center_id: e.target.value })}>
          <option value="">Sem centro de custo</option>{data?.cost_centers.map((c: Row) => <option key={c.id} value={c.id}>{c.name}</option>)}</select></label>
        <label>Observações<textarea rows={2} maxLength={2000} value={editing.notes} onChange={e => setEditing({ ...editing, notes: e.target.value })} /></label>
        <ActionButton type="button" disabled={busy} onClick={() => void saveAnnotation()}>Guardar centro de custo e observações</ActionButton>
      </section>
      <section aria-label="Histórico da classificação" className="rm-edit-block">
        <h3><History size={15} aria-hidden="true" /> Histórico</h3>
        {!history ? <p role="status">A carregar…</p> : !history.length ? <p className="rm-hint">Sem alterações registadas.</p> : <>
          <ul className="rm-history">{history.map(entry => <li key={entry.id}>
            <span>{day(entry.date)} · {entry.user}: {entry.before.join(", ")} → {entry.after.join(", ")}{entry.reason && ` · «${entry.reason}»`}</span>
            {entry.revertible && <ActionButton type="button" disabled={busy || revertReason.trim().length < 5} onClick={() => void revert(entry)}>Reverter</ActionButton>}
          </li>)}</ul>
          {history.some(e => e.revertible) && <ReasonField label="Motivo da reversão" value={revertReason} onChange={setRevertReason} options={["Classificação anterior estava correta"]} />}
        </>}
      </section>
    </AppDialog>}
  </AppDialog>;
}
