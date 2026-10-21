import DatePresets from "./components/DatePresets";
import { ActionButton, Notice } from "./components/InteractionUI";
import { useEffect, useId, useRef, useState, type ReactNode } from "react";
import { ArrowLeftRight, Banknote, CreditCard, FileText, Smartphone, Wallet } from "lucide-react";
import "./RecordList.css";

type Row = Record<string, any>;

function PaymentLabel({ name }: { name: string }) {
  const Icon = name === "Numerário" ? Banknote
    : name === "MB WAY" ? Smartphone
    : name === "Cartão" ? CreditCard
    : name === "Transferência" ? ArrowLeftRight
    : name === "Cheque" || name === "Cheques" ? FileText : Wallet;
  return <span className="record-payment-label"><Icon size={18} aria-hidden="true" /><span>{name}</span></span>;
}

export function paymentGroup(row: Row) {
  if (String(row.payment).startsWith("pm_") && row.payment_name) return String(row.payment_name);
  const name = String(row.payment || "Não indicado").trim();
  const normalized = name
    .normalize("NFD")
    .replace(/[\u0300-\u036f]/g, "")
    .toLowerCase();
  const codes: Record<string, string> = {
    NU: "Numerário",
    MO: "Numerário",
    CD: "Cartão",
    CC: "Cartão",
    TB: "Transferência",
    CH: "Cheque",
  };
  const names: Record<string, string> = {
    numerario: "Numerário",
    mbway: "MB WAY",
    "mb way": "MB WAY",
    cartao: "Cartão",
    "cartao de debito": "Cartão",
    "cartao de credito": "Cartão",
    multibanco: "Cartão",
    transferencia: "Transferência",
    "transferencia bancaria": "Transferência",
    outro: "Outro",
    cheque: "Cheques",
    misto: "Numerário e cheques",
  };
  const codeLabel = codes[row.payment_code];
  const nameLabel = names[normalized];
  return (
    (typeof codeLabel === "string" ? codeLabel : undefined) ||
    (typeof nameLabel === "string" ? nameLabel : undefined) ||
    name ||
    "Não indicado"
  );
}

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

export function currentMonth() {
  const today = calendarDay(new Date().toISOString());
  const [year, month] = today.split("-").map(Number);
  const prefix = today.slice(0, 7);
  const lastDay = new Date(Date.UTC(year, month, 0)).getUTCDate();
  return {
    from: `${prefix}-01`,
    to: `${prefix}-${String(lastDay).padStart(2, "0")}`,
  };
}

