import { useEffect, useId, useRef, useState } from "react";
import { createPortal } from "react-dom";

const money = (cents: number) =>
  new Intl.NumberFormat("pt-PT", { style: "currency", currency: "EUR" }).format(
    cents / 100,
  );
const notes = [50000, 20000, 10000, 5000, 2000, 1000, 500];
const coins = [200, 100, 50, 20, 10, 5, 2, 1];
export type CashCount = {
  mode: "total" | "denominations";
  counts: Record<string, number>;
  total: string;
};

export default function CashCountDialog({
  initial,
  expected,
  onApply,
  onCancel,
}: {
  initial: CashCount;
  expected: number;
  onApply: (value: CashCount) => void;
  onCancel: () => void;
}) {
  const dialog = useRef<HTMLDialogElement>(null);
  const titleId = useId();
  const [mode, setMode] = useState(initial.mode);
  const [quantities, setQuantities] = useState<Record<string, string>>(
    Object.fromEntries(
      Object.entries(initial.counts).map(([key, value]) => [
        key,
        String(value),
      ]),
    ),
  );
  const [total, setTotal] = useState(initial.total);
  useEffect(() => {
    const previous = document.activeElement as HTMLElement | null;
    const element = dialog.current!;
    const overflow = document.body.style.overflow;
    document.body.style.overflow = "hidden";
    element.showModal();
    return () => {
      element.close();
      document.body.style.overflow = overflow;
      previous?.focus();
    };
  }, []);
  const counts = Object.fromEntries(
    [...notes, ...coins].map((value) => [
      value,
      Number(quantities[value] || 0),
    ]),
  );
  const validCounts = Object.values(counts).every(
    (quantity) =>
      Number.isInteger(quantity) && quantity >= 0 && quantity <= 100000,
  );
  const text = total.trim().replace(",", ".");
  const validTotal = /^\d+(\.\d{1,2})?$/.test(text);
  const [whole, fraction = ""] = text.split(".");
  const totalCents = validTotal
    ? Number(whole) * 100 + Number(fraction.padEnd(2, "0"))
    : 0;
  const counted =
    mode === "denominations"
      ? Object.entries(counts).reduce(
          (sum, [value, quantity]) => sum + Number(value) * quantity,
          0,
        )
      : totalCents;
  const valid = mode === "denominations" ? validCounts : validTotal;
  function group(values: number[], kind: "note" | "coin") {
    return (
      <div className={`cash-count-grid ${kind}`}>
        {values.map((value) => (
          <label className="cash-denomination" key={value}>
            <img
              src={`/euro/${value}.${value === 10 ? "png" : "jpg"}`}
              alt={`${kind === "note" ? "Nota" : "Moeda"} de ${money(value)}`}
              width={kind === "note" ? 160 : 80}
              height={80}
            />
            <strong>{money(value)}</strong>
            <input
              aria-label={`Quantidade de ${money(value)}`}
              type="number"
              min="0"
              max="100000"
              step="1"
              inputMode="numeric"
              value={quantities[value] ?? ""}
              placeholder="0"
              onChange={(e) =>
                setQuantities((previous) => ({
                  ...previous,
                  [value]: e.target.value,
                }))
              }
            />
            <small>
              {Number.isInteger(counts[value]) && counts[value] >= 0
                ? money(value * counts[value])
                : "Quantidade inválida"}
            </small>
          </label>
        ))}
      </div>
    );
  }
  return createPortal(
    <dialog
      ref={dialog}
      className="cash-count-dialog"
      aria-labelledby={titleId}
      onCancel={(e) => {
        e.preventDefault();
        onCancel();
      }}
    >
      <form
        className="cash-count-form"
        onSubmit={(e) => {
          e.preventDefault();
          if (valid) onApply({ mode, counts, total });
        }}
      >
        <header className="cash-count-header">
          <div>
            <h2 id={titleId}>Contar numerário</h2>
            <p>Conte o dinheiro físico, incluindo o fundo de maneio.</p>
          </div>
          <button type="button" aria-label="Fechar contagem" onClick={onCancel}>
            ×
          </button>
        </header>
        <div className="cash-count-body">
          <div
            className="cash-count-modes"
            role="group"
            aria-label="Forma de contagem"
          >
            <button
              type="button"
              aria-pressed={mode === "denominations"}
              onClick={() => setMode("denominations")}
            >
              Notas e moedas
            </button>
            <button
              type="button"
              aria-pressed={mode === "total"}
              onClick={() => setMode("total")}
            >
              Valor total
            </button>
          </div>
          {mode === "denominations" ? (
            <>
              <div className="cash-count-groups">
                <section aria-label="Contagem de notas">
                  <h3>
                    Notas <small>· quantidade</small>
                  </h3>
                  {group(notes, "note")}
                </section>
                <section aria-label="Contagem de moedas">
                  <h3>
                    Moedas <small>· quantidade</small>
                  </h3>
                  {group(coins, "coin")}
                </section>
              </div>
              <p className="footnote">
                Imagens:{" "}
                <a
                  href="https://www.ecb.europa.eu/euro/banknotes/html/index.pt.html"
                  target="_blank"
                  rel="noreferrer"
                >
                  Banco Central Europeu
                </a>
                .
              </p>
            </>
          ) : (
            <label className="field">
              Valor total de numerário (€)
              <input
                aria-label="Valor total de numerário (€)"
                inputMode="decimal"
                value={total}
                onChange={(e) => setTotal(e.target.value)}
                placeholder="0,00"
              />
            </label>
          )}
        </div>
        <footer className="cash-count-footer">
          <div className="cash-count-summary" aria-live="polite">
            <div>
              <small>Total contado</small>
              <strong className="cash-count-total">
                {valid ? money(counted) : "Verifique os valores"}
              </strong>
            </div>
            <div>
              <small>Esperado</small>
              <strong>{money(expected)}</strong>
            </div>
            <div>
              <small>Diferença</small>
              <strong
                className={counted === expected ? "positive" : "negative"}
              >
                {valid ? money(counted - expected) : "—"}
              </strong>
            </div>
          </div>
          <div className="actions">
            <button type="button" onClick={onCancel}>
              Cancelar
            </button>
            <button className="primary" disabled={!valid} type="submit">
              Aplicar contagem
            </button>
          </div>
        </footer>
      </form>
    </dialog>,
    document.body,
  );
}
