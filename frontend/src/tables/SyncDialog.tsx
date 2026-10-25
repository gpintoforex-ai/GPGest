import { useEffect, useRef, useState } from "react";
import { CheckCircle2, CircleAlert, GitCompareArrows, MinusCircle, PlusCircle, RefreshCw, Trash2 } from "lucide-react";
import { api } from "../lib/api";
import { ActionButton, AppDialog, Notice } from "../components/InteractionUI";
import { Field } from "../components/Form";
import Processing from "./Processing";
import { DIRECTION_LABELS, type Direction, type Plan, type PlanItem, type SyncRun, type TableContext, type TableInfo } from "./types";

const SUMMARY: { key: PlanItem["summary"]; label: string; Icon: typeof PlusCircle }[] = [
  { key: "criar", label: "A criar", Icon: PlusCircle },
  { key: "atualizar", label: "A atualizar", Icon: RefreshCw },
  { key: "desativar", label: "A desativar", Icon: MinusCircle },
  { key: "remover", label: "A remover", Icon: Trash2 },
  { key: "ignorar", label: "Ignorados", Icon: CheckCircle2 },
  { key: "conflito", label: "Conflitos", Icon: GitCompareArrows },
];
type Resolution = "ignorar" | "local" | "erp";

function show(value: unknown) {
  if (value === null || value === undefined || value === "") return "—";
  if (typeof value === "boolean") return value ? "Sim" : "Não";
  const text = String(value);
  return text.startsWith("erp:") ? "(prazo do ERP por importar)" : text;
}

function minutes(seconds: number) {
  if (seconds < 60) return "menos de 1 minuto";
  const value = Math.round(seconds / 60);
  return value === 1 ? "cerca de 1 minuto" : `cerca de ${value} minutos`;
}

/** Barra de progresso: feitos/total, registo em curso, erros e tempo estimado. */
function SyncProgress({ run }: { run: SyncRun }) {
  const percent = run.total ? Math.round((run.done / run.total) * 100) : 0;
  const elapsed = (Date.now() - new Date(run.started_at).getTime()) / 1000;
  const remaining = run.done && run.total > run.done ? (elapsed / run.done) * (run.total - run.done) : null;
  const comparing = run.done === 0 && run.current === "A comparar com o ERP";
  return <section className="table-sync-progress" aria-label="Progresso da sincronização">
    <div className="table-sync-progress-head">
      <b>{comparing ? "A comparar com o ERP…" : `A sincronizar: ${run.done} de ${run.total}`}</b>
      <span>{percent}%</span>
    </div>
    <div className="table-sync-progress-bar" role="progressbar" aria-label="Registos sincronizados" aria-valuemin={0} aria-valuemax={run.total} aria-valuenow={run.done} aria-valuetext={`${run.done} de ${run.total}`}>
      <span style={{ width: `${comparing ? 0 : percent}%` }} />
    </div>
    <div className="table-sync-progress-foot">
      <span>{run.current && !comparing ? <>A tratar: <b>{run.current}</b></> : <Processing label="A preparar" inline />}</span>
      <span>{run.failed ? <span className="table-sync-progress-errors">{run.failed} com erro · </span> : null}{remaining !== null ? `Falta ${minutes(remaining)}` : ""}</span>
    </div>
    <p className="footnote">Pode fechar esta janela: a sincronização continua no servidor e o progresso volta a aparecer ao reabrir.</p>
  </section>;
}