export default function RecordList({
  rows,
  dateField,
  dateLabel = "Data do movimento",
  resetKey,
  totalField,
  paymentSummary = false,
  accumulatedTotal = false,
  totalsTitle = "Totais do período selecionado",
  movementFilters = false,
  movementAccounts = [],
  plannedFilters = false,
  startAtOldestPending = false,
  defaultToToday = false,
  children,
}: {
  rows: Row[];
  dateField: string;
  dateLabel?: string;
  resetKey: string;
  totalField?: string;
  paymentSummary?: boolean;
  accumulatedTotal?: boolean;
  totalsTitle?: string;
  movementFilters?: boolean;
  movementAccounts?: { id: string; name: string }[];
  plannedFilters?: boolean;
  startAtOldestPending?: boolean;
  defaultToToday?: boolean;
  children: (pageRows: Row[]) => ReactNode;
}) {
  const id = useId();
  const [initialRange] = useState(() => {
    if (defaultToToday) {
      const today = calendarDay(new Date().toISOString());
      return { from: today, to: today };
    }
    return currentMonth();
  });
  const [from, setFrom] = useState(initialRange.from);
  const [to, setTo] = useState(initialRange.to);
  const [size, setSize] = useState(10);
  const [page, setPage] = useState(1);
  const [register, setRegister] = useState("");
  const [payment, setPayment] = useState("");
  const [entity, setEntity] = useState("");
  const [documentGroup, setDocumentGroup] = useState("");
  const fromEdited = useRef(false);
  const oldestPending = rows
    .filter((row) => row.remaining_cents > 0)
    .map((row) => calendarDay(row[dateField]))
    .filter(Boolean)
    .sort()[0] || initialRange.from;
  const normalize = (value: string) =>
    value
      .normalize("NFD")
      .replace(/[\u0300-\u036f]/g, "")
      .toLocaleLowerCase("pt-PT")
      .trim();
  const registers = [
    ...new Map(
      [...movementAccounts.map(account => [account.id, account.name]), ...rows.map((r) => [
        r.filter_register || "unassigned",
        r.filter_register_name || "Sem caixa identificada",
      ])] as [string, string][],
    ).entries(),
  ].sort((a, b) => a[1].localeCompare(b[1], "pt-PT"));
  const payments = [...new Set(rows.map(paymentGroup))].sort((a, b) =>
    a.localeCompare(b, "pt-PT"),
  );
  useEffect(() => {
    setPage(1);
    setRegister("");
    setPayment("");
    setEntity("");
    setDocumentGroup("");
    fromEdited.current = false;
  }, [resetKey]);
  useEffect(() => {
    if (startAtOldestPending && !fromEdited.current) {
      setFrom(oldestPending);
      setPage(1);
    }
  }, [startAtOldestPending, oldestPending, resetKey]);
  const invalid = Boolean(from && to && from > to);
  const matching = (r: Row, includeStart = true) => {
        const day = calendarDay(r[dateField]);
        return (
          (!includeStart || !from || day >= from) &&
          (!to || (day !== "" && day <= to)) &&
          (!plannedFilters ||
            !documentGroup ||
            r.document_group === documentGroup) &&
          (!(movementFilters || plannedFilters) ||
            !entity ||
            normalize(String(r.entity || "")).includes(normalize(entity))) &&
          (!movementFilters ||
            ((!register || (r.filter_register || "unassigned") === register) &&
              (!payment || paymentGroup(r) === payment) &&
              (!entity ||
                normalize(String(r.entity || "")).includes(normalize(entity)))))
        );
      };
  const filtered = invalid ? [] : rows.filter(r => matching(r));
  // Linhas só de consulta (ex.: extrato bancário) não entram nos totais: evitam somar
  // duas vezes o mesmo recebimento já registado na aplicação.
  const counted = filtered.filter(r => !r.excluded_from_totals);
  const consultOnly = filtered.length - counted.length;
  const accumulated = totalField && accumulatedTotal && !invalid
    ? rows.filter(r => matching(r, false) && !r.excluded_from_totals).reduce((sum, row) => sum + (row.direction === 1 ? 1 : row.direction === -1 ? -1 : 0) * Number(row[totalField] || 0), 0)
    : 0;
  const pages = Math.max(1, Math.ceil(filtered.length / size));
  const current = Math.min(page, pages);
  const start = (current - 1) * size;
  const credits = totalField
    ? counted.reduce(
        (sum, row) => sum + (row.direction === 1 ? row[totalField] : 0),
        0,
      )
    : 0;
  const debits = totalField
    ? counted.reduce(
        (sum, row) => sum + (row.direction === -1 ? row[totalField] : 0),
        0,
      )
    : 0;
  const money = (value: number) =>
    new Intl.NumberFormat("pt-PT", {
      style: "currency",
      currency: "EUR",
    }).format(value / 100);
  const groups = new Map<
    string,
    { name: string; count: number; credits: number; debits: number }
  >();
  if (paymentSummary && totalField) {
    for (const row of counted) {
      const name = paymentGroup(row);
      const key = name.toLocaleLowerCase("pt-PT");
      const group = groups.get(key) || {
        name,
        count: 0,
        credits: 0,
        debits: 0,
      };
      group.count += 1;
      if (row.direction === 1) group.credits += row[totalField];
      if (row.direction === -1) group.debits += row[totalField];
      groups.set(key, group);
    }
  }
  return (
    <>
      <div
        className="record-filters filter-bar"
        role="group"
        aria-label={`Filtros: ${dateLabel}`}
      >
        {plannedFilters && (
          <>
            <div className="record-field">
              <label htmlFor={`${id}-document-group`}>Vendas / compras</label>
              <select
                id={`${id}-document-group`}
                value={documentGroup}
                onChange={(e) => {
                  setDocumentGroup(e.target.value);
                  setPage(1);
                }}
              >
                <option value="">Vendas e compras</option>
                <option value="vendas">Vendas</option>
                <option value="compras">Compras</option>
              </select>
            </div>
            <div className="record-field">
              <label htmlFor={`${id}-planned-entity`}>Entidade</label>
              <input
                id={`${id}-planned-entity`}
                type="search"
                placeholder="Pesquisar entidade"
                value={entity}
                onChange={(e) => {
                  setEntity(e.target.value);
                  setPage(1);
                }}
              />
            </div>
          </>
        )}
        {movementFilters && (
          <>
            <div className="record-field">
              <label htmlFor={`${id}-register`}>Caixas / bancos</label>
              <select
                id={`${id}-register`}
                value={register}
                onChange={(e) => {
                  setRegister(e.target.value);
                  setPage(1);
                }}
              >
                <option value="">Todas as caixas e bancos</option>
                {registers.map(([key, name]) => (
                  <option key={key} value={key}>
                    {name}
                  </option>
                ))}
              </select>
            </div>
            <div className="record-field">
              <label htmlFor={`${id}-payment`}>Meio de pagamento</label>
              <select
                id={`${id}-payment`}
                value={payment}
                onChange={(e) => {
                  setPayment(e.target.value);
                  setPage(1);
                }}
              >
                <option value="">Todos os meios</option>
                {payments.map((name) => (
                  <option key={name} value={name}>
                    {name}
                  </option>
                ))}
              </select>
            </div>
            <div className="record-field">
              <label htmlFor={`${id}-entity`}>Entidade</label>
              <input
                id={`${id}-entity`}
                type="search"
                placeholder="Pesquisar entidade"
                value={entity}
                onChange={(e) => {
                  setEntity(e.target.value);
                  setPage(1);
                }}
              />
            </div>
          </>
        )}
        <div className="record-field">
          <label htmlFor={`${id}-from`}>{dateLabel} desde</label>
          <input
            id={`${id}-from`}
            type="date"
            value={from}
            onChange={(e) => {
              setFrom(e.target.value);
              fromEdited.current = true;
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
        {(plannedFilters || movementFilters) && <ActionButton
          type="button"
          onClick={() => {
            if (plannedFilters) setDocumentGroup("");
            if (movementFilters) {
              setRegister("");
              setPayment("");
            }
            setEntity("");
            setPage(1);
          }}
        >
          Limpar filtros
        </ActionButton>}
        <ActionButton
          type="button"
          onClick={() => {
            setFrom("");
            fromEdited.current = true;
            setTo("");
            setPage(1);
          }}
        >
          Limpar datas
        </ActionButton>
        <DatePresets from={from} to={to} onSelect={(start, end) => { setFrom(start); setTo(end); fromEdited.current = true; setPage(1); }} />
      </div>
      {invalid && (
        <Notice className="error" role="alert">
          A data inicial não pode ser posterior à data final.
        </Notice>
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
          {accumulatedTotal && <div className="record-accumulated"><div><b>Valor acumulado</b><small>Entradas menos saídas até à data final, incluindo movimentos anteriores ao período. Mantém os filtros de caixas / bancos, meio de pagamento e entidade.</small></div><strong>{invalid ? "—" : money(accumulated)}</strong></div>}
          <small>
            {totalsTitle === "Totais por liquidar no período"
              ? "Valores ainda por receber e pagar, segundo o vencimento selecionado."
              : "Totais de todos os registos filtrados, incluindo as outras páginas."}
            {consultOnly > 0 && ` ${consultOnly} linha(s) de extrato bancário apresentadas apenas para consulta, fora dos totais: o mesmo dinheiro já está nos movimentos registados ou deve ser registado em Bancos → Extratos.`}
          </small>
        </section>
      )}
      {paymentSummary && totalField && (
        <section
          aria-label="Resumo por meio de pagamento"
          className="record-payment-summary"
        >
          <h3>Resumo por meio de pagamento</h3>
          <div className="table-wrap">
            <table>
              <thead>
                <tr>
                  <th>Meio de pagamento</th>
                  <th>Registos</th>
                  <th className="numeric">Créditos</th>
                  <th className="numeric">Débitos</th>
                  <th className="numeric">Saldo</th>
                </tr>
              </thead>
              <tbody>
                {[...groups.values()]
                  .sort((a, b) => a.name.localeCompare(b.name, "pt-PT"))
                  .map((group) => (
                    <tr key={group.name}>
                      <td><PaymentLabel name={group.name} /></td>
                      <td>{group.count}</td>
                      <td className="positive numeric">{money(group.credits)}</td>
                      <td className="negative numeric">{money(group.debits)}</td>
                      <td
                        className={["numeric", group.credits >= group.debits
                            ? "positive"
                            : "negative"].filter(Boolean).join(" ")}
                      >
                        {money(group.credits - group.debits)}
                      </td>
                    </tr>
                  ))}
              </tbody>
              <tfoot>
                <tr>
                  <th className="numeric">Total</th>
                  <td>{filtered.length}</td>
                  <td className="numeric">{money(credits)}</td>
                  <td className="numeric">{money(debits)}</td>
                  <td className="numeric">{money(credits - debits)}</td>
                </tr>
              </tfoot>
            </table>
          </div>
          {!filtered.length && <p>Sem movimentos no período selecionado.</p>}
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
          <ActionButton
            type="button"
            disabled={current === 1}
            onClick={() => setPage(current - 1)}
          >
            Anterior
          </ActionButton>
          <span>
            Página {current} de {pages}
          </span>
          <ActionButton
            type="button"
            disabled={current === pages}
            onClick={() => setPage(current + 1)}
          >
            Seguinte
          </ActionButton>
        </div>
      </nav>
    </>
  );
}
