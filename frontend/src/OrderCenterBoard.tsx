import OrderCenterSearch from "./OrderCenterSearch";
import { useEffect, useMemo, useRef, useState, type ReactNode } from "react";
import {
  Users,
  ShoppingCart,
  Truck,
  Package,
  CheckCircle,
  Clock,
  ArrowRight,
  ChevronRight,
  Eye,
  MoreHorizontal,
  Sparkles,
  Calculator,
  Bell,
  ClipboardList,
  BarChart3,
  Settings,
  Plus,
  ArrowDown,
  Leaf,
  Check,
  RefreshCw,
} from "lucide-react";
import { api } from "./lib/api";
import { ActionButton, Notice } from "./components/InteractionUI";
import { StatusBadge, type BusinessTone } from "./components/BusinessUI";
import { KpiCard, ProgressGoal, QuickFilters } from "./components/OverviewUI";
import { useLocationSearch } from "./components/WorkspaceUI";
import "./OrderCenterBoard.css";

type Row = Record<string, any>;
type Area =
  | "centro"
  | "pedidos"
  | "simulacoes"
  | "rececoes"
  | "fornecedores"
  | "historico"
  | "analise";
type Props = {
  data: Row | null;
  loading: boolean;
  error: string;
  store: string;
  stores: Row[];
  orders: Row[];
  simulations: Row[];
  receipts: Row[];
  onStore: (store: string) => void;
  onRefresh: () => void;
  onArea: (area: Area) => void;
  onOrder: (id: string) => void;
  onSimulate: (group: Row) => void;
  onSimulation: (id: string) => void;
  onNewOrder: () => void;
  onNewSimulation: () => void;
  onAdvanced: () => void;
  onReadyOrders: () => void;
};
const number = (value: unknown) =>
  value == null
    ? "—"
    : new Intl.NumberFormat("pt-PT", { maximumFractionDigits: 2 }).format(
        Number(value),
      );
const money = (value: number, currency = "EUR") =>
  new Intl.NumberFormat("pt-PT", { style: "currency", currency }).format(
    value / 100,
  );
const day = (value?: string) =>
  value
    ? new Date(value.slice(0, 10) + "T12:00:00").toLocaleDateString("pt-PT")
    : "—";
const normalize = (value: unknown) =>
  String(value || "")
    .normalize("NFD")
    .replace(/[\u0300-\u036f]/g, "")
    .toLocaleLowerCase("pt-PT");
function ready(order: Row) {
  return (
    !order.closed &&
    (order.fully_ready ??
      order.lines.every(
        (line: Row) =>
          Number(line.quantities.available) >=
          Number(line.quantities.requested) -
            Number(line.quantities.delivered) -
            Number(line.quantities.cancelled),
      ))
  );
}
function state(order: Row) {
  if (order.closed) return "closed";
  if (ready(order)) return "ready";
  if (order.ready) return "partial";
  return order.lines.some((line: Row) => Number(line.quantities.ordered) > 0)
    ? "stock"
    : "pending";
}
const stateLabels: Record<string, string> = {
  ready: "Pronta",
  partial: "Parcial",
  stock: "Aguarda stock",
  pending: "Por confirmar",
  closed: "Concluída",
};
const toneFor: Record<string, string> = {
  ready: "green",
  partial: "purple",
  stock: "amber",
  pending: "blue",
  closed: "green",
};
function Badge({
  tone = "blue",
  children,
}: {
  tone?: string;
  children: ReactNode;
}) {
  const tones: Record<string, BusinessTone> = {
    blue: "info",
    green: "success",
    amber: "warning",
    red: "danger",
    purple: "suggestion",
    teal: "accent",
  };
  return (
    <StatusBadge
      tone={tones[tone] || "neutral"}
      className={`ocb-badge ocb-${tone}`}
      compact
    >
      {children}
    </StatusBadge>
  );
}
function Empty({ children }: { children: ReactNode }) {
  return <p className="ocb-empty">{children}</p>;
}
function PanelHeading({
  icon: Icon,
  title,
  count,
  action,
}: {
  icon: typeof Users;
  title: string;
  count?: number;
  action?: ReactNode;
}) {
  return (
    <div className="ocb-panel-heading">
      <h2>
        <Icon size={18} aria-hidden="true" />
        {title}
        {count != null && <span className="ocb-count">{number(count)}</span>}
      </h2>
      {action}
    </div>
  );
}
function OpenButton({
  label,
  onClick,
  more = false,
}: {
  label: string;
  onClick: () => void;
  more?: boolean;
}) {
  return (
    <ActionButton
      inferAction={false}
      className="ocb-icon-button"
      aria-label={label}
      title={label}
      onClick={onClick}
    >
      {more ? <MoreHorizontal size={16} /> : <Eye size={15} />}
    </ActionButton>
  );
}

