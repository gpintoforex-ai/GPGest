import { RecordWorkspace } from "./components/RecordWorkspace";
import { RecordGroup, RecordIndicator } from "./components/RecordSheet";
import { User, Phone, History, Truck, CircleAlert, ShoppingCart, Save } from "lucide-react";
import { ProcessSteps } from "./components/OverviewUI";
import { formatQuantity } from "./lib/quantity";
import { useRecordHandoff } from "./components/useRecordHandoff";
import { useEffect, useId, useRef, useState } from "react";
import { Plus, RefreshCw, Package, Mail, Send, FileText, Pencil, Calculator } from "lucide-react";
import { api } from "./lib/api";
import { Field } from "./components/Form";
import {
  ActionButton,
  ActionLink,
  AppDialog,
  ManagedForm,
  Notice,
  confirmAction,
} from "./components/InteractionUI";
import {
  ModuleTabs,
  PageHeader,
  LoadingState,
  useWorkspaceTab,
  useLocationSearch,
  updateLocationParams,
} from "./components/WorkspaceUI";
import { DataTable } from "./components/DataTable";
import ArticleSearch from "./components/ArticleSearch";
import SupplierPicker from "./components/SupplierPicker";
import RecordDialog from "./tables/RecordDialog";
import type { TableInfo } from "./tables/types";
import { StatusBadge } from "./components/BusinessUI";
import "./CustomerOrders.css";
import SupplyCenter from "./SupplyCenter";
import OrderAnalytics from "./OrderAnalytics";
import {
  ERPCatalogPicker,
  OrderCyclePanel,
  StockAvailability,
} from "./OrderCycle";

type Row = Record<string, any>;
const tabs = [
  ["centro", "Visão geral"],
  ["pedidos", "Pedidos de clientes"],
  ["planeamento", "Planeamento de compras"],
  ["rececoes", "Receção e conferência"],
  ["fornecedores", "Condições dos fornecedores"],
  ["historico", "Histórico"],
  ["analise", "Indicadores"],
];
const qty = (value: unknown) => formatQuantity(value || 0);
const money = (value: number, currency = "EUR") =>
  new Intl.NumberFormat("pt-PT", { style: "currency", currency }).format(
    (value || 0) / 100,
  );
const day = (value: string) =>
  value
    ? new Date(
        value.length === 10 ? value + "T12:00:00" : value,
      ).toLocaleDateString("pt-PT")
    : "—";
/** Hoje em Lisboa, AAAA-MM-DD. */
const todayLisbon = () =>
  new Intl.DateTimeFormat("en-CA", { timeZone: "Europe/Lisbon", year: "numeric", month: "2-digit", day: "2-digit" }).format(new Date());
const daysBetween = (from: string, to: string) =>
  Math.round((Date.parse(to.slice(0, 10) + "T12:00:00Z") - Date.parse(from.slice(0, 10) + "T12:00:00Z")) / 86400000);
/** Prazo de entrega ao cliente: dias em atraso (vermelho), hoje (âmbar) ou dias que faltam. */
function DueBadge({ order }: { order: Row }) {
  if (order.closed) return null;
  if (!order.due_date)
    return <StatusBadge>Sem data de entrega · pedido há {daysBetween(order.requested_at, todayLisbon())} dia(s)</StatusBadge>;
  const late = daysBetween(order.due_date, todayLisbon());
  if (late > 0) return <StatusBadge tone="danger">{late} dia(s) em atraso</StatusBadge>;
  if (late === 0) return <StatusBadge tone="warning">Entrega hoje</StatusBadge>;
  return <StatusBadge tone="info">Faltam {-late} dia(s)</StatusBadge>;
}
/** «PC-895085BE3FD64C9C…» → «PC-895085BE»: o número completo fica no título. */
const shortNumber = (number: string) => (number && number.length > 11 ? number.slice(0, 11) : number);
/** Cor da necessidade: vermelho (sem fornecedor ou pedido em atraso), âmbar (abaixo do mínimo, sem custo), verde. */
const needTone = (g: Row) =>
  !g.supplier_id ||
  g.lines.some((l: Row) => !l.order.closed && l.order.due_date && daysBetween(l.order.due_date, todayLisbon()) > 0)
    ? "danger"
    : g.state === "pronto"
      ? "success"
      : "warning";
const initialTerms = () => ({
  currency: "EUR",
  minimum_cents: 0,
  minimum_basis: "net",
  discount: "0",
  tiers: [],
  shipping_cents: 0,
  free_shipping_cents: null,
  shipping_vat: "23",
  vat: "23",
  lead_days: 7,
  pending_days: 7,
  confirmation_days: 3,
  pickup_days: 7,
  sales_period_days: 90,
  email: "",
  notes: "",
  prices: {},
});
/** Valor para <input type="datetime-local"> na hora local. */
const localDateTime = (value?: string) => {
  const date = value ? new Date(value) : new Date();
  return new Date(date.getTime() - date.getTimezoneOffset() * 60000)
    .toISOString()
    .slice(0, 16);
};
/** Cor do estado de um produto: verde = pronto/entregue, azul = em curso, âmbar = por tratar. */
const stateTone = (state: string) =>
  (({
    delivered: "success",
    ready: "success",
    received: "success",
    notified: "success",
    partial: "info",
    confirmed: "info",
    sent: "info",
    prepared: "info",
    simulated: "info",
    waiting: "warning",
    registered: "warning",
    cancelled: "neutral",
  }) as const)[state] || "neutral";
const blankLine = () => ({
  key: crypto.randomUUID(),
  article_id: "",
  description: "",
  quantity: "1",
  supplier_id: "",
  brand: "",
  reference: "",
  ean: "",
  sale_price_cents: null,
  notes: "",
});

/** Pesquisa de fornecedor (nome ou NIF) com acesso direto à ficha para a corrigir. */
function SupplierField({
  suppliers,
  value,
  label,
  disabled,
  onChange,
  onEdit,
}: {
  suppliers: Row[];
  value: string;
  label: string;
  disabled?: boolean;
  onChange: (id: string) => void;
  onEdit: (id: string) => void;
}) {
  const chosen = suppliers.find((s) => s.id === value);

  return (
    <div className="co-supplier-field">
      <SupplierPicker
        label={label}
        items={suppliers.map((s) => ({ id: s.id, name: s.name, tax_number: s.tax_number }))}
        selectedName={chosen?.name || ""}
        loading={false}
        disabled={disabled}
        canCreate={false}
        onSelect={(s) => onChange(s?.id || "")}
        onRequestCreate={() => undefined}
      />
      {chosen && (
        <ActionButton
          type="button"
          aria-label={`Editar fornecedor ${chosen.name}`}
          title="Editar a ficha do fornecedor"
          onClick={() => onEdit(chosen.id)}
        >
          <Pencil size={16} aria-hidden="true" />
        </ActionButton>
      )}
      {chosen && chosen.missing?.length > 0 && <MissingSupplierFields supplier={chosen} />}
    </div>
  );
}

/** Ficha sem rubrica, meio ou prazo de pagamento: o fornecedor não pode ser usado em movimentos. */
function MissingSupplierFields({ supplier }: { supplier: Row }) {
  return (
    <StatusBadge tone="danger">
      Ficha incompleta: falta {supplier.missing.join(", ")}
    </StatusBadge>
  );
}

/** Junta linhas pendentes de clientes às linhas de compra (mesmo artigo = mesma linha). */
function mergePending(lines: Row[], pendingLines: Row[], terms: Row, articles: Row[]) {
  const result: Row[] = structuredClone(lines),
    used = new Set(result.flatMap((l) => l.allocations.map((a: Row) => a.line_id)));
  for (const line of pendingLines.filter((l) => !used.has(l.id))) {
    const match = result.find((l) =>
      line.article_id
        ? l.article_id === line.article_id
        : !l.article_id && l.description === line.description,
    );
    const amount = Number(line.quantities.to_order),
      article = articles.find((a: Row) => a.id === line.article_id);
    const allocation = { line_id: line.id, quantity: line.quantities.to_order };
    if (match) {
      match.quantity = String(Number((Number(match.quantity) + amount).toFixed(6)));
      match.allocations.push(allocation);
    } else
      result.push({
        article_id: line.article_id,
        description: line.description,
        quantity: String(amount),
        unit_cost: terms.prices?.[line.article_id] ?? article?.last_cost ?? "0",
        discount: null,
        vat: article?.vat_rate ?? null,
        allocations: [allocation],
      });
  }
  return result;
}

/**
 * Necessidades de compra por fornecedor e loja: valor estimado (custo sem IVA, antes de descontos)
 * face ao mínimo do fornecedor. Sem fornecedor primeiro (precisa de ação), depois os prontos.
 */
function supplierNeeds(pending: Row[], suppliers: Row[], articles: Row[], simulations: Row[]) {
  const groups = new Map<string, Row>();
  for (const line of pending) {
    const supplierId = line.supplier_id || "";
    const key = `${supplierId}|${line.order.store_id}`;
    const supplier = suppliers.find((s) => s.id === supplierId);
    const group =
      groups.get(key) ||
      {
        key,
        supplier_id: supplierId,
        store_id: line.order.store_id,
        supplier,
        lines: [] as Row[],
        orders: new Set<string>(),
        value_cents: 0,
        unpriced: 0,
        urgent: false,
        oldest: line.order.requested_at,
      };
    const article = articles.find((a) => a.id === line.article_id);
    const cost = supplier?.terms?.prices?.[line.article_id] ?? article?.last_cost;
    if (cost === undefined || cost === null || cost === "" || Number(cost) <= 0) group.unpriced += 1;
    else group.value_cents += Math.round(Number(cost) * Number(line.quantities.to_order) * 100);
    group.lines.push(line);
    group.orders.add(line.order.id);
    group.urgent ||= line.order.priority === "urgent" || !!line.order.late;
    if (line.order.requested_at < group.oldest) group.oldest = line.order.requested_at;
    groups.set(key, group);
  }
  return [...groups.values()]
    .map((g): Row => {
      const minimum = Number(g.supplier?.terms?.minimum_cents || 0);
      const drafts = simulations.filter(
        (s) => s.supplier_id === g.supplier_id && s.store_id === g.store_id && s.status !== "cancelled" && !s.procurement_id,
      );
      const state = !g.supplier_id
        ? "sem_fornecedor"
        : !g.value_cents && g.unpriced
          ? "sem_custo"
          : g.value_cents >= minimum
            ? "pronto"
            : "abaixo";
      return { ...g, minimum_cents: minimum, missing_cents: Math.max(0, minimum - g.value_cents), drafts, state };
    })
    .sort(
      (a, b) =>
        ["sem_fornecedor", "pronto", "abaixo", "sem_custo"].indexOf(a.state) -
          ["sem_fornecedor", "pronto", "abaixo", "sem_custo"].indexOf(b.state) ||
        Number(b.urgent) - Number(a.urgent) ||
        String(a.oldest).localeCompare(String(b.oldest)),
    );
}

function CustomerPicker({
  customers,
  value,
  onPick,
  id,
}: {
  customers: Row[];
  value?: string;
  onPick: (customer: Row | null) => void;
  id?: string;
}) {
  const list = useId();
  const [text, setText] = useState("");
  const label = (c: Row) => `${c.name}${c.phone ? " · " + c.phone : ""}`;
  useEffect(() => {
    const customer = customers.find((c) => c.id === value);
    if (customer) setText(label(customer));
  }, [value, customers]);
  return (
    <>
      <input
        type="search"
        id={id}
        list={list}
        value={text}
        autoComplete="off"
        placeholder="Pesquisar nome, telefone ou email"
        onChange={(e) => {
          setText(e.target.value);
          onPick(
            customers.find((c) =>
              [label(c), c.phone, c.email].includes(e.target.value),
            ) || null,
          );
        }}
      />
      <datalist id={list}>
        {customers.flatMap((c) => [
          <option key={c.id} value={label(c)}>
            {c.email}
          </option>,
          ...(c.email
            ? [
                <option key={c.id + "-email"} value={c.email}>
                  {c.name}
                </option>,
              ]
            : []),
        ])}
      </datalist>
    </>
  );
}

function TermsFields({
  value,
  change,
  articles,
}: {
  value: Row;
  change: (value: Row) => void;
  articles: Row[];
}) {
  const set = (key: string, val: unknown) => change({ ...value, [key]: val });
  const euro = (key: string, label: string, optional = false) => (
    <Field key={key} label={label}>
      <input
        type="number"
        min="0"
        step="0.01"
        value={value[key] === null ? "" : Number(value[key]) / 100}
        onChange={(e) =>
          set(
            key,
            optional && e.target.value === ""
              ? null
              : Math.round(Number(e.target.value) * 100),
          )
        }
      />
    </Field>
  );
  return (
    <>
      <div className="co-grid">
        <Field label="Moeda">
          <input
            required
            pattern="[A-Z]{3}"
            maxLength={3}
            value={value.currency}
            onChange={(e) => set("currency", e.target.value.toUpperCase())}
          />
        </Field>
        {euro("minimum_cents", "Encomenda mínima aceite pelo fornecedor (sem portes)")}
        <Field label="Critério do mínimo">
          <select
            value={value.minimum_basis}
            onChange={(e) => set("minimum_basis", e.target.value)}
          >
            <option value="gross">Bruto sem IVA</option>
            <option value="net">Líquido após descontos, sem IVA</option>
            <option value="net_vat">Líquido com IVA, sem portes</option>
            <option value="total">Total com IVA e portes (inclui portes)</option>
          </select>
        </Field>
        <Field label="Desconto comercial (%)">
          <input
            type="number"
            min="0"
            max="100"
            step="0.0001"
            value={value.discount}
            onChange={(e) => set("discount", e.target.value)}
          />
        </Field>
        <Field label="IVA dos produtos (%)">
          <input
            type="number"
            min="0"
            max="100"
            step="0.001"
            value={value.vat}
            onChange={(e) => set("vat", e.target.value)}
          />
        </Field>
      </div>
      <details>
        <summary>Escalões de desconto e portes</summary>
        <p className="footnote">
          O escalão aplica-se ao valor após o desconto comercial, sem IVA. Os
          descontos são sucessivos; os portes gratuitos usam o líquido final sem
          IVA.
        </p>
        {(value.tiers || []).map((t: Row, i: number) => (
          <div className="co-grid" key={i}>
            <Field label={`Escalão ${i + 1} — desde`}>
              <input
                type="number"
                min="0"
                step="0.01"
                value={t.threshold_cents / 100}
                onChange={(e) =>
                  set(
                    "tiers",
                    value.tiers.map((r: Row, n: number) =>
                      n === i
                        ? {
                            ...r,
                            threshold_cents: Math.round(
                              Number(e.target.value) * 100,
                            ),
                          }
                        : r,
                    ),
                  )
                }
              />
            </Field>
            <Field label="Desconto adicional (%)">
              <input
                type="number"
                min="0"
                max="100"
                step="0.0001"
                value={t.discount}
                onChange={(e) =>
                  set(
                    "tiers",
                    value.tiers.map((r: Row, n: number) =>
                      n === i ? { ...r, discount: e.target.value } : r,
                    ),
                  )
                }
              />
            </Field>
            <ActionButton
              type="button"
              onClick={() =>
                set(
                  "tiers",
                  value.tiers.filter((_: Row, n: number) => n !== i),
                )
              }
            >
              Remover escalão
            </ActionButton>
          </div>
        ))}
        <ActionButton
          type="button"
          onClick={() =>
            set("tiers", [
              ...value.tiers,
              { threshold_cents: 0, discount: "0" },
            ])
          }
        >
          Adicionar escalão
        </ActionButton>
        <div className="co-grid">
          {euro("shipping_cents", "Portes")}
          {euro(
            "free_shipping_cents",
            "Portes grátis a partir de (vazio: paga sempre)",
            true,
          )}
          <Field label="IVA dos portes (%)">
            <input
              type="number"
              min="0"
              max="100"
              step="0.001"
              value={value.shipping_vat}
              onChange={(e) => set("shipping_vat", e.target.value)}
            />
          </Field>
        </div>
      </details>
      <details>
        <summary>Prazos, alertas, contacto e preços acordados</summary>
        <div className="co-grid">
          {[
            ["lead_days", "Prazo de entrega (dias)"],
            ["pending_days", "Alertar pedido pendente após (dias)"],
            ["confirmation_days", "Alertar falta de confirmação após (dias)"],
            ["pickup_days", "Alertar falta de levantamento após (dias)"],
            ["sales_period_days", "Período de vendas para reposição automática (dias)"],
          ].map(([key, label]) => (
            <Field key={key} label={label}>
              <input
                type="number"
                min={key === "lead_days" ? 0 : key === "sales_period_days" ? 7 : 1}
                max={key === "sales_period_days" ? "730" : "3650"}
                value={value[key]}
                onChange={(e) => set(key, Number(e.target.value))}
              />
            </Field>
          ))}
          <Field label="Email comercial">
            <input
              type="email"
              value={value.email}
              onChange={(e) => set("email", e.target.value)}
            />
          </Field>
          <Field label="Observações comerciais">
            <textarea
              value={value.notes}
              onChange={(e) => set("notes", e.target.value)}
            />
          </Field>
        </div>
        <Field label="Adicionar preço acordado">
          <select
            value=""
            onChange={(e) => {
              if (e.target.value)
                set("prices", { ...value.prices, [e.target.value]: "0" });
            }}
          >
            <option value="">Selecionar artigo</option>
            {articles.map((a) => (
              <option key={a.id} value={a.id}>
                {a.code} · {a.name}
              </option>
            ))}
          </select>
        </Field>
        {Object.entries(value.prices || {}).map(([id, price]) => (
          <div className="co-inline" key={id}>
            <Field label={articles.find((a) => a.id === id)?.name || id}>
              <input
                type="number"
                min="0"
                step="0.000001"
                value={String(price)}
                onChange={(e) =>
                  set("prices", { ...value.prices, [id]: e.target.value })
                }
              />
            </Field>
            <ActionButton
              type="button"
              onClick={() => {
                const next = { ...value.prices };
                delete next[id];
                set("prices", next);
              }}
            >
              Remover preço
            </ActionButton>
          </div>
        ))}
      </details>
    </>
  );
}

