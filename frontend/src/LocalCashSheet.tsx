import { useEffect, useState } from "react";
import {
  Banknote,
  CreditCard,
  Smartphone,
  ArrowLeftRight,
  Wallet,
} from "lucide-react";
import { api, Field } from "./App";
import CashCountDialog from "./CashCountDialog";
import "./CashSheet.css";
import "./LocalCashSheet.css";

type Row = Record<string, any>;
const money = (value: number) =>
  new Intl.NumberFormat("pt-PT", { style: "currency", currency: "EUR" }).format(
    value / 100,
  );
const euros = (value: number) => (value / 100).toFixed(2).replace(".", ",");
const date = (value: string) =>
  new Intl.DateTimeFormat("pt-PT", {
    dateStyle: "short",
    timeStyle: "short",
    timeZone: "Europe/Lisbon",
  }).format(new Date(value));
function amount(value: string): number | null {
  const text = value.trim().replace(",", ".");
  if (!/^-?\d+(\.\d{1,2})?$/.test(text)) return null;
  const [whole, fraction = ""] = text.replace("-", "").split(".");
  const cents =
    (Number(whole) * 100 + Number(fraction.padEnd(2, "0"))) *
    (text.startsWith("-") ? -1 : 1);
  return Number.isSafeInteger(cents) && Math.abs(cents) <= 99999999900
    ? cents
    : null;
}
const icon = (key: string) =>
  key === "numerario"
    ? Banknote
    : key === "cartao"
      ? CreditCard
      : key === "mbway"
        ? Smartphone
        : key === "transferencia"
          ? ArrowLeftRight
          : Wallet;

