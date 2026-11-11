import { useState, type ReactNode } from "react";
import { ArrowDown, ArrowUp, Calculator, CalendarDays, ChevronLeft, ChevronRight, CircleAlert, Clock, Coins, Equal, Gift, Info, Package, TrendingUp, X } from "lucide-react";
import { StatusBadge, type BusinessTone } from "./components/BusinessUI";
import { ActionButton, AppDialog, Notice, confirmAction } from "./components/InteractionUI";
import { api } from "./lib/api";
import { formatQuantity } from "./lib/quantity";
import "./ReceivingInsights.css";

/**
 * Rentabilidade e custos na receção a partir de fatura: margem sobre o PVP, variação do custo face à
 * última compra e benefício dos bónus, por linha, e o resumo «Benefícios comerciais» do documento.
 * O simulador permite guardar o PVP local mediante confirmação.
 */

type Row = Record<string, any>;
const euro = (value: number, digits = 2) => new Intl.NumberFormat("pt-PT", { style: "currency", currency: "EUR", minimumFractionDigits: 2, maximumFractionDigits: Math.max(digits, 2) }).format(value);
const percent = (value: number) => `${value.toLocaleString("pt-PT", { maximumFractionDigits: 1, minimumFractionDigits: 1 })} %`;
const detailTabs = [
  { id: "costs", label: "Cálculos e custos", icon: Calculator },
  { id: "history", label: "Últimas compras", icon: CalendarDays },
  { id: "margin", label: "Margem e venda", icon: TrendingUp },
  { id: "simulation", label: "Simulação de preço", icon: Clock },
] as const;

const day = (value?: string) => value ? value.slice(0, 10).split("-").reverse().join("/") : "—";

export type LineFigures = { net: number; units: number; cost: number };

/** PVP sem IVA do artigo: o local; no ERP, o preço sem IVA ou o com IVA descontado da taxa do ERP. */
export function salePriceNet(article: Row | undefined): number | null {
  if (!article) return null;
  if (Number(article.sale_net) > 0) return Number(article.sale_net);
  if (article.erp_price_net != null && Number(article.erp_price_net) > 0) return Number(article.erp_price_net);
  if (article.erp_price != null && article.erp_vat_rate != null && Number(article.erp_price) > 0) return Number(article.erp_price) / (1 + Number(article.erp_vat_rate) / 100);
  return null;
}

/**
 * Indicadores de uma linha, na mesma base do stock (com IVA não recuperável, o fator da valorização):
 * - custo de referência = valor líquido com descontos ÷ unidades faturadas (sem repartir bónus). É a base
 *   da margem comercial e da comparação com o custo de referência da última compra;
 * - custo efetivo = valor líquido ÷ (faturadas + bónus), o custo em stock: só para a margem efetiva.
 */
function costFactor(line: Row, settings: Row | undefined) {
  // The posting path converts to the invoice VAT rate before inventory adds VAT.
  return settings?.recoverable_vat === false ? 1 + Number(line.vatValue || 0) / 100 : 1;
}

