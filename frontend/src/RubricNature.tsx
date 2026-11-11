import { useEffect, useMemo, useState } from "react";
import { FlaskConical, Lightbulb, ShieldCheck } from "lucide-react";
import { StatusBadge } from "./components/BusinessUI";
import { ActionButton, Notice } from "./components/InteractionUI";
import { api } from "./lib/api";
import { formatEuro } from "./utils/format";
import "./RubricNature.css";

/**
 * Natureza financeira das rubricas: em que análises entra cada rubrica (custos operacionais, compras e
 * investimentos, saídas de tesouraria, obrigações fiscais, orçamento de pagamentos). Qualquer alteração é
 * simulada (antes/depois nos últimos 12 meses e registos afetados) e só um administrador a aplica, com motivo.
 * Os documentos e movimentos nunca são alterados.
 */

type Row = Record<string, any>;
const FLAGS = ["in_operating_costs", "in_purchases", "in_treasury", "in_fiscal", "in_payment_budget"] as const;
type Flag = typeof FLAGS[number];
type Draft = { nature: string } & Record<Flag, boolean>;
const SHORT: Record<Flag, string> = {
  in_operating_costs: "Custos operacionais", in_purchases: "Compras e investimentos", in_treasury: "Saídas de tesouraria",
  in_fiscal: "Obrigações fiscais", in_payment_budget: "Orçamento de pagamentos",
};
const fullDate = (iso?: string | null) => iso ? iso.slice(0, 10).split("-").reverse().join("/") : "—";
const pick = (row: Row): Draft => ({ nature: row.nature, ...Object.fromEntries(FLAGS.map(f => [f, row[f]])) } as Draft);
const same = (a: Draft, b: Draft) => a.nature === b.nature && FLAGS.every(f => a[f] === b[f]);