/** Quantidade a comprar e, com bonificação, a oferta, o total recebido e o custo unitário efetivo. */
function ProposedQuantity({ item }: { item: Row }) {
  const bonus = item.bonus;
  if (!bonus) return <b>{qty(item.quantity)}</b>;
  return (
    <span className="co-bonus-plan">
      <b>{qty(item.quantity)}</b>
      {Number(bonus.free_quantity) > 0 ? (
        <small>
          + {qty(bonus.free_quantity)} oferta = {qty(bonus.received_quantity)}
          {bonus.effective_unit_cost ? ` · ${money(Math.round(Number(bonus.effective_unit_cost) * 100))}/un. efetivo` : ""}
        </small>
      ) : (
        <small>
          Na compra de {qty(bonus.buy)}, oferta de {qty(bonus.free)}: faltam {qty(bonus.missing_for_bonus)}
        </small>
      )}
      {Number(item.needed_quantity) !== Number(item.quantity) && <small>Necessidade: {qty(item.needed_quantity)}</small>}
    </span>
  );
}

/** Oferta automática da bonificação «na compra de X, oferta de Y» (por cada X completo). */
const bonusOffer = (bonus: Row | undefined, quantity: number) =>
  bonus ? Math.floor(quantity / Number(bonus.buy)) * Number(bonus.free) : 0;

/**
 * Bonificação «na compra de X, oferta de Y» do artigo neste fornecedor, para a quantidade da linha.
 * O botão acerta a compra ao bloco seguinte da bonificação (a oferta volta a ser automática).
 */
function BonusHint({ bonus, quantity, onApply }: { bonus?: Row; quantity: number; onApply?: (quantity: number) => void }) {
  if (!bonus) return null;
  const buy = Number(bonus.buy), free = Number(bonus.free);
  const offered = bonusOffer(bonus, quantity);
  const missing = quantity % buy ? buy - (quantity % buy) : 0;
  const target = quantity + missing;
  return (
    <div className="co-bonus">
      <p>
        <StatusBadge tone={offered ? "success" : "info"}>
          Bonificação: na compra de {qty(buy)}, oferta de {qty(free)}
        </StatusBadge>{" "}
        {offered
          ? `Nesta quantidade recebe mais ${qty(offered)} de oferta (linha a preço 0 na encomenda).`
          : `Compre mais ${qty(missing)} para receber ${qty(free)} de oferta.`}
      </p>
      {onApply && missing > 0 && (
        <ActionButton type="button" onClick={() => onApply(target)}>
          {offered ? `Completar para ${qty(target)} (+${qty(free)} oferta)` : `Comprar ${qty(target)} e receber ${qty(bonusOffer(bonus, target))} de oferta`}
        </ActionButton>
      )}
    </div>
  );
}

const basisLabel: Row = {
  gross: "bruto sem IVA",
  net: "líquido após descontos, sem IVA",
  net_vat: "líquido com IVA",
  total: "total com IVA e portes",
};

/** O que conta para encomendar a este fornecedor: mínimo sem portes, portes e prazo. */
function TermsSummary({ terms, onEdit }: { terms: Row; onEdit?: () => void }) {
  const currency = terms.currency || "EUR";
  return (
    <div className="co-terms-summary" aria-label="Condições do fornecedor">
      <span className="co-terms-source">
        Condições da ficha do fornecedor
        {onEdit && (
          <ActionButton type="button" className="co-icon" onClick={onEdit}>
            <Pencil size={14} aria-hidden="true" />
            Editar na ficha
          </ActionButton>
        )}
      </span>
      <span>
        Encomenda mínima aceite (sem portes):{" "}
        <b>{terms.minimum_cents ? money(terms.minimum_cents, currency) : "sem mínimo"}</b>
        {terms.minimum_cents > 0 && <small> · sobre o {basisLabel[terms.minimum_basis] || terms.minimum_basis}</small>}
      </span>
      <span>
        Portes: <b>{terms.shipping_cents ? money(terms.shipping_cents, currency) : "sem portes"}</b>
        {terms.free_shipping_cents != null && <small> · grátis desde {money(terms.free_shipping_cents, currency)}</small>}
      </span>
      <span>
        Prazo de entrega: <b>{terms.lead_days} dias</b>
      </span>
      {terms.minimum_basis === "total" && (
        <StatusBadge tone="warning">O critério do mínimo inclui portes</StatusBadge>
      )}
    </div>
  );
}

const PERIODS = [30, 60, 90, 180, 365];

/**
 * Reposição automática no simulador: artigos do fornecedor com vendas no período escolhido que
 * estão abaixo do mínimo da ficha ou não têm mínimo definido. A quantidade já simula a bonificação
 * acordada (a oferta conta para a necessidade). Ficam todos marcados; adicionar é sempre uma decisão sua.
 */
