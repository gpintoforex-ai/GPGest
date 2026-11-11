import { useEffect, useState } from "react";
import { api } from "./lib/api";
import { ActionButton, Notice } from "./components/InteractionUI";
import {
  DetailDrawer,
  useLocationSearch,
  updateLocationParam,
} from "./components/WorkspaceUI";
import { Field } from "./components/Form";
import { MetricCard } from "./components/BusinessUI";
import { DataTable } from "./components/DataTable";
import DatePresets, { currentMonthRange } from "./components/DatePresets";
import { ChartFrame, ColumnsChart, Legend } from "./DashboardCharts";
import "./OrderAnalytics.css";

type Row = Record<string, any>;
type Navigate = (page: string, params?: Record<string, string>) => void;
const number = (value: unknown) =>
  value == null
    ? "Sem dados"
    : new Intl.NumberFormat("pt-PT", { maximumFractionDigits: 2 }).format(
        Number(value),
      );
const money = (value: number, currency: string) =>
  new Intl.NumberFormat("pt-PT", { style: "currency", currency }).format(
    value / 100,
  );
const day = (value: string) =>
  value
    ? new Date(value.slice(0, 10) + "T12:00:00").toLocaleDateString("pt-PT")
    : "Sem data";
function useAnalytics(
  store: string,
  start: string,
  end: string,
  refresh: unknown = 0,
  revision?: unknown,
) {
  const [data, setData] = useState<Row | null>(null),
    [error, setError] = useState("");
  const [loading, setLoading] = useState(false);
  useEffect(() => {
    let active = true;
    setData(null);
    setError("");
    if (!start || !end || start > end) {
      setError("Escolha um período válido.");
      return;
    }
    setLoading(true);
    api(
      "/order-cycle/analytics?" +
        new URLSearchParams({ store_id: store, start, end }),
    )
      .then((result) => {
        if (!result.summary || !Array.isArray(result.orders))
          throw new Error(
            "Os indicadores de encomendas estão indisponíveis nesta versão da API.",
          );
        if (active) setData(result);
      })
      .catch((e) => {
        if (active) setError(e.message);
      })
      .finally(() => {
        if (active) setLoading(false);
      });
    return () => {
      active = false;
    };
  }, [store, start, end, refresh, revision]);
  return { data, error, loading };
}

export function OrderCycleSignals({
  store,
  start,
  end,
  navigate,
  revision,
}: {
  store: string;
  start: string;
  end: string;
  navigate: Navigate;
  revision?: unknown;
}) {
  const { data, error, loading } = useAnalytics(store, start, end, revision);
  const open = (filter: string) =>
    navigate("Encomendas de clientes", {
      "menu.area": "analise",
      "area.pedidos-clientes": "analise",
      "encomendas.inicio": start,
      "encomendas.fim": end,
      "encomendas.filtro": filter,
    });
  return (
    <section aria-label="Sinais de encomendas" aria-busy={loading}>
      <h3>Do pedido à entrega</h3>
      {loading && (
        <p role="status">A calcular os indicadores das encomendas…</p>
      )}
      {error && <Notice tone="warning">{error}</Notice>}
      {data && (
        <>
          <p className="footnote">{data.definitions.population}</p>
          <div className="co-metrics">
            {[
              ["created", "Pedidos criados", data.summary.created],
              ["pending", "Pedidos pendentes", data.summary.pending],
              ["late", "Pedidos atrasados", data.summary.late],
            ].map(([key, label, value]) => (
              <MetricCard
                key={String(key)}
                label={String(label)}
                value={
                  <ActionButton
                    variant="quiet"
                    aria-label={`Consultar ${label}`}
                    onClick={() => open(String(key))}
                  >
                    {number(value)}
                  </ActionButton>
                }
                description="Abrir os pedidos que sustentam este indicador."
                tone={
                  key === "late" && Number(value) > 0 ? "danger" : "neutral"
                }
              />
            ))}
          </div>
          <ActionButton variant="primary" onClick={() => open("created")}>
            Explorar indicadores de encomendas
          </ActionButton>
        </>
      )}
    </section>
  );
}

