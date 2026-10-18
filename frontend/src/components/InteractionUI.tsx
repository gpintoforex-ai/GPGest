import {
  Children,
  createContext,
  forwardRef,
  isValidElement,
  useContext,
  useEffect,
  useId,
  useLayoutEffect,
  useRef,
  useState,
  useSyncExternalStore,
  type AnchorHTMLAttributes,
  type ButtonHTMLAttributes,
  type DialogHTMLAttributes,
  type FormHTMLAttributes,
  type HTMLAttributes,
  type ReactNode,
} from "react";
import { createPortal } from "react-dom";
import {
  ArrowDownToLine,
  ArrowLeft,
  ArrowUpFromLine,
  Check,
  CheckCircle2,
  CircleAlert,
  CircleMinus,
  Eye,
  Filter,
  Info,
  LoaderCircle,
  LockKeyhole,
  Pencil,
  Plus,
  Printer,
  RotateCcw,
  Save,
  Search,
  Settings,
  Trash2,
  Undo2,
  UnlockKeyhole,
  Wallet,
  X,
  type LucideIcon,
} from "lucide-react";
import "./InteractionUI.css";

type Confirmation = {
  title: string;
  message: ReactNode;
  confirmLabel?: string;
  cancelLabel?: string;
  tone?: "danger" | "warning" | "primary";
};
type PendingConfirmation = Confirmation & {
  id: number;
  resolve: (value: boolean) => void;
};
let confirmations: PendingConfirmation[] = [];
let sequence = 0;
const listeners = new Set<() => void>();
const emit = () => listeners.forEach((listener) => listener());
export function confirmAction(options: Confirmation): Promise<boolean> {
  return new Promise((resolve) => {
    confirmations = [...confirmations, { ...options, id: ++sequence, resolve }];
    emit();
  });
}
function answer(id: number, value: boolean) {
  const item = confirmations.find((entry) => entry.id === id);
  confirmations = confirmations.filter((entry) => entry.id !== id);
  emit();
  item?.resolve(value);
}
const subscribe = (listener: () => void) => {
  listeners.add(listener);
  return () => {
    listeners.delete(listener);
  };
};
export const discardChanges = () =>
  confirmAction({
    title: "Descartar alterações?",
    message: "Tem alterações por guardar. Pretende sair sem guardar?",
    cancelLabel: "Continuar a editar",
    confirmLabel: "Descartar alterações",
    tone: "danger",
  });

type DialogControl = {
  dismiss: (action?: () => void) => Promise<void>;
  saved: (scope?: Element) => void;
  setWorking: (working: boolean) => void;
};
const DialogContext = createContext<DialogControl | null>(null);
const WindowBusyContext = createContext(false);
export const useWindowBusy = () => useContext(WindowBusyContext);
export function useDialogWorking(working: boolean) {
  const control = useContext(DialogContext);
  useEffect(() => {
    if (!working || !control) return;
    control.setWorking(true);
    return () => control.setWorking(false);
  }, [working, control]);
}
export function markDialogSaved(element: Element | null) {
  if (element instanceof HTMLFormElement) {
    formDrafts.set(element, snapshot(element));
    editedForms.delete(element);
  }
  element
    ?.closest("dialog")
    ?.dispatchEvent(
      new CustomEvent("gpgest-saved", { detail: { scope: element } }),
    );
}

type FieldSnapshot = Map<
  Element,
  { value: string; form: HTMLFormElement | null }
