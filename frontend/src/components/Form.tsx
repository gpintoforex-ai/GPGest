import { cloneElement, isValidElement, useId, useRef, useState, type FormEvent, type ReactElement } from "react";
import { Receipt } from "lucide-react";
import { ActionButton, ApiError, ManagedForm, Notice, markDialogSaved, reportFormError, useDialogWorking, useFieldErrors, useWindowBusy } from "./InteractionUI";

type Row = Record<string, any>;

export function Form({
  onSubmit,
  children,
  submitLabel = "Guardar",
  onCancel,
  destructive = false,
  hideSubmit = false,
}: {
  submitLabel?: string;
  onSubmit: (v: Row) => Promise<void | false>;
  children: React.ReactNode;
  onCancel?: () => void;
  destructive?: boolean;
  hideSubmit?: boolean;
}) {
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const saving = useRef(false);
  useDialogWorking(busy);
  async function submit(e: FormEvent<HTMLFormElement>) {
    e.preventDefault();
    if (saving.current) return;
    saving.current = true;
    const form = e.currentTarget;
    const data = Object.fromEntries(new FormData(e.currentTarget));
    setBusy(true);
    setError("");
    try {
      const completed = await onSubmit(data);
      if (completed === false) return;
      markDialogSaved(form);
    } catch (e) {
      if (e instanceof ApiError) reportFormError(form, e);
      setError((e as Error).message);
    } finally {
      setBusy(false);
      saving.current = false;
    }
  }
  return (
    <ManagedForm onSubmit={submit} aria-busy={busy || undefined}>
      <fieldset className="ui-form-fields" disabled={busy}>{children}</fieldset>
      {error && (
        <Notice className="error" role="alert">
          {error}
        </Notice>
      )}
      {!hideSubmit && <div className="ui-dialog-actions">
      {onCancel && <ActionButton type="button" disabled={busy} onClick={onCancel}>Cancelar</ActionButton>}
      <ActionButton className={destructive ? "danger" : "primary"} disabled={busy}>
        {busy ? "A guardar…" : submitLabel}
      </ActionButton>
      </div>}
    </ManagedForm>
  );
}
export function Field({
  label,
  children,
}: {
  label: React.ReactNode;
  children: React.ReactNode;
}) {
  const id = useId();
  const errors = useFieldErrors();
  const windowBusy = useWindowBusy();
  const child = isValidElement(children) ? children as ReactElement<{ id?: string; name?: string; disabled?: boolean; "aria-describedby"?: string }> : null;
  const aliases: [RegExp, string[]][] = [[/^nome/i, ["name"]], [/^nif/i, ["nif", "tax_number"]], [/^email/i, ["email"]], [/^(valor|montante)/i, ["amount", "amount_cents"]], [/^motivo|^justifica/i, ["reason"]], [/^estabelecimento|^loja/i, ["store_id"]], [/^moeda/i, ["currency"]], [/^data/i, ["date", "document_date", "payment_date"]]];
  const names = child?.props.name ? [child.props.name] : aliases.find(([pattern]) => typeof label === "string" && pattern.test(label))?.[1] || [];
  const error = errors[child?.props.name || id] || Object.entries(errors).find(([name]) => names.some(key => name === key || name.endsWith(`.${key}`)))?.[1];
  return (
    <div className="field" data-ignore-dirty={typeof label === "string" && /^(Pesquisar|Procurar)/i.test(label) || undefined}>
      <label htmlFor={id}>{label}</label>
      {isValidElement(children)
        ? cloneElement(children as ReactElement<{ id?: string; disabled?: boolean; "aria-invalid"?: boolean; "aria-describedby"?: string }>, { id, disabled: child?.props.disabled || windowBusy, ...(error ? { "aria-invalid": true, "aria-describedby": [child?.props["aria-describedby"], `${id}-error`].filter(Boolean).join(" ") } : {}) })
        : children}
      {error && <small id={`${id}-error`} className="ui-field-error">{error}</small>}
    </div>
  );
}
export function Select({
  name,
  rows,
  labelKey = "name",
  id,
  onChange,
  ...validation
}: {
  name: string;
  rows: Row[];
  labelKey?: string;
  id?: string;
  onChange?: (event: React.ChangeEvent<HTMLSelectElement>) => void;
  disabled?: boolean;
  "aria-invalid"?: boolean;
  "aria-describedby"?: string;
}) {
  return (
    <select {...validation} id={id} name={name} required onChange={onChange}>
      <option value="">Selecionar…</option>
      {rows.map((r) => (
        <option key={r.id} value={r.id}>
          {r[labelKey]}
        </option>
      ))}
    </select>
  );
}
export function Empty({ text = "Ainda não existem registos." }: { text?: string }) {
  return (
    <div className="empty">
      <Receipt size={32} />
      <h3>{text}</h3>
      <p>Os dados aparecerão aqui assim que forem registados.</p>
    </div>
  );
}
