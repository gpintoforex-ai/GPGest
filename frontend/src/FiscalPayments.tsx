import { useEffect, useMemo, useState } from "react";
import { Landmark, ShieldCheck } from "lucide-react";
import { StatusBadge } from "./components/BusinessUI";
import { ActionButton, Notice } from "./components/InteractionUI";
import ReasonField, { RECLASSIFY_REASONS, UNDO_REASONS } from "./ReasonField";
import { api } from "./lib/api";
import { formatEuro } from "./utils/format";

/**
 * Pagamentos ao Estado (IVA) classificados noutras rubricas, por exemplo em «Impostos» junto com a TSU.
 * Propõe os candidatos pela descrição, cria a rubrica fiscal e reclassifica a rubrica de análise com
 * pré-visualização e motivo (administrador). O registo bancário ou de caixa nunca muda; a reclassificação
 * pode ser anulada e a regra bancária para os pagamentos seguintes só é criada a pedido.
 */

type Row = Record<string, any>;
const fullDate = (iso?: string | null) => iso ? iso.slice(0, 10).split("-").reverse().join("/") : "—";
const strength = (value: number) => value >= 85 ? { tone: "success" as const, label: "Indício forte" }
  : value >= 50 ? { tone: "warning" as const, label: "Indício médio" } : { tone: "neutral" as const, label: "Indício fraco" };

