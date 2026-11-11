import { useEffect, useState } from "react";
import { ActionButton, Notice } from "./components/InteractionUI";
import { ProcessSteps } from "./components/OverviewUI";
import { api } from "./lib/api";
import { formatQuantity } from "./lib/quantity";

type Row = Record<string, any>;

const TONES: Record<string, "success" | "warning" | "info" | "neutral"> = { done: "success", partial: "warning", pending: "info", missing: "neutral" };
const STATES: Record<string, string> = {
  draft: "Rascunho", confirmed: "Confirmado", partial: "Parcialmente recebida", received: "Recebida", cancelled: "Anulado",
};
const day = (value?: string) => (value ? value.slice(0, 10).split("-").reverse().join("/") : "—");
const euro = (cents?: number | null) => cents === null || cents === undefined ? "" : (cents / 100).toLocaleString("pt-PT", { style: "currency", currency: "EUR" });

/**
 * Percurso da compra a partir de um documento (encomenda, receção, fatura, devolução ou nota de crédito):
 * etapas do processo e documentos ligados, com as quantidades de cada linha. Só consulta.
 */
export default function PurchaseProcess({ documentId, onOpen }: { documentId: string; onOpen?: (id: string) => void }) {
  const [data, setData] = useState<Row | null>(null);
  const [error, setError] = useState("");

  useEffect(() => {
    let active = true;
    setData(null);
    setError("");
    api(`/procurement/${encodeURIComponent(documentId)}/process`)
      .then(result => { if (active) setData(result); })
      .catch(e => { if (active) setError((e as Error).message); });
    return () => { active = false; };
  }, [documentId]);

  if (error) return <Notice tone="error">{error}</Notice>;
  if (!data) return <p className="purchase-process-loading" role="status">A carregar o percurso da compra…</p>;
  // Resposta incompleta (por exemplo, servidor antigo): não quebra a ficha onde o percurso é mostrado.
  if (!Array.isArray(data.steps) || !Array.isArray(data.documents)) return <Notice tone="warning">Percurso da compra indisponível.</Notice>;

  return (
    <div className="purchase-process">
      <ProcessSteps label="Percurso da compra" steps={data.steps.map((s: Row) => ({ id: s.key, label: s.label, tone: TONES[s.state] || "neutral" }))} />
      <dl className="purchase-process-steps">
        {data.steps.map((s: Row) => <div key={s.key} data-state={s.state}><dt>{s.label}</dt><dd>{s.detail}</dd></div>)}
      </dl>
      <div className="purchase-process-documents">
        {data.documents.map((d: Row) => (
          <details key={d.id} className="purchase-process-document" open={d.current || data.documents.length <= 2}>
            <summary>
              <strong>{d.label} {d.document}</strong>
              <span>{day(d.document_date)} · {STATES[d.status] || d.status}{d.total_cents !== undefined && d.total_cents !== null ? ` · ${euro(d.total_cents)}` : ""}{d.current ? " · este documento" : ""}</span>
            </summary>
            <div className="table-wrap">
              <table aria-label={`Linhas de ${d.label} ${d.document}`}>
                <thead><tr><th>Artigo</th><th className="numeric">Quantidade</th>
                  {d.kind === "order" && <th className="numeric">Recebida</th>}
                  {(d.kind === "order" || d.kind === "receipt") && <th className="numeric">Faturada</th>}
                  {d.kind === "receipt" && <th className="numeric">Devolvida</th>}
                  {d.kind === "return" && <th className="numeric">Creditada</th>}
                  <th className="numeric">Pendente</th></tr></thead>
                <tbody>{d.lines.map((l: Row, i: number) => (
                  <tr key={i}><td>{l.description}</td><td className="numeric">{formatQuantity(l.quantity)}</td>
                    {d.kind === "order" && <td className="numeric">{formatQuantity(l.received || 0)}</td>}
                    {(d.kind === "order" || d.kind === "receipt") && <td className="numeric">{formatQuantity(l.invoiced || 0)}</td>}
                    {d.kind === "receipt" && <td className="numeric">{formatQuantity(l.returned || 0)}</td>}
                    {d.kind === "return" && <td className="numeric">{formatQuantity(l.credited || 0)}</td>}
                    <td className="numeric">{formatQuantity(l.pending || 0)}</td></tr>
                ))}</tbody>
              </table>
            </div>
            {d.financial.map((f: Row) => (
              <p key={f.id} className="purchase-process-financial">
                Documento {f.document_type} {f.document} · {f.checked ? "conferido" : "por conferir"} ·{" "}
                {f.payment_status === "paid" ? "pago" : f.payment_status === "partial" ? "pagamento parcial" : "por pagar"}
                {f.remaining_cents ? ` (${euro(f.remaining_cents)} em falta)` : ""}
                {f.planned ? ` · previsto para ${day(f.planned.due_date)}${f.planned.settled_on ? `, liquidado a ${day(f.planned.settled_on)}` : ""}` : ""}
                {" · "}{f.erp ? `ERP ${f.erp.internal_document || f.erp.document}${f.erp.cancelled ? " (anulado no ERP)" : ""}` : "sem associação ERP"}
              </p>
            ))}
            {d.kind !== "order" && d.attachment_status === "missing" && d.purchase_id && <p className="purchase-process-financial">Sem documento digital associado.</p>}
            {onOpen && !d.current && <ActionButton className="text-button" onClick={() => onOpen(d.id)}>Abrir {d.label.toLowerCase()}</ActionButton>}
          </details>
        ))}
      </div>
      {data.truncated && <Notice tone="warning">Percurso muito extenso: só são mostrados os primeiros documentos.</Notice>}
    </div>
  );
}
