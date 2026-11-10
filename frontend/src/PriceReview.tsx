import { useEffect, useRef, useState } from "react";
import { CircleAlert, Clock, Gift, TrendingUp } from "lucide-react";
import { StatusBadge } from "./components/BusinessUI";
import { ActionButton, AppDialog, Notice, confirmAction } from "./components/InteractionUI";
import { api } from "./lib/api";
import { formatQuantity } from "./lib/quantity";
import "./PriceReview.css";

type Method = "margin" | "markup" | "increase" | "manual";
type Amount = string | null;
/**
 * Custos por artigo: `reference_cost` (sem repartir bónus) é a base do PVP sugerido e da comparação
 * com a compra anterior; `effective_cost` (com bónus, o custo em stock) só mostra o ganho do bónus.
 */
type Line = {
  id: string; code: string; name: string; revision: number; quantity: string; unit: string;
  reference_cost: Amount; effective_cost: Amount; billed_quantity: Amount; bonus_quantity: Amount; bonus_gain: Amount; bonus_gain_unit: Amount;
  last_cost: Amount; delta: Amount; delta_percent: Amount; net: Amount; gross: Amount;
  margin: Amount; effective_margin: Amount; minimum: string; default_method: "margin" | "markup"; default_value: string;
  suggested: Amount; suggested_gross: Amount; suggested_margin: Amount; suggested_effective_margin: Amount;
  erp: boolean; active: boolean; editable: boolean; pending: boolean;
};
/** erp_write: os preços dos artigos Wintouch podem ser enviados (linha de preço 1) e confirmados por leitura. */
type Review = { document: string; revision: number; lines: Line[]; cost_alert: string; can_change: boolean; erp_write?: boolean; erp_reason?: string };
type ErpOutcome = { code: string; name: string; state: "confirmed" | "failed" | "uncertain"; error: string };
type Draft = { method: Method; value: string };
type Proposal = {
  id: string; code: string; name: string; method: Method; before: Amount; before_gross: Amount; after: string; after_gross: Amount;
  difference: Amount; before_margin: Amount; after_margin: Amount; before_effective_margin: Amount; after_effective_margin: Amount;
  bonus_gain: Amount; destination: string;
};
const METHODS: Record<Method, string> = {
  margin: "Margem sobre a venda (%)", markup: "Markup sobre o custo (%)", increase: "Aumento do PVP (%)", manual: "PVP sem IVA (€)",
};
const hasBonus = (line: Line) => Number(line.bonus_quantity || 0) > 0;
type Pending = { id: string; document: string; date: string; count: number };

const money = (value: Amount, digits = 2) => value === null ? "—" : new Intl.NumberFormat("pt-PT", {
  style: "currency", currency: "EUR", maximumFractionDigits: digits,
}).format(Number(value));
const pct = (value: Amount) => value === null ? "—" : `${Number(value).toLocaleString("pt-PT", { maximumFractionDigits: 2 })} %`;
const significantlyChanged = (before: Amount, after: Amount) => before !== null && after !== null &&
  Math.abs(Number(after) / Number(before) - 1) >= 0.10;
const errorMessage = (error: unknown) => error instanceof Error ? error.message : "Não foi possível concluir a operação.";

export function PendingPriceReviews({ onOpen, onClose }: { onOpen: (id: string) => void; onClose: () => void }) {
  const [rows, setRows] = useState<Pending[] | null>(null);
  const [error, setError] = useState("");
  useEffect(() => {
    let active = true;
    api("/price-reviews").then(data => { if (active) setRows(data); }).catch(error => { if (active) setError(errorMessage(error)); });
    return () => { active = false; };
  }, []);
  return <AppDialog className="price-review-dialog" aria-label="Preços pendentes" onRequestClose={onClose}>
    <h2>Preços pendentes</h2>
    {error ? <Notice tone="error">{error}</Notice> : rows === null ? <p role="status">A carregar revisões…</p>
      : !rows.length ? <p>Sem receções com preços pendentes.</p>
      : <div className="table-wrap"><table><thead><tr><th>Receção</th><th>Data</th><th>Artigos pendentes</th><th>Ação</th></tr></thead>
        <tbody>{rows.map(row => <tr key={row.id}><td>{row.document}</td><td>{row.date?.split("-").reverse().join("/")}</td><td>{row.count}</td>
          <td><ActionButton type="button" onClick={() => onOpen(row.id)}>Rever preços</ActionButton></td></tr>)}</tbody></table></div>}
  </AppDialog>;
}