export default function LocalCashSheet({
  session: s,
  register,
  store,
  operator,
  writable,
  manager,
  reload,
  onAction,
}: {
  session: Row;
  register: string;
  store: string;
  operator: string;
  writable: boolean;
  manager: boolean;
  reload: () => Promise<void>;
  onAction: (kind: string, row: Row) => void;
}) {
  const rec = s.sheet.reconciliation;
  const stored = s.count_data || {};
  const hydrate = () => ({
    values: Object.fromEntries(
      rec.rows.map((row: Row) => [
        row.key,
        row.declared_cents == null ? "" : euros(row.declared_cents),
      ]),
    ) as Record<string, string>,
    notes: rec.realized.notes || "",
    pos: rec.realized.pos_reference || "",
    cash:
      stored.values?.numerario == null ? "" : euros(stored.values.numerario),
    counts: (stored.counts || {}) as Record<string, number>,
    mode: stored.cash_mode === "denominations" ? "denominations" : "total",
    reason: stored.reason || "",
  });
  const [form, setForm] = useState(hydrate);
  const [dirty, setDirty] = useState(false);
  const [sourceDay, setSourceDay] = useState(s.sheet.day);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const [countDialog, setCountDialog] = useState(false);
  const [newMethod, setNewMethod] = useState<string | null>(null);
  useEffect(() => {
    setForm(hydrate());
    setDirty(false);
    setSourceDay(s.sheet.day);
  }, [s.id, stored.updated_at, s.sheet.day]);
  const editable = writable && !s.closed_at;
  const update = (patch: Partial<typeof form>) => {
    setForm((previous) => ({ ...previous, ...patch }));
    setDirty(true);
    setNotice("");
  };
  const rows: Row[] = rec.rows;
  const cashRow = s.sheet.rows.find((row: Row) => row.key === "numerario");
  const counted =
    form.mode === "denominations" && Object.keys(form.counts).length
      ? Object.entries(form.counts).reduce(
          (sum, [key, quantity]) => sum + Number(key) * quantity,
          0,
        )
      : amount(form.cash);
  const complete = rows.every(
    (row) => amount(form.values[row.key] || "") !== null,
  );
  const declaredTotal = rows.reduce(
    (sum, row) => sum + (amount(form.values[row.key] || "") || 0),
    0,
  );
  const registeredTotal = rec.registered_total_cents;
  const difference = complete ? registeredTotal - declaredTotal : null;
  const cashDifference =
    counted === null ? null : counted - cashRow.expected_cents;
  const checked = rec.checked && !dirty;
  const hasDifference =
    rows.some(
      (row) =>
        amount(form.values[row.key] || "") !== null &&
        row.registered_cents !== amount(form.values[row.key]),
    ) ||
    (cashDifference !== null && cashDifference !== 0);
  const tone = (diff: number | null, confirmed = true) =>
    diff === null
      ? "pending"
      : diff !== 0
        ? "difference"
        : confirmed
          ? "matched"
          : "pending";
  async function save(action: "count" | "reconcile" | "close") {
    setError("");
    setNotice("");
    if (
      rows.some(
        (row) =>
          form.values[row.key]?.trim() && amount(form.values[row.key]) === null,
      ) ||
      (form.cash.trim() && amount(form.cash) === null) ||
      (counted !== null && counted < 0)
    ) {
      setError(
        "Introduza valores válidos em euros, com duas casas decimais. O numerário contado não pode ser negativo.",
      );
      return;
    }
    setBusy(true);
    try {
      await api(`/sessions/${s.id}/${action}`, {
        realized: {
          values: Object.fromEntries(
            rows.map((row) => [row.key, form.values[row.key]?.trim() || null]),
          ),
          notes: form.notes,
          pos_reference: form.pos,
        },
        cash_total:
          form.mode === "total" && form.cash.trim() ? form.cash : null,
        counts: form.mode === "denominations" ? form.counts : {},
        cash_mode: form.mode,
        reason: form.reason,
      });
      await reload();
      setDirty(false);
      setNotice(
        action === "close"
          ? "Caixa fechada. As diferenças foram guardadas sem criar ajustes."
          : action === "reconcile"
            ? "Conferência guardada."
            : "Rascunho guardado.",
      );
      if (action === "close")
        window.dispatchEvent(new Event("gpgest-sales-updated"));
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  }
  return (
    <section
      className="panel cash-sheet local-cash-sheet"
      aria-label={`Folha de caixa ${register}`}
    >
      <div className="panel-title">
        <div>
          <h3>
            {register} · {s.sheet.day.split("-").reverse().join("/")}
          </h3>
          <p>
            {store} · {operator}
          </p>
        </div>
        <span
          className={`cash-sheet-status ${s.closed_at ? "closed" : "open"}`}
        >
          {s.closed_at ? "Fechada" : "Aberta"} · Modo local autónomo
        </span>
      </div>
      <div className="local-cash-totals" aria-label="Resumo da conferência">
        {[
          ["Total realizado", money(declaredTotal), tone(difference, checked)],
          [
            "Total registado",
            money(registeredTotal),
            tone(difference, checked),
          ],
          [
            "Diferença da conferência",
            difference === null ? "Por conferir" : money(difference),
            tone(difference, checked),
          ],
          [
            "Saldo esperado em numerário",
            money(cashRow.expected_cents),
            tone(cashDifference),
          ],
          [
            "Numerário contado",
            counted === null ? "Por contar" : money(counted),
            tone(cashDifference),
          ],
        ].map(([label, value, state]) => (
          <div className={`local-cash-metric ${state}`} key={label}>
            <small>{label}</small>
            <strong>{value}</strong>
          </div>
        ))}
      </div>
      <div className="panel-title">
        <h3>Realizado do dia</h3>
        <span className="local-cash-badge">Origem: Manual</span>
      </div>
      <p className="footnote">
        Vendas e recebimentos desta caixa neste dia. Os valores declarados são
        uma referência de conferência; guardar este registo não cria movimentos.
      </p>
      <div className="local-cash-context">
        <Field label="Data">
          <input
            type="date"
            disabled={!editable || busy}
            value={sourceDay}
            onChange={(e) => setSourceDay(e.target.value)}
          />
        </Field>
        <div>
          <small>Caixa / estabelecimento</small>
          <strong>
            {register} · {store}
          </strong>
        </div>
        {editable && (
          <button
            disabled={busy || sourceDay === s.sheet.day || dirty}
            onClick={async () => {
              setBusy(true);
              setError("");
              try {
                await api(`/sessions/${s.id}/sources`, {
                  sheet_day: sourceDay,
                });
                await reload();
              } catch (e) {
                setError((e as Error).message);
              } finally {
                setBusy(false);
              }
            }}
          >
            Atualizar dia
          </button>
        )}
      </div>
      <div className="local-realized-inputs">
        {rows.map((row) => {
          const Icon = icon(row.key);
          return (
            <Field
              key={row.key}
              label={
                <span className="cash-payment-label">
                  <Icon size={18} aria-hidden="true" />
                  {row.name} (€)
                </span>
              }
            >
              <input
                className={tone(
                  amount(form.values[row.key] || "") === null
                    ? null
                    : row.registered_cents - amount(form.values[row.key])!,
                  checked,
                )}
                inputMode="decimal"
                aria-label={`Realizado ${row.name} (€)`}
                placeholder="Por preencher"
                disabled={!editable || busy}
                value={form.values[row.key] || ""}
                onChange={(e) =>
                  update({
                    values: { ...form.values, [row.key]: e.target.value },
                  })
                }
              />
            </Field>
          );
        })}
      </div>
      <div className="local-realized-inputs">
        <Field label="Observações do realizado">
          <textarea
            maxLength={2000}
            disabled={!editable || busy}
            value={form.notes}
            onChange={(e) => update({ notes: e.target.value })}
          />
        </Field>
        <Field label="Referência ao fecho do POS (opcional)">
          <input
            maxLength={200}
            disabled={!editable || busy}
            value={form.pos}
            onChange={(e) => update({ pos: e.target.value })}
          />
        </Field>
      </div>
      {editable && manager && (
        <div className="local-cash-methods">
          {newMethod === null ? (
            <button disabled={busy || dirty} onClick={() => setNewMethod("")}>
              Adicionar meio de pagamento
            </button>
          ) : (
            <form
              onSubmit={async (e) => {
                e.preventDefault();
                setBusy(true);
                setError("");
                try {
                  await api("/cash/payment-methods", { name: newMethod });
                  await reload();
                  setNewMethod(null);
                } catch (e) {
                  setError((e as Error).message);
                } finally {
                  setBusy(false);
                }
              }}
            >
              <Field label="Nome do meio de pagamento">
                <input
                  maxLength={100}
                  value={newMethod}
                  onChange={(e) => setNewMethod(e.target.value)}
                />
              </Field>
              <button disabled={busy || !newMethod.trim()}>Guardar meio</button>
              <button
                type="button"
                disabled={busy}
                onClick={() => setNewMethod(null)}
              >
                Cancelar
              </button>
            </form>
          )}
        </div>
      )}
      <h3>Conferência das vendas e recebimentos</h3>
      <p className="footnote">
        Diferença = registado − realizado. Exclui fundos iniciais,
        transferências internas, retiradas, depósitos e despesas. Os reembolsos
        de vendas reduzem os recebimentos.
      </p>
      <div className="table-wrap">
        <table className="cash-sheet-table local-cash-table">
          <thead>
            <tr>
              <th>Meio de pagamento</th>
              <th>Realizado declarado</th>
              <th>Registado nos movimentos</th>
              <th>Diferença</th>
              <th>Estado</th>
            </tr>
          </thead>
          <tbody>
            {rows.map((row) => {
              const declared = amount(form.values[row.key] || "");
              const diff =
                declared === null ? null : row.registered_cents - declared;
              const status =
                diff === null
                  ? "Por conferir"
                  : diff !== 0
                    ? "Com diferença"
                    : checked
                      ? "Conferido"
                      : "Por conferir";
              return (
                <tr key={row.key}>
                  <td>{row.name}</td>
                  <td>
                    {declared === null ? "Por preencher" : money(declared)}
                    <small>Manual</small>
                  </td>
                  <td>
                    {money(row.registered_cents)}
                    <small>
                      {row.sources?.includes("ERP")
                        ? `Manual: ${money(row.manual_cents)} · ERP: ${money(row.erp_cents)}`
                        : "Manual"}
                    </small>
                  </td>
                  <td className={`local-cash-amount ${tone(diff, checked)}`}>
                    {diff === null ? "—" : money(diff)}
                  </td>
                  <td>
                    <span className={`local-cash-state ${tone(diff, checked)}`}>
                      {status}
                    </span>
                  </td>
                </tr>
              );
            })}
          </tbody>
          <tfoot>
            <tr>
              <th>Total</th>
              <td>
                {money(declaredTotal)}
                {!complete && <small>Realizado incompleto</small>}
              </td>
              <td>{money(registeredTotal)}</td>
              <td>
                {difference === null ? "Por conferir" : money(difference)}
              </td>
              <td>
                {!complete || !checked
                  ? "Por conferir"
                  : hasDifference
                    ? "Com diferença"
                    : "Conferido"}
              </td>
            </tr>
          </tfoot>
        </table>
      </div>
      <h3>Conferência do numerário físico</h3>
      <p className="footnote">
        Saldo esperado = fundo inicial + entradas em numerário − saídas em
        numerário. Inclui retiradas e depósitos efetivamente retirados da
        gaveta. O realizado declarado não é somado a este saldo.
      </p>
      <div className="local-drawer-totals">
        <div>
          <small>Fundo inicial</small>
          <strong>{money(s.opening_cents)}</strong>
        </div>
        <div>
          <small>Entradas em numerário</small>
          <strong>{money(cashRow.credits_cents)}</strong>
        </div>
        <div>
          <small>Saídas em numerário</small>
          <strong>{money(cashRow.debits_cents)}</strong>
        </div>
        <div>
          <small>Saldo esperado</small>
          <strong>{money(cashRow.expected_cents)}</strong>
        </div>
      </div>
      <div className="local-cash-context">
        <Field label="Numerário contado (€)">
          <input
            className={tone(cashDifference)}
            inputMode="decimal"
            disabled={!editable || busy}
            placeholder="Por contar"
            value={
              counted === null
                ? form.cash
                : form.mode === "denominations"
                  ? euros(counted)
                  : form.cash
            }
            onChange={(e) =>
              update({ cash: e.target.value, mode: "total", counts: {} })
            }
          />
        </Field>
        {editable && (
          <button disabled={busy} onClick={() => setCountDialog(true)}>
            Contar notas e moedas
          </button>
        )}
        <div className={`local-cash-amount ${tone(cashDifference)}`}>
          <small>Diferença de numerário (contado − esperado)</small>
          <strong>
            {cashDifference === null ? "Por contar" : money(cashDifference)}
          </strong>
          {cashDifference !== null && cashDifference < 0 && (
            <small>Falta de {money(-cashDifference)}</small>
          )}
          {cashDifference !== null && cashDifference > 0 && (
            <small>Sobra de {money(cashDifference)}</small>
          )}
        </div>
      </div>
      {editable && countDialog && (
        <CashCountDialog
          initial={{
            mode: form.mode as "total" | "denominations",
            counts: form.counts,
            total: form.cash,
          }}
          expected={cashRow.expected_cents}
          onCancel={() => setCountDialog(false)}
          onApply={(draft) => {
            update({
              mode: draft.mode,
              counts: draft.counts,
              cash: draft.total,
            });
            setCountDialog(false);
          }}
        />
      )}
      <Field
        label={
          hasDifference
            ? "Justificação das diferenças (obrigatória para fechar)"
            : "Justificação / observações da conferência"
        }
      >
        <textarea
          disabled={!editable || busy}
          maxLength={2000}
          value={form.reason}
          onChange={(e) => update({ reason: e.target.value })}
        />
      </Field>
      {stored.updated_at && (
        <p className="footnote">
          Última alteração: {stored.updated_by_name || "Utilizador registado"} ·{" "}
          {date(stored.updated_at)}
        </p>
      )}
      {rec.check && checked && (
        <p className="footnote">
          Conferido por {rec.check.checked_by_name} ·{" "}
          {date(rec.check.checked_at)}
        </p>
      )}
      {s.closed_at && (
        <p className="footnote">
          Fechado por {stored.closed_by_name} · {date(s.closed_at)}
        </p>
      )}
      {stored.reopened_at && (
        <p className="footnote">
          Reaberto por {stored.reopened_by_name} · {date(stored.reopened_at)} ·
          Motivo: {stored.reopen_reason}
        </p>
      )}
      {dirty && <p className="footnote">Há alterações por guardar.</p>}
      {error && (
        <p className="error" role="alert">
          {error}
        </p>
      )}
      {notice && <p role="status">{notice}</p>}
      {editable && (!complete || counted === null) && (
        <p className="footnote">
          Para conferir e fechar, preencha todos os meios, incluindo zero nos
          meios sem recebimentos, e indique o numerário contado.
        </p>
      )}
      <div className="actions">
        {editable && (
          <>
            <button disabled={busy} onClick={() => save("count")}>
              Guardar rascunho
            </button>
            <button
              disabled={busy || !complete || counted === null}
              onClick={() => save("reconcile")}
            >
              Conferir
            </button>
            <button
              className="primary"
              disabled={
                busy ||
                !complete ||
                counted === null ||
                (hasDifference && form.reason.trim().length < 5)
              }
              onClick={() => save("close")}
            >
              Fechar caixa
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
