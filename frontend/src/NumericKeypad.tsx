import { ActionButton, AppDialog, Notice } from "./components/InteractionUI";
import { Delete } from "lucide-react";
import { useEffect, useId, useRef, useState, type ReactNode } from "react";
import { Info } from "lucide-react";
import ReadAloud from "./ReadAloud";
import { calculateMoney } from "./utils/calculator";

export default function NumericKeypad({
  label,
  value,
  decimal = false,
  calculator = false,
  onConfirm,
  onCancel,
  children,
  description,
  maxCents,
  onReset,
  resetLabel = "Usar valor dos movimentos",
}: {
  label: string;
  value: string;
  decimal?: boolean;
  calculator?: boolean;
  onConfirm: (value: string) => void;
  onCancel: () => void;
  children?: ReactNode;
  description?: string;
  maxCents?: number;
  onReset?: () => void;
  resetLabel?: string;
}) {
  const dialog = useRef<HTMLDialogElement>(null);
  const titleId = useId();
  const descriptionId = useId();
  const [showDescription, setShowDescription] = useState(false);
  const initialValue = (value.trim() || (!decimal && !calculator ? "1" : "")).replace(".", ",");
  const [draft, setDraft] = useState(initialValue);
  const [replace, setReplace] = useState(true);
  useEffect(() => {
    setDraft(initialValue);
    setReplace(true);
  }, [initialValue, label]);
  useEffect(() => {
    const previous = document.activeElement as HTMLElement | null;
    const element = dialog.current!;
    element.showModal();
    element.focus();
    return () => {
      element.close();
      previous?.focus();
    };
  }, []);
  const number = draft || "0";
  const result = calculator ? calculateMoney(number) : null;
  const exceedsMaximum =
    result !== null &&
    maxCents !== undefined &&
    BigInt(result.replace(",", "")) > BigInt(maxCents);
  const valid = calculator
    ? result !== null && !exceedsMaximum
    : decimal
      ? /^\d+(,\d{0,2})?$/.test(number) &&
        Number.isSafeInteger(Math.round(Number(number.replace(",", ".")) * 100))
      : /^\d+$/.test(number) && Number(number) <= 100000;
  function clear() {
    setDraft("");
    setReplace(false);
  }
  function backspace() {
    setDraft((previous) => previous.slice(0, -1));
    setReplace(false);
  }
  function append(key: string) {
    const current =
      replace && !(calculator && /^[+\-*/]$/.test(key)) ? "" : draft;
    if (calculator) {
      let next = current + key;
      const operand = current.split(/[+\-*/]/).at(-1) || "";
      if (key === ",") {
        if (operand.includes(",")) return;
        next = current + (operand ? "" : "0") + ",";
      }
      if (/[+\-*/]/.test(key)) {
        if (!current) return;
        next = current.replace(/[+\-*/]$/, "") + key;
      }
      if (
        next.length > 120 ||
        (next
          .split(/[+\-*/]/)
          .at(-1)
          ?.split(",")[1]?.length || 0) > 2
      )
        return;
      setDraft(next);
      setReplace(false);
      return;
    }
    let next = current + key;
    if (key === ",") {
      if (current.includes(",")) return;
      next = (current || "0") + ",";
    } else {
      next = next.replace(/^0+(?=\d)/, "");
    }
    if (
      next.length > (decimal ? 16 : 6) ||
      (decimal && (next.split(",")[1]?.length || 0) > 2)
    )
      return;
    setDraft(next);
    setReplace(false);
  }
  function applyValue() {
    if (valid) onConfirm(calculator ? result! : number.replace(/,$/, ""));
  }
  return (
    <AppDialog dirty={draft !== initialValue}
      ref={dialog}
      className="numeric-keypad"
      aria-labelledby={titleId}
      tabIndex={-1}
      onRequestClose={() => {


        onCancel();
      }}
      onKeyDown={(e) => {
        if (
          e.target instanceof HTMLElement &&
          e.target.closest("select, input, textarea")
        )
          return;
        if (
          e.key === "Enter" &&
          e.target instanceof HTMLElement &&
          e.target.closest("button, summary")
        )
          return;
        if (/^\d$/.test(e.key)) append(e.key);
        else if (decimal && (e.key === "," || e.key === ".")) append(",");
        else if (calculator && /^[+\-*/]$/.test(e.key)) append(e.key);
        else if (calculator && e.key === "=") {
          if (result !== null) {
            setDraft(result);
            setReplace(true);
          }
        } else if (e.key === "Backspace") backspace();
        else if (e.key === "Delete") clear();
        else if (e.key === "Enter") applyValue();
        else return;
        e.preventDefault();
        e.stopPropagation();
      }}
    >
      <div className="numeric-keypad-title">
        <div>
          <div className="numeric-keypad-heading">
            <h3 id={titleId}>
              {calculator ? "Calculadora" : "Teclado numérico"}
            </h3>
            {description && (
              <ActionButton
                type="button"
                className="numeric-keypad-info"
                aria-label="Informação da calculadora"
                title="Informação da calculadora"
                aria-expanded={showDescription}
                aria-controls={descriptionId}
                onClick={() => setShowDescription((shown) => !shown)}
              >
                <Info size={20} aria-hidden="true" />
              </ActionButton>
            )}
          </div>
          <p>{label}</p>
        </div>

      </div>
      {description && (
        <div
          id={descriptionId}
          className="numeric-keypad-help"
          hidden={!showDescription}
        >
          <ReadAloud active={showDescription}>
            <p className="footnote">{description}</p>
          </ReadAloud>
        </div>
      )}
      {children}
      {calculator && (
        <div
          className="numeric-keypad-actions"
          aria-label="Operações da calculadora"
        >
          {[
            ["+", "+", "Somar"],
            ["-", "−", "Subtrair"],
            ["*", "×", "Multiplicar"],
            ["/", "÷", "Dividir"],
          ].map(([key, symbol, name]) => (
            <ActionButton
              key={key}
              type="button"
              aria-label={name}
              onClick={() => append(key)}
            >
              {symbol}
            </ActionButton>
          ))}
          <ActionButton
            type="button"
            aria-label="Calcular resultado"
            disabled={!valid}
            onClick={() => {
              setDraft(result!);
              setReplace(true);
            }}
          >
            =
          </ActionButton>
        </div>
      )}
      {calculator && (
        <p aria-live="polite">
          Resultado: <strong>{result === null ? "—" : `${result} €`}</strong>
        </p>
      )}
      <div className="numeric-keypad-display">
        <output aria-label="Valor introduzido" aria-live="polite">
          {number}
        </output>
        {decimal && (
          <ActionButton type="button" onClick={clear}>
            Limpar
          </ActionButton>
        )}
      </div>
      <div className="numeric-keypad-keys">
        {["7", "8", "9", "4", "5", "6", "1", "2", "3"].map((key) => (
          <ActionButton key={key} type="button" onClick={() => append(key)}>
            {key}
          </ActionButton>
        ))}
        {decimal ? (
          <ActionButton
            type="button"
            aria-label="Vírgula decimal"
            onClick={() => append(",")}
          >
            ,
          </ActionButton>
        ) : (
          <ActionButton
            type="button"
            className="numeric-keypad-control"
            onClick={clear}
          >
            Limpar
          </ActionButton>
        )}
        <ActionButton type="button" onClick={() => append("0")}>
          0
        </ActionButton>
        <ActionButton
          type="button"
          aria-label="Apagar último dígito"
          onClick={backspace}
        >
          <Delete size={19} aria-hidden="true" />
        </ActionButton>
      </div>
      {!valid && (
        <Notice className="error" role="alert">
          {exceedsMaximum
            ? "O total ultrapassa o valor máximo permitido."
            : calculator
              ? "Complete a conta. O total deve ser positivo ou zero; não é possível dividir por zero."
              : decimal
                ? "Introduza um valor válido com até duas casas decimais."
                : "Introduza uma quantidade entre 0 e 100000."}
        </Notice>
      )}
      {onReset && (
        <ActionButton
          type="button"
          className="numeric-keypad-reset"
          onClick={onReset}
        >
          {resetLabel}
        </ActionButton>
      )}
      <div className="numeric-keypad-actions">
        <ActionButton type="button" onClick={onCancel}>
          Cancelar
        </ActionButton>
        <ActionButton
          type="button"
          className="primary"
          disabled={!valid}
          onClick={applyValue}
        >
          {calculator ? "Aplicar valor" : "Confirmar"}
        </ActionButton>
      </div>
    </AppDialog>
  );
}
