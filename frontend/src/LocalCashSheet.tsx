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
  CircleMinus,
  Calculator,
  Info,
} from "lucide-react";
import { api, Field } from "./App";
import CashCountDialog from "./CashCountDialog";
import NumericKeypad from "./NumericKeypad";
import ReadAloud from "./ReadAloud";
import SettlementConfirmation from "./SettlementConfirmation";
import CashSupplierDialog, { type CashSupplierAllocation } from "./CashSupplierDialog";
import CashReconciliation from "./CashReconciliation";
import { useCashPreferences } from "./CashPreferences";
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
  const valid = cents !== null;
  const Icon = icon(row.key);
  useEffect(() => {
    const previous = document.activeElement as HTMLElement | null;
    const element = dialog.current!;
    const overflow = document.body.style.overflow;
    document.body.style.overflow = "hidden";
    element.showModal();
    const input = element.querySelector("input");
    input?.focus();
    input?.select();
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
          if (valid) onApply({ ...draft, value: euros(cents!) });
        }}
      >
        <div className="panel-title">
          <h2 id={titleId}>Registar faturado</h2>
          <button type="button" aria-label="Fechar" onClick={onCancel}>
            <X size={20} aria-hidden="true" />
          </button>
        </div>
        <p className="cash-payment-label">
          <Icon size={20} aria-hidden="true" /> {row.name}
        </p>
        <Field label={`Faturado ${row.name} (€)`}>
          <input
            inputMode="decimal"
            placeholder="Introduza o valor"
            value={draft.value}
            onChange={(e) => setDraft({ ...draft, value: e.target.value })}
          />
        </Field>
        {draft.value.trim() && !valid && (
          <p className="error" role="alert">
            Introduza um valor válido em euros, com até duas casas decimais.
          </p>
        )}
        <p className="footnote">
          Indique o faturado neste meio de pagamento. Este é o valor esperado
          para a conferência. Introduza zero para confirmar que não houve
          vendas. Depois de aplicar, escolha «Guardar rascunho» na folha para
          guardar o valor. O fecho cria apenas os movimentos ainda em falta.
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
          <button className="primary" disabled={!valid}>
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
  onSaved,
  onAction,
  active = true,
}: {
  session: Row;
  register: string;
  store: string;
  operator: string;
  writable: boolean;
  manager: boolean;
  reload: () => Promise<void>;
  onSaved?: (saved: Row) => void;
  onAction: (kind: string, row: Row) => void;
  active?: boolean;
}) {
  const rec = s.sheet.reconciliation;
  const closeHelpId = useId();
  const closeTitleId = useId();
  const closeDialog = useRef<HTMLDialogElement>(null);
  const [closeWarnings, setCloseWarnings] = useState<string[] | null>(null);
  const conferenceHelpId = useId();
  const [showConferenceHelp, setShowConferenceHelp] = useState(false);
  const { preferences } = useCashPreferences();
  const stored = s.count_data || {};
  const hydrate = () => ({
    values: Object.fromEntries(
      rec.rows.map((row: Row) => [
        row.key,
        row.declared_cents == null ? "" : euros(row.declared_cents),
      ]),
    ) as Record<string, string>,
    debits: Object.fromEntries(
      rec.rows.map((row: Row) => [
        row.key,
        rec.realized.debits?.[row.key] == null
          ? ""
          : euros(rec.realized.debits[row.key]),
      ]),
    ) as Record<string, string>,
    credits: Object.fromEntries(
      rec.rows.map((row: Row) => [
        row.key,
        rec.realized.credits?.[row.key] == null
          ? ""
          : euros(rec.realized.credits[row.key]),
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
  const [closeNotice, setCloseNotice] = useState("");
  const [supplierPrompt, setSupplierPrompt] = useState(false);
  const [countDialog, setCountDialog] = useState(false);
  const [realizedMethod, setRealizedMethod] = useState<string | null>(null);
  const [debitMethod, setDebitMethod] = useState<string | null>(null);
  const [adjustmentKind, setAdjustmentKind] = useState<"debits" | "credits">(
    "debits",
  );
  const [newMethod, setNewMethod] = useState<string | null>(null);
  useEffect(() => {
    setForm(hydrate());
    setDirty(false);
    setSourceDay(s.sheet.day);
    setRealizedMethod(null);
    setDebitMethod(null);
    setCloseWarnings(null);
  }, [s.id, stored.updated_at, s.sheet.day]);
  useEffect(() => {
    if (!closeWarnings) return;
    const previous = document.activeElement as HTMLElement | null;
    const element = closeDialog.current!;
    const overflow = document.body.style.overflow;
    document.body.style.overflow = "hidden";
    element.showModal();
    element.querySelector<HTMLButtonElement>(".primary")?.focus();
    return () => {
      element.close();
      document.body.style.overflow = overflow;
      previous?.focus();
    };
  }, [closeWarnings]);
  const editable = writable && !s.closed_at;
  const update = (patch: Partial<typeof form>) => {
    setForm((previous) => ({ ...previous, ...patch }));
    setDirty(true);
    setError("");
    setNotice("");
  };
  const rows: Row[] = rec.rows;
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
  const legacyClose =
    !!s.closed_at && rec.expectation_basis !== "declared_sales";
  const registeredDebitFor = (key: string): number =>
    rows.find((row) => row.key === key)?.registered_debits_cents ??
    s.sheet.rows.find((row: Row) => row.key === key)?.debits_cents ??
    0;
  const registeredCreditFor = (key: string): number => {
    const row = rows.find((row) => row.key === key);
    const ledger = s.sheet.rows.find((row: Row) => row.key === key);
    return (
      row?.registered_credits_cents ??
      row?.other_credits_cents ??
      (ledger?.credits_cents ?? 0) -
        (row?.receipt_credits_cents ?? Math.max(row?.registered_cents ?? 0, 0))
    );
  };
  const creditFor = (key: string): number | null => {
    if (s.closed_at)
      return rows.find((row) => row.key === key)?.other_credits_cents ?? 0;
    if (!form.credits[key]?.trim()) return registeredCreditFor(key);
    const credit = amount(form.credits[key]);
    return credit === null || credit < 0 ? null : credit;
  };
  const debitFor = (key: string): number | null => {
    if (s.closed_at)
      return rows.find((row) => row.key === key)?.debits_cents ?? null;
    if (!form.debits[key]?.trim()) return registeredDebitFor(key);
    const debit = amount(form.debits[key]);
    return debit === null || debit < 0 ? null : debit;
  };
  const debitsValid = rows.every(
    (row) => !form.debits[row.key]?.trim() || debitFor(row.key) !== null,
  );
  const creditsValid = rows.every(
    (row) => !form.credits[row.key]?.trim() || creditFor(row.key) !== null,
  );
  const creditsTotal = rows.every((row) => creditFor(row.key) !== null)
    ? rows.reduce((sum, row) => sum + creditFor(row.key)!, 0)
    : null;
  const debitsTotal = rows.every((row) => debitFor(row.key) !== null)
    ? rows.reduce((sum, row) => sum + debitFor(row.key)!, 0)
    : null;
  const otherFor = (key: string): number | null => {
    const ledger = s.sheet.rows.find((row: Row) => row.key === key);
    const receipt = rows.find((row) => row.key === key);
    const original =
      (ledger?.expected_cents ?? 0) -
      (key === "numerario" ? s.opening_cents : 0) -
      (receipt?.registered_cents ?? 0);
    if (s.closed_at) return receipt?.other_movements_cents ?? original;
    const credits = creditFor(key);
    const debit = debitFor(key);
    return debit === null || credits === null ? null : credits - debit;
  };
  const expectedFor = (key: string): number | null => {
    const ledger = s.sheet.rows.find((row: Row) => row.key === key);
    if (s.closed_at)
      return (
        rows.find((row) => row.key === key)?.expected_cents ??
        ledger?.expected_cents ??
        0
      );
    const declared = amount(form.values[key] || "");
    const other = otherFor(key);
    return declared === null || other === null
      ? null
      : declared + (key === "numerario" ? s.opening_cents : 0) + other;
  };
  const cashExpected = expectedFor("numerario");
  const cashDifference =
    counted === null || cashExpected === null ? null : counted - cashExpected;
  const countFor = (key: string) =>
    key === "numerario" ? counted : amount(form.confirmed[key] || "");
  const noMovementBlocked = (key: string) => {
    const ledger = s.sheet.rows.find((row: Row) => row.key === key);
    const receipt = rows.find((row) => row.key === key);
    const hasRecordedMovements = [
      ledger?.count,
      ledger?.credits_cents,
      ledger?.debits_cents,
      receipt?.receipt_credits_cents,
      receipt?.receipt_debits_cents,
      receipt?.registered_cents,
    ].some((value) => value != null && value !== 0);
    const nonZeroOrInvalid = (text: string) =>
      !!text.trim() && amount(text) !== 0;
    return (
      hasRecordedMovements ||
      nonZeroOrInvalid(form.values[key] || "") ||
      nonZeroOrInvalid(form.debits[key] || "") ||
      nonZeroOrInvalid(form.credits[key] || "") ||
      (key !== "numerario" && nonZeroOrInvalid(form.confirmed[key] || ""))
    );
  };
  const isWithoutMovement = (key: string) =>
    !noMovementBlocked(key) &&
    amount(form.values[key] || "") === 0 &&
    (key === "numerario" || countFor(key) === 0);
  function toggleWithoutMovement(key: string) {
    if (!editable || busy || noMovementBlocked(key)) return;
    const value = isWithoutMovement(key) ? "" : "0,00";
    update({
      values: { ...form.values, [key]: value },
      ...(key !== "numerario"
        ? { confirmed: { ...form.confirmed, [key]: value } }
        : {}),
    });
  }
  const countsComplete = rows.every((row) => countFor(row.key) !== null);
  const countedTotal = rows.reduce(
    (sum, row) => sum + (countFor(row.key) ?? 0),
    0,
  );
  const expectedTotal =
    complete && debitsValid && creditsValid
      ? rows.reduce((sum, row) => sum + (expectedFor(row.key) ?? 0), 0)
      : null;
  const countDifference =
    !countsComplete || expectedTotal === null
      ? null
      : countedTotal - expectedTotal;
  const checked = rec.checked && !dirty;
  const hasDifference = rows.some(
    (row) =>
      countFor(row.key) !== null &&
      expectedFor(row.key) !== null &&
      countFor(row.key) !== expectedFor(row.key),
  );
  const valueError =
    rows.some(
      (row) =>
        form.values[row.key]?.trim() && amount(form.values[row.key]) === null,
    ) ||
    rows.some(
      (row) => row.key !== "numerario" && form.confirmed[row.key]?.trim() && amount(form.confirmed[row.key]) === null,
    ) ||
    (form.mode === "total" && form.cash.trim() && amount(form.cash) === null) ||
    (counted !== null && counted < 0);
  const commonIssues = [
    ...(!debitsValid
      ? [
          "Introduza débitos válidos, iguais ou superiores a zero, com até duas casas decimais.",
        ]
      : []),
    ...(!creditsValid
      ? [
          "Introduza créditos válidos, iguais ou superiores a zero, com até duas casas decimais.",
        ]
      : []),
    ...(valueError
      ? [
          "Introduza valores válidos em euros, com até duas casas decimais. O numerário contado não pode ser negativo.",
        ]
      : []),
  ];
  const missingDeclared = rows.filter((row) => !form.values[row.key]?.trim());
  const missingDeclaredIssue = missingDeclared.length
    ? `Preencha o faturado de: ${missingDeclared.map((row) => row.name).join(", ")}. Indique zero nos meios sem vendas ou recebimentos.`
    : null;
  const requiredIssues = [
    ...(missingDeclaredIssue ? [missingDeclaredIssue] : []),
    ...(counted === null
      ? [
          "Indique o numerário contado, incluindo zero se a gaveta estiver vazia.",
        ]
      : []),
  ];
  const closeIssues = [
    ...commonIssues,
    ...requiredIssues,
    ...(hasDifference && form.reason.trim().length < 5
      ? ["Justifique as diferenças com pelo menos 5 caracteres."]
      : []),
  ];
  const tone = (diff: number | null, confirmed = true) =>
    diff === null
      ? "pending"
      : diff !== 0
        ? "difference"
        : confirmed
          ? "matched"
          : "pending";
  async function save(action: "count" | "reconcile" | "close", supplierAllocations?: CashSupplierAllocation[]) {
    setError("");
    setNotice("");
    const issues =
      action === "close"
        ? closeIssues
        : action === "reconcile"
          ? [...commonIssues, ...requiredIssues]
          : commonIssues;
    if (issues.length) {
      if (action === "close") setCloseWarnings(issues);
      else setError(issues.join(" "));
      return;
    }
    if (action === "close" && (debitsTotal || 0) > 0 && !supplierAllocations) {
      setSupplierPrompt(true);
      return;
    }
    setBusy(true);
    try {
      const saved = await api(`/sessions/${s.id}/${action}`, {
        realized: {
          values: Object.fromEntries(
            rows.map((row) => [row.key, form.values[row.key]?.trim() || null]),
          ),
          debits: Object.fromEntries(
            rows.map((row) => [row.key, form.debits[row.key]?.trim() || null]),
          ),
          credits: Object.fromEntries(
            rows.map((row) => [row.key, form.credits[row.key]?.trim() || null]),
          ),
          notes: form.notes,
          pos_reference: form.pos,
        },
        cash_total:
          form.mode === "total" && form.cash.trim() ? form.cash : null,
        counts: form.mode === "denominations" ? form.counts : {},
        payments: Object.fromEntries(
          rows.filter((row) => row.key !== "numerario" && form.confirmed[row.key]?.trim())
            .map((row) => [row.key, form.confirmed[row.key]]),
        ),
        cash_mode: form.mode,
        reason: form.reason,
        ...(action === "close" && supplierAllocations ? {
          supplier_answer: "indicado",
          supplier_allocations: supplierAllocations.map(({ id, amount_cents, payment }) => ({ supplier_id: id, amount_cents, ...(payment ? { payment } : {}) })),
        } : {}),
      });
      if (action !== "close" && onSaved) onSaved(saved);
      else await reload();
      setDirty(false);
      setSupplierPrompt(false);
      if (action === "close") {
        setCloseNotice(`${register} · ${store} · ${s.sheet.day.split("-").reverse().join("/")}. Os valores foram guardados e os montantes ainda não registados foram adicionados aos movimentos reais.`);
        window.dispatchEvent(new CustomEvent("gpgest-sales-updated", { detail: { appRefreshed: true } }));
      } else {
        setNotice(action === "reconcile"
            ? "Conferência guardada."
            : "Rascunho guardado.");
      }
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
      <div className="local-cash-header">
        <div className="local-cash-identity">
          <h3>
            {register} · {store}
          </h3>
          <p>
            {s.sheet.day.split("-").reverse().join("/")} · {operator} ·{" "}
            {s.closed_at ? "Fechada" : "Em aberto"}
          </p>
        </div>
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
      </div>
      {error && (
        <p className="error" role="alert">
          {error}
        </p>
      )}
      {notice && <p role="status">{notice}</p>}
      <CashReconciliation
        opening={s.opening_cents}
        expected={cashExpected}
        counted={counted}
        declaredSales={!legacyClose}
      />
      <details className="cash-sheet-details">
        <summary>
          {s.closed_at ? "Ver detalhes do fecho" : "Conferência e fecho"}
        </summary>
        <div className="local-cash-conference-heading">
          <h3>Conferência do faturado</h3>
          <button
            type="button"
            className="local-cash-info"
            aria-label="Informação sobre a conferência do faturado"
            title="Informação sobre a conferência do faturado"
            aria-expanded={showConferenceHelp}
            aria-controls={conferenceHelpId}
            onClick={() => setShowConferenceHelp((shown) => !shown)}
          >
            <Info size={20} aria-hidden="true" />
          </button>
        </div>
        <div
          id={conferenceHelpId}
          className="local-cash-conference-help"
          hidden={!showConferenceHelp}
        >
          <ReadAloud active={active && showConferenceHelp}>
            <p className="footnote">
              Indique o faturado do dia por meio de pagamento e compare com o valor
              contado ou conferido. Guardar o rascunho não cria movimentos. O fecho regista os montantes ainda em falta nos movimentos reais.
            </p>
            <p className="footnote">
              O faturado declarado é a base do valor esperado. No numerário, soma-se
              o fundo de maneio e incluem-se as restantes entradas e saídas da
              gaveta. Diferença = contado / conferido − esperado.
            </p>
            {editable && (
              <p className="footnote">
                Sem movimento confirma faturado e conferido a zero nos meios sem
                registos. No numerário, confirma apenas faturado zero: conte sempre
                o dinheiro físico, incluindo o fundo de maneio.
              </p>
            )}
            <p className="footnote">
              Débitos / créditos: carregue no valor para abrir a calculadora e
              escolha débito (saída) ou crédito (entrada). Indique os totais do dia,
              incluindo os já registados. A coluna mostra créditos menos débitos.
              Guardar o rascunho não cria movimentos. O fecho regista apenas os montantes ainda em falta.
            </p>
            <p className="footnote">
              Contado / conferido: numerário físico, incluindo o fundo de maneio, ou
              valor líquido conferido no terminal, extrato ou comprovativos nos
              restantes meios. Diferença = contado / conferido − esperado.
            </p>
          </ReadAloud>
        </div>
        {legacyClose && (
          <p className="footnote">
            Fecho anterior: preservado o esperado calculado a partir dos
            movimentos na data do fecho.
          </p>
        )}
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
                <th>Faturado</th>
                <th>Débitos / créditos</th>
                <th className="local-cash-expected">Esperado</th>
                <th>Contado / conferido</th>
                <th>Diferença</th>
              </tr>
            </thead>
            <tbody>
              {rows.map((row) => {
                const declared = amount(form.values[row.key] || "");
                const Icon = icon(row.key);
                const confirmed = countFor(row.key);
                const expected = expectedFor(row.key);
                const debit = debitFor(row.key);
                const credit = creditFor(row.key);
                const adjustment =
                  debit === null || credit === null ? null : credit - debit;
                const adjustmentText =
                  adjustment === null
                    ? "Registar valor"
                    : `${adjustment > 0 ? "+" : ""}${money(adjustment)}`;
                const other = otherFor(row.key);
                const confirmedDifference =
                  confirmed === null || expected === null
                    ? null
                    : confirmed - expected;
                return (
                  <tr key={row.key}>
                    <td>
                      <div className="local-cash-method-cell">
                        <span className="cash-payment-label">
                          <Icon size={18} aria-hidden="true" /> {row.name}
                        </span>
                        {editable && (
                          <button
                            type="button"
                            className="local-cash-no-movement"
                            aria-label={`Sem movimento — ${row.name}`}
                            aria-pressed={isWithoutMovement(row.key)}
                            disabled={busy || noMovementBlocked(row.key)}
                            title={`Sem movimento — ${
                              noMovementBlocked(row.key)
                                ? "Este meio já tem movimentos ou valores preenchidos. Confira os valores registados."
                                : row.key === "numerario"
                                  ? "Confirmar faturado zero. A contagem do dinheiro físico continua obrigatória."
                                  : "Confirmar faturado e conferido a zero. Carregue novamente para deixar por preencher."
                            }`}
                            onClick={() => toggleWithoutMovement(row.key)}
                          >
                            <CircleMinus size={20} aria-hidden="true" />
                          </button>
                        )}
                      </div>
                    </td>
                    <td>
                      {editable ? (
                        <button
                          className={`local-realized-button local-cash-value-box ${tone(confirmedDifference)}`}
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
                      ) : (
                        <span
                          className={`local-cash-value-box ${tone(confirmedDifference)}`}
                        >
                          {declared === null
                            ? "Por preencher"
                            : money(declared)}
                        </span>
                      )}
                    </td>
                    <td>
                      {editable ? (
                        <button
                          className={`local-debit-button local-cash-value-box ${tone(confirmedDifference)}`}
                          aria-label={`Registar débitos / créditos de ${row.name}`}
                          title={`Débitos: ${debit === null ? "Por preencher" : money(debit)} · Créditos: ${credit === null ? "Por preencher" : money(credit)}`}
                          aria-haspopup="dialog"
                          disabled={busy}
                          onClick={() => {
                            setAdjustmentKind(
                              (credit ?? 0) > (debit ?? 0)
                                ? "credits"
                                : "debits",
                            );
                            setDebitMethod(row.key);
                          }}
                        >
                          <Calculator size={16} aria-hidden="true" />
                          {adjustmentText}
                        </button>
                      ) : (
                        <span
                          className={`local-cash-value-box ${tone(confirmedDifference)}`}
                        >
                          {adjustment === null ? "—" : adjustmentText}
                        </span>
                      )}
                    </td>
                    <td className="local-cash-expected">
                      <strong>
                        {expected === null ? "Por preencher" : money(expected)}
                      </strong>
                      {row.key === "numerario" && (
                        <small>Fundo: {money(s.opening_cents)}</small>
                      )}
                      {other !== null && other !== 0 && (
                        <small>Outros movimentos: {money(other)}</small>
                      )}
                    </td>
                    <td>
                      {editable && row.key === "numerario" ? (
                        <button
                          className={`cash-count-trigger local-cash-value-box ${tone(confirmedDifference)}`}
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
                          className={`local-cash-value-box ${tone(confirmedDifference)}`}
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
                      ) : (
                        <span
                          className={`local-cash-value-box ${tone(confirmedDifference)}`}
                        >
                          {confirmed === null
                            ? "Por conferir"
                            : money(confirmed)}
                        </span>
                      )}
                    </td>
                    <td>
                      <span
                        className={`local-cash-value-box ${tone(confirmedDifference)}`}
                      >
                        {confirmedDifference === null
                          ? "Por conferir"
                          : money(confirmedDifference)}
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
                  {!complete && <small>Faturado incompleto</small>}
                </td>
                <td>
                  {debitsTotal === null || creditsTotal === null
                    ? "—"
                    : money(creditsTotal - debitsTotal)}
                </td>
                <td className="local-cash-expected">
                  {expectedTotal === null
                    ? "Por preencher"
                    : money(expectedTotal)}
                </td>
                <td>
                  {countsComplete ? money(countedTotal) : "Contagem incompleta"}
                </td>
                <td>
                  {countDifference === null
                    ? "Por conferir"
                    : money(countDifference)}
                </td>
              </tr>
            </tfoot>
          </table>
        </div>
        <details className="local-realized-details">
          <summary>Consultar movimentos registados</summary>
          <p className="footnote">
            Esta consulta compara os recebimentos registados com o faturado. Não
            é necessário criar movimentos de venda para conferir o faturado
            manual. Os recebimentos já registados não são somados novamente ao
            esperado.
          </p>
          <div className="table-wrap">
            <table className="cash-sheet-table local-cash-movements-table">
              <thead>
                <tr>
                  <th>Meio de pagamento</th>
                  <th>Faturado declarado</th>
                  <th>Recebimentos registados</th>
                  <th>Registado − faturado</th>
                </tr>
              </thead>
              <tbody>
                {rows.map((row) => {
                  const declared = amount(form.values[row.key] || "");
                  return (
                    <tr key={row.key}>
                      <td>{row.name}</td>
                      <td>
                        {declared === null ? "Por preencher" : money(declared)}
                      </td>
                      <td>
                        {money(row.registered_cents)}
                        {row.sources?.includes("ERP") && (
                          <small>ERP: {money(row.erp_cents)}</small>
                        )}
                      </td>
                      <td>
                        {declared === null
                          ? "—"
                          : money(row.registered_cents - declared)}
                      </td>
                    </tr>
                  );
                })}
              </tbody>
              <tfoot>
                <tr>
                  <th>Total</th>
                  <td>{money(declaredTotal)}</td>
                  <td>{money(registeredTotal)}</td>
                  <td>
                    {difference === null ? "Por preencher" : money(difference)}
                  </td>
                </tr>
              </tfoot>
            </table>
          </div>
        </details>
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
        {editable && debitMethod !== null && (
          <NumericKeypad
            key={`debit-${debitMethod}`}
            calculator
            decimal
            maxCents={99999999900}
            label={`${adjustmentKind === "debits" ? "Débitos" : "Créditos"} — ${rows.find((row) => row.key === debitMethod)!.name} (€)`}
            value={euros(
              (adjustmentKind === "debits"
                ? debitFor(debitMethod)
                : creditFor(debitMethod)) ?? 0,
            )}
            description="Indique o total do dia, incluindo os valores já registados. Os débitos reduzem o esperado e os créditos aumentam-no. Depois de aplicar, escolha Guardar rascunho na folha. Não são criados movimentos."
            resetLabel={`Usar ${adjustmentKind === "debits" ? "débitos" : "créditos"} dos movimentos`}
            onReset={
              form[adjustmentKind][debitMethod]?.trim()
                ? () => {
                    update({
                      [adjustmentKind]: {
                        ...form[adjustmentKind],
                        [debitMethod]: "",
                      },
                    });
                    setDebitMethod(null);
                  }
                : undefined
            }
            onCancel={() => setDebitMethod(null)}
            onConfirm={(value) => {
              update({
                [adjustmentKind]: {
                  ...form[adjustmentKind],
                  [debitMethod]: value,
                },
              });
              setDebitMethod(null);
            }}
          >
            <div
              className="cash-adjustment-types"
              role="group"
              aria-label="Tipo de movimento"
            >
              <button
                type="button"
                aria-label="Débito (saída)"
                aria-pressed={adjustmentKind === "debits"}
                onClick={() => setAdjustmentKind("debits")}
              >
                Débitos <strong>{money(debitFor(debitMethod) ?? 0)}</strong>
              </button>
              <button
                type="button"
                aria-label="Crédito (entrada)"
                aria-pressed={adjustmentKind === "credits"}
                onClick={() => setAdjustmentKind("credits")}
              >
                Créditos <strong>{money(creditFor(debitMethod) ?? 0)}</strong>
              </button>
            </div>
          </NumericKeypad>
        )}
        <h3>Conferência do numerário físico</h3>
        <p className="footnote">
          {legacyClose
            ? "Saldo esperado preservado do fecho anterior."
            : "Esperado na gaveta = fundo de maneio + faturado em numerário + créditos − débitos. Inclui despesas, retiradas e depósitos efetivamente retirados da gaveta. Os recebimentos, créditos e débitos já registados não são somados novamente."}
        </p>
        <p className="footnote">
          Fundo: {money(s.opening_cents)} · Faturado em numerário:{" "}
          {amount(form.values.numerario || "") === null
            ? "Por preencher"
            : money(amount(form.values.numerario)!)}{" "}
          · Outros movimentos:{" "}
          {otherFor("numerario") === null
            ? "Por preencher"
            : money(otherFor("numerario")!)}
        </p>
        <div className="local-cash-context">
          <div className={`local-cash-amount ${tone(cashDifference)}`}>
            <small>Diferença de numerário (contado − esperado)</small>
            <strong>
              {cashDifference === null ? "Por conferir" : money(cashDifference)}
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
            defaultMode={preferences.countMode}
            initial={{
              mode: form.mode as "total" | "denominations",
              counts: form.counts,
              total: form.cash,
            }}
            expected={cashExpected}
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
        {s.closed_at && stored.debit_supplier && (
          <div className="cash-supplier-summary">
            <strong>Fornecedores dos débitos:</strong>
            {stored.debit_supplier.allocations?.length
              ? stored.debit_supplier.allocations.map((allocation: Row) => <p key={`${allocation.id}:${allocation.payment || ""}`}>{allocation.name}{allocation.payment ? ` · ${rows.find(row => row.key === allocation.payment)?.name || allocation.payment}` : ""} · {money(allocation.amount_cents)}</p>)
              : <p>{stored.debit_supplier.answer === "indicado" ? `${stored.debit_supplier.name} · ${money(stored.debit_supplier.debits_cents)}` : "Não identificado"}</p>}
            {stored.debit_supplier.unassigned_cents > 0 && <p>Por atribuir: {money(stored.debit_supplier.unassigned_cents)}</p>}
          </div>
        )}
        {stored.reopened_at && (
          <p className="footnote">
            Reaberto por {stored.reopened_by_name} · {date(stored.reopened_at)}{" "}
            · Motivo: {stored.reopen_reason}
          </p>
        )}
        {dirty && <p className="footnote">Há alterações por guardar.</p>}
        <div className="actions">
          {editable && (
            <>
              <button disabled={busy} onClick={() => save("count")}>
                Guardar rascunho
              </button>
              <button
                disabled={
                  busy ||
                  !complete ||
                  !debitsValid ||
                  !creditsValid ||
                  counted === null
                }
                onClick={() => save("reconcile")}
              >
                Conferir
              </button>
              <button
                className="primary"
                disabled={busy}
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
      {closeWarnings && createPortal(
        <dialog
          ref={closeDialog}
          className="local-realized-dialog local-cash-close-dialog"
          role="alertdialog"
          aria-labelledby={closeTitleId}
          aria-describedby={closeHelpId}
          onCancel={(e) => {
            e.preventDefault();
            setCloseWarnings(null);
          }}
        >
          <div className="panel-title">
            <h2 id={closeTitleId}>Para fechar a caixa</h2>
            <button
              type="button"
              aria-label="Fechar aviso"
              onClick={() => setCloseWarnings(null)}
            >
              <X size={20} aria-hidden="true" />
            </button>
          </div>
          <div className="local-cash-close-guidance" id={closeHelpId}>
            <ul>
              {closeWarnings.map((issue) => (
                <li key={issue}>
                  {issue === missingDeclaredIssue ? (
                    <>
                      Preencha o faturado de:{" "}
                      <strong>
                        {missingDeclared.map((row) => row.name).join(", ")}
                      </strong>
                      . Indique zero nos meios sem vendas ou recebimentos.
                    </>
                  ) : issue}
                </li>
              ))}
            </ul>
          </div>
          <div className="actions">
            <button
              type="button"
              className="primary"
              onClick={() => setCloseWarnings(null)}
            >
              Voltar à conferência
            </button>
          </div>
        </dialog>,
        document.body,
      )}
      {supplierPrompt && (
        <CashSupplierDialog
          total={debitsTotal || 0}
          payments={rows.filter(row => (debitFor(row.key) || 0) > 0).map(row => ({ id: row.key, name: row.name, amount_cents: debitFor(row.key)! }))}
          initialId={stored.debit_supplier?.id}
          canCreate={manager}
          busy={busy}
          error={error}
          onCancel={() => setSupplierPrompt(false)}
          onConfirm={(allocations) => save("close", allocations)}
        />
      )}
      {closeNotice && (
        <SettlementConfirmation
          title="Caixa fechada"
          message={closeNotice}
          onClose={() => setCloseNotice("")}
        />
      )}
    </section>
  );
}