export function lineInsight(line: Row, figures: LineFigures, article: Row | undefined, history: Row[] | undefined, settings: Row | undefined, historyState: "loading" | "error" | "ready" = "ready") {
  const factor = costFactor(line, settings);
  const calculatedCost = figures.cost * factor;
  const cost = settings && figures.units > 0 && Number.isFinite(calculatedCost) && calculatedCost >= 0 ? calculatedCost : null;
  const quantity = Number(line.qty || 0), bonus = Number(line.bonusQty || 0);
  const calculatedReference = quantity > 0 ? figures.net / quantity * factor : NaN;
  const reference = settings && Number.isFinite(calculatedReference) && calculatedReference >= 0 ? calculatedReference : null;
  const pvp = salePriceNet(article);
  const family = article?.family;
  const minimum = Number(settings?.margin_by_family?.[family] ?? settings?.margin_minimum ?? 30);
  // O mesmo PVP sem IVA nas duas margens: comercial (referência) e efetiva da receção (com bónus).
  const margin = pvp && reference !== null ? { percent: (pvp - reference) / pvp * 100, unit: pvp - reference } : null;
  const effectiveMargin = pvp && cost !== null ? { percent: (pvp - cost) / pvp * 100, unit: pvp - cost } : null;
  const marginTone: BusinessTone = !margin ? "neutral" : margin.percent < 0 ? "danger" : margin.percent < minimum ? "warning" : "success";
  const last = history?.[0];
  // Compara-se referência com referência: nunca um custo com bónus com outro sem bónus.
  const lastCost = last && last.reference_cost != null ? Number(last.reference_cost) : null;
  const threshold = Number(settings?.cost_alert_percent ?? 5);
  const change = reference === null || lastCost === null || !Number.isFinite(lastCost) || lastCost < 0 ? null : {
    diff: reference - lastCost,
    pct: lastCost > 0 ? (reference - lastCost) / lastCost * 100 : null,
  };
  const alert = change?.pct != null && change.pct > 0 && change.pct >= threshold;
  const unitNet = quantity ? figures.net / quantity : 0;
  const bonusValue = bonus * unitNet;
  return {
    cost, reference, pvp, minimum, family, margin, effectiveMargin, marginTone, last, lastCost, threshold, change, alert, factor, historyState, history: history || [],
    bonus: bonus > 0 ? { units: bonus, value: bonusValue, pct: figures.net + bonusValue ? bonusValue / (figures.net + bonusValue) * 100 : 0, unitNet } : null,
    noCostReason: !settings ? "base de custo indisponível" : "custo indisponível",
  };
}

