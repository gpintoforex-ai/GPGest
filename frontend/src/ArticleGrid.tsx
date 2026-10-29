import { useEffect, useRef, useState, type CSSProperties, type MouseEvent, type ReactNode } from "react";
import { MoreHorizontal, SlidersHorizontal } from "lucide-react";
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
  render: (row: Row) => ReactNode;
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
const day = (value: unknown) =>
  value ? String(value).slice(0, 10).split("-").reverse().join("/") : "";
// Serviços e stock controlado pelo ERP não têm alerta de stock.
const stockAlert = (r: Row) =>
  r.service || r.available_quantity == null
    ? null
    : Number(r.available_quantity) <= 0
      ? "none"
      : Number(r.available_quantity) < Number(r.minimum || 0)
        ? "low"
        : Number(r.maximum || 0) > 0 && Number(r.available_quantity) > Number(r.maximum)
          ? "over"
          : null;
const alertLabels: Row = { none: "Sem stock", low: "Stock baixo", over: "Acima do máximo" };
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
        {r.category && <small>{r.category}</small>}
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
        <small>Última: {money(r.last_cost)}</small>
      </>
    ),
  },
  {
    id: "stock",
    label: "Stock disponível",
    render: (r) => {
      if (r.service)
        return (
          <>
            Serviço
            <small>Não movimenta stock</small>
          </>
        );
      if (r.available_quantity == null)
        return (
          <>
            Sem informação
            <small>Stock no ERP, indisponível no conector</small>
          </>
        );
      const alert = stockAlert(r);
      // Só detalha o stock atual e o reservado quando diferem do disponível.
      const detail =
        Number(r.reserved_quantity || 0) > 0 ||
        Number(r.quantity) !== Number(r.available_quantity);
      return (
        <>
          {number(r.available_quantity)}
          {detail && (
            <small>
              Atual {number(r.quantity)} · reservado {number(r.reserved_quantity || 0)}
            </small>
          )}
          {r.location && <small>{r.location}</small>}
          {alert && (
            <span className={`article-badge ${alert === "over" ? "info" : "warning"}`}>
              {alertLabels[alert]}
            </span>
          )}
        </>
      );
    },
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
    render: (r) =>
      r.origin === "ERP" ? (
        <>
          <span className="article-badge info">Wintouch</span>
          <small>
            {r.synced_at
              ? `Catálogo recebido a ${new Date(r.synced_at).toLocaleDateString("pt-PT")}`
              : "Sem data de receção"}
          </small>
          {/* Nos artigos do ERP o stock é registado aqui (a Wintouch não o fornece). */}
          {r.stock_state === "movimentos locais" && <small>Stock: movimentos locais</small>}
        </>
      ) : (
        "Local"
      ),
  },
  {
    id: "expiry",
    label: "Validade",
    render: (r) =>
      ["near", "expired"].includes(r.expiry_state) ? (
        <>
          <span className="article-badge warning">
            {r.expiry_state === "near" ? "Validade próxima" : "Lote expirado"}
          </span>
          {r.nearest_expiry && <small>{day(r.nearest_expiry)}</small>}
        </>
      ) : r.expiry_state === "valid" ? (
        <>
          Dentro do prazo
          {r.nearest_expiry && <small>Até {day(r.nearest_expiry)}</small>}
        </>
      ) : (
        <span className="article-muted">
          {r.expiry_state === "missing" ? "Sem validade" : "Sem informação"}
        </span>
      ),
  },
];
const defaults = { hidden: ["cost", "expiry"], density: "normal", size: 25 };
const emptyFilters = {
  family: "",
  category: "",
  brand: "",
  supplier_id: "",
  active: "",
  origin: "",
  stock: "",
  expiry: "",
};

/**
 * Menu de ações da linha. Abre por cima da grelha (posição fixa), para não ficar cortado pela
 * área com deslocamento nem empurrar as linhas; fecha com Escape, clique fora ou deslocamento.
 */
