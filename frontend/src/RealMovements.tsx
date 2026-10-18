import { ActionButton, Notice } from "./components/InteractionUI";
import { useEffect, useState, type ReactNode } from "react";
import { api } from "./lib/api";
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
  accounts,
  integrated,
  children,
}: {
  localRows: Row[];
  store: string;
  registers: Row[];
  accounts: Row[];
  integrated: boolean;
  children: (rows: Row[]) => ReactNode;
}) {
  const [sourceRows, setSourceRows] = useState<Row[]>([]);
  const [allLocalRows, setAllLocalRows] = useState<Row[]>([]);
  const [bankRows, setBankRows] = useState<Row[]>([]);
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(true);
  const [documents, setDocuments] = useState(false);
  const showDocuments = integrated && documents;
  useEffect(() => {
    let active = true;
    const load = async () => {
      if (active) setLoading(true);
      try {
        const [result, local, bank] = await Promise.all([
          integrated ? api("/integrations/wintouch/sales") : Promise.resolve([]),
          api("/movements?all_records=true"),
          Promise.all(accounts.map(async account => {
            const transactions = await api(`/accounts/${account.id}/transactions`);
            return transactions.map((transaction: Row) => ({
              ...transaction,
              id: `statement:${transaction.id}`,
              external: true,
              origin: "bank_statement",
              kind: "extrato_bancario",
              occurred_at: `${transaction.movement_date}T00:00:00`,
              direction: transaction.amount_cents < 0 ? -1 : 1,
              amount_cents: Math.abs(transaction.amount_cents),
              entity: transaction.description,
              payment: "transferencia",
              bank_account_id: account.id,
              bank_account_name: account.name,
              bank_name: account.bank,
              bank_store_id: account.store_id,
            }));
          })).then(groups => groups.flat()),
        ]);
        if (active) {
          setSourceRows(result);
          setAllLocalRows(local);
          setBankRows(bank);
          setError("");
        }
      } catch (e) {
        if (active) setError((e as Error).message);
      } finally {
        if (active) setLoading(false);
      }
    };
    load();
    return () => {
      active = false;
    };
  }, [localRows, integrated, accounts]);
  useEffect(() => {
    if (!integrated) { setDocuments(false); setSourceRows([]); }
  }, [integrated]);
  const imported = (integrated ? sourceRows : [])
    .filter(
      (r) =>
        (!store || r.store_id === store) &&
        r.kind === (showDocuments ? "documento" : "real"),
    )
    .map<Row>((r) => ({
      ...r,
      id: `wintouch:${r.id}`,
      external: true,
      origin: "wintouch",
      kind: showDocuments ? "documento" : r.source_kind === "cash_movement" ? r.direction > 0 ? "entrada_caixa" : "saida_caixa" : r.direction > 0 ? "cliente" : "reembolso",
      category_name: r.category_name || (r.source_kind === "cash_movement" ? r.direction > 0 ? "Fundo de maneio" : null : r.document_group === "vendas" ? "Vendas" : null),
    }));
  const local = [...new Map([...allLocalRows, ...localRows].map(row => [row.id, row])).values()]
    .filter(row => !store || (registers.find(register => register.id === row.register_id)?.store_id || row.bank_store_id) === store)
    .flatMap<Row>(row => Array.isArray(row.supplier_parts) ? row.supplier_parts.map((part: Row, index: number) => ({
      ...row, id: `${row.id}:supplier:${index}`, movement_id: row.id, entity: part.name || "Por atribuir",
      amount_cents: part.amount_cents, supplier_id: part.supplier_id,
    })) : [{ ...row, entity: row.supplier_unassigned ? "Por atribuir" : row.entity }]);
  const rows = [...(showDocuments ? [] : [...local, ...bankRows.filter(row => !store || row.bank_store_id === store)]), ...imported]
    .map<Row>((r) => {
      const physical = ["Numerário", "Cheque", "Cheques", "Numerário e cheques"].includes(paymentGroup(r));
      const bankId = r.bank_account_id;
      if (!showDocuments && (bankId || !physical)) return {
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
      {integrated && <div className="actions">
        <ActionButton onClick={() => setDocuments(!documents)}>
          {showDocuments
            ? "Voltar aos movimentos reais"
            : "Ver documentos de venda Wintouch"}
        </ActionButton>
      </div>}
      {showDocuments && (
        <p>
          Documentos de venda Wintouch. O documento, por si só, não comprova um
          recebimento.
        </p>
      )}
      {error && (
        <Notice className="error" role="alert">
          Não foi possível carregar todos os movimentos: {error}
        </Notice>
      )}
      {loading && <p role="status">A carregar movimentos…</p>}
      <RecordList
        rows={rows}
        dateField="occurred_at"
        resetKey={`${store}-${showDocuments}-${integrated}`}
        totalField={showDocuments ? undefined : "amount_cents"}
        paymentSummary={!showDocuments}
        accumulatedTotal={!showDocuments && !error && !loading}
        movementFilters
      >
        {children}
      </RecordList>
    </section>
  );
}