>;
const formDrafts = new WeakMap<HTMLFormElement, FieldSnapshot>();
const editedForms = new WeakSet<HTMLFormElement>();
export async function confirmFormExit(scope: Element | null) {
  if (!scope) return true;
  const forms =
    scope instanceof HTMLFormElement
      ? [scope]
      : Array.from(scope.querySelectorAll("form"));
  if (forms.some((form) => form.getAttribute("aria-busy") === "true"))
    return false;
  const unsaved = forms.some((form) => {
    const baseline = formDrafts.get(form);
    return baseline && editedForms.has(form) && changed(form, baseline);
  });
  return !unsaved || (await discardChanges());
}
function snapshot(element: Element): FieldSnapshot {
  return new Map(
    Array.from(
      element.querySelectorAll<
        HTMLInputElement | HTMLSelectElement | HTMLTextAreaElement
      >("input,select,textarea"),
    )
      .filter(
        (field) =>
          !field.closest("[data-ignore-dirty]") && field.type !== "hidden",
      )
      .map((field) => [
        field,
        {
          form: field.form,
          value: JSON.stringify(
            field instanceof HTMLInputElement &&
              ["checkbox", "radio"].includes(field.type)
              ? field.checked
              : field instanceof HTMLInputElement && field.type === "file"
                ? Array.from(
                    field.files || [],
                    (f) => `${f.name}:${f.size}:${f.lastModified}`,
                  )
                : field.value,
          ),
        },
      ]),
  );
}
function changed(element: Element, baseline: FieldSnapshot) {
  const current = snapshot(element);
  return (
    current.size !== baseline.size ||
    Array.from(current).some(
      ([field, data]) => baseline.get(field)?.value !== data.value,
    )
  );
}
let openDialogs = 0;
let previousOverflow = "";
export function useOverlayFocus(
  active: boolean,
  selector: string,
  onClose: () => void,
) {
  const close = useRef(onClose);
  close.current = onClose;
  useEffect(() => {
    if (!active) return;
    const panel = document.querySelector<HTMLElement>(selector);
    if (!panel) return;
    const opener =
      document.activeElement instanceof HTMLElement
        ? document.activeElement
        : null;
    const workspace = document.querySelector<HTMLElement>(".workspace");
    const wasInert = workspace?.inert || false;
    if (workspace) workspace.inert = true;
    if (openDialogs++ === 0) previousOverflow = document.body.style.overflow;
    document.body.style.overflow = "hidden";
    panel.setAttribute("role", "dialog");
    panel.setAttribute("aria-modal", "true");
    panel.setAttribute("aria-label", "Menu de navegação");
    const focusable = () =>
      Array.from(
        panel.querySelectorAll<HTMLElement>(
          'button:not([disabled]),a[href],summary,[tabindex="0"]',
        ),
      ).filter((node) => node.getClientRects().length);
    focusable()[0]?.focus();
    const keyboard = (event: KeyboardEvent) => {
      if (event.key === "Escape") {
        event.preventDefault();
        event.stopPropagation();
        close.current();
      }
      if (event.key !== "Tab") return;
      const items = focusable(),
        first = items[0],
        last = items.at(-1);
      if (event.shiftKey && document.activeElement === first) {
        event.preventDefault();
        last?.focus();
      } else if (!event.shiftKey && document.activeElement === last) {
        event.preventDefault();
        first?.focus();
      }
    };
    panel.addEventListener("keydown", keyboard);
    return () => {
      panel.removeEventListener("keydown", keyboard);
      panel.removeAttribute("role");
      panel.removeAttribute("aria-modal");
      panel.removeAttribute("aria-label");
      if (workspace) workspace.inert = wasInert;
      if (--openDialogs === 0) document.body.style.overflow = previousOverflow;
      if (opener?.isConnected) opener.focus();
    };
  }, [active, selector]);
}
type DialogProps = Omit<DialogHTMLAttributes<HTMLDialogElement>, "onCancel"> & {
  autoOpen?: boolean;
  busy?: boolean;
  dirty?: boolean;
  guardChanges?: boolean;
  onRequestClose?: () => void;
  initialFocus?: "cancel" | "first";
};
export const AppDialog = forwardRef<HTMLDialogElement, DialogProps>(
  function AppDialog(
    {
      children,
      className = "",
      autoOpen = true,
      busy = false,
      dirty,
      guardChanges = true,
      onRequestClose,
      initialFocus = "first",
      onClick,
      ...props
    },
    forwardedRef,
  ) {
    const element = useRef<HTMLDialogElement | null>(null);
    const previous = useRef<HTMLElement | null>(null);
    const baseline = useRef<FieldSnapshot>(new Map());
    const interacted = useRef(false);
    const asking = useRef(false);
    const working = useRef(0);
    const [pending, setPending] = useState(false);
    const latest = useRef({ busy, dirty, guardChanges, onRequestClose });
    latest.current = { busy, dirty, guardChanges, onRequestClose };
    const control = useRef<DialogControl | null>(null);
    if (!control.current)
      control.current = {
        saved: (scope) => {
          const dialog = element.current;
          if (!dialog) return;
          if (!scope || scope === dialog) baseline.current = snapshot(dialog);
          else {
            for (const [field, data] of baseline.current)
              if (data.form === scope || scope.contains(field))
                baseline.current.delete(field);
            for (const [field, data] of snapshot(scope))
              if (dialog.contains(field)) baseline.current.set(field, data);
          }
          interacted.current = changed(dialog, baseline.current);
        },
        setWorking: (value) => {
          working.current = Math.max(0, working.current + (value ? 1 : -1));
          setPending(working.current > 0);
        },
        dismiss: async (action) => {
          const dialog = element.current;
          if (
            !dialog ||
            asking.current ||
            latest.current.busy ||
            working.current ||
            Number(dialog.dataset.uiPending || 0)
          )
            return;
          const unsaved =
            latest.current.dirty ??
            (interacted.current && changed(dialog, baseline.current));
          if (latest.current.guardChanges && unsaved) {
            asking.current = true;
            const discard = await discardChanges();
            asking.current = false;
            if (!discard || !dialog.isConnected) return;
          }
          if (action) action();
          else if (latest.current.onRequestClose)
            latest.current.onRequestClose();
          else dialog.close();
        },
      };
    useLayoutEffect(() => {
      previous.current =
        document.activeElement instanceof HTMLElement
          ? document.activeElement
          : null;
      const dialog = element.current!;
      if (openDialogs++ === 0) previousOverflow = document.body.style.overflow;
      document.body.style.overflow = "hidden";
      const saved = (event: Event) =>
        control.current!.saved((event as CustomEvent).detail?.scope);
      const activity = () =>
        setPending(
          working.current > 0 || Number(dialog.dataset.uiPending || 0) > 0,
        );
      dialog.addEventListener("gpgest-saved", saved);
      dialog.addEventListener("gpgest-operation", activity);
      if (autoOpen && !dialog.open) dialog.showModal();
      const frame = requestAnimationFrame(() => {
        control.current!.saved();
        const cancel = dialog.querySelector<HTMLElement>(
          "[data-cancel],button:not(.ui-close)",
        );
        const focus =
          initialFocus === "cancel"
            ? dialog.querySelector<HTMLElement>("[data-cancel]") || cancel
            : dialog.querySelector<HTMLElement>(
                "[autofocus],input:not([type=hidden]):not([disabled]),select:not([disabled]),textarea:not([disabled])",
              );
        if (focus) focus.focus();
        else {
          dialog.tabIndex = -1;
          dialog.focus();
        }
      });
      return () => {
        cancelAnimationFrame(frame);
        dialog.removeEventListener("gpgest-saved", saved);
        dialog.removeEventListener("gpgest-operation", activity);
        if (dialog.open) dialog.close();
        if (--openDialogs === 0)
          document.body.style.overflow = previousOverflow;
        const target = previous.current;
        queueMicrotask(() => {
          if (target?.isConnected) target.focus({ preventScroll: true });
        });
      };
    }, []);
    useLayoutEffect(() => {
      if (!interacted.current && element.current)
        baseline.current = snapshot(element.current);
    });
    return (
      <DialogContext.Provider value={control.current}>
        <WindowBusyContext.Provider value={busy || pending}>
          <dialog
            {...props}
            className={`ui-dialog ${className}`}
            ref={(node) => {
              element.current = node;
              if (typeof forwardedRef === "function") forwardedRef(node);
              else if (forwardedRef) forwardedRef.current = node;
            }}
            aria-busy={busy || pending || undefined}
            onCancel={(event) => {
              event.preventDefault();
              event.stopPropagation();
              void control.current!.dismiss();
            }}
            onInputCapture={() => {
              interacted.current = true;
            }}
            onChangeCapture={() => {
              interacted.current = true;
            }}
            onClickCapture={(event) => {
              const target =
                event.target instanceof Element
                  ? event.target.closest("button")
                  : null;
              if (
                target &&
                !target.closest(".ui-dialog-dismissbar,[data-ignore-dirty]") &&
                !target.hasAttribute("aria-expanded")
              )
                interacted.current = true;
            }}
            onPointerDownCapture={() => {
              if (!interacted.current && element.current)
                baseline.current = snapshot(element.current);
            }}
            onClick={(event) => {
              if (event.target === event.currentTarget) return;
              onClick?.(event);
            }}
          >
            <div className="ui-dialog-dismissbar">
              <button
                type="button"
                className="ui-close"
                aria-label="Fechar"
                title="Fechar"
                disabled={busy || pending}
                onClick={() => void control.current!.dismiss()}
              >
                <X size={20} aria-hidden="true" />
              </button>
            </div>
            {children}
          </dialog>
        </WindowBusyContext.Provider>
      </DialogContext.Provider>
    );
  },
);