function RowMenu({ label, children }: { label: string; children: ReactNode }) {
  const ref = useRef<HTMLDetailsElement>(null);
  const [place, setPlace] = useState<CSSProperties>({});
  // Acompanha o botão; fecha quando ele sai do ecrã.
  const position = () => {
    const menu = ref.current;
    if (!menu?.open) return;
    const rect = menu.querySelector("summary")!.getBoundingClientRect();
    if (rect.bottom < 0 || rect.top > window.innerHeight) {
      menu.open = false;
      return;
    }
    // Abre para cima quando não há espaço por baixo.
    const below = window.innerHeight - rect.bottom > 280;
    setPlace({
      right: Math.max(8, window.innerWidth - rect.right),
      ...(below
        ? { top: rect.bottom + 4 }
        : { bottom: window.innerHeight - rect.top + 4 }),
    });
  };
  useEffect(() => {
    const close = (event: Event) => {
      const menu = ref.current;
      if (menu?.open && !(event.target instanceof Node && menu.contains(event.target)))
        menu.open = false;
    };
    const escape = (event: KeyboardEvent) => {
      const menu = ref.current;
      if (event.key === "Escape" && menu?.open) {
        event.stopPropagation();
        menu.open = false;
        menu.querySelector("summary")?.focus();
      }
    };
    document.addEventListener("pointerdown", close);
    document.addEventListener("scroll", position, true);
    window.addEventListener("resize", position);
    document.addEventListener("keydown", escape, true);
    return () => {
      document.removeEventListener("pointerdown", close);
      document.removeEventListener("scroll", position, true);
      window.removeEventListener("resize", position);
      document.removeEventListener("keydown", escape, true);
    };
  }, []);
  return (
    <details
      ref={ref}
      className="article-menu"
      onToggle={(event) => {
        const menu = event.currentTarget;
        if (!menu.open) return;
        position();
        requestAnimationFrame(() =>
          menu.querySelector<HTMLButtonElement>("[role=menu] button")?.focus({ preventScroll: true }),
        );
      }}
    >
      <summary aria-label={label} title={label}>
        <MoreHorizontal size={18} aria-hidden="true" />
        <span>Ações</span>
      </summary>
      <div
        role="menu"
        style={place}
        onClick={(event) => {
          if ((event.target as HTMLElement).closest("button") && ref.current)
            ref.current.open = false;
        }}
      >
        {children}
      </div>
    </details>
  );
}

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
  const [filters, setFilters] = useState<Row>(emptyFilters);
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
  const tools = useRef<HTMLDivElement>(null);
  const rights = catalog.permissions || {};
  // Filtros avançados e Vista abrem por cima da grelha: fecham com clique fora ou Escape,
  // e abrir um fecha o outro.
  useEffect(() => {
    const panels = () =>
      Array.from(tools.current?.querySelectorAll<HTMLDetailsElement>(":scope > details") || []);
    const outside = (event: Event) => {
      for (const panel of panels())
        if (panel.open && !(event.target instanceof Node && panel.contains(event.target)))
          panel.open = false;
    };
    const escape = (event: KeyboardEvent) => {
      const open = panels().find((panel) => panel.open);
      if (event.key !== "Escape" || !open) return;
      event.stopPropagation();
      open.open = false;
      open.querySelector("summary")?.focus();
    };
    document.addEventListener("pointerdown", outside);
    document.addEventListener("keydown", escape, true);
    return () => {
      document.removeEventListener("pointerdown", outside);
      document.removeEventListener("keydown", escape, true);
    };
  }, []);
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
  const filtered = !!query || Object.values(filters).some(Boolean);
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
      new Blob(["﻿", csv], { type: "text/csv;charset=utf-8" }),
    );
    const link = document.createElement("a");
    link.href = url;
    link.download = "artigos.csv";
    link.click();
    setTimeout(() => URL.revokeObjectURL(url), 1000);
  }
  // Clicar numa zona sem controlos da linha abre a consulta (com teclado: o nome do artigo).
  function rowClick(event: MouseEvent, row: Row) {
    if (operationDisabled) return;
    const target = event.target as HTMLElement;
    if (target.closest("button, a, input, select, summary, details, label")) return;
    if (window.getSelection()?.toString()) return;
    onOpen(row, true);
  }
  const operationDisabled = loading || busy;
  const hasMenu = rights.adjust_stock || rights.change_price;
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
            placeholder="Código, designação, código de barras…"
            onChange={(e) => setQuery(e.target.value)}
          />
        </Field>
        <div className="article-grid-tools" ref={tools}>
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
                      ["over", "Acima do máximo"],
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
          <details className="article-view">
            <summary>
              <SlidersHorizontal size={16} aria-hidden="true" />
              Vista
            </summary>
            <div className="article-view-panel">
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
              <fieldset className="article-columns">
                <legend>Colunas</legend>
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
              </fieldset>
            </div>
          </details>
          {filtered && (
            <ActionButton
              onClick={() => {
                setQuery("");
                setFilters(emptyFilters);
                setPage(1);
              }}
            >
              Limpar filtros
            </ActionButton>
          )}
        </div>
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
              <th className="article-select">
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
                  className={c.sort ? "is-sortable" : undefined}
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
                    <button
                      type="button"
                      className="article-sort"
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
                      <span aria-hidden="true">
                        {sort.id === c.sort
                          ? sort.direction === "asc"
                            ? " ↑"
                            : " ↓"
                          : ""}
                      </span>
                    </button>
                  ) : (
                    c.label
                  )}
                </th>
              ))}
              {hasMenu && <th className="article-actions-head">Ações</th>}
            </tr>
          </thead>
          <tbody>
            {rows.map((r) => (
              <tr
                key={r.id}
                onClick={(event) => rowClick(event, r)}
                className={
                  stockAlert(r) === "none"
                    ? "article-no-stock"
                    : stockAlert(r) === "low"
                      ? "article-low-stock"
                      : ""
                }
              >
                <td className="article-select">
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
                  <td key={c.id} data-label={c.label} className={`article-col-${c.id}`}>
                    <div>
                      {c.id === "name" ? (
                        <button
                          type="button"
                          className="article-name"
                          title="Consultar artigo"
                          disabled={operationDisabled}
                          onClick={() => onOpen(r, true)}
                        >
                          {c.render(r)}
                        </button>
                      ) : (
                        c.render(r)
                      )}
                    </div>
                  </td>
                ))}
                {hasMenu && (
                  <td className="article-row-actions" data-label="Ações">
                    <RowMenu label={`Mais ações para ${r.name}`}>
                      <ActionButton
                        role="menuitem"
                        disabled={operationDisabled}
                        onClick={() => onOpen(r, true)}
                      >
                        Consultar
                      </ActionButton>
                      {rights.adjust_stock && (
                        <>
                          <ActionButton
                            role="menuitem"
                            aria-label={`Editar ${r.name}`}
                            disabled={operationDisabled}
                            onClick={() => onOpen(r)}
                          >
                            Editar
                          </ActionButton>
                          <ActionButton
                            role="menuitem"
                            disabled={operationDisabled}
                            onClick={() => onDuplicate(r)}
                          >
                            Duplicar
                          </ActionButton>
                        </>
                      )}
                      {rights.change_price && r.origin === "Local" && (
                        <ActionButton
                          role="menuitem"
                          disabled={operationDisabled}
                          onClick={() => onPrice(r)}
                        >
                          Preço
                        </ActionButton>
                      )}
                      {rights.adjust_stock && r.origin === "Local" && (
                        <>
                          <ActionButton
                            role="menuitem"
                            disabled={operationDisabled}
                            onClick={() => toggle([r], !r.active)}
                          >
                            {r.active ? "Desativar" : "Ativar"}
                          </ActionButton>
                          <ActionButton
                            role="menuitem"
                            disabled={operationDisabled}
                            className="danger"
                            onClick={() => remove(r)}
                          >
                            Eliminar
                          </ActionButton>
                        </>
                      )}
                    </RowMenu>
                  </td>
                )}
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
