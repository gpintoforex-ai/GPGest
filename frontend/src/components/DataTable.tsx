import { useState, type InputHTMLAttributes, type ReactNode } from "react";
import { Download } from "lucide-react";
import { ActionButton, Notice } from "./InteractionUI";
import { Empty } from "./Form";
import { downloadCsv } from "../lib/csv";
import "./DataTable.css";

export function SearchInput({
  label,
  ...props
}: InputHTMLAttributes<HTMLInputElement> & { label: string }) {
  return (
    <label className="workspace-search">
      {label}
      <input {...props} type="search" />
    </label>
  );
}
export function FilterBar({
  children,
  label,
}: {
  children: ReactNode;
  label: string;
}) {
  return (
    <div
      className="workspace-filterbar filter-bar"
      role="group"
      aria-label={label}
      data-ignore-dirty
    >
      {children}
    </div>
  );
}
export function ActionToolbar({
  children,
  label,
}: {
  children: ReactNode;
  label: string;
}) {
  return (
    <div className="actions" role="group" aria-label={label}>
      {children}
    </div>
  );
}
export type TableColumn<T> = {
  id: string;
  label: string;
  render: (row: T) => ReactNode;
  sortValue?: (row: T) => string | number;
  /** Valor na exportação CSV; colunas sem ele (ex.: ações) ficam fora do ficheiro. */
  exportValue?: (row: T) => unknown;
  /** Alinha à direita valores e quantidades. */
  numeric?: boolean;
};

/**
 * Listagens com os dados todos no navegador: pesquisa, ordenação, paginação, filtros próprios
 * (`toolbar`), exportação CSV dos resultados filtrados e estados de carregamento, erro e vazio.
 * Uma falha nunca é apresentada como lista vazia. Grelhas financeiras, editores de linhas e listas
 * paginadas no servidor mantêm os seus controlos.
 */
export function DataTable<T>({
  label,
  rows,
  columns,
  rowKey,
  searchText,
  rowClassName,
  toolbar,
  loading = false,
  error = "",
  emptyText = "Sem registos para apresentar",
  exportName,
  initialSort = null,
}: {
  label: string;
  rows: readonly T[];
  columns: readonly TableColumn<T>[];
  rowKey: (row: T) => string;
  searchText: (row: T) => string;
  /** Classe opcional por linha (ex.: cor do estado). */
  rowClassName?: (row: T) => string | undefined;
  /** Filtros próprios do módulo, apresentados junto à pesquisa. */
  toolbar?: ReactNode;
  /** Primeira consulta em curso: não mostra a tabela nem «sem registos». */
  loading?: boolean;
  /** Falha da consulta; as linhas recebidas antes continuam visíveis. */
  error?: string;
  emptyText?: string;
  /** Nome do ficheiro CSV; sem ele (ou sem colunas exportáveis) não há exportação. */
  exportName?: string;
  initialSort?: { id: string; ascending: boolean } | null;
}) {
  const [query, setQuery] = useState("");
  const [page, setPage] = useState(1);
  const [size, setSize] = useState(10);
  const [sort, setSort] = useState<{ id: string; ascending: boolean } | null>(
    initialSort,
  );
  const normalize = (value: string) =>
    value
      .normalize("NFD")
      .replace(/[\u0300-\u036f]/g, "")
      .toLocaleLowerCase("pt-PT")
      .trim();
  let filtered = rows.filter((row) =>
    normalize(searchText(row)).includes(normalize(query)),
  );
  const column = columns.find((item) => item.id === sort?.id);
  if (sort && column?.sortValue) {
    const value = column.sortValue;
    filtered = [...filtered].sort((a, b) => {
      const left = value(a),
        right = value(b);
      const result =
        typeof left === "number" && typeof right === "number"
          ? left - right
          : String(left).localeCompare(String(right), "pt-PT", {
              numeric: true,
            });
      return sort.ascending ? result : -result;
    });
  }
  const pages = Math.max(1, Math.ceil(filtered.length / size));
  const current = Math.min(page, pages);
  const exported = columns.filter((item) => item.exportValue);
  const exportRows = () =>
    downloadCsv(exportName!, [
      exported.map((item) => item.label),
      ...filtered.map((row) => exported.map((item) => item.exportValue!(row))),
    ]);
  const waiting = loading && !rows.length;
  return (
    <div className="workspace-data-table">
      <FilterBar label={`Filtros de ${label}`}>
        <SearchInput
          label={`Pesquisar ${label}`}
          value={query}
          onChange={(event) => {
            setQuery(event.target.value);
            setPage(1);
          }}
        />
        {toolbar}
        <label>
          Por página
          <select
            value={size}
            onChange={(event) => {
              setSize(Number(event.target.value));
              setPage(1);
            }}
          >
            <option>10</option>
            <option>25</option>
            <option>50</option>
            <option>100</option>
          </select>
        </label>
        {exportName && exported.length > 0 && (
          <ActionButton
            type="button"
            variant="secondary"
            disabled={!filtered.length}
            onClick={exportRows}
          >
            <Download size={16} aria-hidden="true" />
            Exportar CSV
          </ActionButton>
        )}
      </FilterBar>
      {error && <Notice tone="error" role="alert">{error}</Notice>}
      {waiting ? (
        <p role="status">A carregar…</p>
      ) : (
      <>
      <div className="table-wrap">
        <table className="responsive-data-table" aria-label={label}>
          <thead>
            <tr>
              {columns.map((item) => (
                <th
                  key={item.id}
                  className={item.numeric ? "numeric" : undefined}
                  aria-sort={
                    sort?.id === item.id
                      ? sort.ascending
                        ? "ascending"
                        : "descending"
                      : item.sortValue
                        ? "none"
                        : undefined
                  }
                >
                  {item.sortValue ? (
                    <ActionButton
                      type="button"
                      onClick={() =>
                        setSort({
                          id: item.id,
                          ascending: sort?.id !== item.id || !sort.ascending,
                        })
                      }
                    >
                      {item.label}
                      {sort?.id === item.id
                        ? sort.ascending
                          ? " ↑"
                          : " ↓"
                        : ""}
                    </ActionButton>
                  ) : (
                    item.label
                  )}
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {filtered.slice((current - 1) * size, current * size).map((row) => (
              <tr key={rowKey(row)} className={rowClassName?.(row)}>
                {columns.map((item) => (
                  <td key={item.id} data-label={item.label} className={item.numeric ? "numeric" : undefined}>
                    <div>{item.render(row)}</div>
                  </td>
                ))}
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      {!filtered.length && !(error && !rows.length) && (
        <Empty
          text={
            query && rows.length
              ? "Sem resultados para esta pesquisa"
              : emptyText
          }
        />
      )}
      </>
      )}
      {!waiting && <div className="workspace-pagination">
        <span role="status">
          {filtered.length} registo(s) · Página {current} de {pages}
        </span>
        <ActionButton
          type="button"
          disabled={current <= 1}
          onClick={() => setPage(current - 1)}
        >
          Anterior
        </ActionButton>
        <ActionButton
          type="button"
          disabled={current >= pages}
          onClick={() => setPage(current + 1)}
        >
          Seguinte
        </ActionButton>
      </div>}
    </div>
  );
}