function textOf(value: ReactNode): string {
  return Children.toArray(value)
    .map((child) =>
      typeof child === "string" || typeof child === "number"
        ? String(child)
        : isValidElement<{ children?: ReactNode }>(child)
          ? textOf(child.props.children)
          : "",
    )
    .join(" ")
    .trim();
}
const actions: [RegExp, LucideIcon, string, string?][] = [
  [/imprimir/i, Printer, "Imprimir"],
  [/^modelo csv|^relatório/i, ArrowDownToLine, "Descarregar"],
  [/^cancelar (depósito|\/)|^devolver/i, Undo2, "Anular", "danger"],
  [/^(fechar caixa|encerrar período)\b/i, LockKeyhole, "Fechar"],
  [/^abrir (primeira )?(folha de )?caixa\b/i, Wallet, "Abrir caixa"],
  [/^reabrir\b/i, UnlockKeyhole, "Reabrir", "warning"],
  [/^inativar\b/i, CircleMinus, "Inativar", "danger"],
  [/^ativar\b/i, RotateCcw, "Ativar", "success"],
  [/^desfazer\b/i, Undo2, "Desfazer", "danger"],
  [/^(reinicializar|confirmar reinicialização)\b/i, RotateCcw, "Reinicializar", "danger"],
  [/^(eliminar|apagar|remover)\b/i, Trash2, "Eliminar", "danger"],
  [/^(anular|estornar)\b/i, Undo2, "Anular", "danger"],
  [/^descartar\b/i, Trash2, "Descartar alterações", "danger"],
  [/^(cancelar|fechar|ocultar)\b/i, X, "Fechar"],
  [/^(voltar|anterior|regressar)\b/i, ArrowLeft, "Voltar"],
  [/^editar\b/i, Pencil, "Editar"],
  [/^(guardar|gravar)\b/i, Save, "Guardar"],
  [/^(adicionar|acrescentar|criar|novo|nova|registar)\b/i, Plus, "Adicionar"],
  [/^(consultar|ver|visualizar|mostrar|abrir)\b/i, Eye, "Consultar"],
  [/^(pesquisar|procurar)\b/i, Search, "Pesquisar"],
  [/^filtrar\b/i, Filter, "Filtrar"],
  [/^(importar|carregar)\b/i, ArrowUpFromLine, "Importar"],
  [/^(exportar|descarregar)\b/i, ArrowDownToLine, "Exportar"],
  [/^imprimir\b/i, Printer, "Imprimir"],
  [/^(configurar|definições|parâmetros)\b/i, Settings, "Definições"],
  [/^(confirmar|aplicar|concluir|validar)\b/i, Check, "Confirmar"],
];
function iconElement(child: ReactNode) {
  return (
    isValidElement(child) &&
    typeof child.type === "object" &&
    child.type !== null &&
    "render" in child.type &&
    "displayName" in child.type
  );
}
export const CloseButton = forwardRef<
  HTMLButtonElement,
  ButtonHTMLAttributes<HTMLButtonElement> & { guard?: boolean }
