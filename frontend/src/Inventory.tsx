import { useEffect, useRef, useState } from "react";
import {
  ArrowLeftRight,
  Calculator,
  ClipboardCheck,
  FileUp,
  Package,
  Pencil,
  Plus,
  RefreshCw,
  Settings,
  Trash2,
} from "lucide-react";
import { api, Field, Form } from "./App";
import {
  ActionButton,
  AppDialog,
  Notice,
  confirmAction,
} from "./components/InteractionUI";
import "./Inventory.css";

type Row = Record<string, any>;
const today = () =>
  new Intl.DateTimeFormat("en-CA", {
    timeZone: "Europe/Lisbon",
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
  }).format(new Date());
const money = (v: any) =>
  v === null || v === undefined
    ? "Não calculável"
    : new Intl.NumberFormat("pt-PT", {
        style: "currency",
        currency: "EUR",
      }).format(Number(v));
const qty = (v: any) =>
  v === null || v === undefined
    ? "Indisponível"
    : new Intl.NumberFormat("pt-PT", { maximumFractionDigits: 6 }).format(
        Number(v),
      );
const day = (v: any) =>
  v ? String(v).slice(0, 10).split("-").reverse().join("/") : "—";
const percent = (v: any) =>
  v === null || v === undefined ? "Não calculável" : `${qty(v)} %`;
const kinds: Row = {
  purchase: "Compra",
  sale: "Venda",
  customer_return: "Devolução de cliente",
  supplier_return: "Devolução a fornecedor",
  transfer: "Transferência",
  transfer_in: "Transferência · entrada",
  transfer_out: "Transferência · saída",
  loss: "Quebra / perda",
  expired: "Perda por validade",
  adjustment: "Ajuste de inventário",
  opening: "Saldo inicial",
};
const states: Row = {
  missing: "Sem validade registada",
  valid: "Dentro do prazo",
  near: "A terminar",
  expired: "Expirado",
};
const rightsNames: Row = {
  view_cost: "Consultar custos",
  change_price: "Alterar preços",
  adjust_stock: "Gerir artigos e movimentos",
  confirm_inventory: "Confirmar inventários",
  manage_lots: "Gerir lotes",
  negative_stock: "Exceção de stock negativo",
  override_fefo: "Alterar FEFO com justificação",
};
const newArticle: Row = {
  code: "",
  name: "",
  reference: "",
  barcodes: [],
  description: "",
  photo: "",
  family: "",
  category: "",
  brand: "",
  supplier_id: null,
  supplier_reference: "",
  unit: "un",
  precision: 0,
  vat_rate: "23",
  sale_net: "0",
  minimum: "0",
  reorder: "0",
  location: "",
  lots: false,
  expiry: false,
  active: true,
  revision: 0,
};
const articleKeys = Object.keys(newArticle);
const csvFields: Row = {
  code: "Código",
  name: "Designação",
  reference: "Referência",
  barcodes: "Códigos de barras (separados por |)",
  description: "Descrição",
  family: "Família",
  category: "Categoria",
  brand: "Marca",
  supplier_reference: "Referência do fornecedor",
  unit: "Unidade",
  precision: "Casas decimais",
  vat_rate: "IVA (%)",
  sale_net: "Preço sem IVA",
  minimum: "Mínimo",
  reorder: "Reposição",
  location: "Localização",
  lots: "Lotes (sim/não)",
  expiry: "Validade (sim/não)",
  active: "Ativo (sim/não)",
};

function Num({
  label,
  name,
  value,
  onChange,
  min = 0,
  required = false,
  step = "any",
}: {
  label: string;
  name: string;
  value: any;
  onChange: (v: string) => void;
  min?: number;
  required?: boolean;
  step?: string;
}) {
  return (
    <Field label={label}>
      <input
        name={name}
        inputMode="decimal"
        type="number"
        step={step}
        min={min}
        required={required}
        value={value ?? ""}
        onChange={(e) => onChange(e.target.value)}
      />
    </Field>
  );
}

