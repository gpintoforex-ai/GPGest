import { useEffect, useState, useRef, useId } from "react";
import { api, Field, Form } from "./App";

type Row = Record<string, any>;
export default function WintouchSettlementForm({
  row,
  onDone,
  onCancel,
}: {
  row: Row;
  onDone: (result: { message: string; amount: string }) => Promise<void>;
  onCancel: () => void;
}) {
  const [options, setOptions] = useState<Row | null>(null);
  const dialog = useRef<HTMLDialogElement>(null);
  const titleId = useId();
  useEffect(() => {
    const element = dialog.current;
    if (element && !element.open) element.showModal();
    return () => {
      if (element?.open) element.close();
    };
  }, []);
  const [error, setError] = useState("");
  const [type, setType] = useState("");
  const [payment, setPayment] = useState("");
  const [requestKey] = useState(crypto.randomUUID());
  const recordId = row.id.replace("wintouch:", "");
  useEffect(() => {
    let active = true;
    api(`/integrations/wintouch/sales/${recordId}/settlement-options`)
      .then((result) => {
        if (active) {
          setOptions(result);
          setType(result.types[0]?.id || "");
          const transfer = result.methods.find((method: Row) =>
            String(method.name)
              .normalize("NFD")
              .replace(/[\u0300-\u036f]/g, "")
              .toLowerCase()
              .includes("transfer"),
          );
          setPayment(transfer?.id || result.methods[0]?.id || "");
        }
      })
      .catch((e) => {
        if (active) setError(e.message);
      });
    return () => {
      active = false;
    };
  }, [recordId]);
  const series = options?.series.filter((r: Row) => r.type_id === type) || [];
  return (
    <dialog
      ref={dialog}
      className="wintouch-settlement-dialog"
      aria-labelledby={titleId}
      onCancel={(event) => {
        event.preventDefault();
        onCancel();
      }}
    >
      <h2 id={titleId}>Liquidar na Wintouch · {row.document}</h2>
      <p>
        {row.entity} · {row.direction === 1 ? "A receber" : "A pagar"}:{" "}
        {new Intl.NumberFormat("pt-PT", {
          style: "currency",
          currency: "EUR",
        }).format(row.remaining_cents / 100)}
      </p>
      {error && (
        <p className="error" role="alert">
          {error}
        </p>
      )}
      {!options && !error && <p>A consultar opções Wintouch…</p>}
      {options &&
        (!options.types.length ||
          !series.length ||
          !options.methods.length) && (
          <p>
            Não existem documentos, séries ou meios de pagamento disponíveis
            para esta liquidação na Wintouch.
          </p>
        )}
      {options && options.types.length > 0 && (
        <Form
          submitLabel="Confirmar liquidação na Wintouch"
          onSubmit={async (values) => {
            if (values.confirm !== "on")
              throw new Error("Confirme a liquidação antes de enviar.");
            const { confirm, ...fields } = values;
            const result = await api(
              `/integrations/wintouch/sales/${recordId}/settle`,
              {
                ...fields,
                occurred_at: new Date().toISOString(),
                request_key: requestKey,
              },
            );
            await onDone({
              message: result.message,
              amount: String(fields.amount),
            });
          }}
        >
          <Field label="Documento de liquidação">
            <select
              required
              name="document_type_id"
              value={type}
              onChange={(e) => setType(e.target.value)}
            >
              {options.types.map((r: Row) => (
                <option key={r.id} value={r.id}>
                  {r.name}
                </option>
              ))}
            </select>
          </Field>
          <Field label="Série">
            <select required name="document_serie_id" key={type}>
              {series.map((r: Row) => (
                <option key={r.id} value={r.id}>
                  {r.name}
                </option>
              ))}
            </select>
          </Field>
          <Field label="Meio de pagamento Wintouch">
            <select
              required
              name="payment_method_id"
              value={payment}
              onChange={(e) => setPayment(e.target.value)}
            >
              {options.methods.map((r: Row) => (
                <option key={r.id} value={r.id}>
                  {r.name}
                </option>
              ))}
            </select>
          </Field>
          {options.methods.find((r: Row) => r.id === payment)
            ?.requires_number && (
            <Field label="Número do cheque">
              <input
                key={payment}
                name="payment_number"
                required
                maxLength={100}
              />
            </Field>
          )}
          <Field label="Valor a liquidar (€)">
            <input
              name="amount"
              type="number"
              required
              min="0.01"
              step="0.01"
              max={row.remaining_cents / 100}
              defaultValue={(row.remaining_cents / 100).toFixed(2)}
            />
          </Field>
          <Field label="Observações">
            <textarea name="notes" maxLength={2000} />
          </Field>
          <label className="settlement-consent">
            <input type="checkbox" name="confirm" required />
            <span>Confirmo este pagamento/recebimento e a criação do documento na Wintouch.</span>
          </label>
        </Form>
      )}
      <button type="button" onClick={onCancel}>
        Cancelar
      </button>
    </dialog>
  );
}