>(function CloseButton(
  { className = "", onClick, guard = true, ...props },
  ref,
) {
  const dialog = useContext(DialogContext);
  return (
    <button
      {...props}
      ref={ref}
      type="button"
      aria-label="Fechar"
      title="Fechar"
      className={`ui-close ${className}`}
      onClick={(event) => {
        if (guard && dialog && onClick)
          void dialog.dismiss(() => onClick(event));
        else onClick?.(event);
      }}
    >
      <X size={20} aria-hidden="true" />
    </button>
  );
});
export function dismissDisclosure(event: { currentTarget: HTMLElement }) {
  const details = event.currentTarget.closest("details");
  if (details) {
    details.open = false;
    details.querySelector("summary")?.focus();
  }
}
export const ActionButton = forwardRef<
  HTMLButtonElement,
  ButtonHTMLAttributes<HTMLButtonElement> & {
    action?: string;
    dismiss?: boolean;
    confirmation?: Confirmation;
  }
>(function ActionButton(
  {
    children,
    action,
    dismiss,
    confirmation,
    onClick,
    className = "",
    ...props
  },
  ref,
) {
  const dialog = useContext(DialogContext);
  const windowBusy = useWindowBusy();
  const text = textOf(children);
  const matched = actions.find(([pattern]) => pattern.test(action || text));
  const Icon = matched?.[1];
  const direct = Children.toArray(children);
  const onlyIcon = !text && direct.some(iconElement);
  const label =
    props["aria-label"] ||
    props.title ||
    (onlyIcon ? matched?.[2] || "Ação" : undefined);
  const closing =
    dismiss ??
    /^(Cancelar|Fechar|Concluir assistente|Voltar|Voltar ao painel)$/.test(
      text,
    );
  return (
    <button
      {...props}
      disabled={props.disabled || windowBusy}
      ref={ref}
      data-cancel={closing || undefined}
      aria-label={props["aria-label"] || (onlyIcon ? label : undefined)}
      title={props.title || (onlyIcon ? label : undefined)}
      className={`ui-action ${matched?.[3] && !className.includes("primary") ? `ui-action-${matched[3]} ` : ""}${className}`}
      onClick={async (event) => {
        if (confirmation) {
          event.preventDefault();
          const button = event.currentTarget;
          if (!(await confirmAction(confirmation))) return;
          if (onClick) onClick(event);
          else button.form?.requestSubmit(button);
        } else if (closing && dialog && onClick) {
          event.preventDefault();
          void dialog.dismiss(() => onClick(event));
        } else if (closing && onClick) {
          event.preventDefault();
          if (await confirmFormExit(event.currentTarget.closest(".panel")))
            onClick(event);
        } else onClick?.(event);
      }}
    >
      {Icon && text && <Icon size={17} aria-hidden="true" />}
      {Icon && text ? direct.filter((child) => !iconElement(child)) : children}
    </button>
  );
});
export const ActionLink = forwardRef<
  HTMLAnchorElement,
  AnchorHTMLAttributes<HTMLAnchorElement>
