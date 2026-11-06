import { CircleCheck, CircleX, ClipboardList } from "lucide-react";
import { Notice } from "./components/InteractionUI";
import { Field } from "./components/Form";
import { formatQuantity } from "./lib/quantity";

type Row = Record<string, any>;

/** Linhas da encomenda a que uma linha da fatura se liga: a paga do mesmo artigo e, com bónus, a de oferta. */
export function orderLinks(order: Row | undefined, line: Row): { order_line: number | null; order_bonus_line: number | null } {
  const none = { order_line: null, order_bonus_line: null };
  if (!order || !line.include || typeof line.choice !== "string" || line.choice.startsWith("__")) return none;
  const candidates = order.lines.filter((l: Row) => l.article_id === line.choice);
  const paid = candidates.find((l: Row) => Number(l.price) > 0) ?? candidates[0];
  if (!paid) return none;
  const offer = candidates.find((l: Row) => l !== paid && Number(l.price) === 0);
  return { order_line: paid.index, order_bonus_line: Number(line.bonusQty) > 0 && offer ? offer.index : null };
}

/** Encomenda que mais artigos tem em comum com a fatura (com quantidade por receber). */
export function suggestedOrder(orders: Row[], lines: Row[]) {
  const articles = new Set(lines.map((l) => l.selected?.article_id).filter(Boolean));
  const scored = orders
    .map((o) => ({ o, n: o.lines.filter((l: Row) => articles.has(l.article_id) && Number(l.pending) > 0).length }))
    .sort((a, b) => b.n - a.n);
  return scored[0]?.n ? scored[0].o.id : "";
}

/** Comparação por linha da encomenda: o que fica recebido com esta fatura, e entregas acima do encomendado. */
export function orderComparison(order: Row | undefined, lines: Row[]) {
  if (!order) return { rows: [], outside: [] as Row[], over: false };
  const received = new Map<number, number>();
  const outside: Row[] = [];
  for (const line of lines.filter((l) => l.include)) {
    const link = orderLinks(order, line);
    if (link.order_line === null) {
      outside.push(line);
      continue;
    }
    const bonus = Number(line.bonusQty || 0);
    received.set(link.order_line, (received.get(link.order_line) || 0) + Number(line.qty || 0) + (link.order_bonus_line === null ? bonus : 0));
    if (link.order_bonus_line !== null) received.set(link.order_bonus_line, (received.get(link.order_bonus_line) || 0) + bonus);
  }
  const rows = order.lines.map((l: Row) => ({ ...l, received: received.get(l.index) || 0 }));
  return { rows, outside, over: rows.some((r: Row) => r.received > Number(r.pending)) };
}

const day = (value?: string) => (value ? value.slice(0, 10).split("-").reverse().join("/") : "—");

/**
 * Encomenda ao fornecedor recebida com esta fatura: proposta automaticamente (mais artigos em comum),
 * comparada linha a linha (a menos fica por receber; a mais exige autorização) e ligada ao confirmar,
 * para a encomenda ficar recebida e o que vem para pedidos de clientes lhes ficar reservado.
 */
export default function InvoiceOrderLink({ orders, header, lines, onChange }: {
  orders: Row[];
  header: Row;
  lines: Row[];
  onChange: (patch: Row) => void;
}) {
  if (!orders.length) return null;
  const order = orders.find((o) => o.id === header.order_id);
  const { rows, outside, over } = orderComparison(order, lines);
  return (
    <section className="invoice-order-link" aria-labelledby="invoice-order-link-title">
      <h3 id="invoice-order-link-title"><ClipboardList size={18} aria-hidden="true" /> Encomenda ao fornecedor</h3>
      <Field label="Encomenda recebida com esta fatura">
        <select value={header.order_id || ""} onChange={(e) => onChange({ order_id: e.target.value, allow_overdelivery: false })}>
          <option value="">Sem encomenda (receção direta)</option>
          {orders.map((o) => (
            <option key={o.id} value={o.id}>
              {o.document} · {day(o.document_date)}{o.expected_date ? ` · entrega ${day(o.expected_date)}` : ""}
            </option>
          ))}
        </select>
      </Field>
      {order && (
        <>
          <div className="table-wrap">
            <table aria-label="Encomenda e fatura">
              <thead><tr><th>Artigo da encomenda</th><th className="numeric">Por receber</th><th className="numeric">Nesta fatura</th><th>Situação</th></tr></thead>
              <tbody>
                {rows.map((r: Row) => {
                  const pending = Number(r.pending), diff = r.received - pending;
                  return (
                    <tr key={r.index} className={diff ? "is-different" : undefined}>
                      <td>{r.description}{Number(r.price) === 0 && <small>Oferta</small>}</td>
                      <td className="numeric">{formatQuantity(pending)}</td>
                      <td className="numeric">{formatQuantity(r.received)}</td>
                      <td>
                        <span className={`invoice-order-state ${r.received && !diff ? "is-ok" : "is-off"}`}>
                          {r.received && !diff ? <CircleCheck size={18} aria-hidden="true" /> : <CircleX size={18} aria-hidden="true" />}
                          {!r.received ? "Não vem nesta fatura: fica por receber" : diff > 0 ? `A mais: +${formatQuantity(diff)}` : diff < 0 ? `A menos: fica ${formatQuantity(-diff)} por receber` : "Igual à encomenda"}
                        </span>
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
          {outside.length > 0 && (
            <Notice tone="info">
              Fora da encomenda (entram em stock sem ligação): {outside.map((l) => l.description || "linha sem descrição").join("; ")}.
            </Notice>
          )}
          {over && (
            <label className="invoice-order-over">
              <input type="checkbox" checked={!!header.allow_overdelivery} onChange={(e) => onChange({ allow_overdelivery: e.target.checked })} />
              Aceitar as quantidades acima do encomendado (entrega superior)
            </label>
          )}
        </>
      )}
    </section>
  );
}
