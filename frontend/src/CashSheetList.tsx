import { useEffect, useState, type ReactNode } from "react";
import "./RecordList.css";

type Row = Record<string, any>;

export default function CashSheetList({
  rows,
  store,
  children,
}: {
  rows: Row[];
  store: string;
  children: (row: Row) => ReactNode;
}) {
  const [size, setSize] = useState(5);
  const [page, setPage] = useState(1);
  useEffect(() => {
    setPage(1);
  }, [store]);
  const pages = Math.max(1, Math.ceil(rows.length / size));
  const current = Math.min(page, pages);
  const start = (current - 1) * size;

  return (
    <>
      <div className="record-filters">
        <label className="record-field">
          Folhas por página
          <select
            value={size}
            onChange={(e) => {
              setSize(Number(e.target.value));
              setPage(1);
            }}
          >
            {[5, 10, 25, 50].map((n) => (
              <option key={n} value={n}>
                {n}
              </option>
            ))}
          </select>
        </label>
      </div>
      <div className="cash-sheet-list">
        {rows.slice(start, start + size).map(children)}
      </div>
      <nav
        className="record-pagination"
        aria-label="Paginação das folhas de caixa"
      >
        <span role="status">
          A mostrar {rows.length ? start + 1 : 0}–
          {Math.min(start + size, rows.length)} de {rows.length} folhas
        </span>
        <div>
          <button
            type="button"
            disabled={current === 1}
            onClick={() => setPage(current - 1)}
          >
            Anterior
          </button>
          <span>
            Página {current} de {pages}
          </span>
          <button
            type="button"
            disabled={current === pages}
            onClick={() => setPage(current + 1)}
          >
            Seguinte
          </button>
        </div>
      </nav>
    </>
  );
}