>(function ActionLink({ children, className = "", ...props }, ref) {
  const matched = actions.find(([pattern]) => pattern.test(textOf(children)));
  const Icon = matched?.[1];
  return (
    <a
      {...props}
      ref={ref}
      className={`${Icon ? "ui-action-link " : ""}${className}`}
    >
      {Icon && <Icon size={17} aria-hidden="true" />}
      {Icon
        ? Children.toArray(children).filter((child) => !iconElement(child))
        : children}
    </a>
  );
});

const ErrorsContext = createContext<Record<string, string>>({});
export const useFieldErrors = () => useContext(ErrorsContext);
export class ApiError extends Error {
  constructor(
    message: string,
    public fields: Record<string, string> = {},
    public status = 0,
  ) {
    super(message);
  }
}
function validationMessage(entry: {
  type?: string;
  msg?: string;
  ctx?: Record<string, unknown>;
}) {
  const type = entry.type || "";
  if (type === "missing") return "Preencha este campo.";
  if (type.includes("too_short"))
    return `Introduza pelo menos ${entry.ctx?.min_length ?? 1} caracteres.`;
  if (type.includes("too_long"))
    return `Use no máximo ${entry.ctx?.max_length ?? "o número permitido de"} caracteres.`;
  if (type.includes("greater_than"))
    return "Indique um valor superior ao mínimo permitido.";
  if (type.includes("less_than"))
    return "O valor ultrapassa o máximo permitido.";
  if (type.includes("date")) return "Indique uma data válida.";
  if (
    type.includes("int") ||
    type.includes("float") ||
    type.includes("decimal")
  )
    return "Indique um número válido.";
  return "Verifique o valor introduzido.";
}
export function responseError(detail: unknown, status: number) {
  const fields: Record<string, string> = {};
  if (Array.isArray(detail))
    for (const entry of detail) {
      const name = entry.loc
        ?.filter((part: unknown) => part !== "body")
        .join(".");
      if (name) fields[name] = validationMessage(entry);
    }
  const message =
    status >= 500
      ? "Não foi possível concluir a operação no servidor. Os dados preenchidos foram mantidos. Tente novamente dentro de instantes."
      : detail === "Not Found"
        ? "O registo já não está disponível. Atualize a lista e tente novamente."
        : typeof detail === "string"
          ? detail
          : Object.keys(fields).length
            ? "Reveja os campos assinalados e volte a guardar."
            : status >= 500
              ? "Não foi possível concluir a operação. Os dados preenchidos foram mantidos. Tente novamente dentro de instantes."
              : "Não foi possível concluir a operação. Reveja os dados e tente novamente.";
  return new ApiError(message, fields, status);
}
export function beginOperation(path: string, mutating: boolean) {
  const focused = document.activeElement;
  const form = focused?.closest("form");
  const dialog = focused?.closest("dialog");
  if (!mutating) return { form, saved: () => {}, end: () => {} };
  if (dialog) {
    dialog.dataset.uiPending = String(
      Number(dialog.dataset.uiPending || 0) + 1,
    );
    dialog.dispatchEvent(new Event("gpgest-operation"));
  }
  form?.dispatchEvent(
    new CustomEvent("gpgest-form-operation", {
      detail: {
        change: 1,
        label: /import|sync|test|preview/.test(path)
          ? "A processar…"
          : "A guardar…",
      },
    }),
  );
  return {
    form,
    saved: () => {
      if (form) requestAnimationFrame(() => markDialogSaved(form));
    },
    end: () => {
      if (dialog) {
        dialog.dataset.uiPending = String(
          Math.max(0, Number(dialog.dataset.uiPending || 0) - 1),
        );
        dialog.dispatchEvent(new Event("gpgest-operation"));
      }
      form?.dispatchEvent(
        new CustomEvent("gpgest-form-operation", { detail: { change: -1 } }),
      );
    },
  };
}
export function reportFormError(
  form: Element | null | undefined,
  error: ApiError,
) {
  form?.dispatchEvent(
    new CustomEvent("gpgest-form-errors", { detail: error.fields }),
  );
}