export default function OrderAnalytics({
  store,
  onOrder,
  onSimulation,
  navigate,
  revision,
}: {
  store: string;
  onOrder: (id: string) => void;
  onSimulation: (id: string) => void;
  navigate: Navigate;
  revision?: unknown;
}) {
  const location = useLocationSearch();
  const params = new URLSearchParams(location);
  const [start, setStart] = useState(
    params.get("encomendas.inicio") || currentMonthRange().from,
  );
  const [end, setEnd] = useState(
    params.get("encomendas.fim") || currentMonthRange().to,
  );
  const [refresh, setRefresh] = useState(0),
    [selection, setSelection] = useState<Row | null>(null);
  const { data, error, loading } = useAnalytics(
    store,
    start,
    end,
    refresh,
    revision,
  );
  useEffect(() => {
    const p = new URLSearchParams(location);
    if (p.get("encomendas.inicio")) setStart(p.get("encomendas.inicio")!);
    if (p.get("encomendas.fim")) setEnd(p.get("encomendas.fim")!);
  }, [location]);
  function records(filter: string) {
    if (!data) return;
    const rows =
      filter === "completed" || filter === "duration" || filter === "on_time"
        ? data.completed_orders.filter(
            (r: Row) => filter !== "on_time" || r.on_time !== null,
          )
        : data.orders.filter(
            (r: Row) =>
              filter === "created" ||
              (filter === "late" && r.late) ||
              (filter === "pending" &&
                !["delivered", "cancelled", "closed_with_cancel"].includes(
                  r.state,
                )) ||
              r.state === filter,
          );
    setSelection({ title: "Pedidos que sustentam o indicador", rows });
  }
  const requestedFilter = params.get("encomendas.filtro") || "";
  useEffect(() => {
    if (data && requestedFilter) records(requestedFilter);
  }, [data, requestedFilter]);
  const series = [
    { key: "created", label: "Criados", form: "bar" as const },
    { key: "completed", label: "Entregues", form: "bar" as const },
  ];
  return (
    <section
      className="order-analytics"
      aria-label="Indicadores do ciclo de encomendas"
      aria-busy={loading}
    >
      <div className="co-inline">
        <div>
          <h2>Encomendas: progresso e resultados</h2>
          <p>
            Indicadores verificáveis, com acesso aos pedidos correspondentes.
          </p>
        </div>
        <ActionButton
          disabled={loading}
          onClick={() => setRefresh((v) => v + 1)}
        >
          Atualizar indicadores
        </ActionButton>
      </div>
      <div className="co-grid">
        <Field label="Desde">
          <input
            type="date"
            value={start}
            onChange={(e) => setStart(e.target.value)}
          />
        </Field>
        <Field label="Até">
          <input
            type="date"
            value={end}
            onChange={(e) => setEnd(e.target.value)}
          />
        </Field>
        <DatePresets
          from={start}
          to={end}
          onSelect={(a, b) => {
            setStart(a);
            setEnd(b);
          }}
        />
      </div>
      {error && <Notice tone="error">{error}</Notice>}
      {loading && <p role="status">A calcular os indicadores…</p>}
      {data && (
        <>
          <p className="footnote">
            {data.definitions.population} Loja:{" "}
            {store ? "selecionada no cabeçalho" : "todas as lojas autorizadas"}.
          </p>
          <div className="co-metrics">
            {(
              [
                [
                  "created",
                  "Pedidos criados",
                  number(data.summary.created),
                  data.definitions.population,
                ],
                [
                  "pending",
                  "Pedidos pendentes",
                  number(data.summary.pending),
                  "Pedidos do período ainda por concluir.",
                ],
                [
                  "late",
                  "Pedidos atrasados",
                  number(data.summary.late),
                  "Prazo ultrapassado e quantidades ainda em aberto.",
                ],
                [
                  "completed",
                  "Entregas completas",
                  number(data.summary.completed),
                  data.definitions.completed,
                ],
                [
                  "duration",
                  "Prazo médio de satisfação",
                  data.summary.average_days == null
                    ? "Sem dados"
                    : `${number(data.summary.average_days)} dias`,
                  "Desde o registo até à entrega completa, pelos eventos reais.",
                ],
                [
                  "on_time",
                  "Entregas dentro do prazo",
                  data.summary.on_time_percent == null
                    ? "Sem prazo histórico"
                    : `${number(data.summary.on_time_percent)}%`,
                  `${data.summary.on_time_count} / ${data.summary.dated_deliveries} entregas com prazo histórico; ${data.summary.unknown_deadlines} sem prazo.`,
                ],
              ] as string[][]
            ).map(([key, label, value, description]) => (
              <MetricCard
                key={key}
                label={label}
                value={
                  <ActionButton
                    variant="quiet"
                    onClick={() => records(key)}
                    aria-label={`Consultar ${label}`}
                  >
                    {value}
                  </ActionButton>
                }
                description={description}
                tone={
                  key === "late" && data.summary.late ? "danger" : "neutral"
                }
              />
            ))}
          </div>
          <div className="order-analytics-charts">
            <section className="panel">
              <h3>Estado dos pedidos do período</h3>
              <div className="order-state-list">
                {data.states.map((s: Row) => (
                  <ActionButton key={s.id} onClick={() => records(s.id)}>
                    <span>{s.label}</span>
                    <strong>{s.count}</strong>
                  </ActionButton>
                ))}
              </div>
            </section>
            <section className="panel">
              <ChartFrame
                title="Evolução das encomendas"
                legend={<Legend series={series} />}
                table={{
                  columns: ["Período", "Criados", "Entregues"],
                  rows: data.series.map((r: Row) => [
                    day(r.key),
                    r.created,
                    r.completed,
                  ]),
                }}
                footnote="Criação pela data de registo; entregas pela data do evento. Populações diferentes, sem tratar reservas como entrega."
              >
                <ColumnsChart
                  labels={data.series.map((r: Row) => day(r.key))}
                  series={series}
                  values={series.map(s => data.series.map((r: Row) => r[s.key]))}
                  formatValue={number}
                  ariaLabel="Pedidos criados e entregues por período"
                />
              </ChartFrame>
              <ActionButton onClick={() => records("completed")}>
                Consultar entregas do período
              </ActionButton>
            </section>
          </div>
          <DataTable<Row>
            label="artigos mais solicitados"
            rows={data.articles}
            rowKey={(r) => r.article_id || r.order_ids.join(":")}
            searchText={(r) => r.description}
            columns={[
              {
                id: "article",
                label: "Artigo",
                render: (r) => (
                  <>
                    {r.description}
                    {r.provisional && <small>Referência provisória</small>}
                  </>
                ),
              },
              {
                id: "quantity",
                label: "Quantidade pedida líquida",
                render: (r) => number(r.quantity),
              },
              {
                id: "orders",
                label: "Pedidos",
                render: (r) => (
                  <ActionButton
                    onClick={() =>
                      setSelection({
                        title: r.description,
                        rows: data.orders.filter((o: Row) =>
                          r.order_ids.includes(o.id),
                        ),
                      })
                    }
                  >
                    Consultar {r.order_ids.length} pedido(s)
                  </ActionButton>
                ),
              },
            ]}
          />
          <p className="footnote">
            Procura nos pedidos criados no período, descontando cancelamentos
            atuais. Não é uma medida de vendas.
          </p>
          <DataTable<Row>
            label="rotatividade de artigos"
            rows={data.rotation}
            rowKey={(r) => r.article_id}
            searchText={(r) => r.description}
            columns={[
              { id: "article", label: "Artigo", render: (r) => r.description },
              {
                id: "sales",
                label: "Saídas de venda",
                render: (r) => number(r.quantity),
              },
              {
                id: "daily",
                label: "Média diária",
                render: (r) => number(r.daily),
              },
              {
                id: "source",
                label: "Origem",
                render: (r) => (
                  <ActionButton
                    onClick={() =>
                      setSelection({
                        title: `Movimentos: ${r.description}`,
                        movements: r.movements,
                        truncated: r.movements_truncated,
                      })
                    }
                  >
                    Consultar movimentos
                  </ActionButton>
                ),
              },
            ]}
          />
          <p className="footnote">{data.definitions.rotation}</p>
          <section className="panel">
            <div className="co-inline">
              <h3>Compras previstas em rascunhos</h3>
              <ActionButton
                onClick={() =>
                  navigate("Encomendas de clientes", {
                    "menu.area": "compras",
                    "area.pedidos-clientes": "centro",
                    "filtro.abastecimento": "buy",
                  })
                }
              >
                Necessidades atuais por fornecedor
              </ActionButton>
            </div>
            <p className="footnote">{data.definitions.drafts}</p>
            {Object.entries(data.forecast_by_currency).map(
              ([currency, value]) => (
                <p key={currency}>
                  <strong>{money(Number(value), currency)}</strong> previstos
                  com IVA e portes · {currency}
                </p>
              ),
            )}
            <DataTable<Row>
              label="rascunhos por fornecedor"
              rows={data.drafts}
              rowKey={(r) => r.id}
              searchText={(r) => r.supplier}
              columns={[
                {
                  id: "supplier",
                  label: "Fornecedor",
                  render: (r) => r.supplier,
                },
                {
                  id: "total",
                  label: "Total previsto",
                  render: (r) => money(r.total_cents, r.currency),
                },
                {
                  id: "minimum",
                  label: "Falta para mínimo",
                  render: (r) => money(r.minimum_missing_cents, r.currency),
                },
                {
                  id: "shipping",
                  label: "Falta para portes grátis",
                  render: (r) =>
                    r.shipping_missing_cents == null
                      ? "Sem limiar configurado"
                      : money(r.shipping_missing_cents, r.currency),
                },
                {
                  id: "action",
                  label: "Ação",
                  render: (r) => (
                    <ActionButton onClick={() => onSimulation(r.id)}>
                      Abrir rascunho
                    </ActionButton>
                  ),
                },
              ]}
            />
          </section>
        </>
      )}
      {selection && (
        <DetailDrawer
          open
          title={selection.title}
          onRequestClose={() => {
            setSelection(null);
            if (requestedFilter)
              updateLocationParam("encomendas.filtro", "", true);
          }}
        >
          {selection.movements ? (
            <>
              <p>
                Registos reais do ledger físico
                {selection.truncated
                  ? " — primeiros 50; o total do indicador inclui todos os registos autorizados."
                  : "."}
              </p>
              <DataTable<Row>
                label="movimentos que sustentam a rotatividade"
                rows={selection.movements}
                rowKey={(r) => r.id}
                searchText={(r) => r.id}
                columns={[
                  { id: "date", label: "Data", render: (r) => day(r.date) },
                  {
                    id: "warehouse",
                    label: "Armazém",
                    render: (r) => r.warehouse,
                  },
                  {
                    id: "quantity",
                    label: "Quantidade vendida",
                    render: (r) => number(r.quantity),
                  },
                  { id: "id", label: "Registo", render: (r) => r.id },
                ]}
              />
            </>
          ) : (
            <DataTable<Row>
              label="pedidos que sustentam o indicador"
              rows={selection.rows}
              rowKey={(r) => r.id}
              searchText={(r) => `${r.number} ${r.customer}`}
              columns={[
                {
                  id: "order",
                  label: "Pedido",
                  render: (r) => (
                    <>
                      {r.number}
                      <small>{r.customer}</small>
                    </>
                  ),
                },
                {
                  id: "state",
                  label: "Estado / entrega",
                  render: (r) => r.state_label || day(r.delivered_at),
                },
                {
                  id: "open",
                  label: "Ação",
                  render: (r) => (
                    <ActionButton
                      onClick={() => {
                        setSelection(null);
                        onOrder(r.id);
                      }}
                    >
                      Abrir pedido
                    </ActionButton>
                  ),
                },
              ]}
            />
          )}
        </DetailDrawer>
      )}
    </section>
  );
}