/** Pré-visualização obrigatória, execução confirmada e resultado por registo. */
export default function SyncDialog({ table, context, direction, ids, onClose, onDone }: {
  table: TableInfo;
  context: TableContext;
  direction: Direction;
  ids: string[];
  onClose: () => void;
  onDone: () => void;
}) {
  const [options, setOptions] = useState<Record<string, string>>({});
  const [scope, setScope] = useState<string[]>(ids);
  const [plan, setPlan] = useState<Plan | null>(null);
  const [selected, setSelected] = useState<Set<string>>(new Set());
  const [resolutions, setResolutions] = useState<Record<string, Resolution>>({});
  // Execução em segundo plano: progresso enquanto corre; resultados quando termina.
  const [run, setRun] = useState<SyncRun | null>(null);
  const [checked, setChecked] = useState(false);
  const following = useRef("");
  const results = run && run.status !== "em_curso" ? run : null;
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const labels = DIRECTION_LABELS[direction];
  const importOptions = table.erp.import_options || [];

  async function preview(preselect?: string[]) {
    setBusy(true); setError(""); setPlan(null); setRun(null);
    try {
      const data: Plan = await api(`/tables/${table.key}/sync/plan`, { direction, ids: scope, options, confirm_demo: true });
      setPlan(data);
      setSelected(new Set(data.items.filter(i => i.action !== "ignorar" && (preselect ? preselect.includes(i.key) : !i.optional)).map(i => i.key)));
      setResolutions(Object.fromEntries(data.items.filter(i => i.action === "conflito").map(i => [i.key, "ignorar" as Resolution])));
    } catch (e) { setError((e as Error).message); }
    finally { setBusy(false); }
  }
  // Ao abrir: se esta tabela já está a sincronizar (ex.: janela fechada a meio), retoma o acompanhamento.
  useEffect(() => {
    api(`/tables/${table.key}/sync/running`)
      .then((current: SyncRun | null) => { if (current) follow(current); })
      .catch(() => undefined)
      .finally(() => setChecked(true));
    return () => { following.current = ""; };
  }, [table.key]);
  useEffect(() => { if (checked && !following.current) void preview(); }, [options, scope, checked]);

  /** Acompanha a execução no servidor até terminar (consulta a cada segundo). */
  function follow(current: SyncRun) {
    following.current = current.id;
    setRun(current);
    const poll = async () => {
      if (following.current !== current.id) return;
      try {
        const next: SyncRun = await api(`/tables/${table.key}/sync/runs/${current.id}`);
        if (following.current !== current.id) return;
        setRun(next);
        if (next.status === "em_curso") { window.setTimeout(poll, 1000); return; }
        following.current = "";
        if (next.status === "falhou" && next.error) setError(next.error);
        onDone();
      } catch {
        window.setTimeout(poll, 3000);  // Falha momentânea de rede: tenta de novo, sem repetir a execução.
      }
    };
    window.setTimeout(poll, 1000);
  }

  async function execute() {
    if (!plan) return;
    setBusy(true); setError("");
    try {
      const started: SyncRun = await api(`/tables/${table.key}/sync/execute`, {
        direction, ids: plan.ids, options: plan.options, token: plan.token, items: [...selected], resolutions, confirm_demo: true,
      });
      follow(started);
    } catch (e) { setError((e as Error).message); }
    finally { setBusy(false); }
  }

  function retryFailed() {
    if (!results) return;
    const failed = results.retry;
    const local = plan?.items.filter(i => failed.includes(i.key) && i.local_id).map(i => i.local_id!) || [];
    // Só os registos com erro: os concluídos não são repetidos.
    if (local.length === failed.length) setScope(local);
    else void preview(failed);
  }

  const actionable = plan?.items.filter(i => i.action !== "ignorar") || [];
  const ignored = plan?.items.filter(i => i.action === "ignorar") || [];
  const marked = actionable.filter(i => selected.has(i.key)).length;
  const allMarked = actionable.length > 0 && marked === actionable.length;
  const setAll = (on: boolean) => setSelected(on ? new Set(actionable.map(i => i.key)) : new Set());
  const toggle = (key: string, on: boolean) => setSelected(current => { const next = new Set(current); if (on) next.add(key); else next.delete(key); return next; });

  return <AppDialog autoOpen busy={busy} className="modal table-dialog table-sync-dialog" onRequestClose={onClose} aria-labelledby="table-sync-title">
    <div className="panel-title"><h2 id="table-sync-title">{labels.title} — {table.title}</h2></div>
    <Notice className={direction === "import" ? "info" : "warning"} role="status"><b>Destino:</b> {labels.destination}</Notice>
    <p className="footnote">{scope.length ? `Registos selecionados: ${scope.length}.` : "Todos os registos da tabela."} Nada é alterado antes de confirmar.</p>
    {direction !== "export" && importOptions.length > 0 && <div className="table-sync-options">
      {importOptions.map(f => <Field key={f.name} label={f.label}>
        {f.type === "store"
          ? <select value={options[f.name] || ""} onChange={e => setOptions({ ...options, [f.name]: e.target.value })}><option value="">{context.stores.length === 1 ? context.stores[0].name : "Escolha…"}</option>{context.stores.map(s => <option key={s.id} value={s.id}>{s.name}</option>)}</select>
          : <input value={options[f.name] || ""} onChange={e => setOptions({ ...options, [f.name]: e.target.value })} />}
      </Field>)}
    </div>}
    {error && <Notice className="error" role="alert">{error}</Notice>}
    {busy && !plan && !run && <Processing label="A comparar com o ERP" />}
    {!checked && <Processing label="A carregar" />}
    {run && run.status === "em_curso" && <SyncProgress run={run} />}
    {plan && !run && <>
      <ul className="table-sync-summary" aria-label="Resumo">
        {SUMMARY.map(({ key, label, Icon }) => <li key={key} className={plan.summary[key] ? "has-items" : ""}><Icon size={16} aria-hidden="true" /><b>{plan.summary[key]}</b> {label}</li>)}
      </ul>
      {actionable.some(i => i.optional) && <p className="footnote">Os registos que só existem na IAZELA ficam desmarcados: marque os que quer criar também no ERP, ou use «Marcar todos».</p>}
      {actionable.length > 1 && <label className={`table-sync-select${allMarked ? " is-on" : ""}`}>
        <input type="checkbox" aria-label="Marcar todos" checked={allMarked} disabled={busy} ref={el => { if (el) el.indeterminate = marked > 0 && !allMarked; }} onChange={e => setAll(e.target.checked)} />
        <b>Marcar todos</b>
        <span aria-live="polite">{marked} de {actionable.length} marcados</span>
      </label>}
      {plan.summary.conflito > 0 && <p className="footnote">Regra para conflitos: {plan.conflict_policy === "perguntar" ? "decidir em cada registo" : plan.conflict_policy === "erp" ? "prevalece o ERP" : "prevalece a IAZELA"}. Um conflito sem decisão fica por resolver e nada é alterado.</p>}
      {actionable.length ? <div className="table-wrap"><table className="table-sync-items">
        <thead><tr><th aria-label="Incluir"></th><th>Registo</th><th>Ação</th><th>Alterações</th></tr></thead>
        <tbody>{actionable.map(item => <tr key={item.key}>
          <td><input type="checkbox" aria-label={`Incluir ${item.label}`} checked={selected.has(item.key)} onChange={e => toggle(item.key, e.target.checked)} /></td>
          <td>{item.label}</td>
          <td><b>{item.action_label}</b>{item.reason && <small>{item.reason}</small>}
            {item.action === "conflito" && <select aria-label={`Resolução de ${item.label}`} value={resolutions[item.key]} onChange={e => setResolutions({ ...resolutions, [item.key]: e.target.value as Resolution })}>
              <option value="ignorar">Manter por resolver</option>
              <option value="erp">Usar os dados do ERP</option>
              <option value="local" disabled={!table.erp.operations.update?.supported}>Usar os dados da IAZELA{table.erp.operations.update?.supported ? "" : " (indisponível)"}</option>
            </select>}
          </td>
          <td>{item.changes.length ? <table className="table-changes"><thead><tr><th>Campo</th><th>IAZELA</th><th>ERP</th></tr></thead><tbody>{item.changes.map(c => <tr key={c.field}><td>{c.label}</td><td>{show(c.local)}</td><td>{show(c.erp)}</td></tr>)}</tbody></table> : "—"}</td>
        </tr>)}</tbody>
      </table></div> : <p>Não há alterações a aplicar.</p>}
      {ignored.length > 0 && <details className="table-sync-ignored"><summary>{ignored.length} registo(s) ignorado(s)</summary><ul>{ignored.map(i => <li key={i.key}><b>{i.label}</b> — {i.reason}</li>)}</ul></details>}
    </>}
    {results && (results.status === "concluida" || results.results.length > 0) && <>
      <Notice className={results.failed ? "warning" : "success"} role="status">{results.ok} operação(ões) concluída(s){results.failed ? `, ${results.failed} com erro` : ""}.</Notice>
      <ul className="table-sync-results">{results.results.map(r => <li key={r.key} className={r.ok ? "is-ok" : "is-error"}>
        {r.ok ? <CheckCircle2 size={16} aria-hidden="true" /> : <CircleAlert size={16} aria-hidden="true" />}
        <span><b>{r.label}</b> — {r.action_label}: {r.ok ? "concluído" : "erro"}. <small>{r.message}</small></span>
      </li>)}</ul>
    </>}
    <div className="ui-dialog-actions">
      <ActionButton type="button" disabled={busy} onClick={onClose}>{run?.status === "em_curso" ? "Fechar (continua em segundo plano)" : "Fechar"}</ActionButton>
      {results && results.retry.length > 0 && <ActionButton type="button" disabled={busy} onClick={retryFailed}>Repetir os que falharam ({results.retry.length})</ActionButton>}
      {plan && !run && <ActionButton type="button" className="primary" disabled={busy || !selected.size} onClick={execute}>{busy ? "A executar…" : `${labels.confirm} (${selected.size})`}</ActionButton>}
    </div>
  </AppDialog>;
}