/** Faixa compacta na linha (margem, variação do custo, bónus) e detalhe expansível com o histórico. */
export function LineInsights({ insight, unit, articleName, newArticle, article, newArticleVat, canChangePrice, onPriceSaved, onOpen, detail }: {
  insight: ReturnType<typeof lineInsight>;
  unit: string;
  articleName: string;
  newArticle: boolean;
  article?: Row;
  newArticleVat?: string;
  canChangePrice: boolean;
  onPriceSaved: (id: string, saleNet: string, revision: number) => void;
  onOpen: () => void;
  detail?: {
    fields: ReactNode;
    figures: LineFigures;
    unitPrice: number;
    quantity: number;
    bonus: number;
    lineIndex: number;
    onClose: () => void;
    onPrevious?: () => void;
    onNext?: () => void;
  };
}) {
  const [tab, setTab] = useState<(typeof detailTabs)[number]["id"]>("costs");
  const erp = article?.origin === "ERP" || !!article?.erp_id;
  const saleVat = erp ? article?.erp_vat_rate : article?.vat_rate ?? (newArticle ? newArticleVat : undefined);
  const vat = saleVat != null && saleVat !== "" && Number.isFinite(Number(saleVat)) ? Number(saleVat) : null;
  const gross = article?.erp_price_basis === "Com IVA" && article.erp_price != null ? Number(article.erp_price)
    : insight.pvp !== null && vat !== null ? insight.pvp * (1 + vat / 100) : null;
  const { margin, change } = insight;
  const marginText = margin ? `Margem comercial ${percent(margin.percent)} · ${euro(margin.unit)}/un.` : insight.pvp ? `Margem indisponível: ${insight.noCostReason}` : "Margem indisponível: sem PVP";
  const delta = change?.pct ?? change?.diff ?? 0;
  const ChangeIcon = delta > 0 ? ArrowUp : delta < 0 ? ArrowDown : Equal;
  const changeText = newArticle ? "Artigo novo: sem histórico de custo"
    : insight.historyState === "loading" ? "A consultar últimas compras…"
    : insight.historyState === "error" ? "Histórico de compras indisponível"
    : !insight.last ? "Sem compras anteriores" : insight.lastCost === null ? "Última compra sem unidades faturadas" : !change ? "Custo atual indisponível"
    : change.pct === null ? "Último custo 0 €: percentagem indisponível"
    : `${change.pct > 0 ? "+" : ""}${percent(change.pct)} vs último (${euro(insight.lastCost as number, 4)})`;
  const changeTone: BusinessTone = insight.alert ? "danger" : delta < 0 ? "success" : "neutral";
  const indicators = <>
    <div className="receiving-insights-chips">
      <StatusBadge tone={insight.marginTone} icon={<TrendingUp size={14} />} title={margin ? `Mínimo ${percent(insight.minimum)}${insight.family ? ` (família ${insight.family})` : ""}` : undefined}>{marginText}</StatusBadge>
      <StatusBadge tone={changeTone} icon={insight.alert ? <CircleAlert size={14} /> : <ChangeIcon size={14} />}>{changeText}</StatusBadge>
      {insight.bonus && <StatusBadge tone="info" icon={<Gift size={14} />}>Bónus {formatQuantity(insight.bonus.units)} {unit} · {euro(insight.bonus.value)} ({percent(insight.bonus.pct)})</StatusBadge>}
    </div>
    {insight.alert && change?.pct != null && <p className="receiving-insights-alert" role="status"><CircleAlert size={15} aria-hidden="true" />
      Aumento do custo de referência · limite {percent(insight.threshold)}: último {euro(insight.lastCost as number, 4)} · atual {euro(insight.reference as number, 4)} · {percent(change.pct)}.</p>}
  </>;

  if (!detail) return <div className="receiving-insights" aria-label={`Rentabilidade de ${articleName}`}>
    {indicators}
    <ActionButton type="button" className="receiving-open-detail" onClick={onOpen}>Ver cálculos e últimas compras</ActionButton>
  </div>;

  const effective = insight.cost === null ? "—" : euro(insight.cost, 4);
  const referenceText = insight.reference === null ? "—" : euro(insight.reference, 4);
  const selling = <>
    <div><dt>PVP sem IVA</dt><dd>{insight.pvp ? euro(insight.pvp, 4) : "Sem PVP na ficha"}</dd></div>
    <div><dt>PVP com IVA{vat !== null ? ` (${percent(vat)})` : ""}</dt><dd>{gross === null ? "—" : euro(gross)}</dd></div>
  </>;
  const profitability = <>
    <div><dt>Margem comercial</dt><dd>{margin ? <><StatusBadge tone={insight.marginTone}>{percent(margin.percent)}</StatusBadge> {euro(margin.unit, 4)}/un.</> : "Indisponível"}</dd>
      <small>(PVP − custo de referência) ÷ PVP · mínimo {percent(insight.minimum)}{insight.family ? ` (${insight.family})` : ""}</small></div>
    {insight.bonus && <div><dt>Margem efetiva da receção</dt><dd>{insight.effectiveMargin ? <>{percent(insight.effectiveMargin.percent)} {euro(insight.effectiveMargin.unit, 4)}/un.</> : "Indisponível"}</dd>
      <small>(PVP − custo efetivo) ÷ PVP, com o bónus{margin && insight.effectiveMargin ? ` · +${percent(insight.effectiveMargin.percent - margin.percent).replace(" %", " p.p.")} pelo bónus` : ""}</small></div>}
  </>;
  const comparison = <section className="receiving-detail-card">
    <h3><Coins size={20} aria-hidden="true" /> Comparação de custo</h3>
    <dl className="receiving-cost-comparison">
      <div><dt>Último custo de referência</dt><dd>{insight.lastCost === null ? "—" : euro(insight.lastCost, 4)}</dd></div>
      <div><dt>Custo de referência atual</dt><dd>{referenceText}</dd></div>
      <div><dt>Diferença</dt><dd>{change ? `${change.diff > 0 ? "+" : ""}${euro(change.diff, 4)}${change.pct === null ? "" : ` (${percent(change.pct)})`}` : "—"}</dd></div>
      <div><dt>Limite de alerta</dt><dd>{percent(insight.threshold)}</dd></div>
    </dl>
  </section>;

  return <AppDialog className="receiving-detail-dialog" aria-label="Detalhe do artigo na receção" guardChanges={false} onRequestClose={detail.onClose}>
    <header className="receiving-detail-header">
      <Package size={32} aria-hidden="true" />
      <div><h2>Detalhe do artigo na receção</h2><p>{article?.code ? `${article.code} – ` : ""}{articleName}</p></div>
      <div className="receiving-detail-navigation">
        <ActionButton type="button" aria-label="Artigo anterior" disabled={!detail.onPrevious} onClick={detail.onPrevious}><ChevronLeft size={20} aria-hidden="true" /></ActionButton>
        <ActionButton type="button" aria-label="Artigo seguinte" disabled={!detail.onNext} onClick={detail.onNext}><ChevronRight size={20} aria-hidden="true" /></ActionButton>
        <ActionButton type="button" aria-label="Fechar detalhe" onClick={detail.onClose}><X size={20} aria-hidden="true" /></ActionButton>
      </div>
    </header>
    <div className="receiving-detail-top">
      <div className="receiving-detail-inputs"><div className="receiving-detail-fields">{detail.fields}</div>{indicators}</div>
      <section className="receiving-detail-net" aria-label="Valor líquido da linha">
        <h3>Valor líquido da linha</h3><strong>{euro(detail.figures.net)}</strong>
        <p>Custo de referência <b>{referenceText}</b></p>
        {insight.bonus && <p>Custo efetivo com bónus <b>{effective}</b></p>}
        <small>{formatQuantity(detail.figures.units)} {unit} recebidas{insight.factor !== 1 ? " · inclui IVA não recuperável" : ""}</small>
      </section>
    </div>
    <nav className="receiving-detail-tabs" aria-label="Secções do detalhe">
      {detailTabs.map(({ id, label, icon: Icon }) => <ActionButton key={id} type="button" aria-pressed={tab === id}
        className={tab === id ? "primary" : ""} onClick={() => setTab(id)}><Icon size={18} aria-hidden="true" />{label}</ActionButton>)}
    </nav>
    <div className={tab === "costs" ? "receiving-detail-panels" : "receiving-detail-panels single"}>
      {tab === "costs" && <section className="receiving-detail-card">
        <h3><Calculator size={20} aria-hidden="true" /> Cálculos da receção</h3>
        <dl className="receiving-detail-calculations">{selling}
          <div><dt>Custo de referência</dt><dd>{referenceText}</dd><small>Valor líquido com descontos ÷ {formatQuantity(detail.quantity)} compradas, sem repartir o bónus{insight.factor !== 1 ? ", com IVA não recuperável" : ""}</small></div>
          <div><dt>Custo efetivo desta receção</dt><dd>{effective}</dd><small>Valor líquido com descontos ÷ ({formatQuantity(detail.quantity)} compradas + {formatQuantity(detail.bonus)} bónus){insight.factor !== 1 ? ", com IVA não recuperável" : ""}</small></div>
          {profitability}
          {insight.bonus && <div><dt>Bónus</dt><dd>{formatQuantity(insight.bonus.units)} {unit} × {euro(insight.bonus.unitNet, 4)} = {euro(insight.bonus.value)}</dd><small>Benefício equivalente de {percent(insight.bonus.pct)}</small></div>}
        </dl>
      </section>}
      <div className="receiving-detail-stack">
        {tab === "history" && <>{comparison}<section className="receiving-detail-card"><h3>Últimas compras</h3>
          {insight.history.length ? <HistoryTable rows={insight.history} /> : <p>{changeText}</p>}
        </section></>}
        {tab === "margin" && <section className="receiving-detail-card"><h3>Margem e venda</h3><dl className="receiving-detail-calculations">{selling}{profitability}</dl></section>}
        <div hidden={tab !== "costs" && tab !== "simulation"}>
          <PriceSimulation key={detail.lineIndex} cost={detail.unitPrice} vat={vat} articleName={articleName} currentPrice={insight.pvp}
            editableArticle={canChangePrice && !erp && Number.isInteger(article?.revision) ? article : undefined} onPriceSaved={onPriceSaved} />
        </div>
      </div>
    </div>
    <footer className="receiving-detail-footer"><p><Info size={18} aria-hidden="true" /> A margem comercial e a comparação usam o custo de referência, sem repartir o bónus. O custo e a margem efetivos incluem o bónus, que é um ganho adicional e nunca baixa o preço. A simulação usa o preço unitário da fatura antes dos descontos.</p>
      <ActionButton type="button" onClick={detail.onClose}>Fechar</ActionButton></footer>
  </AppDialog>;
}