/** Order workspace matching the owner's visual reference; all business operations use existing dialogs. */
export default function OrderCenterBoard(props: Props) {
  const {
    data,
    store,
    orders: allOrders,
    simulations: allSimulations,
    receipts: allReceipts,
  } = props;
  const [orderFilter, setOrderFilter] = useState("all"),
    [suggestionMode, setSuggestionMode] = useState("rotation");
  const [supplierId, setSupplierId] = useState(""),
    [recommendations, setRecommendations] = useState<Row[]>([]);
  const [selected, setSelected] = useState<string[]>([]),
    [quantities, setQuantities] = useState<Record<string, string>>({}),
    [applied, setApplied] = useState<Row[]>([]);
  const [recommendationError, setRecommendationError] = useState(""),
    [recommendationLoading, setRecommendationLoading] = useState(false);
  const [preview, setPreview] = useState<Row | null>(null),
    [previewError, setPreviewError] = useState(""),
    [previewLoading, setPreviewLoading] = useState(false);
  const [period, setPeriod] = useState(30),
    [analytics, setAnalytics] = useState<Row | null>(null),
    [analyticsError, setAnalyticsError] = useState("");
  const reportPeriod = useMemo(() => {
    const end = new Intl.DateTimeFormat("en-CA", {
      timeZone: "Europe/Lisbon",
      year: "numeric",
      month: "2-digit",
      day: "2-digit",
    }).format(new Date());
    const start = new Date(end + "T12:00:00Z");
    start.setUTCDate(start.getUTCDate() - period + 1);
    return { start: start.toISOString().slice(0, 10), end };
  }, [period, data]);
  const suggestionsRef = useRef<HTMLElement>(null),
    notificationsRef = useRef<HTMLElement>(null);
  const terms = normalize(
    new URLSearchParams(useLocationSearch()).get("pesquisa.encomendas"),
  )
    .split(/\s+/)
    .filter(Boolean);
  const matches = (value: unknown) =>
    terms.every((term) => normalize(value).includes(term));
  const orders = allOrders.filter((order) => order.store_id === store);
  const openOrders = orders.filter((order) => !order.closed);
  const simulations = allSimulations.filter(
    (simulation) => simulation.store_id === store,
  );
  const receipts = allReceipts.filter((receipt) => receipt.store_id === store);
  const suppliers: Row[] = data?.suppliers || [];
  const articles: Row[] = data?.articles || [];
  const group = suppliers.find(
    (supplier) => supplier.supplier_id === supplierId,
  );
  const currency = group?.terms.currency || "EUR";
  useEffect(() => {
    if (!suppliers.some((supplier) => supplier.supplier_id === supplierId))
      setSupplierId(suppliers[0]?.supplier_id || "");
  }, [data, supplierId]);
  useEffect(() => {
    setSelected([]);
    setQuantities({});
    setApplied([]);
  }, [store, supplierId]);
  useEffect(() => {
    let active = true;
    setRecommendations([]);
    setRecommendationError("");
    if (!store || !supplierId) {
      setRecommendationLoading(false);
      return;
    }
    setRecommendationLoading(true);
    api(
      "/order-cycle/recommendations?" +
        new URLSearchParams({
          store_id: store,
          supplier_id: supplierId,
          period_days: "60",
        }),
    )
      .then((result) => {
        if (active) setRecommendations(result.items || []);
      })
      .catch((error) => {
        if (active) setRecommendationError(error.message);
      })
      .finally(() => {
        if (active) setRecommendationLoading(false);
      });
    return () => {
      active = false;
    };
  }, [store, supplierId, data]);
  const composition = useMemo<Row | null>(() => {
    if (!group) return null;
    const lines: Row[] = structuredClone(group.lines);
    for (const item of applied) {
      const existing = lines.find(
        (line) => line.article_id === item.article_id,
      );
      // Same rule as the existing simulator: proposals are target quantities, never duplicate customer demand.
      if (existing)
        existing.quantity = String(
          Math.max(Number(existing.quantity), Number(item.quantity)),
        );
      else
        lines.push({
          article_id: item.article_id,
          description: item.description,
          quantity: item.quantity,
          unit_cost: item.unit_cost,
          discount: null,
          vat: item.vat,
          allocations: [],
        });
    }
    return { ...group, lines };
  }, [group, applied]);
  const signature =
    composition && applied.length
      ? JSON.stringify({
          store_id: store,
          supplier_id: supplierId,
          terms: composition.terms,
          lines: composition.lines,
        })
      : "";
  useEffect(() => {
    let active = true;
    setPreview(null);
    setPreviewError("");
    setPreviewLoading(false);
    if (!signature) return;
    setPreviewLoading(true);
    const timer = setTimeout(() => {
      void api(
        "/customer-orders/simulations/preview",
        { ...JSON.parse(signature), request_key: crypto.randomUUID() },
        { preserveDraft: true },
      )
        .then((result) => {
          if (active) setPreview({ signature, totals: result });
        })
        .catch((error) => {
          if (active) setPreviewError(error.message);
        })
        .finally(() => {
          if (active) setPreviewLoading(false);
        });
    }, 200);
    return () => {
      active = false;
      clearTimeout(timer);
    };
  }, [signature]);
  useEffect(() => {
    let active = true;
    setAnalytics(null);
    setAnalyticsError("");
    if (!store) return;
    api(
      "/order-cycle/analytics?" +
        new URLSearchParams({
          store_id: store,
          ...reportPeriod,
        }),
    )
      .then((result) => {
        if (active && result.summary) setAnalytics(result);
      })
      .catch((error) => {
        if (active) setAnalyticsError(error.message);
      });
    return () => {
      active = false;
    };
  }, [store, reportPeriod]);
  const currentPreview =
    preview?.signature === signature ? preview.totals : null;
  const totals = applied.length ? currentPreview : group?.totals;
  const filterOrders = (value: string) => {
    setOrderFilter(value);
  };
  const orderRows = openOrders
    .filter(
      (order) =>
        (orderFilter === "all" ||
          state(order) === orderFilter ||
          (orderFilter === "late" && order.late)) &&
        matches(
          `${order.number} ${order.customer_name} ${order.lines.map((line: Row) => line.description).join(" ")}`,
        ),
    )
    .sort(
      (a, b) =>
        Number(b.late) - Number(a.late) ||
        Number(b.priority === "urgent") - Number(a.priority === "urgent") ||
        Date.parse(a.created_at) - Date.parse(b.created_at),
    );
  const supplierRows = suppliers.filter((supplier) =>
    matches(
      `${supplier.name} ${supplier.lines.map((line: Row) => line.description).join(" ")}`,
    ),
  );
  const articleRows = articles
    .filter(
      (article) =>
        Number(article.needed) > 0 &&
        matches(
          `${article.description} ${article.code} ${article.customers.map((customer: Row) => customer.customer).join(" ")}`,
        ),
    )
    .sort(
      (a, b) =>
        Number(b.urgent) - Number(a.urgent) ||
        Number(b.needed) - Number(a.needed),
    );
  const readyOrders = openOrders.filter((order) => order.ready);
  const transit = simulations.filter(
    (simulation) =>
      simulation.status === "prepared" &&
      simulation.procurement?.status !== "cancelled" &&
      simulation.procurement?.lines?.some(
        (line: Row) => Number(line.pending) > 0,
      ),
  );
  const suggestionRows = recommendations
    .filter(
      (item) =>
        Number(item.quantity) > 0 &&
        matches(item.description) &&
        (suggestionMode !== "low" || item.below_minimum) &&
        (suggestionMode !== "history" || Number(item.period_sales) > 0) &&
        (suggestionMode !== "seasonal" ||
          Number(item.policy?.seasonal_factor || 1) !== 1),
    )
    .sort((a, b) => Number(b.daily_sales) - Number(a.daily_sales));
  const knownCosts = (item: Row) =>
    Number(item.unit_cost) > 0 &&
    Number.isFinite(Number(quantities[item.article_id] ?? item.quantity)) &&
    Number(quantities[item.article_id] ?? item.quantity) > 0;
  const addSelected = () =>
    setApplied(
      recommendations
        .filter(
          (item) => selected.includes(item.article_id) && knownCosts(item),
        )
        .map((item) => ({
          ...item,
          quantity: quantities[item.article_id] ?? item.quantity,
        })),
    );
  const canSimulate =
    !!composition &&
    !composition.blocked &&
    !previewLoading &&
    !previewError &&
    (!applied.length || !!currentPreview);
  const openSimulation = () => {
    if (canSimulate && composition) props.onSimulate(composition);
  };
  const receiptRows = receipts.filter((receipt) =>
    matches(`${receipt.document} ${receipt.supplier_name}`),
  );
  const summaryPurchases = simulations.filter((simulation) => {
    const at = new Date(simulation.created_at);
    if (Number.isNaN(at.getTime())) return false;
    const created = new Intl.DateTimeFormat("en-CA", {
      timeZone: "Europe/Lisbon",
      year: "numeric",
      month: "2-digit",
      day: "2-digit",
    }).format(at);
    return (
      simulation.status === "prepared" &&
      simulation.procurement?.status !== "cancelled" &&
      created >= reportPeriod.start &&
      created <= reportPeriod.end
    );
  });
  const purchaseTotals = summaryPurchases.reduce(
    (result: Record<string, number>, simulation: Row) => {
      const currency = simulation.terms?.currency || "EUR";
      result[currency] =
        (result[currency] || 0) + Number(simulation.totals?.total_cents || 0);
      return result;
    },
    {},
  );
  const purchaseCurrencies = Object.entries(purchaseTotals) as [
    string,
    number,
  ][];
  const metrics = [
    {
      label: "Encomendas de clientes",
      value: data ? openOrders.length : null,
      detail: `${openOrders.filter((order) => order.priority === "urgent").length} urgentes`,
      tone: "blue",
      icon: Users,
      click: () => filterOrders("all"),
    },
    {
      label: "Artigos por encomendar",
      value: data?.metrics.needs_to_buy,
      detail: `${new Set(articles.flatMap((article) => article.customers.map((customer: Row) => customer.order_id))).size} pedidos abrangidos`,
      tone: "amber",
      icon: ShoppingCart,
      click: () => props.onAdvanced(),
    },
    {
      label: "Fornecedores a processar",
      value: data ? suppliers.length : null,
      detail: `${suppliers.filter((supplier) => supplier.totals.missing_cents > 0).length} abaixo do mínimo`,
      tone: "purple",
      icon: Truck,
      click: () => props.onArea("simulacoes"),
    },
    {
      label: "Em trânsito",
      value: data ? transit.length : null,
      detail: "Compras com saldo por receber",
      tone: "teal",
      icon: Package,
      click: () => props.onArea("rececoes"),
    },
    {
      label: "Prontos para entregar",
      value: data ? openOrders.filter(ready).length : null,
      detail: `${readyOrders.filter((order) => !order.notified).length} por contactar, incl. parciais`,
      tone: "green",
      icon: CheckCircle,
      click: () => filterOrders("ready"),
    },
    {
      label: "Encomendas atrasadas",
      value: data ? openOrders.filter((order) => order.late).length : null,
      detail: "Atenção necessária",
      tone: "red",
      icon: Clock,
      click: () => filterOrders("late"),
    },
  ];
  return (
    <section
      className="order-center-board"
      aria-label="Painel de gestão de encomendas"
      aria-busy={props.loading}
    >
      <div className="ocb-hero">
        <div>
          <h1>Centro de Encomendas</h1>
          <p>Do pedido do cliente à entrega, de forma simples e acompanhada.</p>
        </div>
        <div className="ocb-flow" aria-label="Ciclo da encomenda">
          {["Cliente", "Necessidades", "Fornecedor", "Receção", "Cliente"].map(
            (label, index) => (
              <div key={index}>
                <ActionButton
                  inferAction={false}
                  className={`ocb-step ocb-step-${index}`}
                  onClick={() =>
                    index === 1
                      ? props.onAdvanced()
                      : props.onArea(
                          index === 2
                            ? "simulacoes"
                            : index === 3
                              ? "rececoes"
                              : "pedidos",
                        )
                  }
                >
                  <b aria-hidden="true">{index + 1}</b>
                  {label}
                </ActionButton>
                {index !== 4 && <ArrowRight size={14} aria-hidden="true" />}
              </div>
            ),
          )}
        </div>
      </div>
      <div className="ocb-context" data-ignore-dirty>
        <OrderCenterSearch />
        <label>
          Loja{" "}
          <select
            aria-label="Loja do centro de encomendas"
            value={store}
            onChange={(event) => props.onStore(event.target.value)}
          >
            {props.stores.map((item) => (
              <option key={item.id} value={item.id}>
                {item.name}
              </option>
            ))}
          </select>
        </label>
        <span>
          {props.loading
            ? "A atualizar os dados…"
            : "Pedidos, reservas e compras da loja selecionada"}
        </span>
        {/* Os avisos (clientes por contactar e restantes) estão na barra do menu. */}
        <ActionButton
          className="ocb-link"
          inferAction={false}
          disabled={props.loading}
          onClick={props.onRefresh}
        >
          <RefreshCw size={13} />
          Atualizar
        </ActionButton>
      </div>
      {props.error && <Notice tone="error">{props.error}</Notice>}
      {data && !data.stock_known && (
        <Notice tone="warning">
          Stock controlado pelo ERP por confirmar. As propostas não permitem
          preparar compras sem validar a origem.
        </Notice>
      )}
      <div className="ocb-metrics">
        {metrics.map(({ icon: Icon, ...metric }) => (
          <KpiCard
            key={metric.label}
            className={`ocb-metric ocb-${metric.tone}`}
            label={metric.label}
            value={number(metric.value)}
            description={metric.detail}
            icon={<Icon size={29} />}
            tone={
              (
                {
                  blue: "info",
                  amber: "warning",
                  purple: "suggestion",
                  teal: "accent",
                  green: "success",
                  red: "danger",
                } as Record<string, BusinessTone>
              )[metric.tone]
            }
            onOpen={metric.click}
          />
        ))}
      </div>
      <div className="ocb-first-row">
        <section className="ocb-panel">
          <PanelHeading
            icon={Users}
            title="Encomendas de Clientes (Pendentes)"
            count={openOrders.length}
            action={
              <details className="ocb-quick">
                <summary>Ações rápidas</summary>
                <div>
                  <ActionButton onClick={props.onNewOrder}>
                    Novo pedido
                  </ActionButton>
                  <ActionButton onClick={props.onNewSimulation}>
                    Nova simulação
                  </ActionButton>
                  <ActionButton onClick={() => props.onArea("historico")}>
                    Histórico e fecho
                  </ActionButton>
                </div>
              </details>
            }
          />
          <QuickFilters
            className="ocb-filters"
            buttonClassName="ocb-filter"
            label="Filtrar encomendas do painel"
            value={orderFilter}
            onChange={filterOrders}
            options={[
              ["all", "Todas"],
              ["pending", "Por confirmar"],
              ["stock", "Aguarda stock"],
              ["partial", "Parcial"],
              ["ready", "Prontas"],
            ].map(([id, label]) => ({
              id,
              label,
              count:
                id === "all"
                  ? openOrders.length
                  : openOrders.filter((order) => state(order) === id).length,
            }))}
          >
            <ActionButton
              className="ocb-link"
              inferAction={false}
              onClick={() => props.onArea("pedidos")}
            >
              Ver todas <ArrowRight size={12} />
            </ActionButton>
          </QuickFilters>
          <div className="ocb-table-wrap">
            <table aria-label="Encomendas pendentes do painel">
              <thead>
                <tr>
                  <th>N.º Encomenda</th>
                  <th>Data</th>
                  <th>Cliente</th>
                  <th>Artigos</th>
                  <th>Estado</th>
                  <th>Prioridade</th>
                  <th>Ações</th>
                </tr>
              </thead>
              <tbody>
                {orderRows.slice(0, 5).map((order) => (
                  <tr key={order.id}>
                    <td>{order.number}</td>
                    <td>{day(order.requested_at || order.created_at)}</td>
                    <td>{order.customer_name}</td>
                    <td>{order.lines.length}</td>
                    <td>
                      <Badge tone={toneFor[state(order)]}>
                        {stateLabels[state(order)]}
                      </Badge>
                    </td>
                    <td>
                      <Badge
                        tone={order.priority === "urgent" ? "red" : "green"}
                      >
                        {order.priority === "urgent" ? "Urgente" : "Normal"}
                      </Badge>
                    </td>
                    <td className="ocb-row-actions">
                      <OpenButton
                        label={`Abrir pedido ${order.number}`}
                        onClick={() => props.onOrder(order.id)}
                      />
                      <OpenButton
                        more
                        label={`Mais ações do pedido ${order.number}`}
                        onClick={() => props.onOrder(order.id)}
                      />
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          {!orderRows.length && (
            <Empty>Sem pedidos para os filtros selecionados.</Empty>
          )}
        </section>
        <section className="ocb-panel">
          <PanelHeading
            icon={Sparkles}
            title="Necessidades de Compra por Fornecedor"
            count={suppliers.length}
            action={
              <ActionButton
                inferAction={false}
                className="ocb-purple-button"
                onClick={group ? openSimulation : props.onNewSimulation}
                disabled={!!group && !canSimulate}
              >
                <Calculator size={14} />
                Simular encomenda
              </ActionButton>
            }
          />
          <div className="ocb-suppliers">
            {supplierRows.slice(0, 3).map((supplier, index) => (
              <div
                className={`ocb-supplier ${supplierId === supplier.supplier_id ? "is-selected" : ""}`}
                key={supplier.supplier_id}
              >
                <ActionButton
                  inferAction={false}
                  className={`ocb-supplier-mark ocb-${index % 2 ? "blue" : "green"}`}
                  aria-label={`Selecionar ${supplier.name}`}
                  onClick={() => setSupplierId(supplier.supplier_id)}
                >
                  {index % 2 ? <Truck size={24} /> : <Leaf size={24} />}
                </ActionButton>
                <div className="ocb-supplier-name">
                  <strong>{supplier.name}</strong>
                  <small>
                    {supplier.article_count} artigos · {supplier.client_count}{" "}
                    clientes
                  </small>
                  <ProgressGoal
                    label={`Mínimo de ${supplier.name}`}
                    value={supplier.totals.eligible_cents}
                    target={supplier.terms.minimum_cents}
                    showLabel={false}
                    tone="success"
                  />
                </div>
                <div className="ocb-supplier-value">
                  <b>
                    {money(
                      supplier.totals.eligible_cents,
                      supplier.terms.currency,
                    )}
                  </b>
                  <span>
                    {" "}
                    /{" "}
                    {money(
                      supplier.terms.minimum_cents,
                      supplier.terms.currency,
                    )}
                  </span>
                  <small>
                    {supplier.totals.missing_cents
                      ? `Faltam ${money(supplier.totals.missing_cents, supplier.terms.currency)}`
                      : "Mínimo atingido"}
                  </small>
                </div>
                <Badge
                  tone={
                    supplier.blocked || supplier.totals.missing_cents
                      ? "red"
                      : "green"
                  }
                >
                  {supplier.blocked
                    ? "Requer validação"
                    : supplier.totals.missing_cents
                      ? "Abaixo do mínimo"
                      : "Mínimo atingido"}
                </Badge>
                <ActionButton
                  inferAction={false}
                  className="ocb-icon-button"
                  disabled={supplier.blocked}
                  aria-label={`Simular ${supplier.name}`}
                  onClick={() => props.onSimulate(supplier)}
                >
                  <ShoppingCart size={18} />
                </ActionButton>
              </div>
            ))}
          </div>
          {!supplierRows.length && (
            <Empty>
              Sem necessidades líquidas de compra para estes filtros.
            </Empty>
          )}
        </section>
      </div>
      <div className="ocb-work-row">
        <section className="ocb-panel">
          <PanelHeading
            icon={Package}
            title="Top Artigos a Encomendar"
            count={articleRows.length}
            action={
              <ActionButton
                className="ocb-link"
                inferAction={false}
                onClick={props.onAdvanced}
              >
                Ver todos <ArrowRight size={12} />
              </ActionButton>
            }
          />
          <div className="ocb-table-wrap">
            <table aria-label="Top artigos a encomendar">
              <thead>
                <tr>
                  <th>Artigo</th>
                  <th>Stock</th>
                  <th>Necessária</th>
                  <th>Fornecedor</th>
                  <th>Procura</th>
                  <th>Ações</th>
                </tr>
              </thead>
              <tbody>
                {articleRows.slice(0, 5).map((article) => {
                  const supplier = suppliers.find((supplier) =>
                    supplier.lines.some(
                      (line: Row) => line.article_id === article.article_id,
                    ),
                  );
                  const sales = recommendations.find(
                    (item) => item.article_id === article.article_id,
                  );
                  return (
                    <tr key={article.id}>
                      <td>{article.description}</td>
                      <td>
                        <Badge
                          tone={
                            Number(article.available) <= 0 ? "red" : "green"
                          }
                        >
                          {data?.stock_known
                            ? number(article.available)
                            : "Por confirmar"}
                        </Badge>
                      </td>
                      <td>{number(article.needed)}</td>
                      <td>{supplier?.name || "Por associar"}</td>
                      <td>
                        <Badge tone={article.urgent ? "red" : "green"}>
                          {article.urgent
                            ? "Urgente"
                            : sales
                              ? `${number(sales.period_sales)} / 60d`
                              : "Sem histórico"}
                        </Badge>
                      </td>
                      <td>
                        <OpenButton
                          label={`Ver clientes de ${article.description}`}
                          onClick={() =>
                            article.customers[0] &&
                            props.onOrder(article.customers[0].order_id)
                          }
                        />
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
          {!articleRows.length && <Empty>Sem artigos por comprar.</Empty>}
        </section>
        <section className="ocb-panel ocb-suggestions" ref={suggestionsRef}>
          <PanelHeading
            icon={Sparkles}
            title="Sugestões Automáticas"
            action={
              <ActionButton
                className="ocb-link"
                inferAction={false}
                onClick={() => props.onArea("fornecedores")}
              >
                <Settings size={13} />
                Configurar
              </ActionButton>
            }
          />
          <QuickFilters
            className="ocb-filters"
            buttonClassName="ocb-filter"
            label="Critério das sugestões"
            value={suggestionMode}
            onChange={setSuggestionMode}
            options={[
              ["rotation", "Por Rotatividade"],
              ["low", "Baixo Stock"],
              ["history", "Histórico"],
              ["seasonal", "Sazonais"],
            ].map(([id, label]) => ({ id, label }))}
          />
          {recommendationError && (
            <Notice tone="warning">{recommendationError}</Notice>
          )}
          <div className="ocb-table-wrap">
            <table aria-label="Sugestões de reposição">
              <thead>
                <tr>
                  <th aria-label="Selecionar" />
                  <th>Artigo</th>
                  <th>Vendas (60d)</th>
                  <th>Stock</th>
                  <th>Sugestão</th>
                  <th>Bruto s/IVA</th>
                </tr>
              </thead>
              <tbody>
                {suggestionRows.slice(0, 5).map((item) => (
                  <tr key={item.article_id}>
                    <td>
                      <input
                        type="checkbox"
                        aria-label={`Selecionar ${item.description}`}
                        disabled={Number(item.unit_cost) <= 0 || group?.blocked}
                        checked={selected.includes(item.article_id)}
                        onChange={(event) =>
                          setSelected((values) =>
                            event.target.checked
                              ? [...values, item.article_id]
                              : values.filter((id) => id !== item.article_id),
                          )
                        }
                      />
                    </td>
                    <td title={[...item.reasons, ...item.warnings].join("\n")}>
                      {item.description}
                    </td>
                    <td>{number(item.period_sales)}</td>
                    <td>{number(item.free)}</td>
                    <td>
                      <input
                        className="ocb-quantity"
                        type="number"
                        min="0.000001"
                        step="0.000001"
                        aria-label={`Quantidade sugerida de ${item.description}`}
                        value={quantities[item.article_id] ?? item.quantity}
                        onChange={(event) =>
                          setQuantities((values) => ({
                            ...values,
                            [item.article_id]: event.target.value,
                          }))
                        }
                      />
                    </td>
                    <td>
                      {knownCosts(item)
                        ? money(
                            Math.round(
                              Number(
                                quantities[item.article_id] ?? item.quantity,
                              ) *
                                Number(item.unit_cost) *
                                100,
                            ),
                            currency,
                          )
                        : "Por confirmar"}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          {!suggestionRows.length && (
            <Empty>
              {recommendationLoading
                ? "A calcular sugestões…"
                : !group
                  ? "Escolha um fornecedor com necessidades."
                  : suggestionMode === "seasonal"
                    ? "Sem políticas sazonais configuradas para este fornecedor."
                    : "Sem sugestões úteis segundo este critério."}
            </Empty>
          )}
          <ActionButton
            inferAction={false}
            className="ocb-add-suggestions"
            disabled={
              !selected.length ||
              selected.some((id) => {
                const item = recommendations.find(
                  (item) => item.article_id === id,
                );
                return !item || !knownCosts(item);
              }) ||
              group?.blocked
            }
            onClick={addSelected}
          >
            <Plus size={15} />
            Adicionar à simulação
          </ActionButton>
          {!!applied.length && (
            <small className="ocb-applied">
              <Check size={12} />
              {applied.length} artigo(s) considerados na pré-visualização
            </small>
          )}
        </section>
        <section className="ocb-panel ocb-simulator">
          <PanelHeading
            icon={Calculator}
            title="Simulador de Encomenda"
            action={
              <ActionButton
                inferAction={false}
                className="ocb-icon-button"
                aria-label="Abrir simulação completa"
                disabled={!canSimulate}
                onClick={openSimulation}
              >
                <ChevronRight size={15} />
              </ActionButton>
            }
          />
          <label className="ocb-supplier-select">
            Fornecedor
            <select
              aria-label="Fornecedor do simulador do painel"
              value={supplierId}
              onChange={(event) => setSupplierId(event.target.value)}
            >
              {!suppliers.length && <option value="">Sem necessidades</option>}
              {suppliers.map((supplier) => (
                <option key={supplier.supplier_id} value={supplier.supplier_id}>
                  {supplier.name}
                </option>
              ))}
            </select>
          </label>
          <dl className="ocb-mini-totals">
            <div>
              <dt>Total mercadorias (s/IVA)</dt>
              <dd>{totals ? money(totals.net_cents, currency) : "—"}</dd>
            </div>
            <div>
              <dt>Mínimo de encomenda</dt>
              <dd>
                {group ? money(group.terms.minimum_cents, currency) : "—"}
              </dd>
            </div>
            <div
              className={
                !totals ? "" : totals.missing_cents ? "is-short" : "is-met"
              }
            >
              <dt>
                {!totals || totals.missing_cents
                  ? "Falta para atingir mínimo"
                  : "Mínimo atingido"}
              </dt>
              <dd>{totals ? money(totals.missing_cents, currency) : "—"}</dd>
            </div>
          </dl>
          <ProgressGoal
            label="Progresso do mínimo no painel"
            value={totals?.eligible_cents ?? null}
            target={group?.terms.minimum_cents ?? null}
            showLabel={false}
            tone="warning"
          />
          {group?.terms.free_shipping_cents != null && (
            <p className="ocb-mini-shipping">
              Portes grátis:{" "}
              {!totals
                ? "a calcular"
                : (totals.shipping_missing_cents ??
                      Math.max(
                        0,
                        group.terms.free_shipping_cents - totals.net_cents,
                      )) > 0
                  ? `faltam ${money(totals.shipping_missing_cents ?? Math.max(0, group.terms.free_shipping_cents - totals.net_cents), currency)}`
                  : "limiar atingido"}
              .
            </p>
          )}
          <p className="ocb-suggestion-hint">
            <Sparkles size={22} />
            <span>
              {group?.blocked
                ? "Valide os custos e a disponibilidade antes de preparar a compra."
                : totals?.missing_cents
                  ? "Compare reposição útil e portes. Mínimo e portes grátis são independentes."
                  : "Reveja quantidades e condições antes de preparar a encomenda."}
            </span>
          </p>
          {previewError && <Notice tone="error">{previewError}</Notice>}
          {previewLoading && (
            <p role="status">A recalcular com o motor comercial…</p>
          )}
          <ActionButton
            inferAction={false}
            className="ocb-purple-button ocb-wide"
            onClick={() =>
              suggestionsRef.current?.scrollIntoView({
                block: "nearest",
                behavior: "smooth",
              })
            }
          >
            Ver sugestões de produtos <ArrowRight size={13} />
          </ActionButton>
        </section>
      </div>
      <div className="ocb-bottom-row">
        <section className="ocb-panel">
          <PanelHeading
            icon={ClipboardList}
            title="Receções de Mercadoria"
            count={receipts.length}
            action={
              <ActionButton
                className="ocb-link"
                inferAction={false}
                onClick={() => props.onArea("rececoes")}
              >
                Ver todas <ArrowRight size={12} />
              </ActionButton>
            }
          />
          <div className="ocb-table-wrap">
            <table aria-label="Receções do painel">
              <thead>
                <tr>
                  <th>N.º Documento</th>
                  <th>Fornecedor</th>
                  <th>Data receção</th>
                  <th>Artigos</th>
                  <th>Estado</th>
                </tr>
              </thead>
              <tbody>
                {receiptRows.slice(0, 5).map((receipt) => (
                  <tr
                    key={receipt.id}
                    onDoubleClick={() => props.onArea("rececoes")}
                  >
                    <td>{receipt.document || receipt.id.slice(0, 8)}</td>
                    <td>{receipt.supplier_name}</td>
                    <td>{day(receipt.document_date || receipt.created_at)}</td>
                    <td>{receipt.lines.length}</td>
                    <td>
                      <Badge tone="green">Confirmada</Badge>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          {!receiptRows.length && (
            <Empty>Sem receções confirmadas nesta loja.</Empty>
          )}
        </section>
        <section className="ocb-panel" ref={notificationsRef}>
          <PanelHeading
            icon={Bell}
            title="Notificações a Clientes"
            count={readyOrders.filter((order) => !order.notified).length}
            action={
              <ActionButton
                className="ocb-link"
                inferAction={false}
                onClick={props.onReadyOrders}
              >
                Ver todas <ArrowRight size={12} />
              </ActionButton>
            }
          />
          <div className="ocb-table-wrap">
            <table aria-label="Contactos de clientes no painel">
              <thead>
                <tr>
                  <th>Cliente</th>
                  <th>Encomenda</th>
                  <th>Artigos</th>
                  <th>Estado</th>
                </tr>
              </thead>
              <tbody>
                {readyOrders
                  .filter((order) =>
                    matches(`${order.customer_name} ${order.number}`),
                  )
                  .slice(0, 5)
                  .map((order) => (
                    <tr key={order.id}>
                      <td>
                        <ActionButton
                          inferAction={false}
                          className="ocb-cell-link"
                          onClick={() => props.onOrder(order.id)}
                        >
                          {order.customer_name}
                        </ActionButton>
                      </td>
                      <td>{order.number}</td>
                      <td>{order.lines.length}</td>
                      <td>
                        <Badge tone={order.notified ? "green" : "amber"}>
                          {order.notified
                            ? "Notificado"
                            : ready(order)
                              ? "A avisar"
                              : "Parcial · contactar"}
                        </Badge>
                      </td>
                    </tr>
                  ))}
              </tbody>
            </table>
          </div>
          {!readyOrders.length && (
            <Empty>Sem pedidos com stock disponível para contactar.</Empty>
          )}
        </section>
        <section className="ocb-panel">
          <PanelHeading
            icon={BarChart3}
            title="Resumo e Benefícios"
            action={
              <select
                aria-label="Período do resumo"
                className="ocb-period"
                value={period}
                onChange={(event) => setPeriod(Number(event.target.value))}
              >
                {[7, 30, 90].map((days) => (
                  <option key={days} value={days}>
                    Últimos {days} dias
                  </option>
                ))}
              </select>
            }
          />
          <div className="ocb-summary">
            {[
              [
                "blue",
                <BarChart3 size={22} />,
                new Set(
                  summaryPurchases.flatMap((simulation) =>
                    simulation.lines.map(
                      (line: Row, index: number) =>
                        line.article_id ||
                        `provisorio:${simulation.id}:${index}`,
                    ),
                  ),
                ).size,
                "Artigos encomendados",
              ],
              [
                "green",
                <ShoppingCart size={22} />,
                purchaseCurrencies.length === 1
                  ? money(purchaseCurrencies[0][1], purchaseCurrencies[0][0])
                  : purchaseCurrencies.length
                    ? `${purchaseCurrencies.length} moedas`
                    : "—",
                "Compras preparadas",
              ],
              [
                "blue",
                <Users size={22} />,
                analytics?.summary.completed ?? "—",
                "Pedidos entregues",
              ],
              [
                "green",
                <CheckCircle size={22} />,
                analytics?.summary.on_time_percent == null
                  ? "—"
                  : `${number(analytics.summary.on_time_percent)}%`,
                "Entregas no prazo",
              ],
            ].map(([tone, icon, value, label], index) => (
              <div className={`ocb-summary-item ocb-${tone}`} key={index}>
                <span>{icon}</span>
                <strong>
                  {typeof value === "number" ? number(value) : value}
                </strong>
                <small>{label}</small>
              </div>
            ))}
          </div>
          <div className="ocb-benefit">
            <BarChart3 size={28} />
            <div>
              <strong>Processo acompanhado</strong>
              <p>Necessidades, compras e clientes ligados aos registos.</p>
            </div>
          </div>
          <ActionButton
            className="ocb-link"
            inferAction={false}
            onClick={() => props.onArea("analise")}
          >
            Consultar origem dos indicadores <ArrowRight size={12} />
          </ActionButton>
          {analyticsError && (
            <small className="ocb-warning">
              Indicadores de entregas indisponíveis.
            </small>
          )}
          {purchaseCurrencies.length > 1 && (
            <small>
              {purchaseCurrencies
                .map(([code, value]) => money(value, code))
                .join(" · ")}
            </small>
          )}
        </section>
      </div>
      <div className="ocb-footer">
        <span>
          Pré-visualizar não cria reservas, documentos nem envios. Indicadores
          sem prazo histórico aparecem sem percentagem.
        </span>
        <ActionButton
          inferAction={false}
          className="ocb-link"
          onClick={props.onAdvanced}
        >
          Detalhes de stock, clientes e cobertura <ArrowDown size={12} />
        </ActionButton>
      </div>
    </section>
  );
}