export default function Inventory({
  stores,
  admin,
}: {
  stores: Row[];
  admin: boolean;
}) {
  const [catalog, setCatalog] = useState<Row | null>(null),
    [tab, setTab] = useState("Painel"),
    [error, setError] = useState(""),
    [success, setSuccess] = useState("");
  const [query, setQuery] = useState(""),
    [filters, setFilters] = useState<Row>({
      start: today().slice(0, 8) + "01",
      end: today(),
      warehouse_id: "",
      article_id: "",
      category: "",
      brand: "",
      supplier_id: "",
    });
  const [report, setReport] = useState<Row | null>(null),
    [ledger, setLedger] = useState<Row | null>(null),
    [lots, setLots] = useState<Row[]>([]),
    [inventories, setInventories] = useState<Row[]>([]),
    [config, setConfig] = useState<Row | null>(null);
  const [dialog, setDialog] = useState<Row | null>(null),
    [busy, setBusy] = useState(false),
    [page, setPage] = useState(1),
    [lotState, setLotState] = useState("");
  const running = useRef(false),
    requestKey = useRef(crypto.randomUUID());
  const articles: Row[] = catalog?.articles || [],
    warehouses: Row[] = catalog?.warehouses || [],
    rights = catalog?.permissions || {};
  const findArticle = (id: string) => articles.find((a) => a.id === id),
    articleName = (id: string) => findArticle(id)?.name || id;
  const warehouseName = (id: string) =>
    warehouses.find((w) => w.id === id)?.name || id;
  const supplierName = (id: string) =>
    catalog?.suppliers.find((s: Row) => s.id === id)?.name || "—";
  async function load() {
    const value = await api("/stock/catalog");
    setCatalog(value);
    setConfig(value.settings);
  }
  async function run(task: () => Promise<void>) {
    if (running.current) return;
    running.current = true;
    setBusy(true);
    setError("");
    setSuccess("");
    try {
      await task();
    } catch (e) {
      setError((e as Error).message);
    } finally {
      running.current = false;
      setBusy(false);
    }
  }
  const params = new URLSearchParams(
    filters as Record<string, string>,
  ).toString();
  const [revision, setRevision] = useState(0);
  useEffect(() => {
    let active = true;
    api("/stock/catalog")
      .then((data) => {
        if (active) {
          setCatalog(data);
          setConfig(data.settings);
        }
      })
      .catch((e) => {
        if (active) setError(e.message);
      });
    return () => {
      active = false;
    };
  }, []);
  useEffect(() => {
    if (!catalog) return;
    let active = true;
    const path =
      tab === "Painel"
        ? `/stock/report?${params}`
        : tab === "Movimentos"
          ? `/stock/movements?${params}&page=${page}`
          : tab === "Lotes e validades"
            ? `/stock/lots?${params}&state=${lotState}`
            : tab === "Inventários"
              ? "/stock/inventories"
              : "";
    if (!path) return;
    setError("");
    if (tab === "Painel") setReport(null);
    if (tab === "Movimentos") setLedger(null);
    api(path)
      .then((value) => {
        if (!active) return;
        if (tab === "Painel") setReport(value);
        if (tab === "Movimentos") setLedger(value);
        if (tab === "Lotes e validades") setLots(value.rows);
        if (tab === "Inventários") setInventories(value.rows);
      })
      .catch((e) => {
        if (active) setError(e.message);
      });
    return () => {
      active = false;
    };
  }, [tab, params, page, lotState, revision, !!catalog]);
  function open(kind: string, data: Row = {}) {
    requestKey.current = crypto.randomUUID();
    setError("");
    setDialog({ kind, ...data });
  }
  async function saved(message: string) {
    setDialog(null);
    setSuccess(message);
    setError("");
    setRevision((v) => v + 1);
    try {
      await load();
    } catch (e) {
      setError(
        `A operação foi registada, mas não foi possível atualizar o catálogo. ${(e as Error).message}`,
      );
    }
  }
  function changeFilter(key: string, value: string) {
    setFilters({ ...filters, [key]: value });
    setPage(1);
  }
  if (!catalog)
    return (
      <section className="panel">
        <h1>Artigos, stock e validades</h1>
        {error ? (
          <Notice tone="error">
            {error}
            <ActionButton onClick={() => run(load)}>
              Voltar a tentar
            </ActionButton>
          </Notice>
        ) : (
          <p role="status">A carregar catálogo…</p>
        )}
      </section>
    );
  return (
    <div className="stock-module">
      <div className="page-intro">
        <div>
          <h1>Artigos, stock e validades</h1>
          <p>Catálogo, quantidades rastreáveis e margem bruta.</p>
        </div>
        <div className="stock-actions">
          <ActionButton
            disabled={busy}
            onClick={() =>
              run(async () => {
                await load();
                setRevision((v) => v + 1);
              })
            }
          >
            <RefreshCw size={16} />
            Atualizar
          </ActionButton>
          {rights.adjust_stock && (
            <>
              <ActionButton
                onClick={() => open("article", { article: { ...newArticle } })}
              >
                <Plus size={16} />
                Novo artigo
              </ActionButton>
              <ActionButton
                className="primary"
                disabled={!warehouses.length}
                onClick={() => open("movement")}
              >
                <ArrowLeftRight size={16} />
                Registar movimento
              </ActionButton>
            </>
          )}
        </div>
      </div>
      {error && <Notice tone="error">{error}</Notice>}
      {success && <Notice tone="success">{success}</Notice>}
      {catalog.policy.mode === "integrated" && (
        <Notice tone="warning">
          Artigos do ERP são apenas de leitura. O conector atual não fornece
          stock: as quantidades ERP permanecem indisponíveis, mesmo após
          sincronizar o catálogo. Os artigos locais mantêm um registo de stock
          separado.
        </Notice>
      )}
      <nav className="stock-tabs" aria-label="Artigos e stock">
        {[
          "Painel",
          "Artigos",
          "Movimentos",
          "Lotes e validades",
          "Inventários",
          "Importações",
          ...(admin ? ["Parâmetros"] : []),
        ].map((name) => (
          <ActionButton
            key={name}
            aria-current={tab === name ? "page" : undefined}
            className={tab === name ? "primary" : ""}
            onClick={() => setTab(name)}
          >
            {name}
          </ActionButton>
        ))}
      </nav>
      {!warehouses.length && (
        <Notice tone="warning">
          Crie um armazém para começar a registar stock.
          {rights.adjust_stock && (
            <ActionButton onClick={() => open("warehouse")}>
              <Plus size={16} />
              Criar armazém
            </ActionButton>
          )}
        </Notice>
      )}
      <section className="panel">
        <div className="form-grid stock-filters">
          {tab === "Artigos" ? (
            <Field label="Pesquisar nome, código, barras, marca ou fornecedor">
              <input
                type="search"
                value={query}
                onChange={(e) => setQuery(e.target.value)}
              />
            </Field>
          ) : (
            <>
              <Field label="Armazém">
                <select
                  value={filters.warehouse_id}
                  onChange={(e) => changeFilter("warehouse_id", e.target.value)}
                >
                  <option value="">Todos os autorizados</option>
                  {warehouses.map((w) => (
                    <option key={w.id} value={w.id}>
                      {w.name} · {stores.find((s) => s.id === w.store_id)?.name}
                    </option>
                  ))}
                </select>
              </Field>
              <Field label="Artigo">
                <select
                  value={filters.article_id}
                  onChange={(e) => changeFilter("article_id", e.target.value)}
                >
                  <option value="">Todos os artigos</option>
                  {articles.map((a) => (
                    <option key={a.id} value={a.id}>
                      {a.code} · {a.name}
                    </option>
                  ))}
                </select>
              </Field>
              {["Painel", "Movimentos", "Importações"].includes(tab) && (
                <>
                  <Field label="Desde">
                    <input
                      type="date"
                      value={filters.start}
                      required
                      onChange={(e) => changeFilter("start", e.target.value)}
                    />
                  </Field>
                  <Field label="Até">
                    <input
                      type="date"
                      value={filters.end}
                      required
                      onChange={(e) => changeFilter("end", e.target.value)}
                    />
                  </Field>
                </>
              )}
              {tab === "Painel" && (
                <>
                  {["category", "brand"].map((key) => (
                    <Field
                      key={key}
                      label={key === "category" ? "Categoria" : "Marca"}
                    >
                      <select
                        value={filters[key]}
                        onChange={(e) => changeFilter(key, e.target.value)}
                      >
                        <option value="">Todas</option>
                        {[
                          ...new Set(
                            articles.map((a) => a[key]).filter(Boolean),
                          ),
                        ].map((value) => (
                          <option key={value} value={value}>
                            {value}
                          </option>
                        ))}
                      </select>
                    </Field>
                  ))}
                  <Field label="Fornecedor">
                    <select
                      value={filters.supplier_id}
                      onChange={(e) =>
                        changeFilter("supplier_id", e.target.value)
                      }
                    >
                      <option value="">Todos</option>
                      {catalog.suppliers.map((s: Row) => (
                        <option key={s.id} value={s.id}>
                          {s.name}
                        </option>
                      ))}
                    </select>
                  </Field>
                </>
              )}
            </>
          )}
        </div>
      </section>
      {tab === "Painel" && (
        <section className="panel">
          <h2>Stock atual e resultados do período</h2>
          {!report ? (
            <p role="status">A calcular indicadores…</p>
          ) : (
            <>
              <div className="stock-summary">
                {[
                  ["Stock a custo conhecido", report.totals.stock_value],
                  ["Valor em risco conhecido", report.totals.risk_value],
                  ["Vendas líquidas", report.totals.net_sales],
                  ["Margem bruta coberta", report.totals.gross_margin],
                  ["Perdas conhecidas", report.totals.loss_value],
                ]
                  .filter(([, value]) => value !== null)
                  .map(([label, value]) => (
                    <div key={label}>
                      <span>{label}</span>
                      <strong>{money(value)}</strong>
                    </div>
                  ))}
              </div>
              {report.coverage && (
                <p className="footnote">
                  Cobertura: {report.coverage.sales_known} linhas de
                  venda/devolução com custo; {report.coverage.sales_unknown} sem
                  custo (excluídas da margem). Stock sem custo:{" "}
                  {report.coverage.stock_unknown} artigos. Lotes em risco sem
                  custo: {report.coverage.risk_unknown}. Perdas sem custo:{" "}
                  {report.coverage.loss_unknown}. Stock ERP indisponível:{" "}
                  {report.coverage.erp_stock_unavailable} artigos.
                </p>
              )}
              <div className="table-wrap">
                <table>
                  <thead>
                    <tr>
                      <th>Artigo</th>
                      <th className="numeric">Stock</th>
                      <th>Reposição / risco</th>
                      <th className="numeric">Vendas líquidas</th>
                      {rights.view_cost && (
                        <>
                          <th className="numeric">Margem realizada</th>
                          <th className="numeric">Margem prevista / un.</th>
                          <th className="numeric">Contribuição conhecida</th>
                        </>
                      )}
                    </tr>
                  </thead>
                  <tbody>
                    {[...report.rows]
                      .sort(
                        (a, b) =>
                          Number(b.below_minimum) - Number(a.below_minimum) ||
                          Number(b.risk_quantity) - Number(a.risk_quantity),
                      )
                      .map((r: Row) => (
                        <tr key={r.id}>
                          <td>
                            {r.code} · {r.name}
                            {r.no_sales && <small>Sem vendas no período</small>}
                          </td>
                          <td className="numeric">{qty(r.quantity)}</td>
                          <td>
                            {r.below_minimum && (
                              <strong className="stock-warning">
                                Abaixo do mínimo ·{" "}
                              </strong>
                            )}
                            Sugestão: {qty(r.replenishment)}
                            <small>
                              Em risco: {qty(r.risk_quantity)} · Expirado:{" "}
                              {qty(r.expired_quantity)}
                            </small>
                            {r.excess_with_expiry && (
                              <strong className="stock-warning">
                                Excesso com validade próxima
                              </strong>
                            )}
                          </td>
                          <td className="numeric">{money(r.net_sales)}</td>
                          {rights.view_cost && (
                            <>
                              <td className="numeric">
                                {money(r.gross_margin)}
                                <small>
                                  {percent(r.actual?.margin_pct)} · Acréscimo:{" "}
                                  {percent(r.actual?.markup_pct)}
                                </small>
                                {r.unknown_sales > 0 && (
                                  <small>
                                    {r.unknown_sales} linhas sem custo excluídas
                                  </small>
                                )}
                              </td>
                              <td className="numeric">
                                {money(r.projected?.margin)}
                                <small>
                                  {percent(r.projected?.margin_pct)}
                                </small>
                                <ActionButton
                                  title="Simular desconto"
                                  aria-label={`Simular desconto de ${r.name}`}
                                  onClick={() =>
                                    open("discount", {
                                      article: findArticle(r.id),
                                      cost: r.cost,
                                    })
                                  }
                                >
                                  <Calculator size={16} />
                                </ActionButton>
                              </td>
                              <td className="numeric">
                                {money(r.contribution)}
                              </td>
                            </>
                          )}
                        </tr>
                      ))}
                  </tbody>
                </table>
              </div>
              {!report.rows.length && (
                <p>Sem artigos locais nos filtros selecionados.</p>
              )}
              {rights.view_cost && (
                <div className="stock-rank">
                  <div>
                    <h3>Maiores margens realizadas</h3>
                    {[...report.rows]
                      .filter((r: Row) => r.gross_margin !== null)
                      .sort(
                        (a: Row, b: Row) =>
                          Number(b.gross_margin) - Number(a.gross_margin),
                      )
                      .slice(0, 5)
                      .map((r: Row) => (
                        <p key={r.id}>
                          {r.name} · {money(r.gross_margin)}
                        </p>
                      ))}
                  </div>
                  <div>
                    <h3>Menores margens realizadas</h3>
                    {[...report.rows]
                      .filter((r: Row) => r.gross_margin !== null)
                      .sort(
                        (a: Row, b: Row) =>
                          Number(a.gross_margin) - Number(b.gross_margin),
                      )
                      .slice(0, 5)
                      .map((r: Row) => (
                        <p key={r.id}>
                          {r.name} · {money(r.gross_margin)}
                        </p>
                      ))}
                  </div>
                </div>
              )}
              <details>
                <summary>Regras e disponibilidade dos dados</summary>
                {report.limitations.map((s: string) => (
                  <p key={s}>{s}</p>
                ))}
              </details>
            </>
          )}
        </section>
      )}
      {tab === "Artigos" && (
        <section className="panel">
          <h2>Catálogo de artigos</h2>
          <div className="table-wrap">
            <table>
              <thead>
                <tr>
                  <th>Artigo / referência</th>
                  <th>Categoria / fornecedor</th>
                  <th className="numeric">Preço sem / com IVA</th>
                  {rights.view_cost && (
                    <th className="numeric">Custo médio / última compra</th>
                  )}
                  <th className="numeric">Stock</th>
                  <th>Origem / estado</th>
                  <th>Ações</th>
                </tr>
              </thead>
              <tbody>
                {articles
                  .filter((a) =>
                    [
                      a.name,
                      a.code,
                      a.reference,
                      ...(a.barcodes || []),
                      a.brand,
                      supplierName(a.supplier_id),
                    ]
                      .join(" ")
                      .toLocaleLowerCase("pt-PT")
                      .includes(query.toLocaleLowerCase("pt-PT")),
                  )
                  .map((a) => (
                    <tr key={a.id}>
                      <td>
                        {a.photo && (
                          <img
                            src={a.photo}
                            alt=""
                            className="stock-photo"
                            loading="lazy"
                            referrerPolicy="no-referrer"
                          />
                        )}
                        <strong>
                          {a.code} · {a.name}
                        </strong>
                        <small>
                          {a.reference} · {a.unit} · {a.brand}
                        </small>
                        {a.description && <small>{a.description}</small>}
                      </td>
                      <td>
                        {a.family} / {a.category}
                        <small>{supplierName(a.supplier_id)}</small>
                      </td>
                      <td className="numeric">
                        {a.origin === "ERP" ? (
                          <>
                            {money(a.erp_price)}
                            <small>{a.erp_price_basis}</small>
                          </>
                        ) : (
                          <>
                            {money(a.sale_net)}
                            <small>
                              {money(
                                Number(a.sale_net) *
                                  (1 + Number(a.vat_rate) / 100),
                              )}{" "}
                              · IVA {qty(a.vat_rate)} %
                            </small>
                          </>
                        )}
                      </td>
                      {rights.view_cost && (
                        <td className="numeric">
                          {money(a.average_cost)}
                          <small>{money(a.last_cost)}</small>
                        </td>
                      )}
                      <td className="numeric">
                        {qty(a.quantity)}
                        <small>{a.location}</small>
                      </td>
                      <td>
                        {a.origin} · {a.active ? "Ativo" : "Inativo"}
                        <small>
                          {a.stock_state}
                          {a.synced_at &&
                            ` · Catálogo sincronizado: ${day(a.synced_at)}`}
                        </small>
                      </td>
                      <td>
                        <div className="stock-actions">
                          {a.origin === "Local" && rights.adjust_stock && (
                            <>
                              <ActionButton
                                title="Editar artigo"
                                aria-label={`Editar ${a.name}`}
                                onClick={() => open("article", { article: a })}
                              >
                                <Pencil size={16} />
                              </ActionButton>
                              <ActionButton
                                className="danger"
                                title="Eliminar artigo"
                                aria-label={`Eliminar ${a.name}`}
                                onClick={() =>
                                  run(async () => {
                                    if (
                                      !(await confirmAction({
                                        title: "Eliminar artigo?",
                                        message: `Pretende eliminar «${a.name}»? Só é possível sem histórico; caso contrário, edite a ficha e desative-o.`,
                                        confirmLabel: "Eliminar artigo",
                                        tone: "danger",
                                      }))
                                    )
                                      return;
                                    await api(
                                      `/stock/articles/${a.id}`,
                                      {},
                                      { method: "DELETE" },
                                    );
                                    await saved("Artigo eliminado.");
                                  })
                                }
                              >
                                <Trash2 size={16} />
                              </ActionButton>
                            </>
                          )}
                          {a.origin === "Local" && rights.change_price && (
                            <ActionButton
                              onClick={() => open("price", { article: a })}
                            >
                              Preço
                            </ActionButton>
                          )}
                        </div>
                      </td>
                    </tr>
                  ))}
              </tbody>
            </table>
          </div>
          {!articles.length && (
            <p>
              O catálogo está vazio. Crie um artigo, importe um CSV ou associe
              artigos do ERP.
            </p>
          )}
        </section>
      )}
      {tab === "Movimentos" && (
        <section className="panel">
          <h2>Histórico de stock</h2>
          <p className="footnote">
            Movimentos confirmados são preservados. Corrija através de devolução
            ou ajuste justificado.
          </p>
          {!ledger ? (
            <p role="status">A carregar movimentos…</p>
          ) : (
            <>
              <div className="table-wrap">
                <table>
                  <thead>
                    <tr>
                      <th>Data / origem</th>
                      <th>Artigo / movimento</th>
                      <th>Armazém / lote</th>
                      <th className="numeric">Quantidade</th>
                      {rights.view_cost && (
                        <th className="numeric">Custo aplicado / un.</th>
                      )}
                      <th className="numeric">Venda líquida</th>
                      <th>Documento / motivo</th>
                      <th>Ações</th>
                    </tr>
                  </thead>
                  <tbody>
                    {ledger.rows.map((r: Row) => (
                      <tr key={r.id}>
                        <td>
                          {day(r.occurred_on)}
                          <small>{r.origin}</small>
                        </td>
                        <td>
                          {articleName(r.article_id)}
                          <small>{kinds[r.kind]}</small>
                        </td>
                        <td>
                          {warehouseName(r.warehouse_id)}
                          <small>{r.lot_id || "Sem lote"}</small>
                        </td>
                        <td className="numeric">
                          {qty(r.quantity)} {r.unit}
                        </td>
                        {rights.view_cost && (
                          <td className="numeric">{money(r.unit_cost)}</td>
                        )}
                        <td className="numeric">{money(r.net_sales)}</td>
                        <td>
                          {r.document}
                          <small>{r.reason}</small>
                          <details>
                            <summary>Rastreabilidade</summary>
                            <small>
                              Movimento: {r.id}
                              <br />
                              Utilizador: {r.user_id}
                              <br />
                              Registado: {day(r.created_at)}
                              <br />
                              Operação relacionada: {r.operation_id || "—"}
                              <br />
                              Original: {r.original_id || "—"}
                            </small>
                          </details>
                        </td>
                        <td>
                          {rights.adjust_stock &&
                            ["sale", "purchase"].includes(r.kind) && (
                              <ActionButton
                                onClick={() =>
                                  open("movement", {
                                    movement: {
                                      article_id: r.article_id,
                                      warehouse_id: r.warehouse_id,
                                      lot_id: r.lot_id,
                                      quantity: String(
                                        Math.abs(Number(r.quantity)),
                                      ),
                                      original_id: r.id,
                                      kind:
                                        r.kind === "sale"
                                          ? "customer_return"
                                          : "supplier_return",
                                    },
                                  })
                                }
                              >
                                Devolver
                              </ActionButton>
                            )}
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
              {!ledger.rows.length && <p>Sem movimentos neste período.</p>}
              <div className="stock-actions">
                <ActionButton
                  disabled={page === 1}
                  onClick={() => setPage((p) => p - 1)}
                >
                  Anterior
                </ActionButton>
                <span>Página {page}</span>
                <ActionButton
                  disabled={!ledger.more}
                  onClick={() => setPage((p) => p + 1)}
                >
                  Seguinte
                </ActionButton>
              </div>
            </>
          )}
        </section>
      )}
      {tab === "Lotes e validades" && (
        <section className="panel">
          <div className="panel-title">
            <h2>Lotes com stock</h2>
            <Field label="Estado da validade">
              <select
                value={lotState}
                onChange={(e) => setLotState(e.target.value)}
              >
                <option value="">Todos</option>
                {Object.entries(states).map(([key, label]) => (
                  <option key={key} value={key}>
                    {label}
                  </option>
                ))}
              </select>
            </Field>
          </div>
          <div className="table-wrap">
            <table>
              <thead>
                <tr>
                  <th>Artigo / lote</th>
                  <th>Armazém</th>
                  <th>Prazo / estado</th>
                  <th className="numeric">Quantidade</th>
                  {rights.view_cost && (
                    <th className="numeric">Valor a custo médio</th>
                  )}
                  <th>Origem</th>
                  <th>Ações</th>
                </tr>
              </thead>
              <tbody>
                {lots.map((l: Row) => (
                  <tr key={`${l.id}-${l.warehouse_id}`}>
                    <td>
                      {articleName(l.article_id)}
                      <small>{l.number}</small>
                    </td>
                    <td>{warehouseName(l.warehouse_id)}</td>
                    <td>
                      <strong
                        className={
                          l.state === "expired" || l.state === "near"
                            ? "stock-warning"
                            : ""
                        }
                      >
                        {states[l.state]}
                      </strong>
                      <small>
                        {day(l.expiry)} ·{" "}
                        {l.deadline === "consumo_preferencial"
                          ? "Consumo preferencial"
                          : "Validade"}
                      </small>
                      {l.alert !== null && (
                        <small>
                          Alerta {l.alert} dias · {l.days} dias restantes
                        </small>
                      )}
                    </td>
                    <td className="numeric">{qty(l.quantity)}</td>
                    {rights.view_cost && (
                      <td className="numeric">
                        {money(l.cost_value)}
                        <small>
                          Primeira entrada / un.: {money(l.unit_cost)}
                        </small>
                      </td>
                    )}
                    <td>
                      {supplierName(l.supplier_id)}
                      <small>
                        {l.document} · Entrada {day(l.entry_date)}
                      </small>
                    </td>
                    <td>
                      {rights.manage_lots && (
                        <ActionButton
                          title="Editar validade"
                          aria-label={`Editar validade do lote ${l.number}`}
                          onClick={() => open("lot", { lot: l })}
                        >
                          <Pencil size={16} />
                        </ActionButton>
                      )}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          {!lots.length && (
            <p>
              Sem lotes com saldo nos filtros selecionados. O stock sem validade
              continua registado e pode ser filtrado acima.
            </p>
          )}
        </section>
      )}
      {tab === "Inventários" && (
        <section className="panel">
          <div className="panel-title">
            <h2>Inventários físicos</h2>
            {rights.adjust_stock && (
              <ActionButton
                disabled={!warehouses.length}
                onClick={() => open("inventory-new")}
              >
                <ClipboardCheck size={16} />
                Iniciar contagem
              </ActionButton>
            )}
          </div>
          <p>
            Conte a posição física referida ao instante de início. A confirmação
            aplica contado − esperado inicial; entradas e saídas posteriores são
            preservadas. Se o stock entretanto sair e impedir o ajuste, reconte
            a posição.
          </p>
          <div className="table-wrap">
            <table>
              <thead>
                <tr>
                  <th>Iniciado</th>
                  <th>Armazém</th>
                  <th>Estado</th>
                  <th>Posições</th>
                  <th>Ações</th>
                </tr>
              </thead>
              <tbody>
                {inventories.map((inv) => (
                  <tr key={inv.id}>
                    <td>{day(inv.created_at)}</td>
                    <td>{warehouseName(inv.warehouse_id)}</td>
                    <td>
                      {inv.status === "confirmed" ? "Confirmado" : "Rascunho"}
                    </td>
                    <td>{inv.lines.length}</td>
                    <td>
                      <ActionButton
                        onClick={() => open("inventory", { inventory: inv })}
                      >
                        {inv.status === "confirmed"
                          ? "Consultar"
                          : "Contar / confirmar"}
                      </ActionButton>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          {!inventories.length && <p>Sem sessões de inventário.</p>}
        </section>
      )}
      {tab === "Importações" && (
        <StockImports
          catalog={catalog}
          filters={filters}
          rights={rights}
          admin={admin}
          busy={busy}
          run={run}
          saved={saved}
          config={config}
          setConfig={setConfig}
        />
      )}
      {tab === "Parâmetros" && config && (
        <section className="panel">
          <h2>
            <Settings size={19} /> Parâmetros de stock
          </h2>
          <Form
            onSubmit={async () => {
              await api("/stock/settings", config);
              await saved("Parâmetros guardados.");
            }}
          >
            <label className="stock-check">
              <input
                type="checkbox"
                checked={config.allow_negative}
                onChange={(e) =>
                  setConfig({ ...config, allow_negative: e.target.checked })
                }
              />
              Permitir stock negativo apenas a utilizadores autorizados (artigos
              sem lotes)
            </label>
            <label className="stock-check">
              <input
                type="checkbox"
                checked={config.recoverable_vat}
                onChange={(e) =>
                  setConfig({ ...config, recoverable_vat: e.target.checked })
                }
              />
              IVA das compras recuperável (se desmarcado, novas entradas
              valorizam custo + IVA do artigo)
            </label>
            <p className="footnote">
              A configuração só afeta entradas futuras. Os custos históricos
              mantêm-se.
            </p>
            <Field label="Alertas de validade, em dias (separados por vírgula)">
              <input
                value={config.alerts.join(",")}
                onChange={(e) =>
                  setConfig({
                    ...config,
                    alerts: e.target.value.split(",").map(Number),
                  })
                }
              />
            </Field>
            <h3>Permissões por utilizador</h3>
            <p>
              Administrador mantém todas as permissões. Gestor tem as permissões
              de gestão, sem exceção de stock negativo; restantes perfis começam
              sem permissões de escrita/custos. As opções abaixo substituem os
              valores do perfil.
            </p>
            {catalog.users.map((u: Row) => (
              <details key={u.id}>
                <summary>{u.name}</summary>
                {Object.entries(rightsNames).map(([key, label]) => (
                  <Field key={key} label={label}>
                    <select
                      value={
                        config.permissions[u.id]?.[key] === undefined
                          ? "default"
                          : String(config.permissions[u.id][key])
                      }
                      onChange={(e) => {
                        const updated = {
                          ...config.permissions,
                          [u.id]: { ...config.permissions[u.id] },
                        };
                        if (e.target.value === "default")
                          delete updated[u.id][key];
                        else updated[u.id][key] = e.target.value === "true";
                        setConfig({ ...config, permissions: updated });
                      }}
                    >
                      <option value="default">Herdar perfil</option>
                      <option value="true">Permitir</option>
                      <option value="false">Impedir</option>
                    </select>
                  </Field>
                ))}
              </details>
            ))}
          </Form>
          <h3>Armazéns</h3>
          {warehouses.map((w) => (
            <p key={w.id}>
              {w.name} · {stores.find((s) => s.id === w.store_id)?.name}
            </p>
          ))}
          <ActionButton onClick={() => open("warehouse")}>
            <Plus size={16} />
            Criar armazém
          </ActionButton>
        </section>
      )}
      {dialog && (
        <AppDialog
          key={`${dialog.kind}-${dialog.article?.id || dialog.inventory?.id || dialog.lot?.id || "new"}`}
          className="modal stock-dialog"
          onRequestClose={() => setDialog(null)}
          aria-labelledby="stock-dialog-title"
        >
          <h2 id="stock-dialog-title">
            {
              (
                {
                  article: dialog.article?.id ? "Editar artigo" : "Novo artigo",
                  movement: "Registar movimento de stock",
                  warehouse: "Novo armazém",
                  "inventory-new": "Iniciar inventário",
                  inventory: "Contagem física",
                  lot: "Validade do lote",
                  discount: "Simular desconto",
                  price: "Alterar preço",
                } as Row
              )[dialog.kind]
            }
          </h2>
          {dialog.kind === "article" && (
            <ArticleForm
              initial={dialog.article}
              suppliers={catalog.suppliers}
              rights={rights}
              onSave={async (values) => {
                await api(
                  `/stock/articles${dialog.article.id ? `/${dialog.article.id}` : ""}`,
                  values,
                  dialog.article.id ? { method: "PUT" } : undefined,
                );
                await saved("Artigo guardado.");
              }}
            />
          )}
          {dialog.kind === "movement" && (
            <MovementForm
              articles={articles.filter(
                (a) => a.origin === "Local" && a.active,
              )}
              warehouses={warehouses}
              suppliers={catalog.suppliers}
              rights={rights}
              initial={dialog.movement}
              onSave={async (values) => {
                await api("/stock/movements", {
                  ...values,
                  request_key: requestKey.current,
                });
                await saved("Movimento registado.");
              }}
            />
          )}
          {dialog.kind === "warehouse" && (
            <Form
              onSubmit={async (values) => {
                await api("/stock/warehouses", values);
                await saved("Armazém criado.");
              }}
            >
              <Field label="Estabelecimento">
                <select name="store_id" required>
                  {stores.map((s) => (
                    <option key={s.id} value={s.id}>
                      {s.name}
                    </option>
                  ))}
                </select>
              </Field>
              <Field label="Nome">
                <input name="name" required maxLength={100} />
              </Field>
            </Form>
          )}
          {dialog.kind === "inventory-new" && (
            <Form
              onSubmit={async (values) => {
                const inv = await api("/stock/inventories", {
                  warehouse_id: values.warehouse_id,
                  category: values.category,
                  article_ids: dialog.article_ids || [],
                });
                setRevision((v) => v + 1);
                setDialog({ kind: "inventory", inventory: inv });
              }}
            >
              <Field label="Armazém">
                <select name="warehouse_id" required>
                  {warehouses.map((w) => (
                    <option key={w.id} value={w.id}>
                      {w.name}
                    </option>
                  ))}
                </select>
              </Field>
              <Field label="Categoria (opcional)">
                <select name="category">
                  <option value="">Todas</option>
                  {[
                    ...new Set(articles.map((a) => a.category).filter(Boolean)),
                  ].map((c) => (
                    <option key={c}>{c}</option>
                  ))}
                </select>
              </Field>
              <Field label="Artigos (opcional; sem seleção inclui todos)">
                <select
                  name="article_ids"
                  multiple
                  value={dialog.article_ids || []}
                  onChange={(e) =>
                    setDialog({
                      ...dialog,
                      article_ids: Array.from(
                        e.currentTarget.selectedOptions,
                        (option) => option.value,
                      ),
                    })
                  }
                >
                  {articles
                    .filter((a) => a.origin === "Local")
                    .map((a) => (
                      <option key={a.id} value={a.id}>
                        {a.name}
                      </option>
                    ))}
                </select>
              </Field>
            </Form>
          )}
          {dialog.kind === "inventory" && (
            <CountForm
              inventory={dialog.inventory}
              articleName={articleName}
              canConfirm={rights.confirm_inventory}
              canEdit={rights.adjust_stock}
              onSave={async (values) => {
                const inv = await api(
                  `/stock/inventories/${dialog.inventory.id}`,
                  values,
                );
                setDialog({ kind: "inventory", inventory: inv });
                setRevision((v) => v + 1);
                setSuccess(
                  inv.status === "confirmed"
                    ? "Inventário confirmado; ajustes registados."
                    : "Contagem guardada.",
                );
              }}
            />
          )}
          {dialog.kind === "lot" && (
            <Form
              onSubmit={async (values) => {
                await api(
                  `/stock/lots/${dialog.lot.id}`,
                  { ...values, expiry: values.expiry || null },
                  { method: "PUT" },
                );
                await saved("Validade atualizada com registo de auditoria.");
              }}
            >
              <p>
                {articleName(dialog.lot.article_id)} · Lote {dialog.lot.number}
              </p>
              <Field label="Data de validade">
                <input
                  name="expiry"
                  type="date"
                  defaultValue={dialog.lot.expiry || ""}
                />
              </Field>
              <Field label="Tipo de prazo">
                <select name="deadline" defaultValue={dialog.lot.deadline}>
                  <option value="validade">Validade</option>
                  <option value="consumo_preferencial">
                    Consumo preferencial
                  </option>
                </select>
              </Field>
              <Field label="Motivo da alteração">
                <input name="reason" required minLength={3} />
              </Field>
            </Form>
          )}
          {dialog.kind === "price" && (
            <Form
              onSubmit={async (values) => {
                await api(`/stock/articles/${dialog.article.id}/price`, {
                  sale_net: values.sale_net,
                  revision: dialog.article.revision,
                });
                await saved("Preço guardado; vendas anteriores preservadas.");
              }}
            >
              <p>{dialog.article.name}</p>
              <Field label="Preço sem IVA (€)">
                <input
                  name="sale_net"
                  type="number"
                  step="any"
                  min="0"
                  required
                  defaultValue={dialog.article.sale_net}
                />
              </Field>
            </Form>
          )}
          {dialog.kind === "discount" && (
            <Discount article={dialog.article} cost={dialog.cost} />
          )}
        </AppDialog>
      )}
    </div>
  );
}

function ArticleForm({
  initial,
  suppliers,
  rights,
  onSave,
}: {
  initial: Row;
  suppliers: Row[];
  rights: Row;
  onSave: (v: Row) => Promise<void>;
}) {
  const [value, setValue] = useState<Row>(
    Object.fromEntries(
      articleKeys.map((k) => [k, initial[k] ?? newArticle[k]]),
    ),
  );
  const change = (key: string, v: any) => setValue({ ...value, [key]: v });
  return (
    <Form
      onSubmit={async () =>
        onSave({ ...value, precision: Number(value.precision) })
      }
    >
      <div className="form-grid">
        {[
          ["code", "Código interno"],
          ["name", "Designação"],
          ["reference", "Referência"],
          ["family", "Família"],
          ["category", "Categoria"],
          ["brand", "Marca"],
          ["supplier_reference", "Referência do fornecedor"],
          ["unit", "Unidade"],
          ["location", "Localização"],
          ["photo", "URL da fotografia (HTTPS)"],
        ].map(([key, label]) => (
          <Field key={key} label={label}>
            <input
              name={key}
              required={["code", "name", "unit"].includes(key)}
              value={value[key]}
              onChange={(e) => change(key, e.target.value)}
            />
          </Field>
        ))}
        <Field label="Fornecedor principal">
          <select
            name="supplier_id"
            value={value.supplier_id || ""}
            onChange={(e) => change("supplier_id", e.target.value || null)}
          >
            <option value="">Sem fornecedor definido</option>
            {suppliers.map((s) => (
              <option key={s.id} value={s.id}>
                {s.name}
              </option>
            ))}
          </select>
        </Field>
        <Field label="Códigos de barras (separados por |)">
          <input
            name="barcodes"
            value={value.barcodes.join("|")}
            onChange={(e) => change("barcodes", e.target.value.split("|"))}
          />
        </Field>
        <Num
          name="precision"
          label="Casas decimais (0 a 6)"
          value={value.precision}
          step="1"
          onChange={(v) => change("precision", v)}
          required
        />
        <Num
          name="vat_rate"
          label="IVA (%)"
          value={value.vat_rate}
          onChange={(v) => change("vat_rate", v)}
          required
        />
        {rights.change_price && (
          <>
            <Num
              name="sale_net"
              label="Preço sem IVA (€)"
              value={value.sale_net}
              onChange={(v) => change("sale_net", v)}
              required
            />
            <Num
              name="sale_gross"
              label="Preço com IVA (€)"
              value={
                Number(value.sale_net) * (1 + Number(value.vat_rate) / 100)
              }
              onChange={(v) =>
                change(
                  "sale_net",
                  String(
                    Number(
                      (Number(v) / (1 + Number(value.vat_rate) / 100)).toFixed(
                        6,
                      ),
                    ),
                  ),
                )
              }
              required
            />
          </>
        )}
        <Num
          name="minimum"
          label="Stock mínimo"
          value={value.minimum}
          onChange={(v) => change("minimum", v)}
          required
        />
        <Num
          name="reorder"
          label="Nível de reposição"
          value={value.reorder}
          onChange={(v) => change("reorder", v)}
          required
        />
      </div>
      <Field label="Descrição">
        <textarea
          name="description"
          value={value.description}
          onChange={(e) => change("description", e.target.value)}
        />
      </Field>
      {[
        ["lots", "Controlar lotes"],
        ["expiry", "Controlar validades"],
        ["active", "Artigo ativo"],
      ].map(([key, label]) => (
        <label className="stock-check" key={key}>
          <input
            name={key}
            type="checkbox"
            checked={value[key]}
            onChange={(e) => change(key, e.target.checked)}
          />
          {label}
        </label>
      ))}
      <p className="footnote">
        O stock e os custos são calculados a partir de movimentos. Artigos com
        histórico podem ser desativados.
      </p>
    </Form>
  );
}

function MovementForm({
  articles,
  warehouses,
  suppliers,
  rights,
  initial,
  onSave,
}: {
  articles: Row[];
  warehouses: Row[];
  suppliers: Row[];
  rights: Row;
  initial?: Row;
  onSave: (v: Row) => Promise<void>;
}) {
  const [value, setValue] = useState<Row>({
    article_id: articles[0]?.id || "",
    warehouse_id: warehouses[0]?.id || "",
    kind: "purchase",
    quantity: "1",
    occurred_on: today(),
    reason: "",
    document: "",
    unit_cost: "",
    net_sales: "0",
    direct_cost: "0",
    lot_id: "",
    lot_number: "",
    expiry: "",
    deadline: "validade",
    original_id: "",
    supplier_id: "",
    override_reason: "",
    adjustment_out: false,
    target_id: "",
    ...initial,
  });
  const [batches, setBatches] = useState<Row[]>([]),
    [error, setError] = useState("");
  const product = articles.find((a) => a.id === value.article_id),
    inbound =
      ["purchase", "opening", "customer_return"].includes(value.kind) ||
      (value.kind === "adjustment" && !value.adjustment_out);
  const change = (key: string, v: any) => setValue({ ...value, [key]: v });
  useEffect(() => {
    let active = true;
    api(
      `/stock/lots?article_id=${value.article_id}&warehouse_id=${value.warehouse_id}`,
    )
      .then((result) => {
        if (active) setBatches(result.rows);
      })
      .catch((e) => {
        if (active) setError(e.message);
      });
    return () => {
      active = false;
    };
  }, [value.article_id, value.warehouse_id]);
  return (
    <Form
      onSubmit={async () =>
        onSave({
          ...value,
          unit_cost: value.unit_cost === "" ? null : value.unit_cost,
          target_id: value.target_id || null,
          lot_id: value.lot_id || null,
          expiry: value.expiry || null,
          original_id: value.original_id || null,
          supplier_id: value.supplier_id || null,
        })
      }
      submitLabel="Registar movimento"
    >
      {error && <Notice tone="error">{error}</Notice>}
      <div className="form-grid">
        <Field label="Artigo">
          <select
            name="article_id"
            required
            value={value.article_id}
            onChange={(e) =>
              setValue({
                ...value,
                article_id: e.target.value,
                lot_id: "",
                lot_number: "",
              })
            }
          >
            <option value="">Selecione</option>
            {articles.map((a) => (
              <option key={a.id} value={a.id}>
                {a.code} · {a.name}
              </option>
            ))}
          </select>
        </Field>
        <Field label="Movimento">
          <select
            name="kind"
            value={value.kind}
            onChange={(e) => change("kind", e.target.value)}
          >
            {Object.entries(kinds)
              .filter(([key]) => !key.startsWith("transfer_"))
              .map(([key, label]) => (
                <option key={key} value={key}>
                  {label}
                </option>
              ))}
          </select>
        </Field>
        <Field label="Armazém">
          <select
            name="warehouse_id"
            required
            value={value.warehouse_id}
            onChange={(e) => change("warehouse_id", e.target.value)}
          >
            {warehouses.map((w) => (
              <option key={w.id} value={w.id}>
                {w.name}
              </option>
            ))}
          </select>
        </Field>
        {value.kind === "transfer" && (
          <Field label="Armazém de destino">
            <select
              required
              name="target_id"
              value={value.target_id}
              onChange={(e) => change("target_id", e.target.value)}
            >
              <option value="">Selecione</option>
              {warehouses
                .filter((w) => w.id !== value.warehouse_id)
                .map((w) => (
                  <option key={w.id} value={w.id}>
                    {w.name}
                  </option>
                ))}
            </select>
          </Field>
        )}
        <Num
          name="quantity"
          label={`Quantidade (${product?.unit || "un"})`}
          value={value.quantity}
          onChange={(v) => change("quantity", v)}
          min={0.000001}
          required
        />
        <Field label="Data">
          <input
            name="occurred_on"
            type="date"
            max={today()}
            required
            value={value.occurred_on}
            onChange={(e) => change("occurred_on", e.target.value)}
          />
        </Field>
        {value.kind === "adjustment" && (
          <Field label="Sentido do ajuste">
            <select
              name="adjustment_out"
              value={String(value.adjustment_out)}
              onChange={(e) =>
                change("adjustment_out", e.target.value === "true")
              }
            >
              <option value="false">Entrada (+)</option>
              <option value="true">Saída (−)</option>
            </select>
          </Field>
        )}
        {inbound && rights.view_cost && !value.original_id && (
          <Num
            name="unit_cost"
            label="Custo unitário sem IVA (€); vazio = desconhecido"
            value={value.unit_cost}
            onChange={(v) => change("unit_cost", v)}
          />
        )}
        {["sale", "customer_return"].includes(value.kind) &&
          !value.original_id && (
            <Num
              name="net_sales"
              label="Total líquido de descontos e IVA (€)"
              value={value.net_sales}
              onChange={(v) => change("net_sales", v)}
              required
            />
          )}
        {rights.view_cost && (
          <Num
            name="direct_cost"
            label="Custos adicionais conhecidos (€)"
            value={value.direct_cost}
            onChange={(v) => change("direct_cost", v)}
          />
        )}
        <Field label="Documento associado (referência exata para evitar duplicações)">
          <input
            name="document"
            value={value.document}
            onChange={(e) => change("document", e.target.value)}
            maxLength={200}
          />
        </Field>
        {inbound && (
          <Field label="Fornecedor">
            <select
              name="supplier_id"
              value={value.supplier_id}
              onChange={(e) => change("supplier_id", e.target.value)}
            >
              <option value="">Não indicado</option>
              {suppliers.map((s) => (
                <option key={s.id} value={s.id}>
                  {s.name}
                </option>
              ))}
            </select>
          </Field>
        )}
        {["customer_return", "supplier_return"].includes(value.kind) && (
          <Field label="ID do movimento original (opcional)">
            <input
              name="original_id"
              value={value.original_id}
              onChange={(e) => change("original_id", e.target.value)}
            />
          </Field>
        )}
        {product?.lots && (
          <>
            <Field
              label={
                inbound
                  ? "Lote existente (ou novo abaixo)"
                  : "Lote · proposta FEFO"
              }
            >
              <select
                name="lot_id"
                value={value.lot_id || ""}
                onChange={(e) => change("lot_id", e.target.value)}
              >
                <option value="">
                  {inbound ? "Novo lote" : "Automático · validade mais próxima"}
                </option>
                {batches.map((l) => (
                  <option key={l.id} value={l.id}>
                    {l.number} · {qty(l.quantity)} · {day(l.expiry)} ·{" "}
                    {states[l.state]}
                  </option>
                ))}
              </select>
            </Field>
            {inbound && !value.lot_id && (
              <>
                <Field label="Número do lote">
                  <input
                    name="lot_number"
                    required
                    value={value.lot_number}
                    onChange={(e) => change("lot_number", e.target.value)}
                  />
                </Field>
                <Field label="Validade (opcional; falta fica assinalada)">
                  <input
                    name="expiry"
                    type="date"
                    value={value.expiry}
                    onChange={(e) => change("expiry", e.target.value)}
                  />
                </Field>
                <Field label="Tipo de prazo">
                  <select
                    name="deadline"
                    value={value.deadline}
                    onChange={(e) => change("deadline", e.target.value)}
                  >
                    <option value="validade">Validade</option>
                    <option value="consumo_preferencial">
                      Consumo preferencial
                    </option>
                  </select>
                </Field>
              </>
            )}
            {!inbound && value.lot_id && (
              <Field label="Justificação para alterar FEFO">
                <input
                  name="override_reason"
                  value={value.override_reason}
                  onChange={(e) => change("override_reason", e.target.value)}
                />
              </Field>
            )}
          </>
        )}
      </div>
      <Field label="Motivo / observações">
        <textarea
          name="reason"
          required
          minLength={3}
          value={value.reason}
          onChange={(e) => change("reason", e.target.value)}
        />
      </Field>
      <p className="footnote">
        FEFO escolhe os lotes físicos. O custo aplicado à venda é o custo médio
        móvel e fica preservado. Não é criado um documento fiscal nem um
        movimento de tesouraria. Devoluções ligadas recuperam o custo e o valor
        líquido da venda original.
      </p>
    </Form>
  );
}

function CountForm({
  inventory,
  articleName,
  canConfirm,
  canEdit,
  onSave,
}: {
  inventory: Row;
  articleName: (id: string) => string;
  canConfirm: boolean;
  canEdit: boolean;
  onSave: (v: Row) => Promise<void>;
}) {
  const [lines, setLines] = useState<Row[]>(inventory.lines),
    [confirm, setConfirm] = useState(!canEdit && canConfirm);
  useEffect(() => {
    setLines(inventory.lines);
    setConfirm(!canEdit && canConfirm);
  }, [inventory.revision, inventory.id, canEdit, canConfirm]);
  const readonly =
    inventory.status === "confirmed" || (!canEdit && !canConfirm);
  return (
    <Form
      hideSubmit={readonly}
      onSubmit={async () => {
        if (readonly) return;
        if (
          confirm &&
          !(await confirmAction({
            title: "Confirmar inventário?",
            message:
              "Serão criados ajustes de stock para as diferenças justificadas. Os movimentos entretanto registados serão preservados.",
            confirmLabel: "Confirmar inventário",
            tone: "warning",
          }))
        )
          return false;
        await onSave({
          revision: inventory.revision,
          confirm,
          lines: lines
            .filter((l) => l.counted !== null && l.counted !== "")
            .map((l) => ({
              article_id: l.article_id,
              lot_id: l.lot_id,
              counted: l.counted,
              reason: l.reason,
            })),
        });
      }}
      submitLabel={
        readonly
          ? "Confirmado"
          : confirm
            ? "Confirmar inventário"
            : "Guardar rascunho"
      }
    >
      <div className="table-wrap">
        <table>
          <thead>
            <tr>
              <th>Artigo / lote</th>
              <th className="numeric">Esperado inicial</th>
              <th>Contado</th>
              <th className="numeric">Diferença</th>
              <th>Motivo</th>
            </tr>
          </thead>
          <tbody>
            {lines.map((l, index) => (
              <tr key={`${l.article_id}-${l.lot_id}`}>
                <td>
                  {articleName(l.article_id)}
                  <small>{l.lot_id || "Sem lote"}</small>
                </td>
                <td className="numeric">{qty(l.expected)}</td>
                <td>
                  <input
                    aria-label={`Contado ${articleName(l.article_id)} ${l.lot_id || ""}`}
                    name={`counted-${index}`}
                    type="number"
                    step="any"
                    min="0"
                    value={l.counted ?? ""}
                    disabled={readonly}
                    onChange={(e) =>
                      setLines(
                        lines.map((v, i) =>
                          i === index ? { ...v, counted: e.target.value } : v,
                        ),
                      )
                    }
                  />
                </td>
                <td className="numeric">
                  {l.counted === null || l.counted === ""
                    ? "Por contar"
                    : qty(Number(l.counted) - Number(l.expected))}
                </td>
                <td>
                  <input
                    aria-label={`Motivo ${articleName(l.article_id)} ${l.lot_id || ""}`}
                    name={`reason-${index}`}
                    disabled={readonly}
                    value={l.reason}
                    onChange={(e) =>
                      setLines(
                        lines.map((v, i) =>
                          i === index ? { ...v, reason: e.target.value } : v,
                        ),
                      )
                    }
                  />
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      {!readonly && canConfirm && (
        <label className="stock-check">
          <input
            type="checkbox"
            disabled={!canEdit}
            checked={confirm}
            onChange={(e) => setConfirm(e.target.checked)}
          />
          Confirmar e criar ajustes (operação com impacto no stock)
        </label>
      )}
      {inventory.status === "confirmed" && (
        <Notice tone="success">
          Inventário confirmado em {day(inventory.confirmed_at)}. Contagens e
          diferenças preservadas.
        </Notice>
      )}
    </Form>
  );
}

function Discount({ article, cost }: { article: Row; cost: any }) {
  const [discount, setDiscount] = useState("0");
  const net = Number(article.sale_net) * (1 - Number(discount) / 100),
    margin = cost === null ? null : net - Number(cost);
  return (
    <>
      <p>{article.name} · Simulação, sem alterar preços ou criar encomendas.</p>
      <Num
        name="discount"
        label="Desconto (%)"
        value={discount}
        onChange={(v) =>
          setDiscount(String(Math.min(100, Math.max(0, Number(v)))))
        }
      />
      <dl>
        <dt>Preço final sem IVA / com IVA</dt>
        <dd>
          {money(net)} / {money(net * (1 + Number(article.vat_rate) / 100))}
        </dd>
        <dt>Margem prevista / unidade</dt>
        <dd>
          {money(margin)} ·{" "}
          {percent(margin !== null && net ? (margin / net) * 100 : null)}
        </dd>
        <dt>Acréscimo sobre custo</dt>
        <dd>
          {percent(
            margin !== null && Number(cost)
              ? (margin / Number(cost)) * 100
              : null,
          )}
        </dd>
      </dl>
      <p className="footnote">
        Antes de comissões, perdas e restantes custos. Não representa lucro
        líquido.
      </p>
    </>
  );
}

function StockImports({
  catalog,
  filters,
  rights,
  admin,
  busy,
  run,
  saved,
  config,
  setConfig,
}: {
  catalog: Row;
  filters: Row;
  rights: Row;
  admin: boolean;
  busy: boolean;
  run: (task: () => Promise<void>) => Promise<void>;
  saved: (v: string) => Promise<void>;
  config: Row | null;
  setConfig: (v: Row) => void;
}) {
  const [content, setContent] = useState(""),
    [delimiter, setDelimiter] = useState(";"),
    [mapping, setMapping] = useState<Row>({}),
    [preview, setPreview] = useState<Row | null>(null),
    [source, setSource] = useState<Row | null>(null),
    [erp, setErp] = useState<Row | null>(null),
    [target, setTarget] = useState(
      filters.warehouse_id || catalog.warehouses[0]?.id || "",
    );
  const headers =
    content
      .split(/\r?\n/, 1)[0]
      ?.split(delimiter)
      .map((v) => v.replace(/^"|"$/g, "").trim()) || [];
  const [confirmed, setConfirmed] = useState(false),
    [review, setReview] = useState<Row | null>(null);
  function changed() {
    setPreview(null);
    setConfirmed(false);
  }
  async function prepareCSV() {
    setPreview(
      await api("/stock/csv/preview", { content, delimiter, mapping }),
    );
    setConfirmed(false);
  }
  return (
    <>
      {review && (
        <AppDialog
          className="modal"
          aria-labelledby="stock-source-review-title"
          onRequestClose={() => setReview(null)}
        >
          <h2 id="stock-source-review-title">Rever documento SAF-T alterado</h2>
          <p>
            <strong>{review.document}</strong> ·{" "}
            {review.description || review.code}
          </p>
          <Notice tone="warning">
            Os movimentos já registados serão preservados. Se a alteração exigir
            corrigir quantidades ou valores, registe primeiro uma devolução ou
            ajuste justificado em Movimentos. Esta revisão impede uma nova saída
            automática para esta versão da linha.
          </Notice>
          <Form
            onSubmit={async (values) => {
              await api("/stock/saft/resolve", {
                confirmed: true,
                record_id: review.record_id,
                key: review.key,
                fingerprint: review.fingerprint,
                reason: values.reason,
              });
              setReview(null);
              setSource(
                await api(
                  `/stock/saft?start=${filters.start}&end=${filters.end}`,
                ),
              );
              await saved(
                "Revisão registada, sem alterar os movimentos anteriores.",
              );
            }}
            submitLabel="Confirmar revisão e preservar movimentos"
          >
            <Field label="Justificação e referência da correção, quando aplicável">
              <textarea name="reason" required minLength={3} />
            </Field>
          </Form>
        </AppDialog>
      )}
      <section className="panel">
        <h2>
          <FileUp size={19} /> Importar artigos por CSV
        </h2>
        <p>
          Pré-visualização até 2000 artigos. Apenas cria artigos novos; códigos
          e códigos de barras existentes são apresentados como erros. O CSV não
          altera stock.
        </p>
        {!rights.adjust_stock ? (
          <Notice tone="warning">Sem permissão para importar artigos.</Notice>
        ) : (
          <>
            <Field label="Ficheiro CSV">
              <input
                type="file"
                accept=".csv,text/csv"
                onChange={(e) => {
                  const f = e.target.files?.[0];
                  if (!f) return;
                  void run(async () => {
                    if (f.size > 2_000_000)
                      throw new Error("Limite de 2 MB. Divida o ficheiro.");
                    const bytes = await f.arrayBuffer();
                    let text = new TextDecoder("utf-8").decode(bytes);
                    if (text.includes("\ufffd"))
                      text = new TextDecoder("windows-1252").decode(bytes);
                    setContent(text.replace(/^\ufeff/, ""));
                    setMapping({});
                    changed();
                  });
                }}
              />
            </Field>
            <Field label="Separador">
              <select
                value={delimiter}
                onChange={(e) => {
                  setDelimiter(e.target.value);
                  setMapping({});
                  changed();
                }}
              >
                <option value=";">Ponto e vírgula</option>
                <option value=",">Vírgula</option>
                <option value={"\t"}>Tabulação</option>
              </select>
            </Field>
            {!!content && (
              <>
                <div className="form-grid">
                  {Object.entries(csvFields).map(([key, label]) => (
                    <Field key={key} label={label}>
                      <select
                        value={mapping[key] || ""}
                        onChange={(e) => {
                          const next = { ...mapping };
                          if (e.target.value) next[key] = e.target.value;
                          else delete next[key];
                          setMapping(next);
                          changed();
                        }}
                      >
                        <option value="">
                          Não importar
                          {["code", "name"].includes(key)
                            ? " (obrigatório)"
                            : ""}
                        </option>
                        {headers.map((h, i) => (
                          <option key={i} value={h}>
                            {h}
                          </option>
                        ))}
                      </select>
                    </Field>
                  ))}
                </div>
                <ActionButton disabled={busy} onClick={() => run(prepareCSV)}>
                  Pré-visualizar e validar
                </ActionButton>
              </>
            )}
            {preview && (
              <>
                <h3>
                  {preview.rows.length} artigos válidos ·{" "}
                  {preview.errors.length} erros
                </h3>
                {preview.errors.length > 0 && (
                  <Notice tone="error">
                    <ul>
                      {preview.errors.map((r: Row, i: number) => (
                        <li key={i}>
                          Linha {r.line}: {r.message}
                        </li>
                      ))}
                    </ul>
                  </Notice>
                )}
                <div className="table-wrap">
                  <table>
                    <thead>
                      <tr>
                        <th>Código</th>
                        <th>Designação</th>
                        <th>Categoria</th>
                        <th className="numeric">Preço sem IVA</th>
                      </tr>
                    </thead>
                    <tbody>
                      {preview.rows.slice(0, 100).map((r: Row) => (
                        <tr key={r.code}>
                          <td>{r.code}</td>
                          <td>{r.name}</td>
                          <td>{r.category}</td>
                          <td className="numeric">{money(r.sale_net)}</td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
                {preview.rows.length > 100 && (
                  <p>
                    Mostrados os primeiros 100 de {preview.rows.length}; todos
                    foram validados.
                  </p>
                )}
                <ActionButton
                  className="primary"
                  disabled={busy || !preview.can_confirm}
                  onClick={() =>
                    run(async () => {
                      if (
                        !(await confirmAction({
                          title: "Importar artigos?",
                          message: `Serão criados ${preview.rows.length} artigos do ficheiro validado, sem movimentos de stock.`,
                          confirmLabel: "Importar artigos",
                        }))
                      )
                        return;
                      const result = await api(
                        `/stock/csv/${preview.id}/confirm`,
                        { confirmed: true },
                      );
                      setPreview(null);
                      setContent("");
                      await saved(`${result.imported} artigos importados.`);
                    })
                  }
                >
                  Confirmar importação CSV
                </ActionButton>
              </>
            )}
          </>
        )}
      </section>
      <section className="panel">
        <h2>Vendas SAF-T e início do controlo</h2>
        <p>
          As vendas já importadas continuam disponíveis para análise fiscal em
          “Importar SAF-T de vendas”. Não geram stock por omissão. Consulte as
          linhas, associe cada código e defina a data a partir da qual assume o
          controlo na Zela.
        </p>
        <ActionButton
          disabled={busy}
          onClick={() =>
            run(async () => {
              setSource(
                await api(
                  `/stock/saft?start=${filters.start}&end=${filters.end}`,
                ),
              );
              setConfirmed(false);
            })
          }
        >
          <RefreshCw size={16} />
          Consultar linhas do período
        </ActionButton>
        {source && (
          <>
            <Notice tone="warning">{source.limitations}</Notice>
            <div className="table-wrap">
              <table>
                <thead>
                  <tr>
                    <th>Data / documento</th>
                    <th>Código SAF-T</th>
                    <th>Associação Zela</th>
                    <th className="numeric">Quantidade / líquido</th>
                    <th>Estado</th>
                  </tr>
                </thead>
                <tbody>
                  {source.rows.map((r: Row) => (
                    <tr key={r.key}>
                      <td>
                        {day(r.date)} · {r.document}
                        <small>{r.type}</small>
                      </td>
                      <td>
                        {r.code}
                        <small>{r.description}</small>
                      </td>
                      <td>
                        {admin && config ? (
                          <select
                            aria-label={`Associar código SAF-T ${r.code}`}
                            value={config.saft_mapping[r.code] || ""}
                            onChange={(e) => {
                              const next = { ...config.saft_mapping };
                              if (e.target.value) next[r.code] = e.target.value;
                              else delete next[r.code];
                              setConfig({ ...config, saft_mapping: next });
                              setConfirmed(false);
                            }}
                          >
                            <option value="">Sem associação</option>
                            {catalog.articles
                              .filter((a: Row) => a.origin === "Local")
                              .map((a: Row) => (
                                <option key={a.id} value={a.id}>
                                  {a.code} · {a.name}
                                </option>
                              ))}
                          </select>
                        ) : (
                          catalog.articles.find(
                            (a: Row) => a.id === r.article_id,
                          )?.name || "Sem associação"
                        )}
                      </td>
                      <td className="numeric">
                        {qty(r.quantity)} / {money(r.net_sales)}
                      </td>
                      <td>
                        {
                          {
                            ready: "Pronta para registo",
                            unmapped: "Sem associação",
                            posted: "Já registada",
                            reviewed: "Revisada; movimentos preservados",
                            conflict: "Alterada · rever",
                          }[r.state as string]
                        }
                        {r.state === "conflict" && rights.adjust_stock && (
                          <ActionButton onClick={() => setReview(r)}>
                            Rever conflito
                          </ActionButton>
                        )}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
            {!source.rows.length && <p>Sem linhas SAF-T neste período.</p>}
          </>
        )}
        {admin && config && (
          <Form
            onSubmit={async () => {
              await api("/stock/settings", config);
              await saved(
                "Associações e política SAF-T guardadas. Consulte novamente as linhas para confirmar o registo.",
              );
              setSource(null);
            }}
          >
            <Field label="Início do controlo de stock">
              <input
                name="control_start"
                type="date"
                value={config.control_start || ""}
                onChange={(e) => {
                  setConfig({
                    ...config,
                    control_start: e.target.value || null,
                  });
                  setConfirmed(false);
                }}
              />
            </Field>
            <p className="footnote">
              Defina primeiro o saldo inicial de cada artigo no armazém. Lotes e
              notas de crédito exigem movimentos manuais com rastreabilidade.
              Alterar associações não reescreve movimentos anteriores.
            </p>
          </Form>
        )}
        {rights.adjust_stock && (
          <>
            <Field label="Armazém para as saídas">
              <select
                value={target}
                onChange={(e) => {
                  setTarget(e.target.value);
                  setConfirmed(false);
                }}
              >
                <option value="">Selecione</option>
                {catalog.warehouses.map((w: Row) => (
                  <option key={w.id} value={w.id}>
                    {w.name}
                  </option>
                ))}
              </select>
            </Field>
            <label className="stock-check">
              <input
                type="checkbox"
                checked={confirmed}
                onChange={(e) => setConfirmed(e.target.checked)}
              />
              Confirmo que o saldo inicial e as associações estão corretos e
              pretendo registar as vendas elegíveis deste período.
            </label>
            <ActionButton
              className="primary"
              disabled={busy || !source || !confirmed || !target}
              onClick={() =>
                run(async () => {
                  if (
                    !(await confirmAction({
                      title: "Registar saídas SAF-T?",
                      message:
                        "As vendas elegíveis do estabelecimento do armazém selecionado irão baixar stock. Linhas sem associação, com lotes ou notas de crédito são excluídas. Uma falha impede a aplicação do conjunto.",
                      confirmLabel: "Registar saídas",
                      tone: "warning",
                    }))
                  )
                    return;
                  const r = await api("/stock/saft/apply", {
                    warehouse_id: target,
                    start: filters.start,
                    end: filters.end,
                    confirmed: true,
                  });
                  setConfirmed(false);
                  setSource(null);
                  await saved(
                    `${r.applied} vendas registadas; ${r.skipped} linhas não elegíveis.`,
                  );
                })
              }
            >
              Confirmar saídas de stock
            </ActionButton>
          </>
        )}
      </section>
      <section className="panel">
        <h2>Catálogo ERP existente</h2>
        <p>
          Reutiliza a sincronização atual. Os campos e preços ERP mantêm-se sob
          responsabilidade do ERP; quantidades de stock não disponíveis neste
          conector.
        </p>
        <ActionButton
          disabled={busy}
          onClick={() =>
            run(async () => setErp(await api("/stock/erp-products")))
          }
        >
          Consultar artigos sincronizados
        </ActionButton>
        {erp && (
          <>
            <p>{erp.stock}</p>
            <div className="table-wrap">
              <table>
                <thead>
                  <tr>
                    <th>Código / artigo</th>
                    <th>Última sincronização do catálogo</th>
                    <th>Ação</th>
                  </tr>
                </thead>
                <tbody>
                  {erp.rows.map((r: Row) => (
                    <tr key={r.id}>
                      <td>
                        {r.code} · {r.name}
                      </td>
                      <td>{day(r.synced_at)}</td>
                      <td>
                        {r.linked
                          ? "Associado"
                          : rights.adjust_stock && (
                              <ActionButton
                                disabled={busy}
                                onClick={() =>
                                  run(async () => {
                                    await api("/stock/erp-products", {
                                      erp_id: r.id,
                                    });
                                    setErp(null);
                                    await saved(
                                      "Artigo ERP associado em modo de leitura.",
                                    );
                                  })
                                }
                              >
                                <Package size={16} />
                                Associar
                              </ActionButton>
                            )}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </>
        )}
      </section>
    </>
  );
}