export const ManagedForm = forwardRef<
  HTMLFormElement,
  FormHTMLAttributes<HTMLFormElement>
>(function ManagedForm(
  {
    children,
    onSubmit,
    onSubmitCapture,
    onInputCapture,
    onInvalidCapture,
    ...props
  },
  forwardedRef,
) {
  const element = useRef<HTMLFormElement | null>(null);
  const pending = useRef(0);
  const [working, setWorking] = useState("");
  const [errors, setErrors] = useState<Record<string, string>>({});
  const errorPrefix = useId();
  useLayoutEffect(() => {
    const form = element.current;
    if (!form) return;
    const cleanup: (() => void)[] = [];
    const controls = form.querySelectorAll<
      HTMLInputElement | HTMLSelectElement | HTMLTextAreaElement
    >("input,select,textarea");
    controls.forEach((field, index) => {
      // Field already renders a linked inline error. Support the remaining
      // native labels without changing their markup or the submitted values.
      if (field.closest("div.field")) return;
      const name = field.name || field.id;
      const message =
        errors[name] ||
        (name &&
          Object.entries(errors).find(([key]) =>
            key.endsWith(`.${name}`),
          )?.[1]);
      if (!message) return;
      const hint = document.createElement("small");
      hint.id = `${errorPrefix}-error-${index}`;
      hint.className = "ui-field-error";
      hint.textContent = message;
      // Keep the label's accessible name stable. The explicit description
      // reference still exposes the error to assistive technology.
      hint.setAttribute("aria-hidden", "true");
      const previousInvalid = field.getAttribute("aria-invalid");
      const previousDescription = field.getAttribute("aria-describedby");
      field.setAttribute("aria-invalid", "true");
      field.setAttribute(
        "aria-describedby",
        [previousDescription, hint.id].filter(Boolean).join(" "),
      );
      field.insertAdjacentElement("afterend", hint);
      cleanup.push(() => {
        hint.remove();
        if (previousInvalid === null) field.removeAttribute("aria-invalid");
        else field.setAttribute("aria-invalid", previousInvalid);
        if (previousDescription === null)
          field.removeAttribute("aria-describedby");
        else field.setAttribute("aria-describedby", previousDescription);
      });
    });
    return () => cleanup.forEach((restore) => restore());
  }, [errors, errorPrefix]);
  useLayoutEffect(() => {
    if (element.current && !editedForms.has(element.current))
      formDrafts.set(element.current, snapshot(element.current));
  });
  useLayoutEffect(() => {
    const form = element.current!;
    const operation = (event: Event) => {
      const detail = (event as CustomEvent).detail;
      pending.current = Math.max(0, pending.current + detail.change);
      setWorking(pending.current ? detail.label || "A guardar…" : "");
    };
    const failed = (event: Event) => {
      const fields = (event as CustomEvent).detail;
      setErrors(fields);
      requestAnimationFrame(() => {
        const field = Array.from(form.elements).find(
          (node) =>
            node instanceof HTMLInputElement ||
            node instanceof HTMLSelectElement ||
            node instanceof HTMLTextAreaElement,
        ) as HTMLElement | undefined;
        form.querySelector<HTMLElement>('[aria-invalid="true"]')?.focus();
        if (!Object.keys(fields).length) field?.focus();
      });
    };
    form.addEventListener("gpgest-form-operation", operation);
    form.addEventListener("gpgest-form-errors", failed);
    return () => {
      form.removeEventListener("gpgest-form-operation", operation);
      form.removeEventListener("gpgest-form-errors", failed);
    };
  }, []);
  return (
    <ErrorsContext.Provider value={errors}>
      <form
        {...props}
        ref={(node) => {
          element.current = node;
          if (typeof forwardedRef === "function") forwardedRef(node);
          else if (forwardedRef) forwardedRef.current = node;
        }}
        aria-busy={!!working || props["aria-busy"] || undefined}
        onSubmitCapture={(event) => {
          if (pending.current) {
            event.preventDefault();
            event.stopPropagation();
            return;
          }
          setErrors({});
          onSubmitCapture?.(event);
        }}
        onInvalidCapture={(event) => {
          const field = event.target as HTMLInputElement;
          event.preventDefault();
          if (!field.name && !field.id)
            field.id = `${errorPrefix}-field-${Array.from(element.current?.elements || []).indexOf(field)}`;
          const key = field.name || field.id;
          if (key)
            setErrors((old) => ({
              ...old,
              [key]: field.validity.valueMissing
                ? "Preencha este campo."
                : field.validity.typeMismatch
                  ? "Introduza um endereço de email válido."
                  : field.validity.tooShort
                    ? `Introduza pelo menos ${field.minLength} caracteres.`
                    : field.validity.rangeUnderflow
                      ? `Indique um valor igual ou superior a ${field.min}.`
                      : field.validity.rangeOverflow
                        ? `Indique um valor igual ou inferior a ${field.max}.`
                        : "Verifique o formato do valor introduzido.",
            }));
          requestAnimationFrame(() =>
            element.current
              ?.querySelector<HTMLElement>('[aria-invalid="true"]')
              ?.focus(),
          );
          onInvalidCapture?.(event);
        }}
        onSubmit={onSubmit}
        onInputCapture={(event) => {
          if (element.current) editedForms.add(element.current);
          const field = event.target as HTMLInputElement;
          const key = field.name || field.id;
          if (errors[key])
            setErrors((old) => {
              const next = { ...old };
              delete next[key];
              return next;
            });
          onInputCapture?.(event);
        }}
      >
        <fieldset className="ui-form-fields" disabled={!!working}>
          {children}
        </fieldset>
        {working && (
          <p className="ui-saving" role="status">
            <LoaderCircle size={17} aria-hidden="true" />
            {working}
          </p>
        )}
      </form>
    </ErrorsContext.Provider>
  );
});