export default function RubricNature() {
  const [data, setData] = useState<Row | null>(null), [drafts, setDrafts] = useState<Record<string, Draft>>({});
  const [simulation, setSimulation] = useState<Row | null>(null), [reason, setReason] = useState("");
  const [error, setError] = useState(""), [notice, setNotice] = useState(""), [busy, setBusy] = useState(false), [version, setVersion] = useState(0);
  const [showInactive, setShowInactive] = useState(false);
  useEffect(() => {
    let active = true;
    api("/rubric-nature").then(r => { if (active) { setData(r); setDrafts({}); setSimulation(null); } }).catch(e => { if (active) setError((e as Error).message); });
    return () => { active = false; };
  }, [version]);
  const rubrics: Row[] = useMemo(() => (data?.rubrics || []).filter((r: Row) => showInactive || r.active), [data, showInactive]);
  const changes = useMemo(() => (data?.rubrics || []).filter((r: Row) => drafts[r.id] && !same(drafts[r.id], pick(r)))
    .map((r: Row) => ({ category_id: r.id, ...drafts[r.id] })), [data, drafts]);
  const suggestions = (data?.rubrics || []).filter((r: Row) => r.suggestion && r.active);
  const presets: Record<string, Row> = Object.fromEntries((data?.natures || []).map((n: Row) => [n.key, n.flags]));
  const labels: Record<string, string> = Object.fromEntries((data?.natures || []).map((n: Row) => [n.key, n.label]));
  const current = (r: Row) => drafts[r.id] || pick(r);
  const edit = (r: Row, next: Draft) => { setDrafts(d => ({ ...d, [r.id]: next })); setSimulation(null); setNotice(""); };
  const applySuggestion = (r: Row) => { const { reason: _reason, ...values } = r.suggestion; edit(r, values as Draft); };

  async function simulate() {
    setBusy(true); setError(""); setNotice("");
    try { setSimulation(await api("/rubric-nature/simulate", { changes })); } catch (e) { setError((e as Error).message); } finally { setBusy(false); }
  }
  async function apply() {
    if (!simulation) return;
    setBusy(true); setError("");
    try {
      const result = await api("/rubric-nature/apply", { changes, fingerprint: simulation.fingerprint, reason: reason.trim() });
      setReason(""); setVersion(v => v + 1);
      setNotice(`Natureza aplicada a ${result.applied} rubrica(s). Os documentos e movimentos não foram alterados; as análises passam a usar a nova configuração.`);
    } catch (e) { setError((e as Error).message); } finally { setBusy(false); }
  }

  if (!data) return error ? <Notice tone="error">{error}</Notice> : <p role="status">A carregar a natureza das rubricas…</p>;
  return <div className="rn">
    <p className="rn-intro">Diga em que análises entra cada rubrica. Por exemplo, um pagamento de IVA ao Estado é uma saída de tesouraria e uma obrigação fiscal, mas não é um custo operacional.
      Nada muda nos documentos nem nos movimentos: só muda onde o valor aparece.</p>
    {error && <Notice tone="error">{error}</Notice>}
    {notice && <Notice tone="success">{notice}</Notice>}
    {!!suggestions.length && <section className="rn-card" aria-label="Sugestões">
      <h3><Lightbulb size={16} aria-hidden /> Sugestões (só aplicadas com a sua aprovação)</h3>
      <ul className="rn-suggestions">{suggestions.map((r: Row) => <li key={r.id}>
        <div><strong>{r.name}</strong> → {labels[r.suggestion.nature]}<small>{r.suggestion.reason}</small></div>
        <ActionButton type="button" onClick={() => applySuggestion(r)} disabled={drafts[r.id] && same(drafts[r.id], { ...r.suggestion })}>Usar sugestão em {r.name}</ActionButton>
      </li>)}</ul>
    </section>}
    <section className="rn-card" aria-label="Natureza das rubricas">
      <div className="rn-head">
        <h3>Natureza e análises</h3>
        <label className="checkbox"><input type="checkbox" checked={showInactive} onChange={e => setShowInactive(e.target.checked)} /> Mostrar rubricas inativas</label>
      </div>
      <div className="table-wrap"><table className="rn-grid" aria-label="Natureza e análises de cada rubrica">
        <thead><tr><th>Rubrica</th><th>Natureza</th>{FLAGS.map(f => <th key={f}>{SHORT[f]}</th>)}</tr></thead>
        <tbody>{rubrics.map((r: Row) => {
          const value = current(r), changed = !same(value, pick(r));
          return <tr key={r.id} data-changed={changed || undefined}>
            <td><strong>{r.name}</strong>{!r.active && <small>Inativa</small>}{changed && <StatusBadge tone="info" compact>Alterada (por simular)</StatusBadge>}</td>
            <td><select aria-label={`Natureza de ${r.name}`} value={value.nature}
              onChange={e => edit(r, { ...(presets[e.target.value] as Draft), nature: e.target.value })}>
              {data.natures.map((n: Row) => <option key={n.key} value={n.key}>{n.label}</option>)}</select></td>
            {FLAGS.map(f => <td key={f} className="rn-flag"><input type="checkbox" aria-label={`${r.name}: ${SHORT[f]}`} checked={value[f]}
              onChange={e => edit(r, { ...value, [f]: e.target.checked })} /></td>)}
          </tr>;
        })}</tbody></table></div>
      <p className="rn-hint">Escolher a natureza propõe as análises habituais; pode ajustar cada uma. «Por definir» mantém o comportamento anterior (entra nos custos, na tesouraria e no orçamento).
        A previsão de liquidez, os extratos, a reconciliação e o Mapa de IVA não dependem desta configuração.</p>
      <div className="rn-actions">
        <ActionButton type="button" onClick={() => { setDrafts({}); setSimulation(null); }} disabled={!changes.length || busy}>Descartar alterações</ActionButton>
        <ActionButton type="button" className="primary" onClick={() => void simulate()} disabled={!changes.length || busy}>
          Simular {changes.length} alteração(ões)</ActionButton>
      </div>
    </section>
    {simulation && <section className="rn-card" aria-label="Simulação">
      <h3><FlaskConical size={16} aria-hidden /> Simulação: {fullDate(simulation.start)} a {fullDate(simulation.end)}</h3>
      <ul className="rn-changes">{simulation.changes.map((c: Row) => <li key={c.category_id}><strong>{c.name}</strong>: {labels[c.before.nature]} → {labels[c.after.nature]}
        {FLAGS.filter(f => c.before[f] !== c.after[f]).map(f => <span key={f} className="rn-chip">{c.after[f] ? "Entra em" : "Sai de"} {SHORT[f].toLowerCase()}</span>)}</li>)}</ul>
      <div className="table-wrap"><table className="rn-grid" aria-label="Antes e depois por vista">
        <thead><tr><th>Vista</th><th>Antes</th><th>Depois</th><th>Diferença</th></tr></thead>
        <tbody>{simulation.views.map((v: Row) => <tr key={v.key}><td>{v.label}</td><td>{formatEuro(v.before_cents)}</td><td>{formatEuro(v.after_cents)}</td>
          <td className={v.difference_cents ? "rn-diff" : ""}>{v.difference_cents ? `${v.difference_cents > 0 ? "+" : "−"}${formatEuro(Math.abs(v.difference_cents))}` : "Sem alteração"}</td></tr>)}</tbody>
      </table></div>
      <h4>{simulation.affected.count} registo(s) afetado(s){simulation.affected.count > simulation.affected.rows.length ? ` (os ${simulation.affected.rows.length} mais recentes)` : ""}</h4>
      {simulation.affected.rows.length ? <div className="table-wrap"><table className="rn-grid" aria-label="Registos afetados">
        <thead><tr><th>Data</th><th>Documento ou movimento</th><th>Entidade</th><th>Origem</th><th>Rubrica</th><th>Valor</th></tr></thead>
        <tbody>{simulation.affected.rows.map((r: Row) => <tr key={r.id}><td>{fullDate(r.date)}</td><td>{r.label}</td><td>{r.entity}</td><td>{r.source}</td>
          <td>{r.categories.join(", ")}</td><td>{formatEuro(r.amount_cents)}</td></tr>)}</tbody></table></div>
        : <p className="rn-hint">Nenhum documento ou movimento nos últimos 12 meses usa estas rubricas.</p>}
      <p className="rn-preserved"><ShieldCheck size={15} aria-hidden /> Não muda: {simulation.preserved.join("; ")}.</p>
      {data.can_apply ? <div className="rn-apply">
        <label>Motivo da alteração<textarea value={reason} onChange={e => setReason(e.target.value)} rows={2} maxLength={500}
          placeholder="Ex.: separar pagamentos de IVA ao Estado dos custos operacionais" /></label>
        <ActionButton type="button" className="primary" onClick={() => void apply()} disabled={busy || reason.trim().length < 5}>Aplicar alterações</ActionButton>
      </div> : <Notice tone="info">Só um administrador pode aplicar esta alteração. Partilhe a simulação com o administrador da empresa.</Notice>}
    </section>}
  </div>;
}
