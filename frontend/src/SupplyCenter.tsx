import { ContextSuggestion } from "./components/OverviewUI";
import { formatQuantity } from "./lib/quantity";
import { useEffect, useRef, useState } from "react";
import { Package, RefreshCw, Calculator, ShoppingCart } from "lucide-react";
import { api } from "./lib/api";
import { ActionButton, Notice } from "./components/InteractionUI";
import { MetricCard, StatusBadge } from "./components/BusinessUI";
import { Field } from "./components/Form";
import "./SupplyCenter.css";
import { useLocationSearch } from "./components/WorkspaceUI";
import OrderCenterBoard from "./OrderCenterBoard";

type Row = Record<string, any>;
const quantity = (value: unknown) => formatQuantity(value, "Por confirmar");
const money = (value: number, currency = "EUR") =>
  new Intl.NumberFormat("pt-PT", { style: "currency", currency }).format(
    value / 100,
  );
const date = (value?: string) =>
  value
    ? new Date(value.slice(0, 10) + "T12:00:00").toLocaleDateString("pt-PT")
    : "Sem prazo";

export default function SupplyCenter({
  planning = false,
  stores,
  store,
  revision,
  onSimulate,
  onOrder,
  orders,
  simulations,
  receipts,
  onArea,
  onSimulation,
  onNewOrder,
  onNewSimulation,
  onRefreshData,
  onStoreChange,
  onReadyOrders,
}: {
  planning?: boolean;
  stores: Row[];
  store: string;
  revision: unknown;
  onSimulate: (group: Row) => void;
  onOrder: (id: string) => void;
  orders: Row[];
  simulations: Row[];
  receipts: Row[];
  onArea: (area: string) => void;
  onSimulation: (id: string) => void;
  onNewOrder: () => void;
  onNewSimulation: () => void;
  onRefreshData: () => void;
  onStoreChange: (store: string) => Promise<boolean>;
  onReadyOrders: () => void;
}) {
  const advancedRef = useRef<HTMLDetailsElement>(null);
  const showAdvanced = () => { if (advancedRef.current) { advancedRef.current.open = true; advancedRef.current.scrollIntoView({ block: "start" }); } };
  const [selectedStore, setSelectedStore] = useState(
    store || stores[0]?.id || "",
  );
  const changeStore = (next: string) => { void onStoreChange(next).then(accepted => { if (accepted) setSelectedStore(next); }); };
  const [data, setData] = useState<Row | null>(null),
    [error, setError] = useState("");
  const [loading, setLoading] = useState(false),
    [refresh, setRefresh] = useState(0);
  const [search, setSearch] = useState(""),
    [supplier, setSupplier] = useState("");
  const [customer, setCustomer] = useState(""),
    [filter, setFilter] = useState(planning ? "buy" : "all");
  const [state, setState] = useState("");
  const location = useLocationSearch();
  const requestedFilter = new URLSearchParams(location).get("filtro.abastecimento");
  useEffect(() => { if (requestedFilter && ["all", "buy", "ready", "urgent"].includes(requestedFilter)) setFilter(requestedFilter); }, [requestedFilter]);
  useEffect(() => { if (!planning && requestedFilter === "buy" && advancedRef.current) advancedRef.current.open = true; }, [requestedFilter, planning]);
  useEffect(() => {
    setSelectedStore(store || stores[0]?.id || "");
  }, [store, stores]);
  useEffect(() => {
    if (!selectedStore) return;
    let alive = true;
    setLoading(true);
    setError("");
    setData(null);
    api(
      "/order-cycle/supply-center?store_id=" +
        encodeURIComponent(selectedStore),
    )
      .then((next) => {
        if (alive) setData(next);
      })
      .catch((e) => {
        if (alive) setError(e.message);
      })
      .finally(() => {
        if (alive) setLoading(false);
      });
    return () => {
      alive = false;
    };
  }, [selectedStore, revision, refresh]);
  const articles: Row[] = data?.articles || [],
    suppliers: Row[] = data?.suppliers || [];
  const supplierCatalog: Row[] = data?.supplier_catalog || suppliers;
  const supplierNames = new Map(
    supplierCatalog.map((g) => [g.supplier_id, g.name]),
  );
  const customers = [
    ...new Set<string>(
      articles.flatMap((a) => a.customers.map((c: Row) => c.customer)),
    ),
  ].sort();
  const matchesCustomer = (c: Row) =>
    (!supplier || c.supplier_id === supplier) &&
    (!customer || c.customer === customer) &&
    (!state || c.state === state) &&
    (filter !== "urgent" || c.urgent);
  const visible = articles.filter((a) => {
    const text =
      `${a.code} ${a.description} ${a.customers.map((c: Row) => `${c.customer} ${c.number}`).join(" ")}`
        .normalize("NFD")
        .replace(/\p{Diacritic}/gu, "")
        .toLocaleLowerCase();
    const terms = search
      .normalize("NFD")
      .replace(/\p{Diacritic}/gu, "")
      .toLocaleLowerCase()
      .split(/\s+/)
      .filter(Boolean);
    return (
      terms.every((t) => text.includes(t)) &&
      a.customers.some(matchesCustomer) &&
      (filter !== "buy" || Number(a.needed) > 0) &&
      (filter !== "ready" ||
        a.customers.some((c: Row) => Number(c.ready_quantity) > 0))
    );
  });
  const metrics = data?.metrics || {};
  return (
    <section
      className="supply-center"
      aria-label="Centro inteligente de encomendas"
      aria-busy={loading}
    >
      {!planning && <OrderCenterBoard data={data} loading={loading} error={error} store={selectedStore} stores={stores} orders={orders} simulations={simulations} receipts={receipts}
        onStore={changeStore} onReadyOrders={onReadyOrders} onRefresh={() => { setRefresh(value => value + 1); onRefreshData(); }} onArea={onArea}
        onOrder={onOrder} onSimulate={onSimulate} onSimulation={onSimulation} onNewOrder={onNewOrder} onNewSimulation={onNewSimulation} onAdvanced={showAdvanced} />}
      {!planning && data && !loading && !error && data.stock_known && Number(metrics.needs_to_buy)>0 && <ContextSuggestion title="Rever as necessidades antes de comprar"
        evidence={[`${metrics.needs_to_buy} artigos com necessidade líquida na loja selecionada`,"Origem: necessidades consolidadas, com reservas e compras em curso descontadas."]}
        actionLabel="Rever necessidades e cobertura" onReview={showAdvanced}>
        Compare a procura por cliente, a cobertura e as condições dos fornecedores. A composição continua a exigir simulação e revisão antes de preparar a compra.
      </ContextSuggestion>}
      <details className="ocb-advanced" ref={advancedRef} open={planning || undefined}><summary hidden={planning}>Detalhes de clientes, stock e cobertura</summary>
      <div className="supply-heading">
        <div>
          <span className="supply-eyebrow">
            <Package size={16} aria-hidden="true" /> Encomendas e abastecimento
          </span>
          <h2>{planning ? "Necessidades de compra" : "O que falta para entregar?"}</h2>
          <p>{planning ? "Necessidades por artigo, cliente e fornecedor, com stock e encomendas em curso." : "Clientes, stock e fornecedores num único lugar."}</p>
        </div>
        <div className="actions">
          <Field label="Loja do planeamento">
            <select
              value={selectedStore}
                onChange={(e) => changeStore(e.target.value)}
            >
              <option value="">Escolher loja</option>
              {stores.map((s) => (
                <option key={s.id} value={s.id}>
                  {s.name}
                </option>
              ))}
            </select>
          </Field>
          <ActionButton
            disabled={loading || !selectedStore}
            onClick={() => { setRefresh(n => n + 1); if (planning) onRefreshData(); }}
          >
            <RefreshCw size={16} aria-hidden="true" /> Atualizar
          </ActionButton>
        </div>
      </div>
      {error && <Notice tone="error">{error}</Notice>}
      {loading && (
        <p role="status">A consolidar pedidos, reservas e compras pendentes…</p>
      )}
      {!selectedStore && (
        <Notice>Escolha uma loja para calcular as necessidades.</Notice>
      )}
      {data && (
        <>
          <div className="co-metrics supply-metrics">
            {[
              [
                "pending_orders",
                "Pedidos pendentes",
                "Clientes com quantidades por entregar.",
              ],
              [
                "short_articles",
                "Artigos sem cobertura física",
                "Artigos de clientes que ainda precisam de stock físico.",
              ],
              [
                "needs_to_buy",
                "Artigos por comprar",
                "Necessidade líquida depois do stock, rascunhos e trânsito.",
              ],
              [
                "sent_orders",
                "Enviadas ao fornecedor",
                "Envio aceite pelo transporte existente.",
              ],
              [
                "partial_orders",
                "Receções parciais",
                "Compras com mercadoria recebida e saldo em aberto.",
              ],
              [
                "ready_orders",
                "Pedidos para entregar",
                "Clientes com reservas físicas utilizáveis.",
              ],
              [
                "urgent_orders",
                "Atrasados ou urgentes",
                "Prioridade urgente ou prazo ultrapassado.",
              ],
            ].filter(([key]) => ["pending_orders", "needs_to_buy", "partial_orders", "ready_orders"].includes(key)).map(([key, label, description]) => (
              <MetricCard
                key={key}
                label={label}
                value={
                  metrics[key] == null &&
                  !data.stock_known &&
                  ["short_articles", "needs_to_buy"].includes(key)
                    ? "Por confirmar"
                    : (metrics[key] ?? 0)
                }
                description={description}
                tone={
                  key === "urgent_orders" && metrics[key]
                    ? "danger"
                    : key === "ready_orders"
                      ? "success"
                      : "neutral"
                }
              />
            ))}
          </div>
          {!data.stock_known && (
            <Notice tone="warning">
              O stock pertence ao ERP e não está disponível neste conector.
              Confirme as quantidades na origem antes de comprar.
            </Notice>
          )}
          <details className="supply-metric-details"><summary>Mais indicadores de cobertura e envio</summary><dl><div><dt>Artigos sem cobertura física</dt><dd>{data.stock_known ? metrics.short_articles ?? 0 : "Por confirmar"}</dd></div><div><dt>Enviadas ao fornecedor</dt><dd>{metrics.sent_orders ?? 0}</dd></div><div><dt>Atrasados ou urgentes</dt><dd>{metrics.urgent_orders ?? 0}</dd></div></dl></details>
          <section className="supply-needs-panel" aria-label="Necessidades consolidadas">
          <h2>O que falta comprar?</h2>
          <div className="supply-filters">
            <Field label="Pesquisa global">
              <input
                type="search"
                value={search}
                placeholder="Artigo, cliente ou encomenda"
                onChange={(e) => setSearch(e.target.value)}
              />
            </Field>
            <Field label="Fornecedor">
              <select
                value={supplier}
                onChange={(e) => setSupplier(e.target.value)}
              >
                <option value="">Todos</option>
                {supplierCatalog
                  .filter((g) => g.supplier_id)
                  .map((g) => (
                    <option key={g.supplier_id} value={g.supplier_id}>
                      {g.name}
                    </option>
                  ))}
              </select>
            </Field>
            <Field label="Cliente">
              <select
                value={customer}
                onChange={(e) => setCustomer(e.target.value)}
              >
                <option value="">Todos</option>
                {customers.map((name) => (
                  <option key={name}>{name}</option>
                ))}
              </select>
            </Field>
            <Field label="Estado do processo">
              <select value={state} onChange={(e) => setState(e.target.value)}>
                <option value="">Todos</option>
                {[
                  ...new Set<string>(
                    articles.flatMap((a) =>
                      a.customers.map((c: Row) => c.state),
                    ),
                  ),
                ]
                  .sort()
                  .map((label) => (
                    <option key={label}>{label}</option>
                  ))}
              </select>
            </Field>
          </div>
          <div
            className="supply-quick-filters"
            aria-label="Filtros rápidos de necessidades"
          >
            {[
              ["all", "Todos"],
              ["buy", "Por comprar"],
              ["ready", "Para entregar"],
              ["urgent", "Urgentes / atrasados"],
            ].map(([key, label]) => (
              <ActionButton
                key={key}
                aria-pressed={filter === key}
                onClick={() => setFilter(key)}
              >
                {label}
              </ActionButton>
            ))}
          </div>
          <div className="supply-table-scroll supply-overview-table"><table><caption>Necessidades consolidadas por artigo — dados reais da loja</caption><thead><tr><th>Artigo / clientes</th><th>Pedido</th><th>Stock disponível</th><th>Em trânsito</th><th>Falta comprar</th><th>Ação</th></tr></thead><tbody>{visible.map(article => <tr key={article.id}>
            <td><strong>{article.description}</strong><small>{article.code || "Artigo provisório"} · {article.customers.length} pedido(s)</small>{article.urgent && <StatusBadge tone="danger">Urgente / atrasado</StatusBadge>}</td>
            <td>{quantity(article.customers.reduce((total:number,c:Row)=>total+Number(c.requested),0))}</td><td>{data.stock_known ? quantity(article.available) : "Por confirmar"}</td><td>{quantity(article.incoming)}</td><td><strong>{data.stock_known ? quantity(article.needed) : "Por confirmar"}</strong></td>
            <td><ActionButton onClick={() => {const detail=document.getElementById("supply-article-"+article.id) as HTMLDetailsElement | null; if(detail){ const outer=detail.closest("details.supply-customer-details") as HTMLDetailsElement | null; if(outer)outer.open=true;detail.open=true;detail.scrollIntoView({block:"nearest"});}}}>Ver clientes</ActionButton></td>
          </tr>)}</tbody></table></div>
          <details className="supply-customer-details"><summary>Clientes, reservas e cobertura por artigo</summary>
          <h3>
            Necessidades por artigo <small>({visible.length})</small>
          </h3>
          {!visible.length && (
            <p className="supply-empty">
              Não existem necessidades para estes filtros.
            </p>
          )}
          <div className="supply-articles">
            {visible.map((article) => (
              <details className="supply-article" id={"supply-article-"+article.id} key={article.id}>
                <summary>
                  <span>
                    <strong>{article.description}</strong>
                    <small>
                      {article.code || "Artigo provisório"} ·{" "}
                      {article.customers.length} pedido(s)
                    </small>
                  </span>
                  <span>
                    {article.urgent && (
                      <StatusBadge tone="danger">
                        Urgente / atrasado
                      </StatusBadge>
                    )}
                    <StatusBadge
                      tone={Number(article.needed) ? "warning" : "success"}
                    >
                      {data.stock_known
                        ? `${quantity(article.needed)} por comprar`
                        : "Stock por confirmar"}
                    </StatusBadge>
                  </span>
                </summary>
                {article.reservation_warning && (
                  <p className="co-warning">
                    Existem reservas sem stock utilizável, por exemplo devido à
                    validade. Abra o pedido para rever as reservas antes de
                    voltar a comprar.
                  </p>
                )}
                <dl className="supply-stock">
                  {[
                    ["Físico", article.physical],
                    ["Reservado", article.reserved],
                    ["Disponível", article.available],
                    ["Em trânsito", article.incoming],
                  ].map(([label, value]) => (
                    <div key={label}>
                      <dt>{label}</dt>
                      <dd>{quantity(value)}</dd>
                    </div>
                  ))}
                </dl>
                <p className="footnote">
                  A cobertura proposta ainda não é uma reserva. Abra o pedido
                  para reservar o stock disponível.
                </p>
                <div className="supply-table-scroll">
                  <table>
                    <caption>
                      Clientes que solicitaram {article.description}
                    </caption>
                    <thead>
                      <tr>
                        {[
                          "Cliente / pedido",
                          "Datas",
                          "Pedido / entregue",
                          "Reserva / cobertura livre",
                          "Compra pendente / rascunho",
                          "Falta comprar",
                          "Fornecedor / estado",
                          "Ação",
                        ].map((label) => (
                          <th key={label} scope="col">
                            {label}
                          </th>
                        ))}
                      </tr>
                    </thead>
                    <tbody>
                      {article.customers
                        .filter(matchesCustomer)
                        .map((c: Row) => (
                          <tr key={c.line_id}>
                            <td>
                              <strong>{c.customer}</strong>
                              <small>{c.number}</small>
                            </td>
                            <td>
                              {date(c.requested_at)}
                              <small>Previsto: {date(c.due_date)}</small>
                            </td>
                            <td>
                              {quantity(c.requested)} / {quantity(c.delivered)}
                            </td>
                            <td>
                              {quantity(c.reserved)} / {quantity(c.stock_cover)}
                            </td>
                            <td>
                              {quantity(c.ordered_pending)} /{" "}
                              {quantity(c.simulated)}
                              <small>
                                Trânsito livre: {quantity(c.incoming_cover)}
                              </small>
                            </td>
                            <td>
                              <strong>{quantity(c.needed)}</strong>
                            </td>
                            <td>
                              {supplierNames.get(c.supplier_id) ||
                                "Consultar no pedido"}
                              <small>{c.state}</small>
                            </td>
                            <td>
                              <ActionButton onClick={() => onOrder(c.order_id)}>
                                Abrir pedido
                              </ActionButton>
                            </td>
                          </tr>
                        ))}
                    </tbody>
                  </table>
                </div>
              </details>
            ))}
          </div>
          </details>
          </section>
          <div className="supply-heading">
            <div>
              <h3>
                <ShoppingCart size={19} aria-hidden="true" /> Comprar a quem?
              </h3>
              <p>
                Valores líquidos após descontos. Portes e IVA apresentados
                separadamente.
              </p>
            </div>
          </div>
          <div className="supply-suppliers">
            {suppliers
              .filter(
                (g) =>
                  (!supplier || g.supplier_id === supplier) &&
                  (!customer ||
                    g.lines.some((line: Row) =>
                      line.allocations.some((a: Row) =>
                        articles.some((item) =>
                          item.customers.some(
                            (c: Row) =>
                              c.line_id === a.line_id &&
                              c.customer === customer,
                          ),
                        ),
                      ),
                    )) &&
                  (filter !== "urgent" || g.urgent),
              )
              .map((group) => {
                const t = group.totals,
                  currency = t.currency;
                return (
                  <article
                    key={group.supplier_id || "none"}
                    className="supply-supplier"
                  >
                    <div className="supply-heading">
                      <h4>{group.name}</h4>
                      <StatusBadge
                        tone={
                          group.blocked
                            ? "danger"
                            : t.missing_cents
                              ? "warning"
                              : "success"
                        }
                      >
                        {group.blocked
                          ? "Requer validação"
                          : t.missing_cents
                            ? `Faltam ${money(t.missing_cents, currency)}`
                            : "Mínimo atingido"}
                      </StatusBadge>
                    </div>
                    <p>
                      {group.client_count} cliente(s) · {group.article_count}{" "}
                      artigo(s) · Entrega estimada {date(group.expected_date)}
                    </p>
                    <strong className="supply-value">
                      {money(t.net_cents, currency)}{" "}
                      <small>mercadorias líquidas</small>
                    </strong>
                    <dl className="supply-costs">
                      {[
                        ["Subtotal sem IVA", t.gross_cents],
                        ["Descontos", t.discount_cents],
                        ["Elegível para mínimo", t.eligible_cents],
                        ["Mínimo configurado", group.terms.minimum_cents],
                        ["Portes", t.shipping_cents],
                        ["IVA estimado", t.vat_cents + t.shipping_vat_cents],
                        ["Total previsto a pagar", t.total_cents],
                      ].map(([label, value]) => (
                        <div key={label}>
                          <dt>{label}</dt>
                          <dd>{money(value, currency)}</dd>
                        </div>
                      ))}
                    </dl>
                    <progress
                      aria-label={`Progresso do mínimo de ${group.name}`}
                      max={Math.max(1, group.terms.minimum_cents)}
                      value={Math.min(
                        t.eligible_cents,
                        Math.max(1, group.terms.minimum_cents),
                      )}
                    />
                    {group.terms.minimum_basis !== "net" && (
                      <p className="footnote">
                        Regra contratual específica:{" "}
                        {
                          (
                            {
                              gross: "bruto sem IVA",
                              net_vat: "líquido com IVA",
                              total: "total com IVA e portes",
                            } as Row
                          )[group.terms.minimum_basis]
                        }
                        .
                      </p>
                    )}
                    {group.terms.free_shipping_cents != null && <section className="co-shipping-goal" aria-label={`Portes grátis de ${group.name}`}>
                      <p>Portes grátis desde {money(group.terms.free_shipping_cents, currency)}.</p>
                      <progress aria-label={`Progresso dos portes grátis de ${group.name}`} max={Math.max(1, group.terms.free_shipping_cents)} value={Math.min(t.net_cents, Math.max(1, group.terms.free_shipping_cents))} />
                      <p>{(t.shipping_missing_cents ?? Math.max(0, group.terms.free_shipping_cents - t.net_cents)) > 0 ? `Faltam ${money(t.shipping_missing_cents ?? Math.max(0, group.terms.free_shipping_cents - t.net_cents), currency)} para portes grátis.` : "Limiar de portes grátis atingido."}</p>
                    </section>}
                    {group.reasons.map((reason: string) => (
                      <p className="co-warning" key={reason}>
                        {reason}
                      </p>
                    ))}
                    <ActionButton
                      disabled={group.blocked}
                      onClick={() => onSimulate(group)}
                    >
                      <Calculator size={16} aria-hidden="true" /> Simular
                      encomenda
                    </ActionButton>
                    {t.missing_cents > 0 && (
                      <p className="footnote">
                        No simulador pode acrescentar reposição, comparar
                        cenários, aguardar ou negociar. Avançar abaixo do mínimo
                        exige confirmação autorizada.
                      </p>
                    )}
                  </article>
                );
              })}
          </div>
          {!suppliers.length && (
            <p className="supply-empty">
              Sem compras novas por propor. Consulte os pedidos com stock para
              reservar e os que já estão em curso.
            </p>
          )}
          <p className="footnote">{data.note}</p>
        </>
      )}
      </details>
    </section>
  );
}
