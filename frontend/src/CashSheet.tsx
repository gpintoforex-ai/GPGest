import { useState } from "react";
import { api, Field } from "./App";
import "./CashSheet.css";

type Row = Record<string, any>;
const money = (value: number) =>
  new Intl.NumberFormat("pt-PT", { style: "currency", currency: "EUR" }).format(
    value / 100,
  );
const euros = (value: number) => (value / 100).toFixed(2).replace(".", ",");
const denominations = [
  50000, 20000, 10000, 5000, 2000, 1000, 500, 200, 100, 50, 20, 10, 5, 2, 1,
];
function readAmount(value: string): number | null {
  const text = value.trim().replace(",", ".");
  if (!/^-?\d+(\.\d{1,2})?$/.test(text)) return null;
  const [whole, fraction = ""] = text.replace("-", "").split(".");
  return (
    (Number(whole) * 100 + Number(fraction.padEnd(2, "0"))) *
    (text.startsWith("-") ? -1 : 1)
  );
}

export default function CashSheet({
  session: s,
  register,
  operator,
  writable,
  manager,
  reload,
  onAction,
}: {
  session: Row;
  register: string;
  operator: string;
  writable: boolean;
  manager: boolean;
  reload: () => Promise<void>;
  onAction: (kind: string, row: Row) => void;
}) {
  const stored = s.count_data || {};
  const [values, setValues] = useState<Record<string, string>>(() =>
    Object.fromEntries(
      s.sheet.rows.map((row: Row) => [
        row.key,
        stored.values?.[row.key] != null
          ? euros(stored.values[row.key])
          : row.expected_cents === 0
            ? "0,00"
            : "",
      ]),
    ),
  );
  const [counts, setCounts] = useState<Record<string, number>>(
    stored.counts || {},
  );
  const [cashMode, setCashMode] = useState(stored.cash_mode || "total");
  const [reason, setReason] = useState(stored.reason || "");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const [sourceDay, setSourceDay] = useState(s.sheet.day);
  const editable = writable && !s.closed_at;
  const countedCash = Object.entries(counts).reduce(
    (sum, [key, count]) => sum + Number(key) * count,
    0,
  );
  const amountFor = (key: string) =>
    s.closed_at || !editable
      ? (stored.values?.[key] ?? null)
      : key === "numerario" && cashMode === "denominations"
        ? countedCash
        : readAmount(values[key] || "");
  const rows: Row[] = s.sheet.rows;
  async function updateSources() {
    setError("");
    setNotice("");
    setBusy(true);
    try {
      await api(`/sessions/${s.id}/sources`, {
        sheet_day: sourceDay,
      });
      await reload();
      setNotice(
        "Dia atualizado. Confira a contagem com os novos valores esperados.",
      );
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  }
  const difference = rows.reduce(
    (sum, row) =>
      sum +
      (amountFor(row.key) === null
        ? 0
        : amountFor(row.key)! - row.expected_cents),
    0,
  );
  async function save(closing: boolean) {
    setError("");
    setNotice("");
    if (closing && rows.some((row) => amountFor(row.key) === null)) {
      setError(
        "Introduza a contagem de todos os meios de pagamento antes de fechar.",
      );
      return;
    }
    const payments: Record<string, string> = {};
    for (const row of rows)
      if (row.key !== "numerario" && values[row.key]?.trim())
        payments[row.key] = values[row.key];
    setBusy(true);
    try {
      await api(`/sessions/${s.id}/${closing ? "close" : "count"}`, {
        counts: cashMode === "denominations" ? counts : {},
        cash_mode: cashMode,
        payments,
        cash_total:
          cashMode === "total" && values.numerario?.trim()
            ? values.numerario
            : null,
        reason,
      });
      await reload();
      setNotice(
        closing
          ? "Folha de caixa fechada."
          : "Contagem guardada. Pode continuar a registar movimentos.",
      );
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  }
  return (
    <section
      className="panel cash-sheet"
      aria-label={`Folha de caixa ${register}`}
    >
      <div className="panel-title">
        <div>
          <h3>
            {register} · {s.sheet.day.split("-").reverse().join("/")}
          </h3>
          <p>
            {operator} · {s.closed_at ? "Fechada" : "Em aberto"}
          </p>
        </div>
        <span className={"pill " + (s.closed_at ? "neutral" : "")}>
          {s.closed_at ? "Fechada" : "Aberta"}
        </span>
      </div>
      <div className="cash-sheet-overview">
        <div>
          <small>Fundo de maneio inicial</small>
          <strong>{money(s.opening_cents)}</strong>
        </div>
        <div>
          <small>Numerário esperado (fundo + movimentos)</small>
          <strong className="cash-value">
            {money(rows.find((row) => row.key === "numerario")!.expected_cents)}
          </strong>
        </div>
      </div>
      <p className="footnote">
        {s.sheet.scope === "store_day"
          ? "Totais de todos os movimentos reais da loja neste dia: créditos e débitos, manuais e Wintouch, de todas as sessões de caixa. Os documentos a crédito ficam nos movimentos previstos."
          : "Fecho anterior: valores guardados dos movimentos desta sessão de caixa."}
      </p>
      {s.sheet.include_wintouch && (
        <p className="footnote">
          Os recebimentos Wintouch devem estar importados em Integrações. Evite
          registar novamente no GPGest os mesmos recebimentos.
        </p>
      )}
      {editable && (
        <details className="cash-sheet-sources">
          <summary>Dia dos movimentos reais</summary>
          <Field label="Dia da folha de caixa">
            <input
              type="date"
              value={sourceDay}
              onChange={(e) => setSourceDay(e.target.value)}
            />
          </Field>
          <button disabled={busy || !sourceDay} onClick={updateSources}>
            Atualizar dia
          </button>
        </details>
      )}
      {editable && (
        <Field label="Contagem de numerário">
          <select
            value={cashMode}
            onChange={(e) => setCashMode(e.target.value)}
          >
            <option value="total">Introduzir valor total</option>
            <option value="denominations">Contar notas e moedas</option>
          </select>
        </Field>
      )}
      <div className="table-wrap">
        <table className="cash-sheet-table">
          <thead>
            <tr>
              <th>Meio de pagamento</th>
              <th>GPGest (saldo)</th>
              <th>Wintouch (saldo)</th>
              <th>Créditos</th>
              <th>Débitos</th>
              <th>Esperado</th>
              <th>Contado / conferido</th>
              <th>Diferença</th>
            </tr>
          </thead>
          <tbody>
            {rows.map((row) => {
              const counted = amountFor(row.key);
              return (
                <tr key={row.key}>
                  <td>
                    <strong>{row.name}</strong>
                    {row.key === "numerario" && (
                      <small>Inclui o fundo de maneio no esperado</small>
                    )}
                  </td>
                  <td>{money(row.local_cents)}</td>
                  <td>{money(row.wintouch_cents)}</td>
                  <td className="positive">{money(row.credits_cents)}</td>
                  <td className="negative">{money(row.debits_cents)}</td>
                  <td>
                    <b>{money(row.expected_cents)}</b>
                  </td>
                  <td>
                    {editable &&
                    !(
                      row.key === "numerario" && cashMode === "denominations"
                    ) ? (
                      <input
                        aria-label={`Contagem ${row.name} (€)`}
                        inputMode="decimal"
                        value={values[row.key] || ""}
                        onChange={(e) =>
                          setValues({ ...values, [row.key]: e.target.value })
                        }
                        placeholder="0,00"
                      />
                    ) : counted === null ? (
                      "Por contar"
                    ) : (
                      money(counted)
                    )}
                  </td>
                  <td
                    className={
                      counted === null
                        ? ""
                        : counted === row.expected_cents
                          ? "positive"
                          : "negative"
                    }
                  >
                    {counted === null
                      ? "Por contar"
                      : money(counted - row.expected_cents)}
                  </td>
                </tr>
              );
            })}
          </tbody>
          <tfoot>
            <tr>
              <th>Total</th>
              <td>{money(rows.reduce((sum, r) => sum + r.local_cents, 0))}</td>
              <td>
                {money(rows.reduce((sum, r) => sum + r.wintouch_cents, 0))}
              </td>
              <td>
                {money(rows.reduce((sum, r) => sum + r.credits_cents, 0))}
              </td>
              <td>{money(rows.reduce((sum, r) => sum + r.debits_cents, 0))}</td>
              <td>
                {money(rows.reduce((sum, r) => sum + r.expected_cents, 0))}
              </td>
              <td>
                {rows.some((r) => amountFor(r.key) === null)
                  ? "Contagem incompleta"
                  : money(rows.reduce((sum, r) => sum + amountFor(r.key)!, 0))}
              </td>
              <td>
                {rows.some((r) => amountFor(r.key) === null)
                  ? "Contagem incompleta"
                  : money(difference)}
              </td>
            </tr>
          </tfoot>
        </table>
      </div>
      {editable && cashMode === "denominations" && (
        <div className="denominations cash-sheet-denominations">
          {denominations.map((d) => (
            <Field key={d} label={money(d)}>
              <input
                aria-label={"Quantidade de " + money(d)}
                type="number"
                min="0"
                max="100000"
                step="1"
                value={counts[d] ?? ""}
                onChange={(e) =>
                  setCounts({ ...counts, [d]: Number(e.target.value) })
                }
              />
            </Field>
          ))}
        </div>
      )}
      <p className="footnote">
        Numerário: dinheiro físico. Nos restantes meios, introduza o valor
        líquido conferido no terminal, extrato ou comprovativos. Diferença =
        contado − esperado.
      </p>
      {editable && (
        <Field label="Observações / justificação das diferenças">
          <textarea
            value={reason}
            onChange={(e) => setReason(e.target.value)}
            maxLength={2000}
          />
        </Field>
      )}
      {s.closed_at && s.close_reason && <p>Justificação: {s.close_reason}</p>}
      {error && (
        <p className="error" role="alert">
          {error}
        </p>
      )}
      {notice && <p role="status">{notice}</p>}
      <div className="actions">
        {editable && (
          <>
            <button disabled={busy} onClick={() => save(false)}>
              Guardar contagem
            </button>
            <button
              className="primary"
              disabled={busy}
              onClick={() => save(true)}
            >
              Fechar folha de caixa
            </button>
            <button disabled={busy} onClick={() => onAction("handover", s)}>
              Passar caixa
            </button>
          </>
        )}
        {s.closed_at && (
          <a className="download-link" href={`/api/sessions/${s.id}/report`}>
            Relatório PDF
          </a>
        )}
        {s.closed_at && manager && (
          <button onClick={() => onAction("reopen", s)}>Reabrir</button>
        )}
      </div>
    </section>
  );
}