export default function PriceReview({ receiptId, onClose, onSaved }: { receiptId: string; onClose: () => void; onSaved: () => void }) {
  const [review, setReview] = useState<Review | null>(null);
  const [rounding, setRounding] = useState("net_cent");
  const [drafts, setDrafts] = useState<Record<string, Draft>>({});
  const [selected, setSelected] = useState<string[]>([]);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const [erpIssues, setErpIssues] = useState<ErpOutcome[]>([]);
  const [busy, setBusy] = useState(false);
  const [reload, setReload] = useState(0);
  const [preview, setPreview] = useState<{ key: string; rows: Proposal[]; error?: string } | null>(null);
  const retry = useRef<{ key: string; request: string } | null>(null);
  const path = `/price-reviews/${encodeURIComponent(receiptId)}`;

  useEffect(() => {
    let active = true;
    api(path).then(data => { if (active) setReview(data); }).catch(error => { if (active) setError(errorMessage(error)); });
    return () => { active = false; };
  }, [path, reload]);

  // Método e valor por omissão: os configurados para a família do artigo ou para a empresa.
  const defaultDraft = (line: Line): Draft => ({ method: line.default_method, value: line.default_value });
  const startValue = (line: Line, method: Method) => method === line.default_method ? line.default_value : method === "margin" ? line.minimum : "";
  const proposalInput = (line: Line) => {
    const draft = drafts[line.id] ?? defaultDraft(line);
    return { article_id: line.id, article_revision: line.revision, ...draft };
  };
  const pending = review?.lines.filter(line => line.pending) ?? [];
  const previewLines = pending.filter(line => drafts[line.id] || rounding !== "net_cent").map(proposalInput)
    .filter(line => line.value.trim() && Number(line.value) >= 0 && (line.method !== "margin" || Number(line.value) < 100));
  const previewKey = previewLines.length ? JSON.stringify({ receipt_revision: review?.revision, rounding, lines: previewLines }) : "";

  useEffect(() => {
    if (!previewKey) return;
    let active = true;
    const timer = window.setTimeout(() => {
      api(`${path}/preview`, JSON.parse(previewKey), { preserveDraft: true }).then(rows => {
        if (active) setPreview({ key: previewKey, rows });
      }).catch(error => { if (active) setPreview({ key: previewKey, rows: [], error: errorMessage(error) }); });
    }, 250);
    return () => { active = false; window.clearTimeout(timer); };
  }, [path, previewKey]);

  function edit(line: Line, draft: Draft) {
    setDrafts(current => ({ ...current, [line.id]: draft }));
    setError("");
  }

  async function apply(finish = false) {
    if (!review || busy || (!finish && !selected.length)) return;
    setBusy(true);
    setError("");
    setNotice("");
    setErpIssues([]);
    try {
      const lines = finish ? [] : pending.filter(line => selected.includes(line.id)).map(proposalInput);
      const toErp = finish ? 0 : pending.filter(line => selected.includes(line.id) && line.erp).length;
      const body = { receipt_revision: review.revision, rounding, lines };
      const changes: Proposal[] = lines.length ? await api(`${path}/preview`, body, { preserveDraft: true }) : [];
      const confirmed = await confirmAction({
        title: finish ? "Concluir revisão sem alterar preços?" : "Confirmar atualização de preços",
        message: finish ? "Os artigos pendentes desta receção serão marcados como revistos." : <div className="table-wrap price-review-confirmation">
          <table><thead><tr><th>Artigo</th><th>Método</th><th>PVP anterior</th><th>Novo PVP</th><th>Diferença sem IVA</th><th>Margem comercial anterior → prevista</th><th>Margem efetiva prevista</th><th>Destino</th></tr></thead>
            <tbody>{changes.map(row => <tr key={row.id}><td>{row.code} · {row.name}</td><td>{METHODS[row.method]}</td><td>{money(row.before)}<small>{money(row.before_gross)} com IVA</small></td>
              <td>{money(row.after, 6)}<small>{money(row.after_gross)} com IVA</small></td><td>{money(row.difference, 6)}</td>
              <td>{pct(row.before_margin)} → {pct(row.after_margin)}</td>
              <td>{pct(row.after_effective_margin)}{Number(row.bonus_gain || 0) > 0 && <small>Inclui bónus de {money(row.bonus_gain)}</small>}</td><td>{row.destination}</td></tr>)}</tbody></table>
          {toErp > 0 && <p className="price-review-erp-note">{toErp === 1 ? "1 preço será enviado" : `${toErp} preços serão enviados`} para a Wintouch (linha de preço 1, sem e com IVA) e confirmado(s) por leitura. A alteração fica na Wintouch e na auditoria.</p>}
        </div>,
        confirmLabel: finish ? "Concluir revisão" : toErp ? "Guardar e enviar para a Wintouch" : "Guardar preços selecionados",
      });
      if (!confirmed) return;
      const approved = { ...body, finish, lines: lines.map((line, index) => ({ ...line, confirmed_net: changes[index].after })) };
      const request = JSON.stringify(approved);
      if (retry.current?.request !== request) retry.current = { request, key: crypto.randomUUID() };
      const result = await api(`${path}/apply`, { ...approved, confirmed: true, request_key: retry.current.key }, { preserveDraft: true });
      retry.current = null;  // Um envio recusado pode ser repetido com uma nova referência.
      const outcomes: ErpOutcome[] = result.erp || [];
      const synced = outcomes.filter(item => item.state === "confirmed").length;
      setReview(await api(path));
      setSelected([]);
      setDrafts({});
      setErpIssues(outcomes.filter(item => item.state !== "confirmed"));
      setNotice(finish ? "Revisão concluída." : `Preços revistos: ${result.updated}${synced ? ` (${synced} sincronizado(s) com a Wintouch)` : ""}. Artigos pendentes: ${result.pending}.`);
      onSaved();
    } catch (error) {
      setError(errorMessage(error));
    } finally {
      setBusy(false);
    }
  }

  const increased = (line: Line) => line.delta_percent !== null && Number(line.delta_percent) > 0 && Number(line.delta_percent) >= Number(review?.cost_alert);
  const low = (line: Line) => line.margin !== null && Number(line.margin) < Number(line.minimum);
  const previewRows = preview?.key === previewKey ? preview.rows : [];

  return <AppDialog className="price-review-dialog" aria-label="Validação e Atualização de Preços de Venda" busy={busy} guardChanges={false} onRequestClose={onClose}>
    <h2>Validação e Atualização de Preços de Venda</h2>
    {error && <Notice tone="error">{error} <ActionButton type="button" onClick={() => { setError(""); setDrafts({}); setSelected([]); setReload(value => value + 1); }}>Recarregar revisão</ActionButton></Notice>}
    {notice && <Notice tone="success">{notice}</Notice>}
    {!!erpIssues.length && <Notice tone="warning">
      <strong>Preços não sincronizados com a Wintouch: ficam pendentes e pode repetir o envio.</strong>
      <ul className="price-review-erp-issues">{erpIssues.map(item => <li key={item.code}>{item.code} · {item.name}: {item.state === "failed" ? "recusado" : "não confirmado"}. {item.error}</li>)}</ul>
    </Notice>}
    {!review ? !error && <p role="status">A carregar preços da receção confirmada…</p> : <>
      <p>Receção {review.document} · confirmada</p>
      {pending.some(line => line.erp) && (review.erp_write
        ? <Notice tone="info">Os preços dos artigos Wintouch selecionados são enviados para a Wintouch (linha de preço 1, sem e com IVA) e só ficam revistos depois de a Wintouch os confirmar por leitura.</Notice>
        : <Notice tone="info">Pode simular o PVP dos artigos Wintouch nesta janela. {review.erp_reason || "Para aplicar esses preços, altere-os na ficha do artigo na Wintouch."}</Notice>)}
      {!review.can_change && pending.some(line => !line.erp && line.active) && <Notice tone="warning">O teu perfil pode simular preços. Para gravar no catálogo ZELA é necessária a permissão Alterar preços.</Notice>}
      {pending.some(line => line.editable) && <p>Assinale os artigos que pretende atualizar na coluna «Atualizar» e reveja as alterações selecionadas antes de confirmar.</p>}
      <div className="price-review-metrics">
        <div><Clock size={18} /> Pendentes <strong>{pending.length}</strong></div>
        <div><TrendingUp size={18} /> Aumentos relevantes <strong>{review.lines.filter(increased).length}</strong></div>
        <div><CircleAlert size={18} /> Margens abaixo do mínimo <strong>{review.lines.filter(low).length}</strong></div>
        <div>Selecionados <strong>{selected.length}</strong></div>
      </div>
      <label className="price-review-rounding">Arredondamento comercial <select value={rounding} onChange={event => setRounding(event.target.value)}>
        <option value="net_cent">Ao cêntimo sem IVA</option>
        {["0.05", "0.10", "0.50", "1.00"].map(step => <option key={step} value={step}>Subir para múltiplo de {money(step)} com IVA</option>)}
      </select></label>
      {preview && preview.key === previewKey && preview.error && <Notice tone="warning">{preview.error}</Notice>}
      <div className="table-wrap"><table className="price-review-grid">
        <thead><tr><th>Atualizar</th><th>Artigo</th><th>Custo de referência</th><th>PVP atual</th><th>Margens ao PVP atual</th><th>Método e valor</th><th>PVP sugerido</th><th>Estado</th></tr></thead>
        <tbody>{review.lines.map(line => {
          const draft = drafts[line.id] ?? defaultDraft(line);
          const row = previewRows.find(row => row.id === line.id);
          const useDefault = !drafts[line.id] && rounding === "net_cent";
          const net = row?.after ?? (useDefault ? line.suggested : null);
          const withVat = row?.after_gross ?? (useDefault ? line.suggested_gross : null);
          const nextMargin = row?.after_margin ?? (useDefault ? line.suggested_margin : null);
          const nextEffective = row?.after_effective_margin ?? (useDefault ? line.suggested_effective_margin : null);
          return <tr key={line.id}>
            <td><input type="checkbox" aria-label={`Atualizar ${line.code}`} disabled={!line.editable || !line.pending}
              checked={selected.includes(line.id)} onChange={event => setSelected(current => event.target.checked ? [...current, line.id] : current.filter(id => id !== line.id))} /></td>
            <td><strong>{line.code} · {line.name}</strong><small>{formatQuantity(line.quantity)} {line.unit} recebidas
              {hasBonus(line) && ` (${formatQuantity(line.billed_quantity || "0")} faturadas + ${formatQuantity(line.bonus_quantity || "0")} bónus)`}</small></td>
            <td><strong>{line.reference_cost === null ? "Indisponível" : money(line.reference_cost, 4)}</strong>
              <small>Anterior {money(line.last_cost, 4)} · {money(line.delta, 4)} ({pct(line.delta_percent)})</small>
              {hasBonus(line) && <small>Efetivo com bónus {money(line.effective_cost, 4)}</small>}
              {increased(line) && <StatusBadge tone="warning" icon={<TrendingUp size={14} />}>Aumento relevante</StatusBadge>}
              {hasBonus(line) && <StatusBadge tone="success" icon={<Gift size={14} />}>Ganho do bónus {money(line.bonus_gain)}</StatusBadge>}</td>
            <td>{money(line.net, 4)} sem IVA<small>{money(line.gross)} com IVA</small></td>
            <td>Comercial {pct(line.margin)}<small>Mínimo {pct(line.minimum)}</small>{hasBonus(line) && <small>Efetiva com bónus {pct(line.effective_margin)}</small>}
              {low(line) && <StatusBadge tone={Number(line.margin) < 0 ? "danger" : "warning"} icon={<CircleAlert size={14} />}>{Number(line.margin) < 0 ? "Margem negativa" : "Abaixo do mínimo"}</StatusBadge>}</td>
            <td><select aria-label={`Método de preço de ${line.code}`} value={draft.method} disabled={!line.pending}
              onChange={event => { const method = event.target.value as Method; edit(line, { method, value: startValue(line, method) }); }}>
              {(Object.keys(METHODS) as Method[]).map(method => <option key={method} value={method}>{METHODS[method]}{method === line.default_method ? " · configurado" : ""}</option>)}
            </select><input type="number" min="0" step="any" max={draft.method === "margin" ? 99.999999 : undefined} value={draft.value} disabled={!line.pending}
              aria-label={`Valor proposto para ${line.code}`} onChange={event => edit(line, { ...draft, value: event.target.value })} /></td>
            <td><strong>{money(net, 6)} sem IVA</strong><small>{money(withVat)} com IVA</small><small>Margem comercial prevista {pct(nextMargin)}</small>
              {hasBonus(line) && <small>Margem efetiva prevista {pct(nextEffective)}</small>}
              {significantlyChanged(line.net, net) && <StatusBadge tone="warning">Variação do PVP ≥ 10%</StatusBadge>}</td>
            <td><StatusBadge tone={line.pending ? line.erp ? "info" : "warning" : "success"}>
              {!line.pending ? "Revisto" : line.erp ? line.editable ? "Wintouch · envio disponível" : "Alterar na Wintouch"
                : line.active === false ? "Artigo inativo" : line.editable ? "Catálogo ZELA · pendente" : "Sem permissão para alterar preços"}
            </StatusBadge>{line.net === null && <small>Sem PVP</small>}{line.last_cost === null && <small>Sem histórico de compras</small>}
              {line.reference_cost === null && <small>Sem custo de referência: use o PVP manual</small>}</td>
          </tr>;
        })}</tbody>
      </table></div>
      <p className="footnote">O PVP sugerido parte sempre do custo de referência: valor líquido ÷ unidades faturadas, sem repartir o bónus. O custo efetivo inclui o bónus e serve só para mostrar a margem efetiva desta receção. O bónus é um ganho adicional e nunca baixa o preço sugerido. Todas as margens usam o mesmo PVP sem IVA.</p>
      <div className="ui-dialog-actions">
        <ActionButton type="button" onClick={onClose}>{pending.length ? "Adiar revisão" : "Fechar"}</ActionButton>
        {review.can_change && pending.length > 0 && <ActionButton type="button" onClick={() => void apply(true)}>Concluir sem alterações</ActionButton>}
        <ActionButton type="button" className="primary" disabled={!selected.length} onClick={() => void apply()}>Rever alterações selecionadas</ActionButton>
      </div>
    </>}
  </AppDialog>;
}