export default function FiscalPayments({ onRubricCreated, review, onReviewDone }: {
  onRubricCreated: () => void; review?: Row | null; onReviewDone?: () => void;
}) {
  const [data, setData] = useState<Row | null>(null), [version, setVersion] = useState(0);
  const [selected, setSelected] = useState<string[]>([]), [target, setTarget] = useState("");
  const [preview, setPreview] = useState<Row | null>(null), [reason, setReason] = useState("");
  const [undo, setUndo] = useState<{ id: string; reason: string } | null>(null);
  const [rule, setRule] = useState<{ id: string; pattern: string } | null>(null);
  const [error, setError] = useState(""), [notice, setNotice] = useState(""), [busy, setBusy] = useState(false);
  useEffect(() => {
    let active = true;
    api("/rubric-nature/fiscal-payments").then(r => {
      if (!active) return;
      setData(r); setSelected([]); setPreview(null);
      setTarget(t => r.fiscal_rubrics.some((x: Row) => x.id === t) ? t : r.fiscal_rubrics[0]?.id || "");
    }).catch(e => { if (active) setError((e as Error).message); });
    return () => { active = false; };
  }, [version]);
  // Pedido de reclassificação de um gestor em revisão: carrega a seleção e pré-visualiza de novo.
  useEffect(() => {
    if (!review) return;
    setSelected(review.payload.items); setTarget(review.payload.category_id); setReason("");
    void run(async () => { setPreview(await api("/rubric-nature/reclassify/preview", { items: review.payload.items, category_id: review.payload.category_id })); });
  }, [review]);
  const candidates: Row[] = data?.candidates || [];
  const strong = useMemo(() => candidates.filter(c => c.confidence >= 85).map(c => c.id), [candidates]);
  const toggle = (id: string, on: boolean) => { setSelected(s => on ? [...s, id] : s.filter(x => x !== id)); setPreview(null); };

  async function run(action: () => Promise<void>) {
    setBusy(true); setError(""); setNotice("");
    try { await action(); } catch (e) { setError((e as Error).message); } finally { setBusy(false); }
  }
  const createRubric = () => run(async () => {
    const created = await api("/rubric-nature/fiscal-rubric", { name: "IVA" });
    setNotice(`Rubrica «${created.name}» criada como obrigação fiscal (fora dos custos operacionais).`);
    setTarget(created.id); setVersion(v => v + 1); onRubricCreated();
  });
  const showPreview = () => run(async () => { setPreview(await api("/rubric-nature/reclassify/preview", { items: selected, category_id: target })); });
  const apply = () => run(async () => {
    const result = await api("/rubric-nature/reclassify", { items: selected, category_id: target, fingerprint: preview!.fingerprint, reason: reason.trim(),
      ...(review ? { request_id: review.id } : {}) });
    setReason(""); setVersion(v => v + 1);
    setNotice(`${result.applied} pagamento(s) reclassificado(s)${review ? ` (pedido de ${review.requested_by} aprovado)` : ""}. Os registos bancários e de caixa não foram alterados; pode anular no histórico.`);
    if (review) onReviewDone?.();
  });
  const ask = () => run(async () => {
    await api("/rubric-nature/requests", { kind: "reclassification", items: selected, category_id: target, reason: reason.trim() });
    setReason(""); setPreview(null); setSelected([]); onReviewDone?.();
    setNotice("Pedido enviado ao administrador. Nada muda até ser aprovado.");
  });
  const confirmUndo = () => run(async () => {
    await api(`/rubric-nature/reclassifications/${undo!.id}/undo`, { reason: undo!.reason.trim() });
    setUndo(null); setVersion(v => v + 1); setNotice("Reclassificação anulada: o pagamento volta à rubrica anterior nas análises.");
  });
  const saveRule = (row: Row) => run(async () => {
    await api("/bank-rules", { name: `${row.to_name}: ${rule!.pattern}`.slice(0, 100), pattern: rule!.pattern, direction: -1, kind: "payment",
      category_id: row.rule.category_id, counts_as_cost: row.rule.counts_as_cost });
    setRule(null); setVersion(v => v + 1);
    setNotice("Regra bancária criada: na reconciliação, os pagamentos seguintes com este texto são propostos já nesta rubrica (só proposta, confirmada por si).");
  });

  if (!data) return error ? <Notice tone="error">{error}</Notice> : <p role="status">A procurar pagamentos ao Estado…</p>;
  const history: Row[] = data.history;
  return <section className="rn-card" aria-label="Pagamentos ao Estado">
    <h3><Landmark size={16} aria-hidden /> Pagamentos ao Estado noutras rubricas</h3>
    <p className="rn-hint">Pagamentos de {fullDate(data.start)} a {fullDate(data.end)} cuja descrição parece IVA ao Estado e que estão numa rubrica fora das obrigações fiscais
      (por exemplo, em «Impostos» junto com a TSU). Reclassificar muda só a rubrica usada nas análises: o registo no banco ou na caixa fica como está e pode anular.</p>
    {error && <Notice tone="error">{error}</Notice>}
    {notice && <Notice tone="success">{notice}</Notice>}
    {!data.fiscal_rubrics.length && <div className="rn-create">
      <span>Não existe nenhuma rubrica de obrigação fiscal para onde levar estes pagamentos.</span>
      {data.can_apply ? <ActionButton type="button" className="primary" onClick={() => void createRubric()} disabled={busy}>Criar rubrica IVA (obrigação fiscal)</ActionButton>
        : <span className="rn-hint">Peça a um administrador para criar a rubrica «IVA».</span>}
    </div>}
    {candidates.length ? <>
      <div className="rn-head">
        <h4>{candidates.length} pagamento(s) a confirmar</h4>
        {!!strong.length && <ActionButton type="button" onClick={() => { setSelected(strong); setPreview(null); }}>Selecionar os de indício forte ({strong.length})</ActionButton>}
      </div>
      <div className="table-wrap"><table className="rn-grid" aria-label="Pagamentos que parecem IVA ao Estado">
        <thead><tr><th><span className="sr-only">Selecionar</span></th><th>Data</th><th>Descrição</th><th>Origem</th><th>Rubrica atual</th><th>Valor</th><th>Indício</th></tr></thead>
        <tbody>{candidates.map(c => { const s = strength(c.confidence); return <tr key={c.id} data-changed={selected.includes(c.id) || undefined}>
          <td><input type="checkbox" aria-label={`Selecionar ${c.description} de ${fullDate(c.date)}`} checked={selected.includes(c.id)} onChange={e => toggle(c.id, e.target.checked)} /></td>
          <td>{fullDate(c.date)}</td><td>{c.description}</td><td>{c.origin}</td><td>{c.category_name}</td><td className="numeric">{formatEuro(c.amount_cents)}</td>
          <td><StatusBadge tone={s.tone} compact>{s.label}</StatusBadge><small>{c.reason}</small></td>
        </tr>; })}</tbody></table></div>
      <div className="rn-actions">
        {!!data.fiscal_rubrics.length && <label className="rn-target">Levar para a rubrica
          <select value={target} onChange={e => { setTarget(e.target.value); setPreview(null); }}>
            {data.fiscal_rubrics.map((r: Row) => <option key={r.id} value={r.id}>{r.name}</option>)}</select></label>}
        <ActionButton type="button" className="primary" onClick={() => void showPreview()} disabled={busy || !selected.length || !target}>
          Pré-visualizar {selected.length} reclassificação(ões)</ActionButton>
      </div>
    </> : <p className="rn-hint">Não há pagamentos com descrição de IVA ao Estado fora das rubricas fiscais.</p>}
    {preview && <div className="rn-preview" role="region" aria-label="Pré-visualização da reclassificação">
      <h4>Reclassificar {preview.rows.length} pagamento(s) ({formatEuro(preview.total_cents)}) para «{preview.target}»: {fullDate(preview.start)} a {fullDate(preview.end)}</h4>
      <div className="table-wrap"><table className="rn-grid" aria-label="Antes e depois da reclassificação">
        <thead><tr><th>Vista</th><th>Antes</th><th>Depois</th><th>Diferença</th></tr></thead>
        <tbody>{preview.views.map((v: Row) => <tr key={v.key}><td>{v.label}</td><td>{formatEuro(v.before_cents)}</td><td>{formatEuro(v.after_cents)}</td>
          <td className={v.difference_cents ? "rn-diff" : ""}>{v.difference_cents ? `${v.difference_cents > 0 ? "+" : "−"}${formatEuro(Math.abs(v.difference_cents))}` : "Sem alteração"}</td></tr>)}</tbody>
      </table></div>
      <p className="rn-preserved"><ShieldCheck size={15} aria-hidden /> Não muda: {preview.preserved.join("; ")}.</p>
      {review && <Notice tone="info">A rever o pedido de {review.requested_by} («{review.reason}»). Ao reclassificar, o pedido fica aprovado.</Notice>}
      <div className="rn-apply">
        <ReasonField label={data.can_apply ? "Motivo da reclassificação" : "Motivo do pedido"} value={reason} onChange={setReason} options={RECLASSIFY_REASONS}
          placeholder="Ex.: pagamentos de IVA registados em Impostos, junto com a TSU" />
        {data.can_apply
          ? <ActionButton type="button" className="primary" onClick={() => void apply()} disabled={busy || reason.trim().length < 5}>{review ? "Aprovar e reclassificar" : "Reclassificar"}</ActionButton>
          : <ActionButton type="button" className="primary" onClick={() => void ask()} disabled={busy || reason.trim().length < 5}>Pedir aprovação ao administrador</ActionButton>}
      </div>
      {!data.can_apply && <p className="rn-hint">Só um administrador reclassifica. O pedido guarda esta pré-visualização; o administrador volta a pré-visualizar antes de aprovar.</p>}
    </div>}
    {!!history.length && <details className="rn-history">
      <summary>Histórico de reclassificações ({history.filter(h => !h.undone_at).length} ativa(s))</summary>
      <div className="table-wrap"><table className="rn-grid" aria-label="Histórico de reclassificações">
        <thead><tr><th>Data</th><th>Descrição</th><th>Valor</th><th>Rubrica</th><th>Motivo</th><th>Estado</th></tr></thead>
        <tbody>{history.map(h => <tr key={h.id}>
          <td>{fullDate(h.date)}</td><td>{h.description}<small>{h.origin}</small></td><td className="numeric">{formatEuro(h.amount_cents)}</td>
          <td>{h.from_name} → {h.to_name}</td><td>{h.reason}<small>{h.user}, {fullDate(h.created_at)}</small></td>
          <td>{h.undone_at ? <StatusBadge tone="neutral" compact>Anulada {fullDate(h.undone_at)}</StatusBadge> : <div className="rn-row-actions">
            {data.can_apply && (undo && undo.id === h.id ? <div className="rn-inline">
              <ReasonField label="Motivo da anulação" value={undo.reason} onChange={value => setUndo({ id: h.id, reason: value })} options={UNDO_REASONS} />
              <ActionButton type="button" onClick={() => void confirmUndo()} disabled={busy || undo.reason.trim().length < 5}>Confirmar anulação</ActionButton>
              <ActionButton type="button" onClick={() => setUndo(null)}>Cancelar</ActionButton>
            </div> : <ActionButton type="button" onClick={() => setUndo({ id: h.id, reason: "" })}>Anular</ActionButton>)}
            {h.rule && (h.rule.exists ? <small>Já existe regra bancária para «{h.rule.pattern}».</small>
              : rule && rule.id === h.id ? <div className="rn-inline">
                <label>Texto na descrição do extrato<input value={rule.pattern} onChange={e => setRule({ id: h.id, pattern: e.target.value })} maxLength={200} /></label>
                <ActionButton type="button" onClick={() => void saveRule(h)} disabled={busy || rule.pattern.trim().length < 3}>Guardar regra</ActionButton>
                <ActionButton type="button" onClick={() => setRule(null)}>Cancelar</ActionButton>
              </div> : <ActionButton type="button" onClick={() => setRule({ id: h.id, pattern: h.rule.pattern })}>Criar regra para os próximos</ActionButton>)}
          </div>}</td>
        </tr>)}</tbody></table></div>
    </details>}
  </section>;
}
