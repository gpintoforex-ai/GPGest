import { useEffect, useRef, useState } from "react";
import { api } from "./lib/api";
import { articlePayload } from "./articleValues";
import {
  ActionButton,
  Notice,
  confirmAction,
} from "./components/InteractionUI";
import { Field } from "./components/Form";
import "./ArticleGrid.css";

type Row = Record<string, any>;
type Column = {
  id: string;
  label: string;
  sort?: string;
  cost?: boolean;
  render: (row: Row) => React.ReactNode;
};
const number = (value: unknown) =>
  value == null
    ? "Sem informação"
    : new Intl.NumberFormat("pt-PT", { maximumFractionDigits: 6 }).format(
        Number(value),
      );
const money = (value: unknown) =>
  value == null
    ? "Sem informação"
    : new Intl.NumberFormat("pt-PT", {
        style: "currency",
        currency: "EUR",
      }).format(Number(value));
const columns: Column[] = [
  {
    id: "code",
    label: "Código",
    sort: "code",
    render: (r) => <strong>{r.code}</strong>,
  },
  {
    id: "name",
    label: "Designação",
    sort: "name",
    render: (r) => (
      <>
        {r.photo && (
          <img
            src={r.photo}
            alt=""
            className="stock-photo"
            loading="lazy"
            referrerPolicy="no-referrer"
          />
        )}
        <strong>{r.name}</strong>
        <small>{[r.reference, r.brand].filter(Boolean).join(" · ")}</small>
      </>
    ),
  },
  {
    id: "family",
    label: "Família / categoria",
    sort: "family",
    render: (r) => (
      <>
        {r.family || "Sem família"}
        <small>{r.category}</small>
      </>
    ),
  },
  {
    id: "price",
    label: "Preço de venda",
    render: (r) => (
      <>
        {money(
          r.origin === "ERP"
            ? r.erp_price
            : r.sale_net == null
              ? null
              : Number(r.sale_net) * (1 + Number(r.vat_rate) / 100),
        )}
        <small>{r.origin === "ERP" ? r.erp_price_basis : "Com IVA"}</small>
      </>
    ),
  },
  {
    id: "cost",
    label: "Custo médio / última compra",
    cost: true,
    render: (r) => (
      <>
        {money(r.average_cost)}
        <small>{money(r.last_cost)}</small>
      </>
    ),
  },
  {
    id: "stock",
    label: "Stock disponível",
    render: (r) =>
      r.service ? (
        <>
          Serviço
          <small>Não movimenta stock</small>
        </>
      ) : (
        <>
          {number(r.available_quantity)}
          <small>
            Atual: {number(r.quantity)} · Reservado: {number(r.reserved_quantity)}
          </small>
          <small>{r.location}</small>
        </>
      ),
  },
  {
    id: "active",
    label: "Estado",
    sort: "active",
    render: (r) => (
      <span className={`article-badge ${r.active ? "success" : "muted"}`}>
        {r.active ? "Ativo" : "Inativo"}
      </span>
    ),
  },
  {
    id: "origin",
    label: "ERP / origem",
    sort: "origin",
    render: (r) => (
      <>
        {r.origin === "ERP" ? "Wintouch" : "Local"}
        <small>
          {r.synced_at
            ? `Catálogo recebido: ${new Date(r.synced_at).toLocaleDateString("pt-PT")}`
            : r.origin === "ERP"
              ? "Sem data de sincronização"
              : "Apenas local"}
        </small>
        <small>{r.stock_state}</small>
      </>
    ),
  },
  {
    id: "expiry",
    label: "Validade",
    render: (r) => (
      <span
        className={`article-badge ${["near", "expired"].includes(r.expiry_state) ? "warning" : "muted"}`}
      >
        {(
          {
            near: "Validade próxima",
            expired: "Lote expirado",
            valid: "Dentro do prazo",
            missing: "Sem validade",
          } as Row
        )[r.expiry_state] || "Sem informação"}
      </span>
    ),
  },
];
// Serviços e stock controlado pelo ERP não têm alerta de stock.
const stockAlert = (r: Row) =>
  r.service || r.available_quantity == null
    ? null
    : Number(r.available_quantity) <= 0
      ? "none"
      : Number(r.available_quantity) < Number(r.minimum || 0)
        ? "low"
        : null;