function LowStockSuggestions({
  storeId,
  supplierId,
  defaultPeriod,
  lines,
  onAdd,
}: {
  storeId: string;
  supplierId: string;
  defaultPeriod: number;
  lines: Row[];
  onAdd: (items: Row[]) => void;
}) {
  const [period, setPeriod] = useState(defaultPeriod || 90);
  const [items, setItems] = useState<Row[] | null>(null);
  const [error, setError] = useState("");
  const [skip, setSkip] = useState<string[]>([]);
  const [others, setOthers] = useState(false);
  useEffect(() => setPeriod(defaultPeriod || 90), [defaultPeriod, supplierId]);
  useEffect(() => {
    if (!storeId || !supplierId) return;
    let alive = true;
    setItems(null);
    setError("");
    api(
      `/order-cycle/recommendations?store_id=${encodeURIComponent(storeId)}&supplier_id=${encodeURIComponent(supplierId)}&period_days=${period}`,
    )
      .then((r) => alive && setItems(r.items))
      .catch((e) => alive && setError(e.message));
    return () => {
      alive = false;
    };
  }, [storeId, supplierId, period]);
  if (!supplierId) return null;
  const inDraft = (id: string) => lines.some((l) => l.article_id === id);
  const proposed = (items || []).filter((i) => Number(i.quantity) > 0 && !inDraft(i.article_id));
  // Com mínimo: abaixo dele e com vendas; sem mínimo: basta ter vendas no período do filtro.
  const automatic = (i: Row) => Number(i.period_sales) > 0 && (Number(i.minimum) > 0 ? i.below_minimum : true);
  const urgent = proposed.filter(automatic);
  const rest = proposed.filter((i) => !urgent.includes(i));
  const chosen = urgent.filter((i) => !skip.includes(i.article_id));
  const added = (items || []).filter((i) => inDraft(i.article_id) && automatic(i)).length;
  const row = (i: Row, check: boolean) => (
    <tr key={i.article_id}>
      {check && (
        <td>
          <input
            type="checkbox"
            aria-label={`Incluir ${i.description}`}
            checked={!skip.includes(i.article_id)}
            onChange={(e) =>
              setSkip(e.target.checked ? skip.filter((x) => x !== i.article_id) : [...skip, i.article_id])
            }
          />
        </td>
      )}
      <td>
        <b>{i.code}</b> {i.description}
      </td>
      <td className={i.below_minimum ? "co-text-danger" : undefined}>
        {qty(i.free)} / {qty(i.minimum)}
      </td>
      <td>{qty(i.period_sales)}</td>
      <td>{qty(i.incoming)}</td>
      <td>
        <ProposedQuantity item={i} />
      </td>
      <td>{money(Math.round(Number(i.unit_cost) * 100))}</td>
    </tr>
  );
  const head = (check: boolean) => (
    <thead>
      <tr>
        {check && <th>Incluir</th>}
        {["Artigo", "Stock livre / mínimo", `Vendas ${period} dias`, "Em trânsito", "Comprar (bonificação)", "Custo"].map((h) => (
          <th key={h}>{h}</th>
        ))}
      </tr>
    </thead>
  );
  return (
    <section className="co-low-stock" aria-labelledby="co-low-stock-title">
      <div className="co-inline">
        <h3 id="co-low-stock-title">Reposição: artigos com vendas no período</h3>
        <Field label="Período de vendas">
          <select value={period} onChange={(e) => setPeriod(Number(e.target.value))}>
            {[...new Set([...PERIODS, defaultPeriod || 90])]
              .sort((a, b) => a - b)
              .map((d) => (
                <option key={d} value={d}>
                  Últimos {d} dias{d === defaultPeriod ? " (do fornecedor)" : ""}
                </option>
              ))}
          </select>
        </Field>
      </div>
      <p className="footnote">
        Artigos deste fornecedor com vendas no período: os que têm mínimo na ficha entram quando o
        stock livre está abaixo dele; os que não têm mínimo entram pelas vendas. A quantidade já
        desconta o que está em trânsito e os pedidos dos clientes, respeita mínimos e múltiplos
        acordados e simula a bonificação do fornecedor (a oferta conta para a necessidade).
      </p>
      {error && <Notice tone="error">{error}</Notice>}
      {!items && !error && <p role="status">A calcular a reposição…</p>}
      {items && !urgent.length && (
        <p>
          {added
            ? `Os ${added} artigo(s) a repor com vendas já estão nesta encomenda.`
            : `Nenhum artigo a repor com vendas nos últimos ${period} dias.`}
        </p>
      )}
      {urgent.length > 0 && (
        <>
          <div className="table-wrap">
            <table aria-label="Artigos a repor com vendas no período">
              {head(true)}
              <tbody>{urgent.map((i) => row(i, true))}</tbody>
            </table>
          </div>
          <ActionButton type="button" className="primary" disabled={!chosen.length} onClick={() => onAdd(chosen)}>
            Adicionar à encomenda ({chosen.length})
          </ActionButton>
        </>
      )}
      {rest.length > 0 && (
        <details open={others} onToggle={(e) => setOthers((e.target as HTMLDetailsElement).open)}>
          <summary>Outras sugestões de reposição ({rest.length})</summary>
          <div className="table-wrap">
            <table aria-label="Outras sugestões de reposição">
              {head(false)}
              <tbody>
                {rest.map((i) => (
                  <tr key={i.article_id}>
                    <td>
                      <b>{i.code}</b> {i.description}
                      <ActionButton type="button" className="co-icon" onClick={() => onAdd([i])}>
                        Adicionar
                      </ActionButton>
                    </td>
                    <td>
                      {qty(i.free)} / {qty(i.minimum)}
                    </td>
                    <td>{qty(i.period_sales)}</td>
                    <td>{qty(i.incoming)}</td>
                    <td>
                      <ProposedQuantity item={i} />
                    </td>
                    <td>{money(Math.round(Number(i.unit_cost) * 100))}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </details>
      )}
    </section>
  );
}

const channelLabel = (channel: string) =>
  ({ email: "Email", whatsapp: "WhatsApp", phone: "Telefone" })[channel] ||
  "sem canal";

/** Rascunhos e encomendas emitidas sem receções nem faturas editam-se na janela da emissão. */
const editableSim = (sim: Row) =>
  sim.status === "draft" ||
  (sim.status === "prepared" &&
    sim.procurement?.status !== "cancelled" &&
    !(sim.procurement?.lines || []).some(
      (l: Row) => Number(l.received) > 0 || Number(l.invoiced) > 0,
    ));

/** Estado da simulação/encomenda ao fornecedor, com a cor do passo seguinte. */
function SimulationState({ sim }: { sim: Row }) {
  if (sim.status === "cancelled")
    return <StatusBadge>Simulação cancelada</StatusBadge>;
  if (sim.procurement?.status === "cancelled")
    return <StatusBadge>Encomenda cancelada</StatusBadge>;
  if (sim.procurement?.needs_resend)
    return <StatusBadge tone="warning">Alterada: reenviar ao fornecedor</StatusBadge>;
  if (sim.supplier_confirmation)
    return <StatusBadge tone="success">Confirmação registada</StatusBadge>;
  if (
    sim.procurement?.email_history?.some((e: Row) =>
      ["accepted", "received"].includes(e.status),
    )
  )
    return <StatusBadge tone="info">Enviada ao fornecedor</StatusBadge>;
  if (sim.procurement_id)
    return <StatusBadge tone="info">Encomenda preparada: enviar</StatusBadge>;
  return <StatusBadge tone="warning">Rascunho de simulação</StatusBadge>;
}

function SimulationTotals({ totals, terms }: { totals: Row; terms: Row }) {
  const currency = totals.currency || "EUR";
  return (
    <section className="co-totals" aria-label="Resultado da simulação">
      <dl>
        {[
          ["Bruto", totals.gross_cents],
          ["Descontos", totals.discount_cents],
          ["Líquido", totals.net_cents],
          ["IVA dos produtos", totals.vat_cents],
          ["Portes", totals.shipping_cents],
          ["IVA dos portes", totals.shipping_vat_cents],
          ["Total previsto", totals.total_cents],
          ["Elegível para o mínimo", totals.eligible_cents],
        ].map(([label, val]) => (
          <div key={String(label)}>
            <dt>{label}</dt>
            <dd>{money(Number(val), currency)}</dd>
          </div>
        ))}
      </dl>
      <label>
        Progresso do mínimo{" "}
        <progress
          max={Math.max(terms.minimum_cents, 1)}
          value={Math.min(
            totals.eligible_cents,
            Math.max(terms.minimum_cents, 1),
          )}
        />
      </label>
      <p
        role="status"
        className={`co-highlight co-highlight-${totals.missing_cents ? "warning" : "success"}`}
      >
        <strong>
          {totals.missing_cents
            ? `Faltam ${money(totals.missing_cents, currency)} para atingir o mínimo de encomenda.`
            : "Mínimo de encomenda atingido."}
        </strong>
      </p>
      <small>Simulação: não cria nem envia encomendas reais.</small>
      {terms.free_shipping_cents != null ? <section className="co-shipping-goal" aria-label="Progresso dos portes grátis">
        <label>Limiar de portes grátis: {money(terms.free_shipping_cents, currency)}<progress max={Math.max(1, terms.free_shipping_cents)} value={Math.min(totals.net_cents, Math.max(1, terms.free_shipping_cents))} /></label>
        <p role="status">{(totals.shipping_missing_cents ?? Math.max(0, terms.free_shipping_cents - totals.net_cents)) > 0
          ? `Faltam ${money(totals.shipping_missing_cents ?? Math.max(0, terms.free_shipping_cents - totals.net_cents), currency)} para portes grátis.`
          : "Limiar de portes grátis atingido."}</p>
        <small>Calculado sobre mercadorias líquidas sem IVA. O mínimo da encomenda é independente. Compare o custo de reposição com a poupança de portes.</small>
      </section> : <p className="footnote">Sem limiar de portes grátis configurado. Aplicam-se os portes estimados indicados.</p>}
    </section>
  );
}

export default function CustomerOrders({
  stores,
  store = "",
  company = {},
  navigate,
  onStoreChange,
}: {
  stores: Row[];
  store?: string;
  company?: Row;
  navigate: (page: string, params?: Record<string, string>) => void;
  onStoreChange: (store: string) => Promise<boolean>;
}) {
  const [data, setData] = useState<Row | null>(null),
    [catalog, setCatalog] = useState<Row>({
      articles: [],
      warehouses: [],
      customers: [],
      suppliers: [],
    });
  const [history, setHistory] = useState<Row[]>([]),
    [receipts, setReceipts] = useState<Row[]>([]);
  const [area, setArea] = useWorkspaceTab(
    "pedidos-clientes",
    "centro",
    [...tabs.map(([id]) => id), "simulacoes"],
  );
  const [planningTab, setPlanningTab] = useWorkspaceTab("planeamento-compras", "necessidades", ["necessidades", "simulacoes"]);
  useEffect(() => {
    if (area === "simulacoes") updateLocationParams({
      "area.pedidos-clientes": "planeamento", "area.planeamento-compras": "simulacoes",
      "menu.area": "encomendas", "menu.destino": "compras:Simulador de encomendas", "filtro.abastecimento": "buy",
    }, true);
  }, [area]);
  const [error, setError] = useState(""),
    [notice, setNotice] = useState(""),
    [busy, setBusy] = useState(false);
  const [selected, setSelected] = useState(""),
    [simId, setSimId] = useState("");
  const [modal, setModal] = useState(""),
    [draft, setDraft] = useState<Row>({}),
    [preview, setPreview] = useState<Row | null>(null),
    [checks, setChecks] = useState<string[]>([]);
  const [mail, setMail] = useState<Row | null>(null),
    [contact, setContact] = useState<Row | null>(null);
  const [previewError, setPreviewError] = useState("");
  const [scenarios, setScenarios] = useState<Row[]>([]);
  const [editingSupplier, setEditingSupplier] = useState<{
    table: TableInfo;
    recordId: string;
    categories: Row[];
  } | null>(null);
  const [orderFilter, setOrderFilter] = useState("abertos");
  const orderLocation = useLocationSearch();
  useEffect(() => {
    const filter = new URLSearchParams(orderLocation).get("filtro.pedidos");
    if (filter && ["abertos", "prontos", "todos", "atrasados", "urgentes", "fechados"].includes(filter)) setOrderFilter(filter);
  }, [orderLocation]);
  const keys = useRef(new Map<string, { body: string; key: string }>());
  const orders: Row[] = data?.orders || [],
    simulations: Row[] = data?.simulations || [];
  useRecordHandoff("order", ["Encomendas de clientes"], id => {
    if (!data) return false;
    if (!orders.some(order=>order.id===id)) { setError("O pedido já não está disponível nesta loja. Atualize a pesquisa."); return true; }
    setSelected(id); return true;
  });
  const active = orders.find((o) => o.id === selected),
    activeSim = simulations.find((s) => s.id === simId);
  const pending = orders.flatMap((o) =>
    o.lines
      .filter((l: Row) => Number(l.quantities.to_order) > 0)
      .map((l: Row) => ({ ...l, order: o })),
  );
  const warehouses = (id: string) =>
    catalog.warehouses.filter((w: Row) => w.store_id === id);
  const supplierName = (id: string) =>
    catalog.suppliers.find((s: Row) => s.id === id)?.name || "Sem fornecedor";
  const canBuy = !!data?.can_buy;
  const simulationSignature = modal === "simulation" ? JSON.stringify({
    store_id: draft.store_id, supplier_id: draft.supplier_id, terms: draft.terms,
    lines: draft.lines, notes: draft.notes || "", revision: draft.revision || 0,
  }) : "";
  useEffect(() => {
    setPreview(null); setPreviewError("");
    if (!simulationSignature) return;
    const body = JSON.parse(simulationSignature);
    if (!body.supplier_id || !body.store_id || !body.lines?.length || body.lines.some((l: Row) =>
      !l.description.trim() || !Number.isFinite(Number(l.quantity)) || Number(l.quantity) <= 0 ||
      !Number.isFinite(Number(l.unit_cost)) || Number(l.unit_cost) < 0)) return;
    let alive = true;
    const timer = setTimeout(() => {
      void api("/customer-orders/simulations/preview", { ...body, request_key: crypto.randomUUID() }, { preserveDraft: true })
        .then(next => { if (alive) setPreview(next); })
        .catch(e => { if (alive) setPreviewError(e.message); });
    }, 250);
    return () => { alive = false; clearTimeout(timer); };
  }, [simulationSignature]);

  const loadVersion = useRef(0);
  async function load() {
    const version = ++loadVersion.current;
    const query = store ? "?store_id=" + encodeURIComponent(store) : "";
    const [next, stock, people, events] = await Promise.all([
      api("/customer-orders" + query),
      api("/stock/catalog"),
      api("/customer-orders/catalog"),
      api("/customer-orders/history" + query),
    ]);
    if (version !== loadVersion.current) return;
    setData(next);
    setCatalog({ ...stock, ...people });
    setHistory(events);
    if (next.can_buy) {
      const documents = (await api("/procurement")).documents;
      if (version === loadVersion.current) setReceipts(documents.filter((d: Row) => d.kind === "receipt" && d.confirmed_at));
    } else setReceipts([]);
  }
  useEffect(() => {
    let alive = true;
    setData(null);
    void load().catch((e) => {
      if (alive) setError(e.message);
    });
    return () => {
      alive = false;
      ++loadVersion.current;
    };
  }, [store]);
  async function run(action: () => Promise<void>) {
    if (busy) return;
    setBusy(true);
    setError("");
    try {
      await action();
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  }
  async function mutate(
    path: string,
    body: Row,
    method: "POST" | "PUT" = "POST",
  ) {
    const encoded = JSON.stringify(body),
      previous = keys.current.get(path);
    const key = previous?.body === encoded ? previous.key : crypto.randomUUID();
    keys.current.set(path, { body: encoded, key });
    const result = await api(path, { ...body, request_key: key }, { method });
    keys.current.delete(path);
    return result;
  }
  const feedback = (
    <>
      {error && <Notice tone="error">{error}</Notice>}
      {notice && <Notice tone="success">{notice}</Notice>}
    </>
  );
  function open(kind: string, value: Row) {
    setModal(kind);
    setDraft(value);
    setPreview(null);
    setPreviewError("");
    setScenarios([]);
    setMail(null);
    setContact(null);
    setChecks([]);
    setError("");
    setNotice("");
  }
  function newOrder() {
    open("order", {
      store_id: store || stores[0]?.id || "",
      customer_id: "",
      customer_name: "",
      phone: "",
      email: "",
      preferred_channel: "none",
      contact_allowed: false,
      due_date: "",
      priority: "normal",
      notes: "",
      requested_at: localDateTime(),
      lines: [blankLine()],
    });
  }
  /** Mesma janela do novo pedido. Produtos já em curso mantêm o artigo e o mínimo comprometido. */
  function editOrder(row: Row) {
    const open_lines = row.lines.filter(
      (l: Row) => !["delivered", "cancelled"].includes(l.state),
    );
    open("order", {
      id: row.id,
      number: row.number,
      revision: row.revision,
      store_id: row.store_id,
      customer_id: row.customer_id || "",
      customer_name: row.customer_name || "",
      phone: row.phone || "",
      email: row.email || "",
      preferred_channel: row.preferred_channel || "none",
      contact_allowed: !!row.contact_allowed,
      due_date: row.due_date || "",
      priority: row.priority || "normal",
      notes: row.notes || "",
      requested_at: localDateTime(row.requested_at),
      original_requested: row.requested_at,
      reason: "",
      closed_lines: row.lines.length - open_lines.length,
      original_ids: open_lines.map((l: Row) => l.id),
      lines: open_lines.map((l: Row) => ({
        key: l.id,
        id: l.id,
        article_id: l.article_id || "",
        description: l.description || "",
        quantity: String(Number(l.quantities.requested)),
        supplier_id: l.supplier_id || "",
        brand: l.brand || "",
        reference: l.reference || "",
        ean: l.ean || "",
        sale_price_cents: l.sale_price_cents ?? null,
        notes: l.notes || "",
        state: l.state,
        state_label: l.state_label,
        committed: Number(l.quantities.requested) - Number(l.quantities.to_order),
      })),
    });
  }
  function newSim() {
    open("simulation", {
      store_id: store || stores[0]?.id || "",
      supplier_id: "",
      terms: initialTerms(),
      lines: [],
      notes: "",
    });
  }
  const editLine = (index: number, changes: Row) => {
    setDraft((d) => ({
      ...d,
      lines: d.lines.map((l: Row, i: number) =>
        i === index ? { ...l, ...changes } : l,
      ),
    }));
    setPreview(null);
  };
  const supplierPending = pending.filter(
    (l) =>
      l.supplier_id === draft.supplier_id &&
      l.order.store_id === draft.store_id &&
      !(draft.lines || []).some((r: Row) =>
        r.allocations?.some((a: Row) => a.line_id === l.id),
      ),
  );
  function addPending(all = false) {
    const lines = mergePending(
      draft.lines,
      supplierPending.filter((l) => all || checks.includes(l.id)),
      draft.terms,
      catalog.articles,
    );
    setDraft({ ...draft, lines });
    setChecks([]);
    setPreview(null);
  }
  /** Simulação já preenchida com tudo o que falta encomendar a este fornecedor nesta loja. */
  function simulateFor(group: Row) {
    const terms = structuredClone(group.supplier?.terms || initialTerms());
    open("simulation", {
      store_id: group.store_id,
      supplier_id: group.supplier_id,
      terms,
      lines: mergePending([], group.lines, terms, catalog.articles),
      notes: "",
    });
  }
  /** Junta artigos de reposição às linhas de compra (o mesmo artigo fica numa só linha). */
  function addReplenishment(items: Row[]) {
    const lines = structuredClone(draft.lines);
    for (const item of items) {
      const found = lines.find((line: Row) => line.article_id === item.article_id);
      if (found) found.quantity = String(Math.max(Number(found.quantity), Number(item.quantity)));
      else
        lines.push({
          article_id: item.article_id,
          description: item.description,
          quantity: item.quantity,
          unit_cost: item.unit_cost,
          vat: item.vat,
          discount: null,
          allocations: [],
        });
    }
    setDraft({ ...draft, lines });
    setPreview(null);
  }
  function editSupplier(id: string) {
    if (!id) return;
    // Fornecedores do ERP usados num pedido têm referência local, mas a ficha é a «wt:…».
    const recordId =
      catalog.suppliers.find((s: Row) => s.id === id)?.record_id ||
      (id.startsWith("wt:") ? id : `local:${id}`);
    void run(async () => {
      const [table, categories] = await Promise.all([
        api("/tables/suppliers"),
        api("/categories"),
      ]);
      if (!table.permissions?.edit)
        throw new Error("Sem autorização para editar fornecedores.");
      setEditingSupplier({ table, categories, recordId });
    });
  }
  async function saveOrder() {
    const lines = draft.lines.map(
      ({ key, state, state_label, committed, ...l }: Row) => ({
        ...l,
        article_id: l.article_id || null,
        supplier_id: l.supplier_id || null,
      }),
    );
    const requested_at =
      draft.id && draft.requested_at === localDateTime(draft.original_requested)
        ? draft.original_requested
        : new Date(draft.requested_at).toISOString();
    if (draft.id) {
      const { id, number, original_requested, original_ids, closed_lines, store_id, ...header } = draft;
      await mutate(
        `/customer-orders/${id}`,
        {
          ...header,
          customer_id: draft.customer_id || null,
          due_date: draft.due_date || null,
          requested_at,
          lines,
        },
        "PUT",
      );
      await load();
      setModal("");
      setNotice("Pedido atualizado.");
      return;
    }
    const row = await mutate("/customer-orders", {
      ...draft,
      customer_id: draft.customer_id || null,
      due_date: draft.due_date || null,
      requested_at,
      lines,
    });
    await load();
    setSelected(row.id);
    setModal("");
    setNotice("Pedido registado. Não foi enviada nenhuma mensagem.");
  }
  async function saveSim() {
    const {
      id,
      revision = 0,
      store_id,
      supplier_id,
      terms,
      lines,
      notes,
    } = draft;
    const saved = await mutate(
      "/customer-orders/simulations" + (id ? "/" + id : ""),
      { revision, store_id, supplier_id, terms, lines, notes },
      id ? "PUT" : "POST",
    );
    await load();
    setModal("");
    setArea("simulacoes");
    setNotice(
      saved.status === "prepared"
        ? `Encomenda ${saved.procurement?.document || ""} atualizada.${saved.procurement?.needs_resend ? " Foi alterada depois do envio: reenvie-a ao fornecedor." : ""}`
        : "Simulação guardada sem criar uma encomenda real.",
    );
  }
  async function prepareSim(sim: Row) {
    open("prepare", {
      simulation_id: sim.id,
      revision: sim.revision,
      warehouse_id: warehouses(sim.store_id)[0]?.id || "",
      allow_below_minimum: false,
    });
  }
  function contactOrder(row: Row) {
    const products = row.lines
      .filter((l: Row) => Number(l.quantities.available) > 0)
      .map((l: Row) => `${l.description} (${qty(l.quantities.available)})`)
      .join(", ");
    open("contact", {
      order_id: row.id,
      revision: row.revision,
      channel:
        row.preferred_channel === "none" ? "phone" : row.preferred_channel,
      message: `Olá ${row.customer_name},\n\nInformamos que ${products}, do pedido de ${day(row.requested_at)}, já se encontra disponível para levantamento.\n\nObrigado pela preferência.\n${company.name || ""}`,
      subject: "O seu pedido está disponível",
    });
    const prepared = history.find(
      (h) => h.order_id === row.id && h.dispatch_id && h.status === "prepared",
    );
    if (prepared)
      void run(async () => {
        const saved = await api(
          `/customer-orders/${row.id}/email/${prepared.dispatch_id}`,
        );
        setMail(saved);
      });
  }
  function supplierEmail(sim: Row) {
    open("supplier-email", {
      simulation_id: sim.id,
      procurement_id: sim.procurement_id,
      revision: sim.procurement.revision,
      to:
        sim.terms.email ||
        catalog.suppliers.find((s: Row) => s.id === sim.supplier_id)?.email ||
        "",
      subject: `Encomenda ${sim.procurement.document} · ${company.name || ""}`,
      message: `${company.name || "Empresa"}${company.nif ? " · NIF " + company.nif : ""}\nFornecedor: ${sim.supplier_name}\nEncomenda: ${sim.procurement.document}\nData: ${sim.procurement.document_date}\n\n${sim.lines.map((l: Row) => `${catalog.articles.find((a: Row) => a.id === l.article_id)?.code || ""} · ${l.description} · ${qty(l.quantity)} · ${money(Math.round(Number(l.unit_cost) * 100), sim.terms.currency)}`).join("\n")}\n\n${sim.notes || ""}\nSolicitamos confirmação de disponibilidade e prazo de entrega.\nSegue a encomenda em PDF.`,
    });
    const prepared = sim.procurement.email_history?.find(
      (e: Row) => e.status === "prepared",
    );
    if (prepared)
      void run(async () =>
        setMail(
          await api(
            `/customer-orders/simulations/${sim.id}/email/${prepared.id}`,
          ),
        ),
      );
  }
  const display = (
    rows: Row[],
    type: "orders" | "simulations" | "history",
  ) => (
    <DataTable<Row>
      label={
        {
          orders: "pedidos de clientes",
          simulations: "simulações de fornecedores",
          history: "histórico de encomendas",
        }[type]
      }
      rows={rows}
      rowKey={(r) => r.id}
      searchText={(r) =>
        type === "orders"
          ? `${r.number} ${r.legacy_number || ""} ${r.customer_name} ${r.phone} ${r.email} ${r.lines.map((l: Row) => l.description).join(" ")}`
            : type === "simulations"
              ? `${r.supplier_name} ${r.id} ${r.status}`
              : `${r.action} ${r.user} ${r.order_id || ""} ${r.detail?.message || ""}`
      }
      columns={
        type === "orders"
          ? [
              {
                id: "customer",
                label: "Pedido / cliente",
                sortValue: (r) => r.customer_name,
                render: (r) => (
                  <>
                    <strong>{r.number}</strong>
                    <div>{r.customer_name}</div>
                    <small>{day(r.requested_at)}</small>
                    {r.priority === "urgent" && (
                      <StatusBadge tone="danger">Urgente</StatusBadge>
                    )}
                  </>
                ),
              },
              {
                id: "products",
                label: "Produtos / estado",
                render: (r) => (
                  <>
                    {r.lines.map((l: Row) => (
                      <div key={l.id} className="co-line-state">
                        <span>
                          {l.description} · <b>{qty(l.quantities.requested)}</b>
                        </span>
                        <StatusBadge tone={stateTone(l.state)}>{l.state_label}</StatusBadge>
                      </div>
                    ))}
                  </>
                ),
              },
              {
                id: "due",
                label: "Entrega prevista",
                sortValue: (r) => r.due_date || "",
                render: (r) => (
                  <>
                    {day(r.due_date)}
                    <DueBadge order={r} />
                  </>
                ),
              },
              {
                id: "ready",
                label: "Levantamento",
                render: (r) =>
                  r.ready
                    ? r.notified
                      ? (
                        <StatusBadge tone="success">
                          {r.fully_ready === false ? "Disponível parcialmente · cliente notificado" : "Cliente notificado"}
                        </StatusBadge>
                      )
                      : (
                        <StatusBadge tone="info">
                          {r.fully_ready === false ? "Disponível parcialmente · contacto manual" : "Cliente por contactar"}
                        </StatusBadge>
                      )
                    : r.closed
                      ? <StatusBadge tone="success">Concluído</StatusBadge>
                      : (
                        <StatusBadge tone="warning">
                          A aguardar produtos
                        </StatusBadge>
                      ),
              },
              {
                id: "actions",
                label: "Ações",
                render: (r) => (
                  <div className="actions">
                    <ActionButton onClick={() => setSelected(r.id)}>
                      Abrir pedido
                    </ActionButton>
                    {data?.can_write && (
                      <ActionButton
                        aria-label={`Editar pedido ${r.number}`}
                        onClick={() => editOrder(r)}
                      >
                        <Pencil size={16} aria-hidden="true" />
                        Editar
                      </ActionButton>
                    )}
                    {r.ready && data?.can_write && (
                      <ActionButton onClick={() => contactOrder(r)}>
                        Contactar cliente
                      </ActionButton>
                    )}
                  </div>
                ),
              },
            ]
            : type === "simulations"
              ? [
                  {
                    id: "supplier",
                    label: "Fornecedor",
                    sortValue: (r) => r.supplier_name,
                    render: (r) => (
                      <>
                        {r.supplier_name}
                        <small>
                          {r.procurement?.document || r.id.slice(0, 8)}
                        </small>
                      </>
                    ),
                  },
                  {
                    id: "state",
                    label: "Estado",
                    render: (r) => <SimulationState sim={r} />,
                  },
                  {
                    id: "total",
                    label: "Total / mínimo",
                    sortValue: (r) => r.totals.total_cents,
                    render: (r) => (
                      <>
                        <strong>{money(r.totals.total_cents, r.terms.currency)}</strong>
                        {r.totals.missing_cents ? (
                          <StatusBadge tone="warning">
                            Faltam {money(r.totals.missing_cents, r.terms.currency)}
                          </StatusBadge>
                        ) : (
                          <StatusBadge tone="success">Mínimo atingido</StatusBadge>
                        )}
                      </>
                    ),
                  },
                  {
                    id: "actions",
                    label: "Ações",
                    render: (r) => (
                      <div className="actions">
                        <ActionButton onClick={() => setSimId(r.id)}>
                          Abrir
                        </ActionButton>
                        {canBuy && r.status !== "cancelled" && (
                          <ActionButton
                            aria-label={`Editar ${r.procurement?.document || "simulação"} de ${r.supplier_name}`}
                            disabled={!editableSim(r)}
                            title={editableSim(r) ? undefined : "Já tem receções ou faturas: receba o que chegou e faça outra encomenda para o resto."}
                            onClick={() => open("simulation", structuredClone(r))}
                          >
                            <Pencil size={16} aria-hidden="true" />
                            Editar
                          </ActionButton>
                        )}
                      </div>
                    ),
                  },
                ]
              : [
                  {
                    id: "date",
                    label: "Data",
                    sortValue: (r) => r.at,
                    render: (r) => new Date(r.at).toLocaleString("pt-PT"),
                  },
                  { id: "action", label: "Operação", render: (r) => r.action },
                  { id: "user", label: "Responsável", render: (r) => r.user },
                  {
                    id: "details",
                    label: "Detalhe / contacto",
                    render: (r) => (
                      <>
                        {r.detail?.channel && (
                          <strong>
                            {r.detail.channel} · {r.status}
                          </strong>
                        )}
                        <div>
                          {r.detail?.message ||
                            r.detail?.reason ||
                            r.detail?.document ||
                            "Registo interno"}
                        </div>
                        {r.message_ids?.map((id: string) => (
                          <small key={id}>{id}</small>
                        ))}
                      </>
                    ),
                  },
                ]
      }
    />
  );

  if (!data && !error)
    return <LoadingState>A carregar encomendas…</LoadingState>;
  const outstanding = orders.filter((o) => !o.closed);
  const needs = supplierNeeds(pending, catalog.suppliers, catalog.articles, simulations);
  const lateOrders = new Set(
    pending
      .filter((l) => l.order.due_date && daysBetween(l.order.due_date, todayLisbon()) > 0)
      .map((l) => l.order.id),
  ).size;
  // «Prontos a levantar» substitui o antigo separador Levantamentos.
  const orderFilters: [string, string, Row[]][] = [
    ["abertos", "Em aberto", outstanding],
    ["prontos", "Disponíveis para levantar", orders.filter((o) => o.ready)],
    ["concluidos", "Concluídos", orders.filter((o) => o.closed)],
    ["todos", "Todos", orders],
  ];
  const simulationContent = (modal === "simulation" || modal === "terms") ? <>

                  {modal === "simulation" && draft.status === "prepared" && (
                    <Notice tone="warning">
                      Ao guardar, a encomenda {draft.procurement?.document} é atualizada com o mesmo
                      número e a versão anterior fica registada.
                      {draft.procurement?.email_history?.some((e: Row) => ["accepted", "received"].includes(e.status))
                        ? " Já foi enviada ao fornecedor: fica marcada para reenviar."
                        : ""}
                      {draft.supplier_confirmation ? " A confirmação do fornecedor deixa de valer (fica no histórico)." : ""}
                    </Notice>
                  )}
                  {modal === "simulation" && (
                    <>
                      <div className="co-grid">
                        <Field label="Estabelecimento">
                          <select
                            required
                            disabled={!!draft.id}
                            value={draft.store_id}
                            onChange={(e) =>
                              setDraft({
                                ...draft,
                                store_id: e.target.value,
                                lines: [],
                              })
                            }
                          >
                            {stores.map((s) => (
                              <option key={s.id} value={s.id}>
                                {s.name}
                              </option>
                            ))}
                          </select>
                        </Field>
                        <SupplierField
                          label="Fornecedor"
                          suppliers={catalog.suppliers}
                          value={draft.supplier_id}
                          disabled={!!draft.id}
                          onChange={(id) => {
                            if (id === draft.supplier_id) return;
                            const s = catalog.suppliers.find(
                              (s: Row) => s.id === id,
                            );
                            setDraft({
                              ...draft,
                              supplier_id: id,
                              terms: structuredClone(s?.terms || initialTerms()),
                              lines: [],
                            });
                            setPreview(null);
                          }}
                          onEdit={editSupplier}
                        />
                      </div>
                      {draft.supplier_id && (
                        <details className="co-supplier-conditions"><summary>Consultar condições da ficha do fornecedor</summary><TermsSummary terms={draft.terms} onEdit={() => editSupplier(draft.supplier_id)} /></details>
                      )}
                      {draft.lines.map((l: Row, i: number) => (
                        <fieldset key={i} className="co-product">
                          <legend>Produto de compra {i + 1}</legend>
                          <Field label="Artigo de reposição">
                            <ArticleSearch
                              articles={catalog.articles}
                              value={l.article_id}
                              onPick={(a) => {
                                if (!l.allocations.length)
                                  editLine(
                                    i,
                                    a
                                      ? {
                                          article_id: a.id,
                                          description: a.name,
                                          unit_cost:
                                            draft.terms.prices?.[a.id] ??
                                            a.last_cost ??
                                            "0",
                                          vat: a.vat_rate ?? null,
                                        }
                                      : { article_id: null },
                                  );
                              }}
                            />
                          </Field>
                          <div className="co-grid">
                            <Field label="Descrição">
                              <input
                                required
                                value={l.description}
                                onChange={(e) =>
                                  editLine(i, { description: e.target.value })
                                }
                              />
                            </Field>
                            <Field label="Quantidade de compra">
                              <input
                                required
                                type="number"
                                min="0.000001"
                                step="0.000001"
                                value={l.quantity}
                                onChange={(e) => {
                                  let remaining = Number(e.target.value);
                                  const allocations = l.allocations
                                    .map((a: Row) => {
                                      const q = Math.min(
                                        remaining,
                                        Number(a.quantity),
                                      );
                                      remaining -= q;
                                      return {
                                        ...a,
                                        quantity: String(Number(q.toFixed(6))),
                                      };
                                    })
                                    .filter((a: Row) => Number(a.quantity) > 0);
                                  editLine(i, {
                                    quantity: e.target.value,
                                    allocations,
                                  });
                                }}
                              />
                            </Field>
                            <Field label="Preço de custo unitário">
                              <input
                                required
                                type="number"
                                min="0"
                                step="0.000001"
                                value={l.unit_cost}
                                onChange={(e) =>
                                  editLine(i, { unit_cost: e.target.value })
                                }
                              />
                            </Field>
                            <Field label="Desconto da linha (%) — vazio: fornecedor">
                              <input
                                type="number"
                                min="0"
                                max="100"
                                step="0.0001"
                                value={l.discount ?? ""}
                                onChange={(e) =>
                                  editLine(i, {
                                    discount:
                                      e.target.value === ""
                                        ? null
                                        : e.target.value,
                                  })
                                }
                              />
                            </Field>
                            <Field label="IVA da linha (%) — vazio: fornecedor">
                              <input
                                type="number"
                                min="0"
                                max="100"
                                step="0.001"
                                value={l.vat ?? ""}
                                onChange={(e) =>
                                  editLine(i, {
                                    vat:
                                      e.target.value === ""
                                        ? null
                                        : e.target.value,
                                  })
                                }
                              />
                            </Field>
                            <Field label="Quantidade de oferta — vazio: bonificação">
                              <input
                                type="number"
                                min="0"
                                step="any"
                                value={l.bonus_quantity ?? ""}
                                placeholder={`Automática: ${qty(bonusOffer(catalog.suppliers.find((x: Row) => x.id === draft.supplier_id)?.bonuses?.[l.article_id], Number(l.quantity)))}`}
                                onChange={(e) =>
                                  editLine(i, {
                                    bonus_quantity:
                                      e.target.value === ""
                                        ? null
                                        : e.target.value,
                                  })
                                }
                              />
                            </Field>
                          </div>
                          <BonusHint
                            bonus={catalog.suppliers.find((x: Row) => x.id === draft.supplier_id)?.bonuses?.[l.article_id]}
                            quantity={Number(l.quantity)}
                            onApply={(quantity) => editLine(i, { quantity: String(quantity), bonus_quantity: null })}
                          />
                          <p>
                            Destinado a clientes:{" "}
                            {qty(
                              l.allocations.reduce(
                                (n: number, a: Row) => n + Number(a.quantity),
                                0,
                              ),
                            )}{" "}
                            · Reposição adicional:{" "}
                            {qty(
                              Number(l.quantity) -
                                l.allocations.reduce(
                                  (n: number, a: Row) => n + Number(a.quantity),
                                  0,
                                ),
                            )}
                          </p>
                          <ActionButton
                            type="button"
                            onClick={() => {
                              setDraft({
                                ...draft,
                                lines: draft.lines.filter(
                                  (_: Row, n: number) => n !== i,
                                ),
                              });
                              setPreview(null);
                            }}
                          >
                            Remover linha de compra
                          </ActionButton>
                        </fieldset>
                      ))}
                      <ActionButton
                        type="button"
                        onClick={() =>
                          setDraft({
                            ...draft,
                            lines: [
                              ...draft.lines,
                              {
                                article_id: null,
                                description: "",
                                quantity: "1",
                                unit_cost: "0",
                                discount: null,
                                vat: null,
                                allocations: [],
                              },
                            ],
                          })
                        }
                      >
                        Adicionar reposição de stock
                      </ActionButton>
                      <details>
                        <summary>
                          Pedidos pendentes deste fornecedor ({supplierPending.length})
                        </summary>
                        {draft.supplier_id && !supplierPending.length && (
                          <p className="footnote">
                            Sem pedidos de clientes por encomendar a este
                            fornecedor nesta loja.
                          </p>
                        )}
                        {supplierPending.map((l) => (
                            <label key={l.id} className="doc-check">
                              <input
                                type="checkbox"
                                checked={checks.includes(l.id)}
                                onChange={(e) =>
                                  setChecks(
                                    e.target.checked
                                      ? [...checks, l.id]
                                      : checks.filter((id) => id !== l.id),
                                  )
                                }
                              />
                              {l.order.customer_name} · {l.description} ·{" "}
                              {qty(l.quantities.to_order)}
                            </label>
                          ))}
                        <div className="actions">
                          <ActionButton
                            type="button"
                            disabled={!checks.length}
                            onClick={() => addPending()}
                          >
                            Adicionar pedidos selecionados
                          </ActionButton>
                          <ActionButton
                            type="button"
                            disabled={!supplierPending.length}
                            onClick={() => addPending(true)}
                          >
                            Adicionar todos ({supplierPending.length})
                          </ActionButton>
                        </div>
                      </details>
                      <LowStockSuggestions
                        storeId={draft.store_id}
                        supplierId={draft.supplier_id}
                        defaultPeriod={Number(draft.terms?.sales_period_days) || 90}
                        lines={draft.lines}
                        onAdd={addReplenishment}
                      />

                    </>
                  )}
                  {modal === "simulation" ? (
                    <details className="co-terms-adjust">
                      <summary>Ajustar condições só nesta simulação</summary>
                      <p className="footnote">
                        Por omissão usam-se as condições da ficha do fornecedor (Tabelas → Fornecedores →
                        Condições comerciais). O que mudar aqui vale só para esta simulação.
                      </p>
                      <TermsFields
                        articles={catalog.articles}
                        value={draft.terms}
                        change={(terms) => {
                          setDraft({ ...draft, terms });
                          setPreview(null);
                        }}
                      />
                    </details>
                  ) : (
                    <TermsFields
                      articles={catalog.articles}
                      value={draft.terms}
                      change={(terms) => {
                        setDraft({ ...draft, terms });
                        setPreview(null);
                      }}
                    />
                  )}
                  {modal === "simulation" && (
                    <>
                      <Field label="Observações da compra">
                        <textarea
                          value={draft.notes}
                          onChange={(e) =>
                            setDraft({ ...draft, notes: e.target.value })
                          }
                        />
                      </Field>
                      <ActionButton
                        type="button"
                        disabled={!draft.lines.length || !draft.supplier_id}
                        onClick={() =>
                          void run(async () =>
                            setPreview(
                              await api(
                                "/customer-orders/simulations/preview",
                                {
                                  store_id: draft.store_id,
                                  supplier_id: draft.supplier_id,
                                  terms: draft.terms,
                                  lines: draft.lines,
                                  notes: draft.notes,
                                  revision: draft.revision || 0,
                                  request_key: crypto.randomUUID(),
                                },
                                { preserveDraft: true },
                              ),
                            ),
                          )
                        }
                      >
                        Calcular simulação
                      </ActionButton>
                      <aside className="co-simulator-summary" aria-label="Resumo da simulação">
                      <h3>Resumo da simulação</h3>
                      {!preview && <p className="footnote">Preencha os artigos e as quantidades para calcular mínimo, portes, IVA e total.</p>}
                      {preview && (
                        <>
                        <SimulationTotals
                          totals={preview}
                          terms={draft.terms}
                        />
                        <ActionButton type="button" disabled={scenarios.length >= 3} onClick={() => setScenarios(items => [...items, { ...preview, name: `Cenário ${items.length + 1}` }])}>Guardar cenário para comparar</ActionButton>
                        </>
                      )}
                      {previewError && <Notice tone="error">{previewError}</Notice>}
                      {scenarios.length > 0 && <section aria-label="Comparação de cenários" className="co-totals">
                        <h3>Comparar cenários desta simulação</h3>
                        <div className="co-grid">{scenarios.map((scenario, i) => <div key={i}><strong>{scenario.name}</strong><p>{money(scenario.total_cents, scenario.currency)} total</p><p>{scenario.missing_cents ? `Faltam ${money(scenario.missing_cents, scenario.currency)} para o mínimo` : "Mínimo atingido"}</p><ActionButton type="button" onClick={() => setScenarios(items => items.filter((_, n) => n !== i))}>Retirar cenário {i + 1}</ActionButton></div>)}</div>
                        <small>Comparação temporária de valores. Guarde o rascunho para conservar a encomenda escolhida.</small>
                      </section>}
                      </aside>
                    </>
                  )}
                </> : null;
  return (
    <section className="customer-orders">
      {(area !== "centro" || !canBuy) && <PageHeader
        title={area === "centro" ? "Do pedido à entrega, num só lugar." : area === "planeamento" ? "Planeamento de compras" : "Encomendas de clientes"}
        description={area === "centro" ? "Necessidades líquidas por cliente, artigo e fornecedor." : area === "planeamento" ? "Compare necessidades, cobertura de stock e simulações por fornecedor." : "Do pedido ao fornecedor e ao levantamento, com quantidades e contactos acompanhados."}
        actions={
          <>
            {(area !== "planeamento" || planningTab === "simulacoes") && <ActionButton disabled={busy} onClick={() => void run(load)}>
              <RefreshCw size={16} />
              Atualizar
            </ActionButton>}
            {data?.can_write && area !== "planeamento" && (
              <ActionButton className="primary" onClick={newOrder}>
                <Plus size={16} />
                Novo pedido
              </ActionButton>
            )}
          </>
        }
      />}
      {feedback}
      {(area !== "centro" || !canBuy) && <ModuleTabs
        label="Áreas das encomendas de clientes"
        tabs={tabs.map(([id, label]) => ({ id, label }))}
        value={area}
        onChange={setArea}
      />}
      {(area !== "centro" || !canBuy) && <p className="footnote">{data?.integration_note}</p>}
      {area === "planeamento" && <ModuleTabs label="Planeamento de compras" tabs={[{ id: "necessidades", label: "Necessidades" }, { id: "simulacoes", label: "Simulações" }]} value={planningTab} onChange={setPlanningTab} />}
      {area === "planeamento" && planningTab === "necessidades" && (canBuy ? <SupplyCenter planning stores={stores} store={store} revision={data}
        orders={orders} simulations={simulations} receipts={receipts} onArea={setArea} onSimulation={setSimId} onStoreChange={onStoreChange}
        onReadyOrders={() => navigate("Encomendas de clientes", { "menu.area": "encomendas", "menu.destino": "encomendas:notificacoes", "area.pedidos-clientes": "pedidos", "filtro.pedidos": "prontos" })}
        onNewOrder={newOrder} onNewSimulation={newSim} onRefreshData={() => void run(load)} onOrder={setSelected}
        onSimulate={group => open("simulation", { store_id: group.store_id, supplier_id: group.supplier_id, terms: structuredClone(group.terms), lines: structuredClone(group.lines), notes: "" })}
      /> : <Notice>O planeamento de compras requer autorização para consultar custos e preparar compras.</Notice>)}
      {area === "centro" && (canBuy ? <SupplyCenter stores={stores} store={store} revision={data}
        orders={orders} simulations={simulations} receipts={receipts} onArea={setArea} onSimulation={setSimId} onStoreChange={onStoreChange}
        onReadyOrders={() => navigate("Encomendas de clientes", {"menu.area":"encomendas","menu.destino":"encomendas:notificacoes","area.pedidos-clientes":"pedidos","filtro.pedidos":"prontos"})}
        onNewOrder={newOrder} onNewSimulation={newSim} onRefreshData={() => void run(load)}
        onOrder={setSelected}
        onSimulate={group => open("simulation", { store_id: group.store_id, supplier_id: group.supplier_id,
          terms: structuredClone(group.terms), lines: structuredClone(group.lines), notes: "" })}
      /> : <Notice>O Centro de Encomendas requer autorização para consultar custos e preparar compras. Os pedidos continuam disponíveis nos restantes separadores.</Notice>)}
      {area === "pedidos" && (
        <>
          <div className="co-filters" role="group" aria-label="Filtrar pedidos">
            {orderFilters.map(([id, label, rows]) => (
              <ActionButton
                key={id}
                aria-pressed={orderFilter === id}
                onClick={() => setOrderFilter(id)}
              >
                {label} ({rows.length})
              </ActionButton>
            ))}
          </div>
          <details className="panel co-attention">
            <summary>Precisa de atenção</summary>
            {!outstanding.length && <p>Sem pedidos pendentes.</p>}
            {outstanding.map((o) => {
              const age = Math.floor(
                (Date.now() - new Date(o.requested_at).getTime()) / 86400000,
              );
              const limits = o.lines.map(
                (l: Row) =>
                  catalog.suppliers.find((s: Row) => s.id === l.supplier_id)
                    ?.terms?.pending_days || 7,
              );
              const warnings = [
                o.late && "Entrega em atraso",
                o.ready &&
                  !o.notified &&
                  "Produto disponível; cliente por contactar",
                o.lines.some(
                  (l: Row) =>
                    !l.supplier_id && Number(l.quantities.to_order) > 0,
                ) && "Produto sem fornecedor",
                age >= Math.min(...limits) && "Pedido pendente há vários dias",
                o.ready &&
                  o.notified &&
                  age >=
                    Math.max(
                      ...o.lines.map(
                        (l: Row) =>
                          catalog.suppliers.find(
                            (s: Row) => s.id === l.supplier_id,
                          )?.terms?.pickup_days || 7,
                      ),
                    ) &&
                  "Cliente notificado; confirmar levantamento",
              ].filter(Boolean);
              return warnings.length ? (
                <div className="co-alert" key={o.id}>
                  <div>
                    <strong>{o.customer_name}</strong>
                    <p>{warnings.join(" · ")}</p>
                  </div>
                  <ActionButton onClick={() => setSelected(o.id)}>
                    Abrir pedido
                  </ActionButton>
                </div>
              ) : null;
            })}
            {simulations
              .filter(
                (s) =>
                  s.status !== "cancelled" &&
                  (s.totals.missing_cents > 0 ||
                    (s.procurement_id &&
                      !s.supplier_confirmation &&
                      Date.now() - new Date(s.created_at).getTime() >
                        (s.terms.confirmation_days || 3) * 86400000)),
              )
              .map((s) => (
                <div className="co-alert" key={s.id}>
                  <p>
                    {s.supplier_name} ·{" "}
                    {s.totals.missing_cents
                      ? `Encomenda abaixo do mínimo: faltam ${money(s.totals.missing_cents, s.terms.currency)}`
                      : "Fornecedor ainda não confirmou"}
                  </p>
                  <ActionButton onClick={() => setSimId(s.id)}>
                    Abrir simulação
                  </ActionButton>
                </div>
              ))}
          </details>
          {display(
            orderFilters.find(([id]) => id === orderFilter)?.[2] || orders,
            "orders",
          )}
        </>
      )}
      {area === "fornecedores" && (
        <>
          {canBuy ? (
            <DataTable<Row>
              label="condições dos fornecedores"
              rows={catalog.suppliers}
              rowKey={(r) => r.id}
              searchText={(r) => `${r.name} ${r.tax_number || ""} ${r.email || ""}`}
              columns={[
                { id: "name", label: "Fornecedor", render: (r) => r.name },
                {
                  id: "minimum",
                  label: "Mínimo",
                  render: (r) => money(r.terms.minimum_cents, r.terms.currency),
                },
                {
                  id: "lead",
                  label: "Prazo",
                  render: (r) => `${r.terms.lead_days} dias`,
                },
                {
                  id: "needs",
                  label: "Por encomendar",
                  sortValue: (r) => needs.filter((g) => g.supplier_id === r.id).reduce((n, g) => n + g.lines.length, 0),
                  render: (r) => {
                    const mine = needs.filter((g) => g.supplier_id === r.id);
                    const lines = mine.reduce((n, g) => n + g.lines.length, 0);
                    return lines ? (
                      <StatusBadge tone={mine.some((g) => g.state === "pronto") ? "success" : "warning"}>
                        {lines} produto(s)
                      </StatusBadge>
                    ) : (
                      <span className="co-muted">—</span>
                    );
                  },
                },
                {
                  id: "actions",
                  label: "Ações",
                  render: (r) => (
                    <div className="actions">
                      <ActionButton
                        onClick={() =>
                          open("terms", {
                            supplier_id: r.id,
                            revision: r.revision,
                            terms: structuredClone(r.terms),
                          })
                        }
                      >
                        Condições comerciais
                      </ActionButton>
                      <ActionButton
                        aria-label={`Editar fornecedor ${r.name}`}
                        onClick={() => editSupplier(r.id)}
                      >
                        <Pencil size={16} aria-hidden="true" />
                        Editar ficha
                      </ActionButton>
                    </div>
                  ),
                },
              ]}
            />
          ) : (
            <Notice>
              As condições comerciais e custos estão reservados à gestão
              autorizada.
            </Notice>
          )}
          <div className="actions">
            <ActionButton onClick={() => navigate("Fornecedores")}>
              Gerir fornecedores
            </ActionButton>
          </div>
        </>
      )}
      {(area === "simulacoes" || area === "planeamento" && planningTab === "simulacoes") && (
        <>
          {canBuy ? (
            <>
              <section className="co-section" aria-labelledby="co-needed-title">
                <div className="co-inline">
                  <h2 id="co-needed-title">Encomendas necessárias para pedidos de clientes</h2>
                  <ActionButton className="primary" onClick={newSim}>
                    <Plus size={16} aria-hidden="true" />
                    Nova simulação
                  </ActionButton>
                </div>
                {needs.length > 0 && (
                  <div className="co-need-summary" aria-label="Resumo das encomendas necessárias">
                    <StatusBadge tone="info">{needs.filter((g) => g.supplier_id).length} fornecedor(es)</StatusBadge>
                    <StatusBadge tone="info">{new Set(pending.map((l) => l.order.id)).size} pedido(s) de clientes</StatusBadge>
                    {lateOrders > 0 && <StatusBadge tone="danger">{lateOrders} pedido(s) em atraso</StatusBadge>}
                    {needs.some((g) => !g.supplier_id) && (
                      <StatusBadge tone="danger">Produtos sem fornecedor</StatusBadge>
                    )}
                    {needs.filter((g) => g.state === "pronto").length > 0 && (
                      <StatusBadge tone="success">
                        {needs.filter((g) => g.state === "pronto").length} pronta(s) a encomendar
                      </StatusBadge>
                    )}
                  </div>
                )}
                <p className="footnote">
                  O que ainda falta encomendar, por fornecedor e loja; produtos já em simulações ou
                  encomendas não aparecem. Valor estimado sem IVA e antes de descontos, comparado com o
                  mínimo de encomenda sem portes. Vermelho: atraso ou sem fornecedor; âmbar: abaixo do
                  mínimo ou sem custo; verde: pronta a encomendar.
                </p>
                {!needs.length ? (
                  <Notice tone="success">Todos os pedidos de clientes já estão em simulações ou encomendas.</Notice>
                ) : (
                  <DataTable<Row>
                    label="encomendas necessárias por fornecedor"
                    rows={needs}
                    rowKey={(g) => g.key}
                    rowClassName={(g) => `co-need-row co-need-row-${needTone(g)}`}
                    searchText={(g) => `${g.supplier?.name || "Sem fornecedor"} ${g.lines.map((l: Row) => `${l.description} ${l.order.customer_name} ${l.order.number}`).join(" ")}`}
                    columns={[
                      {
                        id: "supplier",
                        label: "Fornecedor",
                        sortValue: (g) => g.supplier?.name || "",
                        render: (g) => (
                          <div className="co-need-supplier">
                            {g.supplier_id ? (
                              <span className="co-inline-supplier">
                                <strong>{g.supplier?.name || supplierName(g.supplier_id)}</strong>
                                <ActionButton
                                  className="co-icon"
                                  aria-label={`Editar fornecedor ${g.supplier?.name || supplierName(g.supplier_id)}`}
                                  title="Editar a ficha do fornecedor"
                                  onClick={() => editSupplier(g.supplier_id)}
                                >
                                  <Pencil size={14} aria-hidden="true" />
                                </ActionButton>
                              </span>
                            ) : (
                              <StatusBadge tone="danger">Sem fornecedor</StatusBadge>
                            )}
                            <small>{stores.find((x) => x.id === g.store_id)?.name || ""}</small>
                            {g.supplier?.missing?.length > 0 && <MissingSupplierFields supplier={g.supplier} />}
                          </div>
                        ),
                      },
                      {
                        id: "products",
                        label: "Produtos a encomendar",
                        sortValue: (g) => g.lines.length,
                        render: (g) => (
                          <>
                            {g.lines.map((l: Row) => (
                              <div key={l.id} className="co-need-line">
                                <span className="co-qty" aria-label={`Quantidade ${qty(l.quantities.to_order)}`}>
                                  {qty(l.quantities.to_order)}
                                </span>
                                <span>
                                  <b>{l.description}</b>
                                  <small title={l.order.number}>
                                    {shortNumber(l.order.number)} · {l.order.customer_name} · pedido em {day(l.order.requested_at)}
                                  </small>
                                </span>
                              </div>
                            ))}
                          </>
                        ),
                      },
                      {
                        id: "value",
                        label: "Valor / mínimo sem portes",
                        sortValue: (g) => g.value_cents,
                        render: (g) =>
                          g.supplier_id ? (
                            <div className="co-need-value">
                              <strong className="co-amount">{money(g.value_cents)}</strong>
                              {g.minimum_cents > 0 && (
                                <progress
                                  max={g.minimum_cents}
                                  value={Math.min(g.value_cents, g.minimum_cents)}
                                  aria-label={`Valor face ao mínimo de ${money(g.minimum_cents)}`}
                                />
                              )}
                              <small>{g.minimum_cents ? `de ${money(g.minimum_cents)} mínimo` : "sem mínimo"}</small>
                              <StatusBadge tone={g.state === "pronto" ? "success" : "warning"}>
                                {g.state === "pronto"
                                  ? g.minimum_cents ? "Mínimo atingido" : "Pronto a encomendar"
                                  : g.state === "sem_custo"
                                    ? "Sem custo conhecido"
                                    : `Faltam ${money(g.missing_cents)}`}
                              </StatusBadge>
                              {g.unpriced > 0 && (
                                <small className="co-text-warning">
                                  {g.unpriced} produto(s) sem custo: indique o preço na simulação
                                </small>
                              )}
                            </div>
                          ) : (
                            <small>Associe um fornecedor no pedido.</small>
                          ),
                      },
                      {
                        id: "urgency",
                        label: "Entrega ao cliente",
                        sortValue: (g) => String(g.oldest),
                        render: (g) => (
                          <>
                            {[...new Map<string, Row>(g.lines.map((l: Row) => [l.order.id, l.order] as [string, Row])).values()].map((o: Row) => (
                              <div key={o.id} className="co-due">
                                <DueBadge order={o} />
                                {o.priority === "urgent" && <StatusBadge tone="danger">Urgente</StatusBadge>}
                                <small>
                                  {shortNumber(o.number)}{o.due_date ? ` · entrega pretendida ${day(o.due_date)}` : ""}
                                </small>
                              </div>
                            ))}
                            {g.drafts.length > 0 && <StatusBadge tone="info">{g.drafts.length} rascunho(s)</StatusBadge>}
                          </>
                        ),
                      },
                      {
                        id: "actions",
                        label: "Ações",
                        render: (g) => (
                          <div className="co-need-actions">
                            {g.supplier_id ? (
                              <ActionButton className="primary" onClick={() => simulateFor(g)}>
                                <Calculator size={16} aria-hidden="true" />
                                Simular encomenda
                              </ActionButton>
                            ) : (
                              <ActionButton onClick={() => setSelected(g.lines[0].order.id)}>
                                <Pencil size={16} aria-hidden="true" />
                                Associar no pedido
                              </ActionButton>
                            )}
                            {g.drafts[0] && (
                              <ActionButton onClick={() => setSimId(g.drafts[0].id)}>
                                <FileText size={16} aria-hidden="true" />
                                Abrir rascunho
                              </ActionButton>
                            )}
                          </div>
                        ),
                      },
                    ]}
                  />
                )}
              </section>
              <section className="co-section" aria-labelledby="co-supplier-orders-title">
                <h2 id="co-supplier-orders-title">Simulações e encomendas a fornecedores</h2>
                {display(simulations, "simulations")}
              </section>
            </>
          ) : (
            <Notice>
              As simulações de compra estão reservadas à gestão autorizada.
            </Notice>
          )}
        </>
      )}
      {area === "rececoes" && (
        <>
          {canBuy ? (
            <>
              {display(
                simulations.filter(
                  (s) =>
                    s.procurement_id && s.procurement?.status !== "cancelled",
                ),
                "simulations",
              )}
              <p>
                Abra a encomenda para registar quantidades efetivamente
                recebidas ou associar uma receção já confirmada.
              </p>
              <ActionButton onClick={() => navigate("Receções de mercadoria")}>
                Circuito de receções de mercadoria
              </ActionButton>
            </>
          ) : (
            <Notice>As receções estão reservadas à gestão autorizada.</Notice>
          )}
        </>
      )}
      {area === "rececoes" && canBuy && (
        <details>
          <summary>Originais, OCR e cloud</summary>
          <OrderCyclePanel
            mode="receiving"
            store={store}
            stores={stores}
            simulations={simulations}
            catalog={catalog}
            navigate={navigate}
          />
        </details>
      )}
      {area === "rececoes" && canBuy && (
        <section className="co-section" aria-labelledby="co-conference-title">
          <h2 id="co-conference-title">Conferência com o ERP</h2>
          <OrderCyclePanel
            mode="review"
            store={store}
            stores={stores}
            simulations={simulations}
            catalog={catalog}
            navigate={navigate}
          />
        </section>
      )}
      {area === "historico" && display(history, "history")}
      {area === "analise" && (canBuy ? <OrderAnalytics store={store} revision={data} onOrder={setSelected} onSimulation={setSimId} navigate={navigate} /> : <Notice>Os indicadores de compras requerem autorização para consultar custos.</Notice>)}

      {active && <AppDialog className="business-record-dialog co-record-dialog" aria-label={`Pedido ${active.number}`} busy={busy} onRequestClose={() => setSelected("")}>
        <RecordWorkspace key={active.id} title={`Pedido ${active.number}`} subtitle={active.customer_name} icon={ShoppingCart}
          actions={data?.can_write && <ActionButton onClick={() => editOrder(active)}><Pencil size={16} />Editar pedido</ActionButton>}
          indicators={<>
            <RecordIndicator label="Artigos solicitados" value={active.lines.length} icon={Package} />
            <RecordIndicator label="Linhas disponíveis" value={active.lines.filter((l: Row) => Number(l.quantities.available) > 0).length} tone="success" icon={Package} />
            <RecordIndicator label="Por encomendar" value={active.lines.filter((l: Row) => Number(l.quantities.to_order) > 0).length} note="Número de linhas" tone="warning" icon={Truck} />
            <RecordIndicator label="Artigos por criar" value={active.lines.filter((l: Row) => l.provisional).length} tone="warning" icon={CircleAlert} />
          </>}
          sections={[
            { label: "Geral", icon: User, content: <RecordGroup title="Resumo do pedido" icon={ShoppingCart}>          <div className="co-flags">
            {active.closed ? (
              <StatusBadge tone="success">Concluído</StatusBadge>
            ) : active.ready ? (
              <StatusBadge tone="success">
                {active.fully_ready === false ? "Disponível parcialmente: combinar entrega" : active.notified ? "Cliente notificado" : "Pronto: contactar cliente"}
              </StatusBadge>
            ) : (
              <StatusBadge tone="warning">A aguardar produtos</StatusBadge>
            )}
            {active.priority === "urgent" && <StatusBadge tone="danger">Urgente</StatusBadge>}
            {active.late && <StatusBadge tone="danger">Entrega em atraso</StatusBadge>}
            {active.lines.some(
              (l: Row) => !l.supplier_id && Number(l.quantities.to_order) > 0,
            ) && <StatusBadge tone="danger">Produto sem fornecedor</StatusBadge>}
            {active.lines.some((l: Row) => l.provisional) && (
              <StatusBadge tone="warning">Produto provisório</StatusBadge>
            )}
          </div>
          {active.ready && (
            <p className="co-highlight co-highlight-success">
              Valor estimado dos produtos disponíveis, sem IVA:{" "}
              {money(
                active.lines.reduce(
                  (total: number, line: Row) =>
                    total +
                    (line.sale_price_cents == null
                      ? 0
                      : Number(line.sale_price_cents) *
                        Number(line.quantities.available || 0)),
                  0,
                ),
              )}
              .{" "}
              {active.lines.some(
                (line: Row) =>
                  Number(line.quantities.available) > 0 &&
                  line.sale_price_cents == null,
              )
                ? "Existem preços por confirmar. "
                : ""}
              O pagamento e o documento fiscal seguem o circuito de faturação.
            </p>
          )}
          <StockAvailability
            storeId={active.store_id}
            articleIds={active.lines
              .map((line: Row) => line.article_id)
              .filter(Boolean)}
          />
          <ProcessSteps label="Progresso da encomenda" current={active.closed ? "fecho" : active.notified ? "entrega" : active.fully_ready ? "aviso" : active.ready ? "rececao" : active.lines.some((line: Row)=>Number(line.quantities.ordered)>0) ? "fornecedor" : active.lines.some((line: Row)=>Number(line.quantities.simulated)>0) ? "simulacao" : "necessidades"}
            steps={[{id:"pedido",label:"Pedido"},{id:"necessidades",label:"Necessidades"},{id:"simulacao",label:"Simulação"},{id:"fornecedor",label:"Fornecedor"},{id:"rececao",label:"Receção"},{id:"aviso",label:"Aviso"},{id:"entrega",label:"Entrega"},{id:"fecho",label:"Fecho"}]} />
          <dl className="co-facts">
            <div>
              <dt>Pedido em</dt>
              <dd>{day(active.requested_at)}</dd>
            </div>
            <div>
              <dt>Entrega prevista</dt>
              <dd className={active.late ? "co-text-danger" : undefined}>
                {day(active.due_date)}
              </dd>
            </div>
            <div>
              <dt>Telefone</dt>
              <dd>{active.phone || "—"}</dd>
            </div>
            <div>
              <dt>Email</dt>
              <dd>{active.email || "—"}</dd>
            </div>
            <div>
              <dt>Contacto</dt>
              <dd>
                {active.contact_allowed ? (
                  <StatusBadge tone="success">
                    Autorizado · {channelLabel(active.preferred_channel)}
                  </StatusBadge>
                ) : (
                  <StatusBadge tone="warning">Não autorizado</StatusBadge>
                )}
              </dd>
            </div>
          </dl>
          {active.notes && <p className="co-notes">{active.notes}</p>}
          {feedback}
</RecordGroup> },
            { label: "Artigos e disponibilidade", icon: Package, content: <>          {active.lines.map((l: Row) => (
            <section className="panel co-product" key={l.id}>
              <div className="co-need-head">
                <h3>{l.description}</h3>
                <StatusBadge tone={stateTone(l.state)}>{l.state_label}</StatusBadge>
              </div>
              <p>
                {l.provisional ? (
                  <StatusBadge tone="warning">Provisório: associe um artigo</StatusBadge>
                ) : (
                  l.article_code
                )}{" "}
                ·{" "}
                {l.supplier_id ? (
                  supplierName(l.supplier_id)
                ) : (
                  <StatusBadge tone="danger">Sem fornecedor</StatusBadge>
                )}
              </p>
              <dl className="co-quantities">
                {[
                  ["requested", "Pedida"],
                  ["reserved", "Reservada"],
                  ["available", "Disponível válida"],
                  ["to_order", "Por encomendar"],
                  ["simulated", "Em simulação"],
                  ["ordered", "Encomendada"],
                  ["confirmed", "Confirmada"],
                  ["received", "Recebida"],
                  ["delivered", "Entregue"],
                  ["cancelled", "Cancelada"],
                ].map(([key, label]) => {
                  const value = Number(l.quantities[key] || 0);
                  // Só se destaca o que pede ação: por encomendar (âmbar) e pronto a entregar (verde).
                  const tone =
                    !value || key === "requested"
                      ? ""
                      : key === "to_order"
                        ? "warning"
                        : key === "available"
                          ? "success"
                          : key === "cancelled"
                            ? "muted"
                            : "";
                  return (
                    <div key={key} className={tone ? `co-q-${tone}` : undefined}>
                      <dt>{label}</dt>
                      <dd>{qty(value)}</dd>
                    </div>
                  );
                })}
              </dl>
              <p>{l.notes}</p>
              {data?.can_write && (
                <div className="actions">
                  <ActionButton
                    onClick={() =>
                      open("quantity", {
                        line_id: l.id,
                        order_id: active.id,
                        revision: active.revision,
                        action: "adjust",
                        quantity: l.quantities.requested,
                        reason: "",
                      })
                    }
                  >
                    Alterar quantidade pedida
                  </ActionButton>
                  {(!l.article_id || !l.supplier_id) && (
                    <ActionButton
                      onClick={() =>
                        open("associate", {
                          line_id: l.id,
                          revision: active.revision,
                          article_id: l.article_id || "",
                          supplier_id: l.supplier_id || "",
                        })
                      }
                    >
                      Associar artigo / fornecedor
                    </ActionButton>
                  )}
                  {l.article_id && Number(l.quantities.to_order) > 0 && (
                    <ActionButton
                      onClick={() =>
                        open("quantity", {
                          line_id: l.id,
                          order_id: active.id,
                          revision: active.revision,
                          action: "reserve",
                          quantity: l.quantities.to_order,
                          warehouse_id:
                            warehouses(active.store_id)[0]?.id || "",
                          reason: "Reserva para o cliente",
                        })
                      }
                    >
                      Reservar stock
                    </ActionButton>
                  )}
                  {Number(l.quantities.reserved) > 0 && (
                    <ActionButton
                      onClick={() =>
                        open("quantity", {
                          line_id: l.id,
                          order_id: active.id,
                          revision: active.revision,
                          action: "release",
                          quantity: l.quantities.reserved,
                          reason: "",
                        })
                      }
                    >
                      Libertar reserva
                    </ActionButton>
                  )}
                  {Number(l.quantities.available) > 0 && (
                    <ActionButton
                      className="primary"
                      onClick={() =>
                        open("quantity", {
                          line_id: l.id,
                          order_id: active.id,
                          revision: active.revision,
                          action: "deliver",
                          quantity: l.quantities.available,
                          reason: "Levantamento no balcão",
                        })
                      }
                    >
                      Registar entrega
                    </ActionButton>
                  )}
                  {Number(l.quantities.requested) >
                    Number(l.quantities.delivered) +
                      Number(l.quantities.cancelled) && (
                    <ActionButton
                      className="danger"
                      onClick={() =>
                        open("quantity", {
                          line_id: l.id,
                          order_id: active.id,
                          revision: active.revision,
                          action: "cancel",
                          quantity: String(
                            Number(l.quantities.requested) -
                              Number(l.quantities.delivered) -
                              Number(l.quantities.cancelled),
                          ),
                          reason: "",
                        })
                      }
                    >
                      Cancelar quantidade
                    </ActionButton>
                  )}
                </div>
              )}
            </section>
          ))}
</> },
            { label: "Contacto e levantamento", icon: Phone, content: <RecordGroup title="Contacto e levantamento" icon={Phone}><p>{active.fully_ready ? "Encomenda pronta para levantamento." : active.ready ? "Existem artigos disponíveis para entrega parcial." : "A aguardar disponibilidade dos artigos."}</p><p>{active.notified ? "Cliente notificado." : "Cliente ainda não notificado."}</p><p>{active.phone || "Sem telefone"} · {active.email || "Sem email"}</p>          {active.ready && data?.can_write && (
            <ActionButton onClick={() => contactOrder(active)}>
              Preparar contacto ao cliente
            </ActionButton>
          )}
</RecordGroup> },
            { label: "Histórico", icon: History, content: <RecordGroup title="Histórico" icon={History}>          <h3>Histórico deste pedido</h3>
          {history
            .filter((h) => h.order_id === active.id)
            .map((h) => (
              <p key={h.id}>
                {day(h.at)} · {h.user} · {h.action}{" "}
                {h.detail?.channel && `· ${h.detail.channel} · ${h.status}`}
              </p>
            ))}
</RecordGroup> },
          ]} />
      </AppDialog>}

      {activeSim && <AppDialog className="business-record-dialog co-record-dialog" aria-label="Detalhe da simulação" busy={busy} onRequestClose={() => setSimId("")}>
        <RecordWorkspace key={activeSim.id} title={activeSim.procurement?.document ? `Encomenda ${activeSim.procurement.document}` : "Simulação de compra"} subtitle={activeSim.supplier_name} icon={Truck}
          indicators={<>
            <RecordIndicator label="Total da simulação" value={activeSim.totals?.total_cents == null ? "—" : money(activeSim.totals.total_cents, activeSim.terms.currency)} icon={Calculator} />
            <RecordIndicator label="Falta para o mínimo" value={activeSim.totals?.missing_cents == null ? "—" : money(activeSim.totals.missing_cents, activeSim.terms.currency)} tone={activeSim.totals?.missing_cents ? "warning" : "success"} icon={ShoppingCart} />
            <RecordIndicator label="Linhas de compra" value={activeSim.lines.length} icon={Package} />
          </>}
          sections={[
            { label: "Geral", icon: Truck, content: <RecordGroup title="Condições e valores" icon={Calculator}>          <div className="co-flags">
            <SimulationState sim={activeSim} />
          </div>
          {feedback}
          <SimulationTotals totals={activeSim.totals} terms={activeSim.terms} />
</RecordGroup> },
            { label: "Artigos e clientes", icon: Package, content: <>          {activeSim.lines.map((l: Row, i: number) => (
            <section className="co-product" key={i}>
              <h3>{l.description}</h3>
              <p>
                {qty(l.quantity)} ·{" "}
                {money(
                  Math.round(Number(l.unit_cost) * 100),
                  activeSim.terms.currency,
                )}{" "}
                por unidade
              </p>
              <p>
                Clientes:{" "}
                {qty(
                  l.allocations.reduce(
                    (n: number, a: Row) => n + Number(a.quantity),
                    0,
                  ),
                )}{" "}
                · Reposição:{" "}
                {qty(
                  Number(l.quantity) -
                    l.allocations.reduce(
                      (n: number, a: Row) => n + Number(a.quantity),
                      0,
                    ),
                )}
              </p>
              {l.allocations.map((a: Row) => {
                const o = orders.find((o) =>
                  o.lines.some((line: Row) => line.id === a.line_id),
                );
                return (
                  <p key={a.line_id}>
                    {o?.customer_name || "Pedido"} · {qty(a.quantity)}{" "}
                    <ActionButton
                      onClick={() => {
                        setSimId("");
                        if (o) setSelected(o.id);
                      }}
                    >
                      Pedido original
                    </ActionButton>
                  </p>
                );
              })}
            </section>
          ))}
</> },
            { label: "Confirmação e receções", icon: ShoppingCart, content: <RecordGroup title="Operações da encomenda" icon={Truck}>          <div className="actions">
            {activeSim.status === "draft" && (
              <>
                <ActionButton
                  onClick={() => open("simulation", structuredClone(activeSim))}
                >
                  Editar simulação
                </ActionButton>
                <ActionButton
                  className="primary"
                  onClick={() => void prepareSim(activeSim)}
                >
                  Preparar encomenda real
                </ActionButton>
                <ActionButton
                  className="danger"
                  onClick={() =>
                    void run(async () => {
                      if (
                        !(await confirmAction({
                          title: "Cancelar simulação?",
                          message:
                            "As quantidades deixam de estar atribuídas a esta proposta. Não cancela compras reais.",
                          confirmLabel: "Cancelar simulação",
                          tone: "danger",
                        }))
                      )
                        return;
                      await mutate(
                        `/customer-orders/simulations/${activeSim.id}/cancel`,
                        { revision: activeSim.revision },
                      );
                      await load();
                    })
                  }
                >
                  Cancelar simulação
                </ActionButton>
              </>
            )}
            {activeSim.procurement_id &&
              activeSim.procurement?.status !== "cancelled" && (
                <>
                  {editableSim(activeSim) && (
                    <ActionButton onClick={() => open("simulation", structuredClone(activeSim))}>
                      <Pencil size={16} aria-hidden="true" />
                      Editar encomenda
                    </ActionButton>
                  )}
                  <ActionLink
                    href={`/api/procurement/${activeSim.procurement_id}/pdf`}
                    target="_blank"
                    rel="noopener noreferrer"
                  >
                    <FileText size={16} />
                    PDF da encomenda
                  </ActionLink>
                  <ActionButton onClick={() => supplierEmail(activeSim)}>
                    <Mail size={16} />
                    Preparar email ao fornecedor
                  </ActionButton>
                  <ActionButton
                    onClick={() =>
                      open("supplier-confirm", {
                        simulation_id: activeSim.id,
                        revision: activeSim.revision,
                        quantities: activeSim.lines.map((l: Row) => l.quantity),
                        note: "",
                      })
                    }
                  >
                    Registar confirmação do fornecedor
                  </ActionButton>
                  <ActionButton
                    className="primary"
                    onClick={() =>
                      open("receive", {
                        simulation_id: activeSim.id,
                        revision: activeSim.revision,
                        document: "",
                        receipt_id: "",
                        quantities: activeSim.lines.map(() => "0"),
                        lots: activeSim.lines.map(() => ""),
                        expiries: activeSim.lines.map(() => ""),
                      })
                    }
                  >
                    <Package size={16} />
                    Receber produtos
                  </ActionButton>
                </>
              )}
          </div>
          {activeSim.supplier_confirmation && (
            <Notice tone="success">
              Fornecedor confirmou em {day(activeSim.supplier_confirmation.at)}:{" "}
              {activeSim.supplier_confirmation.note}
            </Notice>
          )}
</RecordGroup> },
            { label: "Histórico", icon: History, content: <RecordGroup title="Comunicações com o fornecedor" icon={History}>          {activeSim.procurement?.email_history?.map((e: Row) => (
            <p key={e.id}>
              Email · {e.status} · {day(e.created_at)}
            </p>
          ))}
{!activeSim.procurement?.email_history?.length && <p>Sem envios registados.</p>}</RecordGroup> },
          ]} />
      </AppDialog>}

      {modal && (
        <AppDialog
          aria-label={
            {
              order: draft.id ? "Editar pedido de cliente" : "Novo pedido de cliente",
              simulation: "Simulador de compra",
              terms: "Condições comerciais",
              prepare: "Preparar encomenda real",
              quantity: "Atualizar quantidade",
              associate: "Associar artigo ao pedido",
              contact: "Contactar cliente",
              "supplier-email": "Email ao fornecedor",
              "supplier-confirm": "Confirmação do fornecedor",
              receive: "Receção de produtos",
            }[modal]
          }
          className={modal === "order" ? "co-dialog business-record-dialog" : modal === "simulation" ? "co-dialog co-simulation-dialog business-record-dialog" : "co-dialog"}
          busy={busy}
          onRequestClose={() => setModal("")}
        >
          {feedback}
          <ManagedForm
            onSubmit={(e) => {
              e.preventDefault();
              void run(async () => {
                if (modal === "order") await saveOrder();
                else if (modal === "simulation") await saveSim();
                else if (modal === "terms") {
                  await mutate(
                    `/customer-orders/suppliers/${draft.supplier_id}/terms`,
                    { revision: draft.revision, terms: draft.terms },
                  );
                  await load();
                  setModal("");
                } else if (modal === "associate") {
                  await mutate(
                    `/customer-orders/lines/${draft.line_id}/associate`,
                    {
                      revision: draft.revision,
                      article_id: draft.article_id,
                      supplier_id: draft.supplier_id || null,
                    },
                  );
                  await load();
                  setModal("");
                } else if (modal === "quantity") {
                  if (
                    draft.action !== "reserve" &&
                    !(await confirmAction({
                      title:
                        draft.action === "deliver"
                          ? "Confirmar entrega física?"
                          : draft.action === "adjust"
                            ? "Alterar quantidade pedida?"
                            : draft.action === "release"
                              ? "Libertar reserva?"
                              : "Cancelar quantidade?",
                      message: `${qty(draft.quantity)} unidade(s). ${draft.reason}`,
                      confirmLabel: "Confirmar",
                      tone: draft.action === "cancel" ? "danger" : "warning",
                    }))
                  )
                    return;
                  await mutate(
                    `/customer-orders/lines/${draft.line_id}/${draft.action}`,
                    {
                      revision: draft.revision,
                      quantity: draft.quantity,
                      warehouse_id: draft.warehouse_id || null,
                      reason: draft.reason,
                    },
                  );
                  await load();
                  setModal("");
                } else if (modal === "prepare") {
                  if (
                    !(await confirmAction({
                      title: "Criar encomenda real?",
                      message:
                        "Será criada uma encomenda no circuito de compras. Não envia emails nem confirma o fornecedor.",
                      confirmLabel: "Criar encomenda",
                    }))
                  )
                    return;
                  await mutate(
                    `/customer-orders/simulations/${draft.simulation_id}/prepare`,
                    {
                      revision: draft.revision,
                      warehouse_id: draft.warehouse_id,
                      confirmed: true,
                      allow_below_minimum: draft.allow_below_minimum,
                    },
                  );
                  await load();
                  setModal("");
                } else if (modal === "supplier-confirm") {
                  await mutate(
                    `/customer-orders/simulations/${draft.simulation_id}/supplier-confirm`,
                    {
                      revision: draft.revision,
                      quantities: draft.quantities,
                      note: draft.note,
                    },
                  );
                  await load();
                  setModal("");
                } else if (modal === "receive") {
                  if (
                    !(await confirmAction({
                      title: "Confirmar receção física?",
                      message: draft.receipt_id
                        ? "A receção existente será distribuída pelos clientes sem repetir a entrada de stock."
                        : "As quantidades indicadas serão registadas no stock local e reservadas aos pedidos relacionados.",
                      confirmLabel: "Confirmar receção",
                    }))
                  )
                    return;
                  await mutate(
                    `/customer-orders/simulations/${draft.simulation_id}/receive`,
                    {
                      revision: draft.revision,
                      confirmed: true,
                      receipt_id: draft.receipt_id || null,
                      document: draft.document,
                      quantities: draft.quantities,
                      lots: draft.lots,
                      expiries: draft.expiries.map((v: string) => v || null),
                    },
                  );
                  await load();
                  setModal("");
                } else if (modal === "supplier-email") {
                  const result = await api(
                    `/procurement/${draft.procurement_id}/email-preview`,
                    {
                      revision: draft.revision,
                      to: draft.to,
                      subject: draft.subject,
                      message: draft.message,
                    },
                  );
                  setMail(result);
                  await load();
                  setDraft({ ...draft, revision: draft.revision + 1 });
                } else if (modal === "contact") {
                  const result = await mutate(
                    `/customer-orders/${draft.order_id}/contact`,
                    {
                      revision: draft.revision,
                      channel: draft.channel,
                      subject: draft.subject,
                      message: draft.message,
                    },
                  );
                  setContact(result);
                  setMail(result.email);
                  await load();
                  setDraft({ ...draft, revision: draft.revision + 1 });
                }
              });
            }}
          >
            <fieldset disabled={busy || !!mail || !!contact}>
              {modal === "order" && <RecordWorkspace key={draft.id || "new-order"}
                title={draft.id ? `Editar pedido ${draft.number}` : "Novo pedido de cliente"} subtitle={draft.customer_name || "Encomenda de cliente"} icon={ShoppingCart}
                actions={<ActionButton type="submit" className="record-save" variant="primary" disabled={busy}><Save size={16} />{draft.id ? "Guardar alterações" : "Guardar"}</ActionButton>}
                indicators={<><RecordIndicator label="Artigos no pedido" value={draft.lines.length} icon={Package} /><RecordIndicator label="Por associar a artigo" value={draft.lines.filter((l: Row) => !l.article_id).length} tone="warning" icon={CircleAlert} /></>}
                sections={[
                  { label: "Geral e contactos", icon: User, content: <RecordGroup title="Cliente e condições do pedido" icon={User}>                  {draft.id && (
                    <Notice tone="info">
                      Produtos já reservados, em compra ou entregues mantêm o
                      artigo e não podem descer abaixo da quantidade em curso.
                      Retirar um produto cancela-o e fica no histórico.
                      {draft.closed_lines > 0 &&
                        ` ${draft.closed_lines} produto(s) entregue(s) ou cancelado(s) não aparecem aqui.`}
                    </Notice>
                  )}
                  <div className="co-grid">
                    <Field label="Estabelecimento">
                      <select
                        required
                        disabled={!!draft.id}
                        value={draft.store_id}
                        onChange={(e) =>
                          setDraft({ ...draft, store_id: e.target.value })
                        }
                      >
                        {stores.map((s) => (
                          <option key={s.id} value={s.id}>
                            {s.name}
                          </option>
                        ))}
                      </select>
                    </Field>
                    <Field label="Cliente existente (opcional)">
                      <CustomerPicker
                        customers={catalog.customers}
                        value={draft.customer_id}
                        onPick={(c) => {
                          setDraft({
                            ...draft,
                            customer_id: c?.id || "",
                            ...(c
                              ? {
                                  customer_name: c.name,
                                  phone: c.phone,
                                  email: c.email,
                                }
                              : {}),
                          });
                        }}
                      />
                    </Field>
                    {[
                      ["customer_name", "Nome do cliente", "text"],
                      ["phone", "Telefone (com indicativo)", "tel"],
                      ["email", "Email", "email"],
                      [
                        "requested_at",
                        "Data e hora do pedido",
                        "datetime-local",
                      ],
                      ["due_date", "Entrega pretendida", "date"],
                    ].map(([key, label, type]) => (
                      <Field key={key} label={label}>
                        <input
                          type={type}
                          required={
                            key === "customer_name" || key === "requested_at"
                          }
                          value={draft[key]}
                          onChange={(e) =>
                            setDraft({ ...draft, [key]: e.target.value })
                          }
                        />
                      </Field>
                    ))}
                    <Field label="Canal preferido">
                      <select
                        value={draft.preferred_channel}
                        onChange={(e) =>
                          setDraft({
                            ...draft,
                            preferred_channel: e.target.value,
                          })
                        }
                      >
                        <option value="none">Sem contacto autorizado</option>
                        <option value="email">Email</option>
                        <option value="whatsapp">WhatsApp</option>
                        <option value="phone">Telefone</option>
                      </select>
                    </Field>
                    <Field label="Prioridade">
                      <select
                        value={draft.priority}
                        onChange={(e) =>
                          setDraft({ ...draft, priority: e.target.value })
                        }
                      >
                        <option value="normal">Normal</option>
                        <option value="urgent">Urgente</option>
                      </select>
                    </Field>
                  </div>
                  <label className="doc-check">
                    <input
                      type="checkbox"
                      checked={draft.contact_allowed}
                      onChange={(e) =>
                        setDraft({
                          ...draft,
                          contact_allowed: e.target.checked,
                        })
                      }
                    />
                    Cliente autorizou contactos sobre este pedido pelo canal
                    escolhido
                  </label>
                  <Field label="Observações do pedido">
                    <textarea
                      value={draft.notes}
                      onChange={(e) =>
                        setDraft({ ...draft, notes: e.target.value })
                      }
                    />
                  </Field>
</RecordGroup> },
                  { label: "Artigos", icon: Package, content: <RecordGroup title="Artigos solicitados" icon={Package}>                  {draft.lines.map((l: Row, i: number) => (
                    <fieldset
                      className={`co-product${l.committed > 0 ? " co-product-locked" : ""}`}
                      key={l.key}
                    >
                      <legend>Produto {i + 1}</legend>
                      {l.id && (
                        <div className="co-flags">
                          <StatusBadge tone={stateTone(l.state)}>{l.state_label}</StatusBadge>
                          {l.committed > 0 && (
                            <StatusBadge tone="info">
                              {qty(l.committed)} em curso: artigo fixo, mínimo {qty(l.committed)}
                            </StatusBadge>
                          )}
                        </div>
                      )}
                      <Field label="Artigo existente">
                        <ArticleSearch
                          articles={catalog.articles}
                          disabled={l.committed > 0}
                          value={l.article_id}
                          onPick={(a) =>
                            editLine(
                              i,
                              a
                                ? {
                                    article_id: a.id,
                                    description: a.name,
                                    supplier_id: a.supplier_id || "",
                                    brand: a.brand || "",
                                    reference: a.reference || "",
                                    ean: a.barcodes?.[0] || "",
                                    sale_price_cents: a.sale_net
                                      ? Math.round(Number(a.sale_net) * 100)
                                      : null,
                                  }
                                : { article_id: "" },
                            )
                          }
                        />
                      </Field>
                      {!l.article_id && (
                        <p className="footnote">
                          Produto provisório: preencha a descrição e os dados
                          conhecidos. Não cria um artigo no ERP.
                        </p>
                      )}
                      {!(l.committed > 0) && <ERPCatalogPicker
                        canAttach={!!catalog.permissions?.adjust_stock}
                        onSelect={(a) => {
                          editLine(i, {
                            article_id: a.id,
                            description: a.name,
                            supplier_id: a.supplier_id || l.supplier_id || "",
                          });
                          setCatalog((c) => ({
                            ...c,
                            articles: [
                              ...c.articles.filter(
                                (item: Row) => item.id !== a.id,
                              ),
                              a,
                            ],
                          }));
                        }}
                      />}
                      <div className="co-grid">
                        {[
                          ["description", "Descrição"],
                          ["brand", "Marca"],
                          ["reference", "Referência"],
                          ["ean", "EAN"],
                        ].map(([key, label]) => (
                          <Field key={key} label={label}>
                            <input
                              required={key === "description"}
                              value={l[key]}
                              onChange={(e) =>
                                editLine(i, { [key]: e.target.value })
                              }
                            />
                          </Field>
                        ))}
                        <Field label={l.committed > 0 ? `Quantidade pedida (mínimo ${qty(l.committed)})` : "Quantidade pedida"}>
                          <input
                            required
                            type="number"
                            min={l.committed > 0 ? String(l.committed) : "0.000001"}
                            step="0.000001"
                            value={l.quantity}
                            onChange={(e) =>
                              editLine(i, { quantity: e.target.value })
                            }
                          />
                        </Field>
                        <Field label="Preço de venda sem IVA (quando conhecido)">
                          <input
                            type="number"
                            min="0"
                            step="0.01"
                            value={
                              l.sale_price_cents === null
                                ? ""
                                : l.sale_price_cents / 100
                            }
                            onChange={(e) =>
                              editLine(i, {
                                sale_price_cents:
                                  e.target.value === ""
                                    ? null
                                    : Math.round(Number(e.target.value) * 100),
                              })
                            }
                          />
                        </Field>
                        <SupplierField
                          label="Fornecedor preferencial"
                          suppliers={catalog.suppliers}
                          value={l.supplier_id}
                          onChange={(id) => editLine(i, { supplier_id: id })}
                          onEdit={editSupplier}
                        />
                        <Field label="Notas do produto">
                          <input
                            value={l.notes}
                            onChange={(e) =>
                              editLine(i, { notes: e.target.value })
                            }
                          />
                        </Field>
                      </div>
                      <ActionButton
                        type="button"
                        className="danger"
                        disabled={draft.lines.length === 1 || l.committed > 0}
                        title={
                          l.committed > 0
                            ? "Em curso: use «Cancelar quantidade» no detalhe do pedido."
                            : undefined
                        }
                        onClick={() =>
                          setDraft({
                            ...draft,
                            lines: draft.lines.filter(
                              (_: Row, n: number) => n !== i,
                            ),
                          })
                        }
                      >
                        Remover produto
                      </ActionButton>
                    </fieldset>
                  ))}
                  <ActionButton
                    type="button"
                    onClick={() =>
                      setDraft({
                        ...draft,
                        lines: [...draft.lines, blankLine()],
                      })
                    }
                  >
                    Adicionar produto
                  </ActionButton>
                  {draft.id ? (
                    (draft.original_ids || []).some(
                      (id: string) => !draft.lines.some((l: Row) => l.id === id),
                    ) && (
                      <>
                        <Notice tone="warning">
                          Os produtos retirados ficam cancelados no pedido.
                        </Notice>
                        <Field label="Motivo para retirar produtos">
                          <input
                            required
                            minLength={3}
                            value={draft.reason}
                            onChange={(e) =>
                              setDraft({ ...draft, reason: e.target.value })
                            }
                          />
                        </Field>
                      </>
                    )
                  ) : (
                    <label className="doc-check">
                      <input
                        type="checkbox"
                        checked={!!draft.allow_duplicate}
                        onChange={(e) =>
                          setDraft({
                            ...draft,
                            allow_duplicate: e.target.checked,
                          })
                        }
                      />
                      Confirmo a criação de outro pedido igual, caso já exista
                      um em aberto
                    </label>
                  )}
</RecordGroup> },
                ]} />}
              {(modal === "simulation" || modal === "terms") && (modal === "simulation" ? <RecordWorkspace key={draft.id || "new-simulation"}
                title={draft.status === "prepared" ? `Editar encomenda ${draft.procurement?.document || ""}` : draft.id ? "Editar simulação" : "Simulador de compra"}
                subtitle={supplierName(draft.supplier_id)} icon={Truck}
                actions={<ActionButton type="submit" variant="primary" className="record-save" disabled={busy || !draft.lines.length}><Save size={16} />{draft.status === "prepared" ? "Guardar alterações à encomenda" : "Guardar simulação"}</ActionButton>}
                indicators={<>
                  <RecordIndicator label="Total previsto" value={preview?.total_cents == null ? "Por calcular" : money(preview.total_cents, draft.terms.currency)} icon={Calculator} />
                  <RecordIndicator label="Mínimo da encomenda" value={money(draft.terms.minimum_cents, draft.terms.currency)} icon={ShoppingCart} />
                  <RecordIndicator label="Falta para portes grátis" value={preview?.shipping_missing_cents == null ? "—" : money(preview.shipping_missing_cents, draft.terms.currency)} tone="warning" icon={Truck} />
                </>}
                sections={[
                  { label: "Simulação e artigos", icon: Calculator, content: <div className="co-simulator-layout">{simulationContent}</div> },
                  { label: "Clientes associados", icon: User, content: <RecordGroup title="Necessidades dos clientes" icon={User}>{draft.lines.flatMap((l: Row, i: number) => l.allocations.map((a: Row) => { const order = orders.find(o => o.lines.some((line: Row) => line.id === a.line_id)); return <p key={`${i}-${a.line_id}`}>{order?.number || "Pedido associado"} · {order?.customer_name || "Cliente"} · {l.description} · {qty(a.quantity)}</p>; }))}{!draft.lines.some((l: Row) => l.allocations.length) && <p>Sem quantidades atribuídas a pedidos de clientes.</p>}</RecordGroup> },
                  { label: "Receções e histórico", icon: History, content: <RecordGroup title="Acompanhamento" icon={History}>{draft.procurement ? <><p>Documento: {draft.procurement.document}</p>{draft.procurement.lines?.map((l: Row, i: number) => <p key={i}>{l.description || `Linha ${i + 1}`} · Recebida: {qty(l.received)} · Faturada: {qty(l.invoiced)}</p>)}{draft.procurement.email_history?.map((e: Row) => <p key={e.id}>{day(e.created_at)} · {e.status}</p>)}</> : <p>Rascunho de simulação: ainda sem encomenda real ou receções.</p>}</RecordGroup> },
                ]} /> : <><h2>Condições comerciais do fornecedor</h2>{simulationContent}</>)}
              {modal === "associate" && (
                <>
                  <h2>Associar produto e fornecedor</h2>
                  <Field label="Artigo real">
                    <ArticleSearch
                      articles={catalog.articles}
                      value={draft.article_id}
                      onPick={(a) =>
                        setDraft({ ...draft, article_id: a?.id || "" })
                      }
                    />
                  </Field>
                  <SupplierField
                    label="Fornecedor do produto"
                    suppliers={catalog.suppliers}
                    value={draft.supplier_id}
                    onChange={(id) => setDraft({ ...draft, supplier_id: id })}
                    onEdit={editSupplier}
                  />
                  <p>
                    O registo provisório e o histórico são preservados. Não cria
                    artigos no ERP.
                  </p>
                </>
              )}
              {modal === "quantity" && (
                <>
                  <h2>
                    {draft.action === "reserve"
                      ? "Reservar unidades disponíveis"
                      : draft.action === "deliver"
                        ? "Registar entrega ao cliente"
                        : draft.action === "adjust"
                          ? "Alterar quantidade pedida"
                          : draft.action === "release"
                            ? "Libertar reserva"
                            : "Cancelar quantidade pendente"}
                  </h2>
                  <Field label="Quantidade">
                    <input
                      required
                      type="number"
                      min="0.000001"
                      step="0.000001"
                      value={draft.quantity}
                      onChange={(e) =>
                        setDraft({ ...draft, quantity: e.target.value })
                      }
                    />
                  </Field>
                  {draft.action === "reserve" && (
                    <Field label="Armazém">
                      <select
                        required
                        value={draft.warehouse_id}
                        onChange={(e) =>
                          setDraft({ ...draft, warehouse_id: e.target.value })
                        }
                      >
                        {warehouses(
                          orders.find((o) => o.id === draft.order_id)?.store_id,
                        ).map((w: Row) => (
                          <option key={w.id} value={w.id}>
                            {w.name}
                          </option>
                        ))}
                      </select>
                    </Field>
                  )}
                  <Field label="Motivo / referência">
                    <textarea
                      required={draft.action !== "reserve"}
                      minLength={3}
                      value={draft.reason}
                      onChange={(e) =>
                        setDraft({ ...draft, reason: e.target.value })
                      }
                    />
                  </Field>
                  <Notice
                    tone={
                      draft.action === "cancel"
                        ? "warning"
                        : draft.action === "deliver"
                          ? "info"
                          : undefined
                    }
                  >
                    {draft.action === "deliver"
                      ? "A entrega física local dá baixa de stock. Não emite fatura nem cria recebimentos financeiros."
                      : draft.action === "cancel"
                        ? "A quantidade cancelada deixa de estar pendente e liberta reservas e atribuições a compras. Os produtos já entregues e o histórico mantêm-se."
                        : "Os produtos já entregues e o histórico mantêm-se."}
                  </Notice>
                </>
              )}
              {modal === "prepare" && (
                <>
                  <h2>Preparar encomenda real</h2>
                  <Field label="Armazém de destino">
                    <select
                      required
                      value={draft.warehouse_id}
                      onChange={(e) =>
                        setDraft({ ...draft, warehouse_id: e.target.value })
                      }
                    >
                      {warehouses(
                        simulations.find((s) => s.id === draft.simulation_id)
                          ?.store_id,
                      ).map((w: Row) => (
                        <option key={w.id} value={w.id}>
                          {w.name}
                        </option>
                      ))}
                    </select>
                  </Field>
                  {(simulations.find((x) => x.id === draft.simulation_id)?.totals
                    ?.missing_cents || 0) > 0 && (
                    <Notice tone="warning">
                      Encomenda abaixo do mínimo do fornecedor: faltam{" "}
                      {money(
                        simulations.find((x) => x.id === draft.simulation_id)
                          ?.totals.missing_cents,
                      )}
                      . Só avança com a autorização abaixo.
                    </Notice>
                  )}
                  <label className="doc-check">
                    <input
                      type="checkbox"
                      checked={draft.allow_below_minimum}
                      onChange={(e) =>
                        setDraft({
                          ...draft,
                          allow_below_minimum: e.target.checked,
                        })
                      }
                    />
                    Autorizo preparar abaixo do mínimo, se aplicável
                  </label>
                  <p>
                    Reutiliza o circuito de compras. Não envia email nem
                    confirma a disponibilidade do fornecedor.
                  </p>
                </>
              )}
              {(modal === "supplier-confirm" || modal === "receive") && (
                <>
                  <h2>
                    {modal === "receive"
                      ? "Receber produtos"
                      : "Confirmação do fornecedor"}
                  </h2>
                  {modal === "receive" && (
                    <>
                      <Field label="Associar receção já confirmada">
                        <select
                          value={draft.receipt_id}
                          onChange={(e) =>
                            setDraft({ ...draft, receipt_id: e.target.value })
                          }
                        >
                          <option value="">Registar nova receção física</option>
                          {receipts
                            .filter((r) =>
                              r.lines.some(
                                (l: Row) =>
                                  l.source_id ===
                                  simulations.find(
                                    (s) => s.id === draft.simulation_id,
                                  )?.procurement_id,
                              ),
                            )
                            .map((r) => (
                              <option key={r.id} value={r.id}>
                                {r.document}
                              </option>
                            ))}
                        </select>
                      </Field>
                      {!draft.receipt_id && (
                        <Field label="Documento de compra ou guia">
                          <input
                            required
                            value={draft.document}
                            onChange={(e) =>
                              setDraft({ ...draft, document: e.target.value })
                            }
                          />
                        </Field>
                      )}
                    </>
                  )}
                  {!draft.receipt_id &&
                    simulations
                      .find((s) => s.id === draft.simulation_id)
                      ?.lines.map((l: Row, i: number) => (
                        <section className="co-product" key={i}>
                          <Field
                            label={`${l.description} — quantidade ${modal === "receive" ? "recebida" : "confirmada"}`}
                          >
                            <input
                              required
                              type="number"
                              min="0"
                              step="0.000001"
                              value={draft.quantities[i]}
                              onChange={(e) =>
                                setDraft({
                                  ...draft,
                                  quantities: draft.quantities.map(
                                    (q: string, n: number) =>
                                      n === i ? e.target.value : q,
                                  ),
                                })
                              }
                            />
                          </Field>
                          {modal === "receive" && (
                            <div className="co-grid">
                              <Field label="Lote (quando aplicável)">
                                <input
                                  value={draft.lots[i]}
                                  onChange={(e) =>
                                    setDraft({
                                      ...draft,
                                      lots: draft.lots.map(
                                        (v: string, n: number) =>
                                          n === i ? e.target.value : v,
                                      ),
                                    })
                                  }
                                />
                              </Field>
                              <Field label="Validade">
                                <input
                                  type="date"
                                  value={draft.expiries[i]}
                                  onChange={(e) =>
                                    setDraft({
                                      ...draft,
                                      expiries: draft.expiries.map(
                                        (v: string, n: number) =>
                                          n === i ? e.target.value : v,
                                      ),
                                    })
                                  }
                                />
                              </Field>
                            </div>
                          )}
                        </section>
                      ))}
                  {modal === "supplier-confirm" && (
                    <Field label="Referência da confirmação">
                      <textarea
                        required
                        minLength={3}
                        value={draft.note}
                        onChange={(e) =>
                          setDraft({ ...draft, note: e.target.value })
                        }
                      />
                    </Field>
                  )}
                  <p>
                    {modal === "receive"
                      ? "Registe apenas quantidades efetivamente recebidas. Uma receção existente não gera outro movimento de stock."
                      : "A confirmação comercial não regista receção nem torna o produto disponível."}
                  </p>
                </>
              )}
              {(modal === "contact" || modal === "supplier-email") && (
                <>
                  <h2>
                    {modal === "contact"
                      ? "Preparar contacto ao cliente"
                      : "Pré-visualizar email ao fornecedor"}
                  </h2>
                  {modal === "contact" ? (
                    <Field label="Canal de contacto">
                      <select
                        value={draft.channel}
                        onChange={(e) =>
                          setDraft({ ...draft, channel: e.target.value })
                        }
                      >
                        <option value="email">Email</option>
                        <option value="whatsapp">WhatsApp</option>
                        <option value="phone">Telefone</option>
                      </select>
                    </Field>
                  ) : (
                    <Field label="Email do fornecedor">
                      <input
                        required
                        type="email"
                        value={draft.to}
                        onChange={(e) =>
                          setDraft({ ...draft, to: e.target.value })
                        }
                      />
                    </Field>
                  )}
                  <Field label="Assunto">
                    <input
                      required
                      value={draft.subject}
                      onChange={(e) =>
                        setDraft({ ...draft, subject: e.target.value })
                      }
                    />
                  </Field>
                  <Field label="Mensagem preparada">
                    <textarea
                      required
                      rows={8}
                      value={draft.message}
                      onChange={(e) =>
                        setDraft({ ...draft, message: e.target.value })
                      }
                    />
                  </Field>
                  <p>
                    A preparação não envia mensagens. O envio SMTP exige
                    confirmação; abrir WhatsApp não confirma o envio.
                  </p>
                </>
              )}
              {modal !== "order" && modal !== "simulation" && <div className="workspace-savebar">
                <ActionButton
                  className="primary"
                  type="submit"
                  disabled={
                    busy ||
                    (modal === "simulation" && !draft.lines.length) ||
                    (modal === "associate" && !draft.article_id)
                  }
                >
                  {["contact", "supplier-email"].includes(modal)
                    ? "Preparar pré-visualização"
                    : modal === "receive"
                      ? "Confirmar receção"
                      : modal === "simulation"
                        ? draft.status === "prepared"
                          ? "Guardar alterações à encomenda"
                          : "Guardar simulação"
                        : modal === "order" && draft.id
                          ? "Guardar alterações"
                          : "Guardar"}
                </ActionButton>
              </div>}
            </fieldset>
          </ManagedForm>
          {mail && (
            <section className="panel">
              <h3>Email preparado · {mail.status}</h3>
              <p>Destinatário: {mail.draft.to.join(", ")}</p>
              <p>{mail.draft.subject}</p>
              <pre className="co-message">{mail.draft.message}</pre>
              <p>
                {mail.manifest.files.length} anexo(s) ·{" "}
                {mail.manifest.plan?.messages || 1} mensagem(ns)
              </p>
              <ActionButton
                className="primary"
                disabled={
                  busy ||
                  ["accepted", "received", "sending"].includes(mail.status)
                }
                onClick={() =>
                  void run(async () => {
                    if (
                      !(await confirmAction({
                        title: "Enviar este email?",
                        message: `Destinatário: ${mail.draft.to.join(", ")}. O envio será efetuado com a configuração SMTP guardada.`,
                        confirmLabel: "Enviar email",
                      }))
                    )
                      return;
                    const path =
                      modal === "contact"
                        ? `/customer-orders/${draft.order_id}/email/${mail.id}/send`
                        : `/procurement/${draft.procurement_id}/email-send/${mail.id}`;
                    const result = await mutate(path, {
                      revision: mail.revision,
                      confirmed: true,
                    });
                    setMail(result);
                    await load();
                  })
                }
              >
                <Send size={16} />
                Confirmar envio por email
              </ActionButton>
            </section>
          )}
          {contact && !mail && (
            <section className="panel">
              <h3>
                Contacto{" "}
                {contact.result === "confirmed"
                  ? "confirmado manualmente"
                  : "preparado"}
              </h3>
              <pre className="co-message">{contact.message}</pre>
              {contact.url && (
                <ActionLink
                  href={contact.url}
                  target="_blank"
                  rel="noopener noreferrer"
                >
                  Abrir WhatsApp com a mensagem
                </ActionLink>
              )}
              <p>A abertura da ligação não confirma o envio.</p>
              <ActionButton
                disabled={busy || contact.result === "confirmed"}
                onClick={() =>
                  void run(async () => {
                    if (
                      !(await confirmAction({
                        title: "Confirmar contacto realizado?",
                        message:
                          "Confirme apenas se enviou a mensagem ou contactou efetivamente o cliente.",
                        confirmLabel: "Confirmar contacto",
                      }))
                    )
                      return;
                    const result = await mutate(
                      `/customer-orders/${draft.order_id}/contact`,
                      {
                        revision: draft.revision,
                        channel: draft.channel,
                        subject: draft.subject,
                        message: draft.message,
                        result: "confirmed",
                        prepared_event_id: contact.event_id,
                      },
                    );
                    setContact(result);
                    await load();
                    setDraft({ ...draft, revision: draft.revision + 1 });
                  })
                }
              >
                Confirmar contacto realizado
              </ActionButton>
            </section>
          )}
        </AppDialog>
      )}
      {editingSupplier && (
        <RecordDialog
          table={editingSupplier.table}
          context={{
            stores: stores.map((s) => ({ id: s.id, name: s.name })),
            categories: editingSupplier.categories.map((c) => ({
              id: c.id,
              name: c.name,
              active: c.active,
            })),
          }}
          mode="edit"
          recordId={editingSupplier.recordId}
          onClose={() => setEditingSupplier(null)}
          onSaved={() => {
            setEditingSupplier(null);
            setNotice("Ficha do fornecedor atualizada.");
            void (async () => {
              await load();
              if (modal !== "simulation" || !draft.supplier_id) return;
              const fresh = (await api("/customer-orders/catalog")).suppliers.find(
                (x: Row) => x.id === draft.supplier_id,
              );
              if (fresh?.terms) {
                setDraft((d) => ({ ...d, terms: structuredClone(fresh.terms) }));
                setPreview(null);
              }
            })().catch((e) => setError(e.message));
          }}
        />
      )}
    </section>
  );
}
