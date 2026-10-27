import { useEffect, useRef, useState } from "react";
import { api } from "./lib/api";
import { Field } from "./components/Form";
import { Notice } from "./components/InteractionUI";

type Row = Record<string, any>;
export type ReceiptFinanceValues = {
  document_type_id: string;
  payment_method: string;
  due_date: string;
  credit_note_purchase_ids: string[];
};
const euro = (cents: number) => new Intl.NumberFormat("pt-PT", { style: "currency", currency: "EUR" }).format(cents / 100);
const addDays = (day: string, days: number) => {
  const [y, m, d] = day.split("-").map(Number);
  return new Date(Date.UTC(y, m - 1, d + days)).toISOString().slice(0, 10);
};

/**
 * Tipo de documento da receção (FC, FCD, NCC, NCCD…) e o que dele depende: meio de pagamento (a dinheiro),
 * vencimento (a crédito) e faturas em aberto que uma nota de crédito abate. O efeito financeiro só
 * acontece ao confirmar a receção.
 */
export default function ReceiptFinance({ supplierId, documentDate, value, onChange, defaultDueDate, suggestedTypeId }: {
  supplierId: string;
  // Tipo proposto pelo documento do fornecedor (FT → a crédito, FR/FS → a dinheiro); aplicado uma vez.
  suggestedTypeId?: string | null;
  documentDate: string;
  // Vencimento proposto pelo prazo da ficha do fornecedor (senão, pelos dias do tipo).
  defaultDueDate?: string;
  value: ReceiptFinanceValues;
  onChange: (patch: Partial<ReceiptFinanceValues>) => void;
}) {
  const [options, setOptions] = useState<Row | null>(null);
  const [error, setError] = useState("");
  useEffect(() => {
    let alive = true;
    api(`/purchase-document-types/options${supplierId ? `?supplier_id=${encodeURIComponent(supplierId)}` : ""}`)
      .then(data => { if (alive) setOptions(data); })
      .catch(e => { if (alive) setError((e as Error).message); });
    return () => { alive = false; };
  }, [supplierId]);
  const kind = options?.types.find((t: Row) => t.id === value.document_type_id);
  const suggested = useRef(false);

  function choose(id: string) {
    const next = options?.types.find((t: Row) => t.id === id);
    onChange({
      document_type_id: id,
      // Pré-preenche com o que o tipo define; continua editável.
      payment_method: next?.creates_cash_movement ? next.default_payment_method || "numerario" : "",
      due_date: next?.creates_debt && documentDate ? defaultDueDate || addDays(documentDate, next.default_due_days || 0) : documentDate,
      credit_note_purchase_ids: [],
    });
  }

  useEffect(() => {
    if (suggested.current || !options || !suggestedTypeId || value.document_type_id) return;
    suggested.current = true;
    if (options.types.some((t: Row) => t.id === suggestedTypeId)) choose(suggestedTypeId);
  }, [options, suggestedTypeId, value.document_type_id]);

  if (error) return <Notice tone="error">{error}</Notice>;
  return <>
    <Field label="Tipo de documento">
      <select required value={value.document_type_id} onChange={e => choose(e.target.value)} title="Define o efeito financeiro ao confirmar a receção.">
        <option value="">Selecionar tipo de documento</option>
        {options?.types.map((t: Row) => <option key={t.id} value={t.id}>{t.code} · {t.description}</option>)}
      </select>
    </Field>
    {kind?.creates_cash_movement && <Field label="Meio de pagamento">
      <select required value={value.payment_method} onChange={e => onChange({ payment_method: e.target.value })}>
        {options?.methods.map((m: Row) => <option key={m.id} value={m.id}>{m.name}</option>)}
      </select>
    </Field>}
    {kind?.creates_debt && <Field label="Vencimento">
      <input required type="date" min={documentDate} value={value.due_date} onChange={e => onChange({ due_date: e.target.value })} />
    </Field>}
    {kind && <p className="footnote receipt-finance-effect">
      {kind.creates_cash_movement
        ? `Ao confirmar: movimento real de ${kind.sign > 0 ? "saída" : "entrada"} na data do documento (sem dívida).`
        : kind.creates_debt
          ? `Ao confirmar: ${kind.sign > 0 ? "dívida ao fornecedor" : "crédito do fornecedor"} e movimento previsto no vencimento.`
          : "Este tipo não gera movimentos financeiros."}
      {kind.sign < 0 ? " Os artigos (não serviços) saem do stock." : ""}
    </p>}
    {kind?.nature === "nota_credito_compra" && kind.creates_debt && <fieldset className="receipt-finance-invoices">
      <legend>Faturas em aberto a abater (opcional)</legend>
      {!options?.open_invoices.length && <small>Este fornecedor não tem faturas em aberto.</small>}
      {options?.open_invoices.map((invoice: Row) => <label key={invoice.id} className="stock-check">
        <input type="checkbox" checked={value.credit_note_purchase_ids.includes(invoice.id)} onChange={e => onChange({
          credit_note_purchase_ids: e.target.checked
            ? [...value.credit_note_purchase_ids, invoice.id]
            : value.credit_note_purchase_ids.filter(id => id !== invoice.id),
        })} />
        {invoice.document} · {invoice.document_date.split("-").reverse().join("/")} · em aberto {euro(invoice.remaining_cents)}
      </label>)}
    </fieldset>}
  </>;
}
