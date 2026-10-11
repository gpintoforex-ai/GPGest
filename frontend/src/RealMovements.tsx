import { useEffect, useState, type ReactNode } from "react";
import { api } from "./App";
import RecordList, { calendarDay } from "./RecordList";

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
  children,
}: {
  localRows: Row[];
  store: string;
  children: (rows: Row[]) => ReactNode;
}) {
  const [sourceRows, setSourceRows] = useState<Row[]>([]);
  const [error, setError] = useState("");
  const [documents, setDocuments] = useState(false);
  useEffect(() => {
    let active = true;
    const load = async () => {
      try {
        const result = await api("/integrations/wintouch/sales");
        if (active) {
          setSourceRows(result);
          setError("");
        }
      } catch (e) {
        if (active) setError((e as Error).message);
      }
    };
    load();
    window.addEventListener("gpgest-sales-updated", load);
    return () => {
      active = false;
      window.removeEventListener("gpgest-sales-updated", load);
    };
  }, []);
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
      kind: documents ? "documento" : r.direction > 0 ? "cliente" : "reembolso",
    }));
  const rows = [...(documents ? [] : localRows), ...imported].sort(
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
          Não foi possível carregar os registos Wintouch: {error}
        </p>
      )}
      <RecordList
        rows={rows}
        dateField="occurred_at"
        resetKey={`${store}-${documents}`}
      >
        {children}
      </RecordList>
    </section>
  );
}
