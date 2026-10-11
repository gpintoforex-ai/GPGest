import { useEffect, useId, useRef, useState } from "react";

export default function NumericKeypad({
  label,
  value,
  decimal = false,
  onConfirm,
  onCancel,
}: {
  label: string;
  value: string;
  decimal?: boolean;
  onConfirm: (value: string) => void;
  onCancel: () => void;
}) {
  const dialog = useRef<HTMLDialogElement>(null);
  const titleId = useId();
  const [draft, setDraft] = useState(value.replace(".", ","));
  const [replace, setReplace] = useState(true);
  useEffect(() => {
    const previous = document.activeElement as HTMLElement | null;
    const element = dialog.current!;
    element.showModal();
    return () => {
      element.close();
      previous?.focus();
    };
  }, []);
  const number = draft || "0";
  const valid = decimal
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
    const current = replace ? "" : draft;
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
  function confirm() {
    if (valid) onConfirm(number.replace(/,$/, ""));
  }
  return (
    <dialog
      ref={dialog}
      className="numeric-keypad"
      aria-labelledby={titleId}
      onCancel={(e) => {
        e.preventDefault();
        e.stopPropagation();
        onCancel();
      }}
      onKeyDown={(e) => {
        if (/^\d$/.test(e.key)) append(e.key);
        else if (decimal && (e.key === "," || e.key === ".")) append(",");
        else if (e.key === "Backspace") backspace();
        else if (e.key === "Delete") clear();
        else if (e.key === "Enter") confirm();
        else return;
        e.preventDefault();
        e.stopPropagation();
      }}
    >
      <div className="numeric-keypad-title">
        <div>
          <h3 id={titleId}>Teclado numérico</h3>
          <p>{label}</p>
        </div>
        <button type="button" aria-label="Fechar teclado" onClick={onCancel}>
          ×
        </button>
      </div>
      <div className="numeric-keypad-display">
        <output aria-label="Valor introduzido" aria-live="polite">
          {number}
        </output>
        {decimal && (
          <button type="button" onClick={clear}>
            Limpar
          </button>
        )}
      </div>
      <div className="numeric-keypad-keys">
        {["7", "8", "9", "4", "5", "6", "1", "2", "3"].map((key) => (
          <button key={key} type="button" onClick={() => append(key)}>
            {key}
          </button>
        ))}
        {decimal ? (
          <button
            type="button"
            aria-label="Vírgula decimal"
            onClick={() => append(",")}
          >
            ,
          </button>
        ) : (
          <button
            type="button"
            className="numeric-keypad-control"
            onClick={clear}
          >
            Limpar
          </button>
        )}
        <button type="button" onClick={() => append("0")}>
          0
        </button>
        <button
          type="button"
          aria-label="Apagar último dígito"
          onClick={backspace}
        >
          ⌫
        </button>
      </div>
      {!valid && (
        <p className="error" role="alert">
          {decimal
            ? "Introduza um valor válido com até duas casas decimais."
            : "Introduza uma quantidade entre 0 e 100000."}
        </p>
      )}
      <div className="numeric-keypad-actions">
        <button type="button" onClick={onCancel}>
          Cancelar
        </button>
        <button
          type="button"
          className="primary"
          disabled={!valid}
          onClick={confirm}
        >
          Confirmar
        </button>
      </div>
    </dialog>
  );
}