function PriceSimulation({ cost, vat, articleName, currentPrice, editableArticle, onPriceSaved }: {
  cost: number;
  vat: number | null;
  articleName: string;
  currentPrice: number | null;
  editableArticle?: Row;
  onPriceSaved: (id: string, saleNet: string, revision: number) => void;
}) {
  const [markup, setMarkup] = useState<string | null>(null);
  const [saving, setSaving] = useState(false);
  const [feedback, setFeedback] = useState<{ message: string; error: boolean } | null>(null);
  const validCost = Number.isFinite(cost) && cost > 0;
  const currentMarkup = validCost && currentPrice !== null ? ((currentPrice - cost) / cost * 100).toFixed(2) : "";
  const rate = markup === null || markup === "" ? NaN : Number(markup);
  const net = validCost && markup === null ? currentPrice
    : validCost && Number.isFinite(rate) && rate > -100 ? Math.round(cost * (1 + rate / 100) * 100) / 100 : null;
  const price = net !== null && net > 0 && net <= 1e9 ? net : null;
  const gross = price !== null && vat !== null ? price * (1 + vat / 100) : null;

  async function updatePrice() {
    if (!editableArticle || markup === null || price === null || gross === null || saving) return;
    setSaving(true);
    setFeedback(null);
    try {
      const confirmed = await confirmAction({
        title: "Atualizar preço de venda?",
        message: `${articleName}: ${currentPrice === null ? "sem PVP" : euro(currentPrice, 4)} → ${euro(price)} sem IVA (${euro(gross)} com IVA). A alteração é imediata.`,
        confirmLabel: "Guardar preço de venda",
      });
      if (!confirmed) return;
      const saleNet = price.toFixed(2);
      const { id, revision } = editableArticle;
      await api(`/stock/articles/${encodeURIComponent(id)}/price`, { sale_net: saleNet, revision }, { preserveDraft: true });
      onPriceSaved(id, saleNet, revision);
      setMarkup(null);
      setFeedback({ message: "Preço de venda atualizado.", error: false });
    } catch (error) {
      setFeedback({ message: error instanceof Error ? error.message : "Não foi possível guardar o preço.", error: true });
    } finally {
      setSaving(false);
    }
  }

  return <section className="receiving-price-simulation" aria-label="Simulação de preço de venda">
    <h4>Simular preço de venda</h4>
    <label>Margem sobre o custo (%)
      <input type="number" min="-100" step="any" value={markup ?? currentMarkup} disabled={saving || !validCost}
        onChange={event => { setMarkup(event.target.value); setFeedback(null); }} />
    </label>
    <small>{validCost ? `Base: ${euro(cost, 4)} por unidade, antes de descontos e bónus.` : "A simulação requer um preço unitário superior a zero."}</small>
    <dl>
      <div><dt>Proposta sem IVA</dt><dd>{price === null ? "—" : euro(price)}</dd></div>
      <div><dt>Proposta com IVA</dt><dd>{gross === null ? "—" : euro(gross)}</dd></div>
    </dl>
    {editableArticle && <ActionButton type="button" disabled={saving || markup === null || price === null || gross === null || price === currentPrice}
      onClick={updatePrice}>Atualizar preço de venda</ActionButton>}
    {feedback && <Notice tone={feedback.error ? "error" : "success"}>{feedback.message}</Notice>}
  </section>;
}

