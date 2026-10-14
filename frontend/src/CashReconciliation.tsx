import "./CashSheet.css";

const money = (value: number) =>
  new Intl.NumberFormat("pt-PT", { style: "currency", currency: "EUR" }).format(
    value / 100,
  );

export default function CashReconciliation({
  opening,
  expected,
  counted,
}: {
  opening: number;
  expected: number;
  counted: number | null;
}) {
  const difference = counted === null ? null : counted - expected;
  const withdrawal = counted === null ? null : counted - opening;
  return (
    <div
      className={`cash-reconciliation ${difference === null ? "pending" : difference === 0 ? "balanced" : "unbalanced"}`}
      role="status"
      aria-label="Apuramento de numerário"
    >
      <div className="cash-reconciliation-heading">
        <b>Apuramento de numerário</b>
        <span>
          {difference === null
            ? "Por contar"
            : difference === 0
              ? "Caixa correta"
              : "Caixa com diferença"}
        </span>
      </div>
      <div className="cash-reconciliation-values">
        <div>
          <small>Fundo de maneio a manter</small>
          <strong>{money(opening)}</strong>
        </div>
        <div>
          <small>Movimentos de numerário (entradas − saídas)</small>
          <strong>{money(expected - opening)}</strong>
        </div>
        <div>
          <small>Total esperado (fundo + movimentos)</small>
          <strong>{money(expected)}</strong>
        </div>
        <div>
          <small>Diferença de caixa (contado − esperado)</small>
          <strong>
            {difference === null ? "Por contar" : money(difference)}
          </strong>
        </div>
        <div
          className={`cash-withdrawal-highlight ${withdrawal === null ? "pending" : withdrawal < 0 ? "replenish" : ""}`}
        >
          <small>
            {withdrawal !== null && withdrawal < 0
              ? "Valor a repor no fundo"
              : "Valor a retirar"}
          </small>
          <strong>
            {withdrawal === null ? "Por contar" : money(Math.abs(withdrawal))}
          </strong>
        </div>
      </div>
      <p>
        Valor a retirar = numerário contado − fundo de maneio. Se faltar
        dinheiro para o fundo, é apresentado o valor a repor.
      </p>
    </div>
  );
}