export function Notice({
  children,
  className = "",
  tone,
  onDismiss,
  ...props
}: HTMLAttributes<HTMLDivElement> & {
  tone?: "success" | "error" | "warning" | "info";
  onDismiss?: () => void;
}) {
  const dialog = useContext(DialogContext);
  const kind =
    tone ||
    (className.includes("error")
      ? "error"
      : className.includes("success")
        ? "success"
        : className.includes("warning")
          ? "warning"
          : "info");
  const Icon =
    kind === "success"
      ? CheckCircle2
      : kind === "error" || kind === "warning"
        ? CircleAlert
        : Info;
  const message = textOf(children);
  const [visible, setVisible] = useState(true);
  const [host, setHost] = useState<HTMLElement | null>(null);
  useEffect(() => {
    setVisible(true);
    setHost(document.getElementById("gpgest-notifications"));
    if (kind !== "success") return;
    const timer = window.setTimeout(() => {
      setVisible(false);
      onDismiss?.();
    }, 8000);
    return () => clearTimeout(timer);
  }, [message, kind]);
  if (!visible) return null;
  const content = (
    <div
      {...props}
      className={`ui-notice ui-notice-${kind} ${className}`}
      role={kind === "error" ? "alert" : "status"}
    >
      <Icon size={20} aria-hidden="true" />
      <div>{children}</div>
      {kind === "success" && (
        <button
          type="button"
          className="ui-close"
          aria-label="Ocultar mensagem"
          title="Ocultar mensagem"
          onClick={() => {
            setVisible(false);
            onDismiss?.();
          }}
        >
          <X size={18} aria-hidden="true" />
        </button>
      )}
    </div>
  );
  return kind === "success" && !dialog && host
    ? createPortal(content, host)
    : content;
}