function HistoryTable({ rows }: { rows: Row[] }) {
  if (!rows.length) return null;
  return <div className="table-wrap"><table aria-label="Últimas compras do artigo">
    <thead><tr><th>Data</th><th>Fornecedor</th><th>Documento</th><th className="numeric">Quantidade</th><th className="numeric">Custo de referência</th><th className="numeric">Custo efetivo</th></tr></thead>
    <tbody>{rows.map((row, i) => <tr key={i}><td>{day(row.date)}</td><td>{row.supplier_name || "—"}</td><td>{row.document || "—"}</td>
      <td className="numeric">{formatQuantity(row.quantity)}</td><td className="numeric">{row.reference_cost == null ? "—" : euro(Number(row.reference_cost), 4)}</td>
      <td className="numeric">{euro(Number(row.unit_cost), 4)}</td></tr>)}</tbody>
  </table></div>;
}

/**
 * Benefícios comerciais do documento (informativo): descontos de linha e global, bónus e o benefício
 * equivalente. Os bónus valem-se ao preço líquido da linha (já com descontos), por isso nada é contado
 * duas vezes; o total da fatura e o IVA não mudam.
 */
export function CommercialBenefits({ lines, global, figures, settings }: { lines: Row[]; global: number; figures: (line: Row) => LineFigures; settings?: Row }) {
  let lineDiscounts = 0, globalDiscount = 0, bonusUnits = 0, bonusValue = 0, gross = 0, net = 0, bonusLines = 0;
  const stock = new Map<string, { net: number; units: number }>();
  for (const line of lines.filter(l => l.include)) {
    const quantity = Number(line.qty || 0), price = Number(line.priceValue || 0), own = Number(line.discountValue || 0);
    const values = figures(line);
    const list = quantity * price;
    lineDiscounts += list * own / 100;
    globalDiscount += list * (1 - own / 100) * global / 100;
    const bonus = Number(line.bonusQty || 0);
    if (!line.service && bonus > 0) {
      bonusUnits += bonus; bonusLines += 1;
      bonusValue += quantity ? bonus * values.net / quantity : 0;
    }
    net += values.net;
    if (!line.service && settings && line.articleKey) {
      const key = line.articleKey;
      const current = stock.get(key) || { net: 0, units: 0 };
      stock.set(key, { net: current.net + values.net * costFactor(line, settings), units: current.units + values.units });
    }
  }
  gross = net + lineDiscounts + globalDiscount + bonusValue;
  const benefits = lineDiscounts + globalDiscount + bonusValue;
  const single = stock.size === 1 ? [...stock.values()][0] : null;
  return <section className="receiving-benefits" aria-label="Benefícios comerciais">
    <h3><Gift size={18} aria-hidden="true" />Benefícios comerciais</h3>
    <dl>
      <div><dt>Descontos nas linhas</dt><dd>{euro(lineDiscounts)}</dd></div>
      <div><dt>Desconto global</dt><dd>{euro(globalDiscount)}{global ? <small>{percent(global)} sobre o valor das linhas de mercadoria</small> : null}</dd></div>
      <div><dt>Bónus</dt><dd>{formatQuantity(bonusUnits)} unidade(s){bonusLines ? <small>em {bonusLines} linha(s)</small> : null}</dd></div>
      <div><dt>Valor comercial dos bónus</dt><dd>{euro(bonusValue)}<small>Unidades oferecidas ao preço líquido da linha</small></dd></div>
      <div><dt>Benefício comercial equivalente</dt><dd>{gross ? percent(benefits / gross * 100) : "—"}<small>{euro(benefits)} sobre {euro(gross)} (valor sem benefícios)</small></dd></div>
      {single && single.units > 0 && <div><dt>Custo efetivo médio</dt><dd>{euro(single.net / single.units, 4)}<small>Custo de aquisição ÷ unidades recebidas do artigo</small></dd></div>}
    </dl>
    <p className="footnote">Informativo: os bónus e descontos já estão no valor líquido das linhas. O total da fatura, o IVA e o custo em stock não mudam.</p>
  </section>;
}
