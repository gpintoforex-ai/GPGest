import { useEffect, useState, type ReactNode } from "react";
import { api } from "./lib/api";
import { ActionButton, Notice } from "./components/InteractionUI";
import {
  visibleNavigation,
  navigationParams,
  type NavigationGroup,
} from "./navigationCatalog";
import { ChartFrame, ColumnsChart, Legend } from "./DashboardCharts";
import "./WorkOverview.css";

type Row = Record<string, any>;
type Navigate = (page: string, params?: Record<string, string>) => void;
const count = (value: number) => new Intl.NumberFormat("pt-PT").format(value);
const euro = (value: number, currency: string) =>
  new Intl.NumberFormat("pt-PT", { style: "currency", currency }).format(
    value / 100,
  );
const dateLabel = (value: string) => value.split("-").reverse().join("/");
function range() {
  const end = new Intl.DateTimeFormat("en-CA", {
    timeZone: "Europe/Lisbon",
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
  }).format(new Date());
  const first = new Date(end + "T12:00:00Z");
  first.setUTCDate(first.getUTCDate() - 41);
  return { start: first.toISOString().slice(0, 10), end };
}

/** The approved work dashboard, populated exclusively from authorized live records. */
export default function WorkOverview({
  store,
  role,
  mode,
  navigate,
  financial,
  embedded = false,
}: {
  store: string;
  role: string;
  mode: string;
  navigate: Navigate;
  financial?: ReactNode;
  /** Dentro do Dashboard novo: a página já tem título; a saudação passa a título de secção. */
  embedded?: boolean;
}) {
  const [{ start, end }] = useState(range);
  const [data, setData] = useState<Row | null>(null),
    [error, setError] = useState("");
  const [refresh, setRefresh] = useState(0),
    [loading, setLoading] = useState(false),
    [allDestinations, setAllDestinations] = useState(false);
  const manager = ["administrador", "gestor"].includes(role);
  const groups = visibleNavigation(role, mode);
  useEffect(() => {
    let active = true;
    setData(null);
    setError("");
    setLoading(false);
    if (!manager) return;
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
  }, [store, manager, start, end, refresh]);
  const analytics = (filter = "") =>
    navigate("Encomendas de clientes", {
      "menu.area": "analise",
      "area.pedidos-clientes": "analise",
      "encomendas.inicio": start,
      "encomendas.fim": end,
      "encomendas.filtro": filter,
    });
  const center = () =>
    navigate("Encomendas de clientes", {
      "menu.area": "compras",
      "area.pedidos-clientes": "centro",
      "filtro.abastecimento": "all",
    });
  const simulations = () =>
    navigate("Encomendas de clientes", {
      "menu.area": "compras",
      "area.pedidos-clientes": "simulacoes",
    });
  const stateCount = (state: string) =>
    Number(data?.states.find((s: Row) => s.id === state)?.count || 0);
  const forecast = (data?.drafts || []).reduce(
    (currencies: Record<string, number>, draft: Row) => {
      currencies[draft.currency] =
        (currencies[draft.currency] || 0) + draft.net_cents;
      return currencies;
    },
    {},
  );
  const currencies = Object.entries(forecast) as [string, number][];
  const shipping = (data?.drafts || []).filter(
    (draft: Row) => draft.shipping_missing_cents > 0,
  );
  const pendingDetail = data
    ? `${stateCount("waiting")} a aguardar · ${stateCount("ordered")} em compra · ${stateCount("partial")} parciais · ${stateCount("ready")} prontos`
    : "";
  const availability = data ? count(stateCount("ready")) : "—";
  const weeks = Array.from({ length: 6 }, (_, index) => ({
    label: `Semana ${index + 1}`,
    value: 0,
  }));
  if (data)
    for (const point of data.series) {
      const index = Math.floor(
        (Date.parse(point.key + "T12:00:00Z") -
          Date.parse(start + "T12:00:00Z")) /
          (7 * 86400000),
      );
      if (weeks[index]) weeks[index].value += point.created;
    }
  function openGroup(group: NavigationGroup) {
    const preferred: Record<string, string> = {
      compras: "Centro de Encomendas",
      stocks: "Existências / rotatividade",
      financeiro: "Folha de caixa",
      comercial: "Encomendas de clientes",
    };
    const item =
      group.items.find((item) => item.label === preferred[group.id]) ||
      group.items[0];
    navigate(item.page, navigationParams(group, item));
  }
  const hour = Number(
    new Intl.DateTimeFormat("en-GB", {
      timeZone: "Europe/Lisbon",
      hour: "2-digit",
      hourCycle: "h23",
    }).format(new Date()),
  );
  const greeting =
    hour >= 5 && hour < 12
      ? "Bom dia"
      : hour >= 12 && hour < 20
        ? "Boa tarde"
        : "Boa noite";
  const cards = [
    {
      label: "Pedidos criados",
      value: data ? count(data.summary.created) : "—",
      description: "Últimas seis semanas",
      action: "Ver origem e registos",
      open: () => analytics("created"),
    },
    {
      label: "Pedidos por concluir",
      value: data ? count(data.summary.pending) : "—",
      description: pendingDetail || "Pedidos do período ainda por concluir",
      action: "Tratar pedidos",
      open: () => analytics("pending"),
    },
    {
      label: "Entregas no prazo",
      value: data
        ? data.summary.on_time_percent == null
          ? "Sem prazo histórico"
          : `${count(data.summary.on_time_percent)}%`
        : "—",
      description: data
        ? `${data.summary.on_time_count} / ${data.summary.dated_deliveries} com prazo conhecido · ${data.summary.unknown_deadlines} sem prazo`
        : "Entregas completas com prazo histórico conhecido",
      action: "Consultar metodologia e registos",
      open: () => analytics("on_time"),
    },
    {
      label: "Compras previstas",
      value: data
        ? currencies.length === 1
          ? euro(currencies[0][1], currencies[0][0])
          : currencies.length
            ? `${currencies.length} moedas`
            : "Sem rascunhos"
        : "—",
      description:
        "Rascunhos atuais · mercadorias líquidas, sem IVA nem portes",
      action: "Rever simulações",
      open: simulations,
    },
  ];
  return (
    <section
      className="priority-dashboard"
      aria-label="Visão de trabalho"
      aria-busy={loading}
    >
      <div className="priority-heading">
        <div>
          {embedded ? <>
            <h2>Pedidos, abastecimento e trabalho por área</h2>
            <p>{greeting}. Prioridades dos pedidos das últimas seis semanas, com ligações aos registos.</p>
          </> : <>
            <span className="priority-eyebrow">O negócio, sem ruído</span>
            <h1>{greeting}. Estas são as prioridades.</h1>
            <p>Resumo operacional e financeiro com ligações aos registos.</p>
          </>}
        </div>
        <div className="priority-actions">
          <ActionButton
            inferAction={false}
            onClick={() => navigate("Aparência")}
          >
            Ver sistema visual
          </ActionButton>
          <ActionButton
            variant="primary"
            inferAction={false}
            onClick={
              manager
                ? center
                : () =>
                    navigate("Encomendas de clientes", {
                      "menu.area": "comercial",
                      "area.pedidos-clientes": "pedidos",
                    })
            }
          >
            {manager ? "Centro de Encomendas" : "Pedidos de clientes"}
          </ActionButton>
        </div>
      </div>
      {financial && (
        <section className="priority-financial" aria-label="Gestão financeira e operação de caixa">
          <h2>Resumo financeiro</h2>
          <p>Valores do período indicado abaixo. As prioridades de encomendas usam as últimas seis semanas.</p>
          {financial}
        </section>
      )}
      <div className="priority-context">
        <span>
          {dateLabel(start)} a {dateLabel(end)} ·{" "}
          {store ? "Loja selecionada" : "Lojas autorizadas"}
        </span>
        {manager && (
          <ActionButton
            variant="quiet"
            onClick={() => setRefresh((v) => v + 1)}
            disabled={loading}
          >
            Atualizar prioridades
          </ActionButton>
        )}
      </div>
      {error && <Notice tone="warning">{error}</Notice>}
      {!manager && (
        <Notice>
          Os indicadores de compras requerem autorização para consultar custos.
          Pode continuar a trabalhar nos destinos permitidos ao seu perfil.
        </Notice>
      )}
      {loading && <p role="status">A consultar os pedidos e as simulações…</p>}
      <div className="priority-kpis">
        {cards.map((card) => (
          <article className="priority-kpi" key={card.label}>
            <h2>{card.label}</h2>
            <strong>{card.value}</strong>
            <p>{card.description}</p>
            {card.label === "Compras previstas" &&
              currencies.length > 1 &&
              currencies.map(([currency, value]) => (
                <small key={currency}>{euro(value, currency)}</small>
              ))}
            {manager && (
              <ActionButton
                inferAction={false}
                variant="quiet"
                onClick={card.open}
              >
                {card.action} →
              </ActionButton>
            )}
          </article>
        ))}
      </div>
      <div className="priority-columns">
        <section
          className="priority-panel"
          aria-label="Prioridades dos pedidos"
        >
          <div className="priority-section-head">
            <h2>O que precisa da sua atenção</h2>
            <span className="priority-badge">
              {data ? "Dados reais" : "Sem leitura disponível"}
            </span>
          </div>
          {data && (
            <>
              {data.summary.late > 0 && (
                <div className="priority-task is-danger">
                  <span className="priority-dot" />
                  <div>
                    <h3>
                      {count(data.summary.late)} pedido(s) ultrapassaram o prazo
                    </h3>
                    <p>
                      Consulte as quantidades e as necessidades antes de
                      contactar o cliente.
                    </p>
                  </div>
                  <ActionButton onClick={() => analytics("late")}>
                    Ver pedidos
                  </ActionButton>
                </div>
              )}
              {shipping.slice(0, 2).map((draft: Row) => (
                <div className="priority-task" key={draft.id}>
                  <span className="priority-dot" />
                  <div>
                    <h3>{draft.supplier}: abaixo dos portes grátis</h3>
                    <p>
                      Faltam{" "}
                      {euro(draft.shipping_missing_cents, draft.currency)} em
                      mercadorias líquidas. Compare reposição útil no simulador.
                    </p>
                  </div>
                  <ActionButton onClick={simulations}>Simular</ActionButton>
                </div>
              ))}
              {stateCount("ready") > 0 && (
                <div className="priority-task is-success">
                  <span className="priority-dot" />
                  <div>
                    <h3>{availability} pedido(s) têm mercadoria pronta</h3>
                    <p>
                      Disponibilidade completa. O contacto continua a exigir o
                      canal e a autorização existentes.
                    </p>
                  </div>
                  <ActionButton onClick={() => analytics("ready")}>
                    Consultar pedidos
                  </ActionButton>
                </div>
              )}
              {stateCount("partial") > 0 && (
                <div className="priority-task">
                  <span className="priority-dot" />
                  <div>
                    <h3>
                      {count(stateCount("partial"))} pedido(s) disponíveis
                      parcialmente
                    </h3>
                    <p>
                      Combine o levantamento parcial com o cliente através do
                      pedido.
                    </p>
                  </div>
                  <ActionButton onClick={() => analytics("partial")}>
                    Ver parciais
                  </ActionButton>
                </div>
              )}
              {!data.summary.late &&
                !shipping.length &&
                !stateCount("ready") &&
                !stateCount("partial") && (
                  <p className="priority-empty">
                    Sem atrasos, disponibilidades por tratar ou rascunhos abaixo
                    dos portes grátis nesta leitura.
                  </p>
                )}
            </>
          )}
          {!data && (
            <p className="priority-empty">
              {loading
                ? "A consultar prioridades…"
                : "Os dados indisponíveis não são apresentados como tarefas ou valores de exemplo."}
            </p>
          )}
        </section>
        <section
          className="priority-panel priority-evolution"
          aria-label="Evolução dos pedidos no dashboard"
        >
          <div className="priority-section-head">
            <h2>Evolução dos pedidos</h2>
            <span className="priority-badge">6 semanas</span>
          </div>
          <p>
            Pedidos criados · {dateLabel(start)} a {dateLabel(end)}
          </p>
          {data ? (
            <ChartFrame
              title="Pedidos criados por semana"
              legend={
                <Legend
                  series={[
                    { key: "created", label: "Pedidos criados", form: "bar" },
                  ]}
                />
              }
              table={{
                columns: ["Semana", "Pedidos"],
                rows: weeks.map((week) => [week.label, week.value]),
              }}
            >
              <ColumnsChart
                labels={weeks.map((week) => week.label)}
                series={[
                  { key: "created", label: "Pedidos criados", form: "bar" },
                ]}
                values={[weeks.map((week) => week.value)]}
                formatValue={count}
                ariaLabel="Pedidos reais criados nas últimas seis semanas"
              />
            </ChartFrame>
          ) : (
            <p className="priority-empty">
              Sem dados disponíveis para desenhar o gráfico.
            </p>
          )}
          {manager && (
            <ActionButton onClick={() => analytics()}>
              Abrir análise
            </ActionButton>
          )}
        </section>
      </div>
      <section className="priority-panel" aria-label="Trabalho por área">
        <div className="priority-section-head">
          <div>
            <h2>O seu trabalho, por área</h2>
            <p>A mesma aplicação, organizada pelas tarefas.</p>
          </div>
          <ActionButton
            aria-expanded={allDestinations}
            onClick={() => setAllDestinations((v) => !v)}
          >
            {allDestinations
              ? "Mostrar atalhos principais"
              : "Ver todos os destinos"}
          </ActionButton>
        </div>
        <div className="priority-route-grid">
          {groups
            .filter((group) =>
              ["comercial", "compras", "stocks", "financeiro"].includes(
                group.id,
              ),
            )
            .map((group) => (
              <ActionButton
                inferAction={false}
                key={group.id}
                onClick={() => openGroup(group)}
              >
                <strong>{group.label}</strong>
                <small>{group.description}</small>
              </ActionButton>
            ))}
        </div>
        {allDestinations && (
          <div className="priority-destinations">
            {groups.map((group) => (
              <section key={group.id}>
                <h3>{group.label}</h3>
                {group.items.map((item) => (
                  <ActionButton
                    key={item.id}
                    variant="quiet"
                    inferAction={false}
                    onClick={() =>
                      navigate(item.page, navigationParams(group, item))
                    }
                  >
                    {item.label}
                  </ActionButton>
                ))}
              </section>
            ))}
          </div>
        )}
      </section>
      <p className="priority-footnote">
        Estados dos pedidos criados no período; disponibilidades e rascunhos
        refletem a leitura atual. Os totais de moedas diferentes permanecem
        separados.
      </p>
    </section>
  );
}
