import { useEffect, useId, useState, type ReactNode } from "react";
import "./RecordList.css";

type Row = Record<string, any>;

// Naive source dates retain their calendar day; timestamps with offsets use Lisbon.
export function calendarDay(value: string): string {
  if (!value) return "";
  if (!/(Z|[+-]\d{2}:?\d{2})$/.test(value)) return value.slice(0, 10);
  const stamp = new Date(value);
  if (Number.isNaN(stamp.getTime())) return "";
  const parts = new Intl.DateTimeFormat("en-CA", {
    timeZone: "Europe/Lisbon",
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
  }).formatToParts(stamp);
  return ["year", "month", "day"]
    .map((k) => parts.find((p) => p.type === k)?.value)
    .join("-");
}

export default function RecordList({
  rows,
  dateField,
  dateLabel = "Data do movimento",
  resetKey,
  totalField,
  totalsTitle = "Totais do período selecionado",
  children,
}: {
  rows: Row[];
  dateField: string;
  dateLabel?: string;
  resetKey: string;
  totalField?: string;
  totalsTitle?: string;
  children: (pageRows: Row[]) => ReactNode;
}) {
  const id = useId();
  const [from, setFrom] = useState("");
  const [to, setTo] = useState("");
  const [size, setSize] = useState(10);
  const [page, setPage] = useState(1);
  useEffect(() => {
    setPage(1);
  }, [resetKey]);
  const invalid = Boolean(from && to && from > to);
  const filtered = invalid
    ? []
    : rows.filter((r) => {
        const day = calendarDay(r[dateField]);
        return (!from || day >= from) && (!to || (day !== "" && day <= to));
      });
  const pages = Math.max(1, Math.ceil(filtered.length / size));
  const current = Math.min(page, pages);
  const start = (current - 1) * size;
  const credits = totalField
    ? filtered.reduce(
        (sum, row) => sum + (row.direction === 1 ? row[totalField] : 0),
        0,
      )
    : 0;
  const debits = totalField
    ? filtered.reduce(
        (sum, row) => sum + (row.direction === -1 ? row[totalField] : 0),
        0,
      )
    : 0;
  const money = (value: number) =>
    new Intl.NumberFormat("pt-PT", {
      style: "currency",
      currency: "EUR",
    }).format(value / 100);
  return (
    <>
      <div
        className="record-filters"
        role="group"
        aria-label={`Filtros: ${dateLabel}`}
      >
        <div className="record-field">
          <label htmlFor={`${id}-from`}>{dateLabel} desde</label>
          <input
            id={`${id}-from`}
            type="date"
            value={from}
            onChange={(e) => {
              setFrom(e.target.value);
              setPage(1);
            }}
          />
        </div>
        <div className="record-field">
          <label htmlFor={`${id}-to`}>Até</label>
          <input
            id={`${id}-to`}
            type="date"
            value={to}
            onChange={(e) => {
              setTo(e.target.value);
              setPage(1);
            }}
          />
        </div>
        <div className="record-field">
          <label htmlFor={`${id}-size`}>Registos por página</label>
          <select
            id={`${id}-size`}
            value={size}
            onChange={(e) => {
              setSize(Number(e.target.value));
              setPage(1);
            }}
          >
            {[10, 25, 50, 100].map((n) => (
              <option key={n} value={n}>
                {n}
              </option>
            ))}
          </select>
        </div>
        <button
          type="button"
          onClick={() => {
            setFrom("");
            setTo("");
            setPage(1);
          }}
        >
          Limpar datas
        </button>
      </div>
      {invalid && (
        <p className="error" role="alert">
          A data inicial não pode ser posterior à data final.
        </p>
      )}
      {totalField && (
        <section className="record-totals" aria-label={totalsTitle}>
          <h3>{totalsTitle}</h3>
          <dl>
            <div>
              <dt>Créditos (entradas)</dt>
              <dd className="positive">{money(credits)}</dd>
            </div>
            <div>
              <dt>Débitos (saídas)</dt>
              <dd className="negative">{money(debits)}</dd>
            </div>
            <div>
              <dt>Saldo (créditos − débitos)</dt>
              <dd className={credits >= debits ? "positive" : "negative"}>
                {money(credits - debits)}
              </dd>
            </div>
          </dl>
          <small>
            {totalsTitle === "Totais por liquidar no período"
              ? "Valores ainda por receber e pagar, segundo o vencimento selecionado."
              : "Totais de todos os registos filtrados, incluindo as outras páginas."}
          </small>
        </section>
      )}
      {children(filtered.slice(start, start + size))}
      {!filtered.length && rows.length > 0 && !invalid && (
        <p>Sem registos para o período selecionado.</p>
      )}
      <nav className="record-pagination" aria-label={`Paginação: ${dateLabel}`}>
        <span role="status">
          A mostrar {filtered.length ? start + 1 : 0}–
          {Math.min(start + size, filtered.length)} de {filtered.length}{" "}
          registos
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