export function FeedbackHost() {
  const queue = useSyncExternalStore(subscribe, () => confirmations);
  const item = queue[0];
  const id = useId();
  return (
    <>
      <aside
        id="gpgest-notifications"
        className="ui-notifications"
        aria-label="Notificações"
      />
      {item && (
        <AppDialog
          key={item.id}
          autoOpen
          className="ui-confirmation"
          role="alertdialog"
          aria-labelledby={`${id}-title`}
          aria-describedby={`${id}-message`}
          guardChanges={false}
          initialFocus="cancel"
          onRequestClose={() => answer(item.id, false)}
        >
          <h2 id={`${id}-title`}>{item.title}</h2>
          <div id={`${id}-message`} className="ui-confirmation-message">
            {item.message}
          </div>
          <div className="ui-dialog-actions">
            <ActionButton
              type="button"
              data-cancel="true"
              onClick={() => answer(item.id, false)}
            >
              <X size={17} aria-hidden="true" />
              {item.cancelLabel || "Cancelar"}
            </ActionButton>
            <ActionButton
              type="button"
              className={
                item.tone === "danger"
                  ? "danger"
                  : item.tone === "warning"
                    ? "ui-warning-action"
                    : "primary"
              }
              onClick={() => answer(item.id, true)}
            >
              {item.confirmLabel || "Confirmar"}
            </ActionButton>
          </div>
        </AppDialog>
      )}
    </>
  );
}
