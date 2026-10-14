import { useEffect, useState, type ReactNode } from "react";
import { api } from "./App";
import RecordList, { calendarDay, paymentGroup } from "./RecordList";

type Row = Record<string, any>;

function chronology(value: string) {
  const time = /(Z|[+-]\d{2}:?\d{2})$/.test(value)
    ? new Intl.DateTimeFormat("en-GB", {
        timeZone: "Europe/Lisbon",
        hour: "2-digit",
        minute: "2-digit",
        second: "2-digit",
        hourCycle: "h23",
      }).format(new Date(value))
    : value.slice(11, 19);
  return calendarDay(value) + " " + time;
}

export default function RealMovements({
  localRows,
  store,
  registers,
  children,
}: {
  localRows: Row[];
  store: string;
  registers: Row[];
  children: (rows: Row[]) => ReactNode;
}) {
  const [sourceRows, setSourceRows] = useState<Row[]>([]);
  const [allLocalRows, setAllLocalRows] = useState<Row[]>([]);
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(true);
  const [documents, setDocuments] = useState(false);
  useEffect(() => {
    let active = true;
    const load = async () => {
      if (active) setLoading(true);
      try {
        const [result, local] = await Promise.all([api("/integrations/wintouch/sales"), api("/movements?all_records=true")]);
        if (active) {
          setSourceRows(result);
          setAllLocalRows(local);
          setError("");
        }
      } catch (e) {
        if (active) setError((e as Error).message);
      } finally {
        if (active) setLoading(false);
      }
    };
    load();
    window.addEventListener("gpgest-sales-updated", load);
    return () => {
      active = false;
      window.removeEventListener("gpgest-sales-updated", load);
    };
  }, [localRows]);
  const imported = sourceRows
    .filter(
      (r) =>
        (!store || r.store_id === store) &&
        r.kind === (documents ? "documento" : "real"),
    )
    .map<Row>((r) => ({
      ...r,
      id: `wintouch:${r.id}`,
      external: true,
      origin: "wintouch",
      kind: documents ? "documento" : r.source_kind === "cash_movement" ? r.direction > 0 ? "entrada_caixa" : "saida_caixa" : r.direction > 0 ? "cliente" : "reembolso",
      category_name: r.category_name || (r.source_kind === "cash_movement" ? r.direction > 0 ? "Fundo de maneio" : null : r.document_group === "vendas" ? "Vendas" : null),
    }));
  const local = [...new Map([...allLocalRows, ...localRows].map(row => [row.id, row])).values()]
    .filter(row => !store || registers.find(register => register.id === row.register_id)?.store_id === store);
  const rows = [...(documents ? [] : local), ...imported]
    .map<Row>((r) => {
      const physical = ["Numerário", "Cheque", "Cheques", "Numerário e cheques"].includes(paymentGroup(r));
      const bankId = r.bank_account_id;
      if (!documents && !physical) return {
        ...r,
        filter_register: bankId ? `bank:${bankId}` : "bank:unassigned",
        filter_register_name: bankId ? `Banco · ${r.bank_account_name || r.bank_name || "Conta bancária"}` : "Bancos · conta por associar",
      };
      return {
      ...r,
      filter_register:
        r.register_id ||
        (r.account_id ? `wintouch:${r.account_id}` : "unassigned"),
      filter_register_name:
        registers.find((register) => register.id === r.register_id)?.name ||
        (r.account_id
          ? `${r.account_name || r.account_id} (Wintouch)`
          : "Sem caixa identificada"),
      };
    })
    .sort(
      (a, b) =>
        chronology(b.occurred_at).localeCompare(chronology(a.occurred_at)) ||
        a.id.localeCompare(b.id),
    );
  return (
    <section className="panel">
      <div className="actions">
        <button onClick={() => setDocuments(!documents)}>
          {documents
            ? "Voltar aos movimentos reais"
            : "Ver documentos de venda Wintouch"}
        </button>
      </div>
      {documents && (
        <p>
          Documentos de venda Wintouch. O documento, por si só, não comprova um
          recebimento.
        </p>
      )}
      {error && (
        <p className="error" role="alert">
          Não foi possível carregar todos os movimentos: {error}
        </p>
      )}
      {loading && <p role="status">A carregar movimentos…</p>}
      <RecordList
        rows={rows}
        dateField="occurred_at"
        resetKey={`${store}-${documents}`}
        totalField={documents ? undefined : "amount_cents"}
        paymentSummary={!documents}
        accumulatedTotal={!documents && !error && !loading}
        movementFilters
      >
        {children}
      </RecordList>
    </section>
  );
}
