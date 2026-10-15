import { useEffect, useId, useRef, useState } from "react";
import { createPortal } from "react-dom";
import {
  Banknote,
  CreditCard,
  Smartphone,
  ArrowLeftRight,
  Wallet,
  Pencil,
  Plus,
  X,
  LockKeyhole,
  LockKeyholeOpen,
} from "lucide-react";
import { api, Field } from "./App";
import CashCountDialog from "./CashCountDialog";
import CashReconciliation from "./CashReconciliation";
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

function RealizedDialog({
  row,
  initial,
  onApply,
  onCancel,
}: {
  row: Row;
  initial: { value: string; notes: string; pos: string };
  onApply: (value: { value: string; notes: string; pos: string }) => void;
  onCancel: () => void;
}) {
  const dialog = useRef<HTMLDialogElement>(null);
  const titleId = useId();
  const [draft, setDraft] = useState(initial);
  const cents = amount(draft.value);
  const Icon = icon(row.key);
  useEffect(() => {
    const previous = document.activeElement as HTMLElement | null;
    const element = dialog.current!;
    const overflow = document.body.style.overflow;
    document.body.style.overflow = "hidden";
    element.showModal();
    element.querySelector("input")?.focus();
    return () => {
      element.close();
      document.body.style.overflow = overflow;
      previous?.focus();
    };
  }, []);
  return createPortal(
    <dialog
      ref={dialog}
      className="local-realized-dialog"
      aria-labelledby={titleId}
      onCancel={(e) => {
        e.preventDefault();
        onCancel();
      }}
    >
      <form
        onSubmit={(e) => {
          e.preventDefault();
          if (cents !== null) onApply({ ...draft, value: euros(cents) });
        }}
      >
        <div className="panel-title">
          <h2 id={titleId}>Registar realizado</h2>
          <button type="button" aria-label="Fechar" onClick={onCancel}>
            <X size={20} aria-hidden="true" />
          </button>
        </div>
        <p className="cash-payment-label">
          <Icon size={20} aria-hidden="true" /> {row.name}
        </p>
        <Field label={`Realizado ${row.name} (€)`}>
          <input
            inputMode="decimal"
            placeholder="Introduza o valor"
            value={draft.value}
            onChange={(e) => setDraft({ ...draft, value: e.target.value })}
          />
        </Field>
        {draft.value.trim() && cents === null && (
          <p className="error" role="alert">
            Introduza um valor válido em euros, com até duas casas decimais.
          </p>
        )}
        <p className="footnote">
          Introduza zero para confirmar que não houve recebimentos neste meio.
          Depois de aplicar, escolha «Guardar rascunho» na folha para guardar o
          valor. Este registo não cria movimentos.
        </p>
        <details className="local-realized-details">
          <summary>Observações do dia e referência POS</summary>
          <p className="footnote">
            Informação comum a todos os meios de pagamento.
          </p>
          <Field label="Observações do realizado">
            <textarea
              maxLength={2000}
              value={draft.notes}
              onChange={(e) => setDraft({ ...draft, notes: e.target.value })}
            />
          </Field>
          <Field label="Referência ao fecho do POS (opcional)">
            <input
              maxLength={200}
              value={draft.pos}
              onChange={(e) => setDraft({ ...draft, pos: e.target.value })}
            />
          </Field>
        </details>
        <div className="actions">
          <button className="primary" disabled={cents === null}>
            Aplicar valor
          </button>
          {initial.value.trim() && (
            <button
              type="button"
              onClick={() => onApply({ ...draft, value: "" })}
            >
              Deixar por preencher
            </button>
          )}
          <button type="button" onClick={onCancel}>
            Cancelar
          </button>
        </div>
      </form>
    </dialog>,
    document.body,
  );
}

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
    confirmed: Object.fromEntries(
      rec.rows
        .filter((row: Row) => row.key !== "numerario")
        .map((row: Row) => [
          row.key,
          stored.values?.[row.key] == null ? "" : euros(stored.values[row.key]),
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
  const [realizedMethod, setRealizedMethod] = useState<string | null>(null);
  const [newMethod, setNewMethod] = useState<string | null>(null);
  useEffect(() => {
    setForm(hydrate());
    setDirty(false);
    setSourceDay(s.sheet.day);
    setRealizedMethod(null);
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
  const expectedFor = (key: string) =>
    s.sheet.rows.find((row: Row) => row.key === key)?.expected_cents ?? 0;
  const countFor = (key: string) =>
    key === "numerario" ? counted : amount(form.confirmed[key] || "");
  const countsComplete = rows.every((row) => countFor(row.key) !== null);
  const countedTotal = rows.reduce(
    (sum, row) => sum + (countFor(row.key) ?? 0),
    0,
  );
  const countDifference =
    countedTotal - rows.reduce((sum, row) => sum + expectedFor(row.key), 0);
  const checked = rec.checked && !dirty;
  const hasDifference =
    rows.some(
      (row) =>
        amount(form.values[row.key] || "") !== null &&
        row.registered_cents !== amount(form.values[row.key]),
    ) ||
    rows.some(
      (row) =>
        countFor(row.key) !== null &&
        countFor(row.key) !== expectedFor(row.key),
    );
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
      Object.values(form.confirmed).some(
        (value) => value.trim() && amount(value) === null,
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
        payments: Object.fromEntries(
          Object.entries(form.confirmed).filter(([, value]) => value.trim()),
        ),
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
            {register} · {store}
          </h3>
          <p>
            {s.sheet.day.split("-").reverse().join("/")} · {operator} ·{" "}
            {s.closed_at ? "Fechada" : "Em aberto"}
          </p>
        </div>
        <span
          className={`cash-sheet-status ${s.closed_at ? "closed" : "open"}`}
        >
          {s.closed_at ? (
            <LockKeyhole size={16} aria-hidden="true" />
          ) : (
            <LockKeyholeOpen size={16} aria-hidden="true" />
          )}
          {s.closed_at ? "Fechada" : "Aberta"}
        </span>
      </div>
      <CashReconciliation
        opening={s.opening_cents}
        expected={cashRow.expected_cents}
        counted={counted}
      />
      <details className="cash-sheet-details">
        <summary>
          {s.closed_at ? "Ver detalhes do fecho" : "Conferência e fecho"}
        </summary>
        <div className="local-realized-toolbar">
          <h3>Realizado do dia</h3>
          <div className="local-cash-context local-cash-day-context">
            <Field label="Data">
              <input
                type="date"
                disabled={!editable || busy}
                value={sourceDay}
                onChange={(e) => setSourceDay(e.target.value)}
              />
            </Field>
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
            {editable && manager && newMethod === null && (
              <button disabled={busy || dirty} onClick={() => setNewMethod("")}>
                Adicionar meio de pagamento
              </button>
            )}
          </div>
        </div>
        <p className="footnote">
          Vendas e recebimentos desta caixa neste dia. Os valores declarados são
          uma referência de conferência; guardar este registo não cria
          movimentos.
        </p>
        {editable && manager && newMethod !== null && (
          <div className="local-cash-methods">
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
          </div>
        )}
        <h3>Conferência das vendas e recebimentos</h3>
        <p className="footnote">
          Diferença = registado − realizado. Exclui fundos iniciais,
          transferências internas, retiradas, depósitos e despesas. Os
          reembolsos de vendas reduzem os recebimentos.
        </p>
        <div className="table-wrap">
          <table className="cash-sheet-table local-cash-table">
            <thead>
              <tr>
                <th>
                  <span className="local-cash-payment-heading">
                    Meio de pagamento
                    <span className="local-cash-badge">Origem: Manual</span>
                  </span>
                </th>
                <th>Realizado declarado</th>
                <th>Registado nos movimentos</th>
                <th>Diferença do realizado</th>
                <th>Estado</th>
                <th>Contado / conferido</th>
                <th>Diferença</th>
              </tr>
            </thead>
            <tbody>
              {rows.map((row) => {
                const declared = amount(form.values[row.key] || "");
                const Icon = icon(row.key);
                const confirmed = countFor(row.key);
                const confirmedDifference =
                  confirmed === null ? null : confirmed - expectedFor(row.key);
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
                    <td>
                      <span className="cash-payment-label">
                        <Icon size={18} aria-hidden="true" /> {row.name}
                      </span>
                    </td>
                    <td>
                      {editable ? (
                        <button
                          className={`local-realized-button ${tone(diff, checked)}`}
                          disabled={busy}
                          aria-haspopup="dialog"
                          aria-label={`${declared === null ? "Registar" : "Alterar"} valor de ${row.name}`}
                          onClick={() => setRealizedMethod(row.key)}
                        >
                          {declared === null ? (
                            <Plus size={16} aria-hidden="true" />
                          ) : (
                            <Pencil size={16} aria-hidden="true" />
                          )}
                          {declared === null
                            ? "Registar valor"
                            : money(declared)}
                        </button>
                      ) : declared === null ? (
                        "Por preencher"
                      ) : (
                        money(declared)
                      )}
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
                      <span
                        className={`local-cash-state ${tone(diff, checked)}`}
                      >
                        {status}
                      </span>
                    </td>
                    <td>
                      {editable && row.key === "numerario" ? (
                        <button
                          className="cash-count-trigger"
                          aria-label="Contar numerário"
                          aria-haspopup="dialog"
                          disabled={busy}
                          onClick={() => setCountDialog(true)}
                        >
                          <span>
                            {confirmed === null
                              ? "Por contar"
                              : money(confirmed)}
                          </span>
                          <small>Contar notas e moedas</small>
                        </button>
                      ) : editable ? (
                        <input
                          aria-label={`Contagem ${row.name} (€)`}
                          inputMode="decimal"
                          placeholder="Por conferir"
                          disabled={busy}
                          value={form.confirmed[row.key] || ""}
                          onChange={(e) =>
                            update({
                              confirmed: {
                                ...form.confirmed,
                                [row.key]: e.target.value,
                              },
                            })
                          }
                        />
                      ) : confirmed === null ? (
                        "Por conferir"
                      ) : (
                        money(confirmed)
                      )}
                      <small>Esperado: {money(expectedFor(row.key))}</small>
                    </td>
                    <td
                      className={
                        confirmedDifference === null
                          ? ""
                          : confirmedDifference === 0
                            ? "positive"
                            : "negative"
                      }
                    >
                      {confirmedDifference === null
                        ? "Por conferir"
                        : money(confirmedDifference)}
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
                <td>
                  {countsComplete ? money(countedTotal) : "Contagem incompleta"}
                </td>
                <td>
                  {countsComplete ? money(countDifference) : "Por conferir"}
                </td>
              </tr>
            </tfoot>
          </table>
        </div>
        <p className="footnote">
          Contado / conferido: numerário físico, incluindo o fundo de maneio, ou
          valor líquido conferido no terminal, extrato ou comprovativos nos
          restantes meios. Diferença = contado / conferido − esperado.
        </p>
        {!editable && (form.notes || form.pos) && (
          <details className="local-realized-details">
            <summary>Observações do dia e referência POS</summary>
            {form.notes && <p>{form.notes}</p>}
            {form.pos && <p>Referência ao fecho do POS: {form.pos}</p>}
          </details>
        )}
        {editable && realizedMethod !== null && (
          <RealizedDialog
            key={realizedMethod}
            row={rows.find((row) => row.key === realizedMethod)!}
            initial={{
              value: form.values[realizedMethod] || "",
              notes: form.notes,
              pos: form.pos,
            }}
            onCancel={() => setRealizedMethod(null)}
            onApply={(draft) => {
              update({
                values: { ...form.values, [realizedMethod]: draft.value },
                notes: draft.notes,
                pos: draft.pos,
              });
              setRealizedMethod(null);
            }}
          />
        )}
        <h3>Conferência do numerário físico</h3>
        <p className="footnote">
          Saldo esperado = fundo inicial + entradas em numerário − saídas em
          numerário. Inclui retiradas e depósitos efetivamente retirados da
          gaveta. O realizado declarado não é somado a este saldo.
        </p>
        <p className="footnote">
          Entradas em numerário: {money(cashRow.credits_cents)} · Saídas em
          numerário: {money(cashRow.debits_cents)}
        </p>
        <div className="local-cash-context">
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
            Última alteração: {stored.updated_by_name || "Utilizador registado"}{" "}
            · {date(stored.updated_at)}
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
            Reaberto por {stored.reopened_by_name} · {date(stored.reopened_at)}{" "}
            · Motivo: {stored.reopen_reason}
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
      </details>
    </section>
  );
}