const defaults = { hidden: ["cost", "expiry"], density: "normal", size: 25 };
export default function ArticleGrid({
  catalog,
  revision,
  onOpen,
  onDuplicate,
  onPrice,
  onChanged,
}: {
  catalog: Row;
  revision: number;
  onOpen: (row: Row, readOnly?: boolean) => void;
  onDuplicate: (row: Row) => void;
  onPrice: (row: Row) => void;
  onChanged: (message: string) => void;
}) {
  const key = `iazela.articles.${catalog.preference_scope || "session"}.v1`;
  const [prefs, setPrefs] = useState(() => {
    try {
      const saved = JSON.parse(localStorage.getItem(key) || "null");
      return saved && Array.isArray(saved.hidden)
        ? {
            hidden: saved.hidden.filter(
              (id: unknown) => typeof id === "string" && id !== "name",
            ),
            density: ["compact", "normal", "comfortable"].includes(
              saved.density,
            )
              ? saved.density
              : "normal",
            size: [25, 50, 100].includes(saved.size) ? saved.size : 25,
          }
        : defaults;
    } catch {
      return defaults;
    }
  });
  const [query, setQuery] = useState(""),
    [search, setSearch] = useState("");
  const [filters, setFilters] = useState<Row>({
    family: "",
    category: "",
    brand: "",
    supplier_id: "",
    active: "",
    origin: "",
    stock: "",
    expiry: "",
  });
  const [sort, setSort] = useState({ id: "name", direction: "asc" });
  const [page, setPage] = useState(1),
    [data, setData] = useState<Row | null>(null);
  const [loading, setLoading] = useState(true),
    [error, setError] = useState(""),
    [retry, setRetry] = useState(0);
  const [actionError, setActionError] = useState("");
  const [selected, setSelected] = useState<string[]>([]),
    [busy, setBusy] = useState(false);
  const changing = useRef(false);
  const rights = catalog.permissions || {};
  useEffect(() => {
    try {
      localStorage.setItem(key, JSON.stringify(prefs));
    } catch {
      /* preferências continuam disponíveis nesta sessão */
    }
  }, [key, prefs]);
  useEffect(() => {
    const timer = setTimeout(() => {
      setSearch(query);
      setPage(1);
    }, 250);
    return () => clearTimeout(timer);
  }, [query]);
  useEffect(() => {
    let current = true;
    setLoading(true);
    setError("");
    setSelected([]);
    const params = new URLSearchParams({
      ...filters,
      q: search,
      page: String(page),
      size: String(prefs.size),
      sort: sort.id,
      direction: sort.direction,
    });
    api(`/stock/catalog?${params}`)
      .then((result) => {
        if (current) {
          setData(result);
          if (result.page && result.page !== page) setPage(result.page);
        }
      })
      .catch((e) => {
        if (current) setError(e.message);
      })
      .finally(() => {
        if (current) setLoading(false);
      });
    return () => {
      current = false;
    };
  }, [search, filters, page, prefs.size, sort, revision, retry]);
  const rows: Row[] = data?.articles || [];
  const visible = columns.filter(
    (c) => !prefs.hidden.includes(c.id) && (!c.cost || rights.view_cost),
  );
  const available = columns.filter((c) => !c.cost || rights.view_cost);
  const selectedRows = rows.filter((r) => selected.includes(r.id));
  const changeFilter = (name: string, value: string) => {
    setFilters((old) => ({ ...old, [name]: value }));
    setPage(1);
  };
  async function toggle(targets: Row[], active: boolean) {
    if (changing.current || !targets.length) return;
    if (
      !(await confirmAction({
        title: active ? "Ativar artigos?" : "Desativar artigos?",
        message: `${targets.length} artigo(s) locais serão ${active ? "ativados" : "desativados"}. O histórico é preservado.`,
        confirmLabel: active ? "Ativar" : "Desativar",
      }))
    )
      return;
    changing.current = true;
    setBusy(true);
    setActionError("");
    let done = 0;
    const failures: string[] = [];
    try {
      for (const row of targets) {
        try {
          await api(
            `/stock/articles/${row.id}`,
            { ...articlePayload(row), active },
            { method: "PUT" },
          );
          done++;
        } catch (e) {
          failures.push(`${row.code}: ${(e as Error).message}`);
        }
      }
      setSelected([]);
      if (done)
        onChanged(`${done} artigo(s) ${active ? "ativados" : "desativados"}.`);
      setRetry((v) => v + 1);
    } finally {
      changing.current = false;
      setBusy(false);
    }
    // Keep partial failures visible after the refresh has started.
    if (failures.length) setActionError(failures.join(" · "));
  }
  async function remove(row: Row) {
    if (
      changing.current ||
      !(await confirmAction({
        title: "Eliminar artigo?",
        message: `Eliminar «${row.name}»? O servidor só permite eliminar artigos sem histórico ou associações.`,
        confirmLabel: "Eliminar artigo",
        tone: "danger",
      }))
    )
      return;
    changing.current = true;
    setBusy(true);
    setActionError("");
    try {
      await api(`/stock/articles/${row.id}`, {}, { method: "DELETE" });
      onChanged("Artigo eliminado.");
    } catch (e) {
      setActionError((e as Error).message);
    } finally {
      changing.current = false;
      setBusy(false);
    }
  }
  function exportRows() {
    const targets = selectedRows.length ? selectedRows : rows;
    const escape = (value: unknown) =>
      `"${String(value ?? "")
        .replace(/^[=+@\-\t\r]/, "'$&")
        .replaceAll('"', '""')}"`;
    const csv = [
      [
        "Código",
        "Designação",
        "Referência",
        "Família",
        "Categoria",
        "Estado",
        "Origem",
      ],
      ...targets.map((r) => [
        r.code,
        r.name,
        r.reference,
        r.family,
        r.category,
        r.active ? "Ativo" : "Inativo",
        r.origin,
      ]),
    ]
      .map((row) => row.map(escape).join(";"))
      .join("\r\n");
    const url = URL.createObjectURL(
      new Blob(["\ufeff", csv], { type: "text/csv;charset=utf-8" }),
    );
    const link = document.createElement("a");
    link.href = url;
    link.download = "artigos.csv";
    link.click();
    setTimeout(() => URL.revokeObjectURL(url), 1000);
  }
  const operationDisabled = loading || busy;
  return (
    <section
      className={`panel article-grid density-${prefs.density}`}
      aria-label="Catálogo de artigos"
      aria-busy={loading || busy}
    >
      <div className="article-grid-heading">
        <div>
          <h2>Catálogo de artigos</h2>
          <p role="status">
            {loading
              ? "A carregar artigos…"
              : `${data?.total ?? rows.length} artigos encontrados · ${data?.total_all ?? rows.length} no catálogo`}
          </p>
        </div>
        <ActionButton
          disabled={operationDisabled || !rows.length}
          onClick={exportRows}
        >
          {selected.length ? "Exportar seleção" : "Exportar página"}
        </ActionButton>
      </div>
      <div className="article-grid-filters" data-ignore-dirty>
        <Field label="Pesquisar código, designação, barras ou referência do fornecedor">
          <input
            type="search"
            value={query}
            onChange={(e) => setQuery(e.target.value)}
          />
        </Field>
        <details className="article-advanced">
          <summary>
            Filtros avançados
            {Object.values(filters).some(Boolean) ? " · ativos" : ""}
          </summary>
          <div className="article-advanced-fields">
            {[
              ["family", "Família"],
              ["category", "Categoria"],
              ["brand", "Marca"],
            ].map(([name, label]) => (
              <Field key={name} label={label}>
                <select
                  value={filters[name]}
                  onChange={(e) => changeFilter(name, e.target.value)}
                >
                  <option value="">Todas</option>
                  {(data?.facets?.[name] || catalog.facets?.[name] || []).map(
                    (v: string) => (
                      <option key={v}>{v}</option>
                    ),
                  )}
                </select>
              </Field>
            ))}
            <Field label="Fornecedor">
              <select
                value={filters.supplier_id}
                onChange={(e) => changeFilter("supplier_id", e.target.value)}
              >
                <option value="">Todos</option>
                {(catalog.suppliers || []).map((r: Row) => (
                  <option key={r.id} value={r.id}>
                    {r.name}
                  </option>
                ))}
              </select>
            </Field>
            {(
              [
                [
                  "active",
                  "Estado",
                  [
                    ["true", "Ativos"],
                    ["false", "Inativos"],
                  ],
                ],
                [
                  "origin",
                  "Sincronização / origem",
                  [
                    ["Local", "Apenas local"],
                    ["ERP", "Associado ao ERP"],
                  ],
                ],
                [
                  "stock",
                  "Situação de stock",
                  [
                    ["positive", "Com stock"],
                    ["zero", "Sem stock / negativo"],
                    ["low", "Abaixo do mínimo"],
                    ["unknown", "Sem informação"],
                  ],
                ],
                [
                  "expiry",
                  "Validade",
                  [
                    ["near", "A terminar"],
                    ["expired", "Expirado"],
                    ["valid", "Dentro do prazo"],
                    ["missing", "Sem validade"],
                  ],
                ],
              ] as [string, string, string[][]][]
            ).map(([name, label, options]) => (
              <Field key={name} label={label}>
                <select
                  value={filters[name]}
                  onChange={(e) => changeFilter(name, e.target.value)}
                >
                  <option value="">Todos</option>
                  {options.map(([value, text]) => (
                    <option key={value} value={value}>
                      {text}
                    </option>
                  ))}
                </select>
              </Field>
            ))}
          </div>
        </details>
      </div>
      <div className="article-grid-tools" data-ignore-dirty>
        <Field label="Densidade">
          <select
            value={prefs.density}
            onChange={(e) =>
              setPrefs((p) => ({ ...p, density: e.target.value }))
            }
          >
            <option value="compact">Compacta</option>
            <option value="normal">Normal</option>
            <option value="comfortable">Confortável</option>
          </select>
        </Field>
        <Field label="Por página">
          <select
            value={prefs.size}
            onChange={(e) => {
              setPrefs((p) => ({ ...p, size: Number(e.target.value) }));
              setPage(1);
            }}
          >
            {[25, 50, 100].map((v) => (
              <option key={v}>{v}</option>
            ))}
          </select>
        </Field>
        <details className="article-columns">
          <summary>Colunas</summary>
          <div>
            {available.map((c) => (
              <label key={c.id}>
                <input
                  type="checkbox"
                  aria-label={`Mostrar coluna ${c.label}`}
                  checked={!prefs.hidden.includes(c.id)}
                  disabled={c.id === "name"}
                  onChange={(e) =>
                    setPrefs((p) => ({
                      ...p,
                      hidden: e.target.checked
                        ? p.hidden.filter((id: string) => id !== c.id)
                        : [...p.hidden, c.id],
                    }))
                  }
                />
                {c.label}
              </label>
            ))}
          </div>
        </details>
        <ActionButton
          onClick={() => {
            setQuery("");
            setFilters({
              family: "",
              category: "",
              brand: "",
              supplier_id: "",
              active: "",
              origin: "",
              stock: "",
              expiry: "",
            });
            setPage(1);
          }}
        >
          Limpar filtros
        </ActionButton>
      </div>
      {selected.length > 0 && (
        <div
          className="article-selection"
          role="region"
          aria-label="Ações da seleção"
        >
          <span>{selected.length} selecionado(s) nesta página</span>
          {rights.adjust_stock && (
            <>
              <ActionButton
                disabled={
                  operationDisabled ||
                  !selectedRows.some((r) => !r.active) ||
                  selectedRows.some((r) => r.origin !== "Local")
                }
                onClick={() =>
                  toggle(
                    selectedRows.filter((r) => !r.active),
                    true,
                  )
                }
              >
                Ativar seleção
              </ActionButton>
              <ActionButton
                disabled={
                  operationDisabled ||
                  !selectedRows.some((r) => r.active) ||
                  selectedRows.some((r) => r.origin !== "Local")
                }
                onClick={() =>
                  toggle(
                    selectedRows.filter((r) => r.active),
                    false,
                  )
                }
              >
                Desativar seleção
              </ActionButton>
            </>
          )}
          <ActionButton onClick={() => setSelected([])}>
            Limpar seleção
          </ActionButton>
        </div>
      )}
      {actionError && <Notice tone="error">{actionError}</Notice>}
      {error && (
        <Notice tone="error">
          {error}
          <ActionButton disabled={busy} onClick={() => setRetry((v) => v + 1)}>
            Voltar a tentar
          </ActionButton>
        </Notice>
      )}
      <div className="article-grid-scroll table-wrap">
        <table aria-label="Artigos">
          <thead>
            <tr>
              <th>
                <input
                  type="checkbox"
                  aria-label="Selecionar todos os artigos desta página"
                  checked={!!rows.length && selected.length === rows.length}
                  disabled={operationDisabled || !rows.length}
                  onChange={(e) =>
                    setSelected(e.target.checked ? rows.map((r) => r.id) : [])
                  }
                />
              </th>
              {visible.map((c) => (
                <th
                  key={c.id}
                  aria-sort={
                    c.sort
                      ? sort.id === c.sort
                        ? sort.direction === "asc"
                          ? "ascending"
                          : "descending"
                        : "none"
                      : undefined
                  }
                >
                  {c.sort ? (
                    <ActionButton
                      onClick={() => {
                        setSort({
                          id: c.sort!,
                          direction:
                            sort.id === c.sort && sort.direction === "asc"
                              ? "desc"
                              : "asc",
                        });
                        setPage(1);
                      }}
                    >
                      {c.label}
                      {sort.id === c.sort
                        ? sort.direction === "asc"
                          ? " ↑"
                          : " ↓"
                        : ""}
                    </ActionButton>
                  ) : (
                    c.label
                  )}
                </th>
              ))}
              <th>Ações</th>
            </tr>
          </thead>
          <tbody>
            {rows.map((r) => (
              <tr
                key={r.id}
                className={
                  stockAlert(r) === "none"
                    ? "article-no-stock"
                    : stockAlert(r) === "low"
                      ? "article-low-stock"
                      : ""
                }
              >
                <td>
                  <input
                    type="checkbox"
                    aria-label={`Selecionar ${r.name}`}
                    checked={selected.includes(r.id)}
                    disabled={operationDisabled}
                    onChange={(e) =>
                      setSelected((old) =>
                        e.target.checked
                          ? [...old, r.id]
                          : old.filter((id) => id !== r.id),
                      )
                    }
                  />
                </td>
                {visible.map((c) => (
                  <td key={c.id} data-label={c.label}>
                    {c.id === "name" ? (
                      <ActionButton
                        className="article-name"
                        disabled={operationDisabled}
                        onClick={() => onOpen(r, true)}
                      >
                        {c.render(r)}
                      </ActionButton>
                    ) : (
                      c.render(r)
                    )}
                    {c.id === "stock" && stockAlert(r) && (
                      <span className="article-badge warning">
                        {stockAlert(r) === "none" ? "Sem stock" : "Stock baixo"}
                      </span>
                    )}
                  </td>
                ))}
                <td>
                  <div className="article-row-actions">
                    <ActionButton
                      disabled={operationDisabled}
                      onClick={() => onOpen(r, true)}
                    >
                      Consultar
                    </ActionButton>
                    {(rights.adjust_stock || rights.change_price) && (
                      <details>
                        <summary aria-label={`Mais ações para ${r.name}`}>
                          Mais ações
                        </summary>
                        <div>
                          {rights.adjust_stock && (
                            <>
                              <ActionButton
                                aria-label={`Editar ${r.name}`}
                                disabled={operationDisabled}
                                onClick={() => onOpen(r)}
                              >
                                Editar
                              </ActionButton>
                              <ActionButton
                                disabled={operationDisabled}
                                onClick={() => onDuplicate(r)}
                              >
                                Duplicar
                              </ActionButton>
                            </>
                          )}
                          {rights.change_price && r.origin === "Local" && (
                            <ActionButton
                              disabled={operationDisabled}
                              onClick={() => onPrice(r)}
                            >
                              Preço
                            </ActionButton>
                          )}
                          {rights.adjust_stock && r.origin === "Local" && (
                            <>
                              <ActionButton
                                disabled={operationDisabled}
                                onClick={() => toggle([r], !r.active)}
                              >
                                {r.active ? "Desativar" : "Ativar"}
                              </ActionButton>
                              <ActionButton
                                disabled={operationDisabled}
                                className="danger"
                                onClick={() => remove(r)}
                              >
                                Eliminar
                              </ActionButton>
                            </>
                          )}
                        </div>
                      </details>
                    )}
                  </div>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      {!loading && !error && !rows.length && (
        <Notice tone="info">
          {data?.total_all
            ? "Nenhum artigo corresponde à pesquisa e aos filtros."
            : "O catálogo está vazio. Crie um artigo, importe um CSV ou associe artigos do ERP."}
        </Notice>
      )}
      <nav
        className="article-grid-pagination"
        aria-label="Paginação de artigos"
      >
        <span>
          Página {data?.page || page} de {data?.pages || 1}
        </span>
        <ActionButton
          disabled={operationDisabled || page <= 1}
          onClick={() => setPage((p) => p - 1)}
        >
          Anterior
        </ActionButton>
        <ActionButton
          disabled={operationDisabled || page >= (data?.pages || 1)}
          onClick={() => setPage((p) => p + 1)}
        >
          Seguinte
        </ActionButton>
      </nav>
      <p className="footnote">
        A seleção e a exportação abrangem apenas esta página. A data apresentada
        refere-se à receção do catálogo; alterações apenas no ERP são
        verificadas na próxima importação.
      </p>
    </section>
  );
}
