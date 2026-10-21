import { useState, type InputHTMLAttributes, type ReactNode } from "react";
import { ActionButton } from "./InteractionUI";
import { Empty } from "./Form";
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
};

/** Simple catalog/audit tables only. Financial and reconciliation grids retain their own controls. */
export function DataTable<T>({
  label,
  rows,
  columns,
  rowKey,
  searchText,
}: {
  label: string;
  rows: readonly T[];
  columns: readonly TableColumn<T>[];
  rowKey: (row: T) => string;
  searchText: (row: T) => string;
}) {
  const [query, setQuery] = useState("");
  const [page, setPage] = useState(1);
  const [size, setSize] = useState(10);
  const [sort, setSort] = useState<{ id: string; ascending: boolean } | null>(
    null,
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
          </select>
        </label>
      </FilterBar>
      <div className="table-wrap">
        <table className="responsive-data-table" aria-label={label}>
          <thead>
            <tr>
              {columns.map((item) => (
                <th
                  key={item.id}
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
              <tr key={rowKey(row)}>
                {columns.map((item) => (
                  <td key={item.id} data-label={item.label}>
                    <div>{item.render(row)}</div>
                  </td>
                ))}
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      {!filtered.length && (
        <Empty
          text={
            query
              ? "Sem resultados para esta pesquisa"
              : "Sem registos para apresentar"
          }
        />
      )}
      <div className="workspace-pagination">
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
      </div>
    </div>
  );
}
