import { useId, useRef, type ReactNode } from "react";
import { ArrowRight, Sparkles } from "lucide-react";
import { ActionButton } from "./InteractionUI";
import type { BusinessTone } from "./BusinessUI";

/** Dashboard composition is loaded with its consumer, separately from common badges. */
type ValueState = "ready" | "loading" | "empty" | "error" | "restricted";
const stateText: Record<Exclude<ValueState, "ready">, string> = {
  loading: "A calcular…",
  empty: "Sem dados",
  error: "Indisponível",
  restricted: "Acesso reservado",
};

export function KpiCard({
  label,
  value,
  description,
  tone = "info",
  icon,
  onOpen,
  disabled = false,
  state = "ready",
  className = "",
}: {
  label: string;
  value: ReactNode;
  description?: ReactNode;
  tone?: BusinessTone;
  icon?: ReactNode;
  onOpen?: () => void;
  disabled?: boolean;
  state?: ValueState;
  className?: string;
}) {
  const content = (
    <>
      {icon && (
        <span className="ui-kpi-icon" aria-hidden="true">
          {icon}
        </span>
      )}
      <span className="ui-kpi-content">
        <strong className="ui-kpi-value">
          {state === "ready" ? (value ?? "—") : stateText[state]}
        </strong>
        <b className="ui-kpi-label">{label}</b>
        {description && (
          <small className="ui-kpi-description">{description}</small>
        )}
      </span>
      {onOpen && (
        <ArrowRight className="ui-kpi-arrow" size={16} aria-hidden="true" />
      )}
    </>
  );
  const classes = `ui-kpi ui-tone ${className}`;
  return onOpen ? (
    <ActionButton
      type="button"
      inferAction={false}
      className={classes}
      data-tone={tone}
      data-state={state}
      aria-busy={state === "loading"}
      disabled={disabled || state !== "ready"}
      onClick={onOpen}
    >
      {content}
    </ActionButton>
  ) : (
    <section
      className={classes}
      data-tone={tone}
      data-state={state}
      aria-label={label}
      aria-busy={state === "loading"}
    >
      {content}
    </section>
  );
}

/** A missing/zero target is not a measured percentage. No financial rules here. */
export function ProgressGoal({
  label,
  value,
  target,
  valueText,
  detail,
  tone = "info",
  showLabel = true,
  className = "",
}: {
  label: string;
  value: number | null;
  target: number | null;
  valueText?: string;
  detail?: ReactNode;
  tone?: BusinessTone;
  showLabel?: boolean;
  className?: string;
}) {
  const id = useId();
  const configured = target !== null && Number.isFinite(target) && target > 0;
  const known = value !== null && Number.isFinite(value);
  const measured = configured && known;
  const text =
    valueText ||
    (!configured
      ? "Sem objetivo configurado"
      : !known
        ? "A calcular…"
        : `${new Intl.NumberFormat("pt-PT", { maximumFractionDigits: 1 }).format((Math.max(0, value) / target) * 100)}%`);
  return (
    <div
      className={`ui-progress-goal ui-tone ${className}`}
      data-tone={tone}
      data-measured={measured}
    >
      {showLabel && (
        <div className="ui-progress-heading">
          <label htmlFor={id}>{label}</label>
          <span>{text}</span>
        </div>
      )}
      <progress
        id={id}
        aria-label={label}
        aria-valuetext={text}
        max={configured ? target : undefined}
        value={measured ? Math.max(0, Math.min(value, target)) : undefined}
      />
      {detail && <small className="ui-progress-detail">{detail}</small>}
    </div>
  );
}

type QuickFilter<T extends string> = {
  id: T;
  label: string;
  count?: number;
  disabled?: boolean;
};
export function QuickFilters<T extends string>({
  label,
  value,
  options,
  onChange,
  children,
  className = "",
  buttonClassName = "",
}: {
  label: string;
  value: T;
  options: readonly QuickFilter<T>[];
  onChange: (value: T) => void;
  children?: ReactNode;
  className?: string;
  buttonClassName?: string;
}) {
  const ref = useRef<HTMLDivElement>(null);
  return (
    <div
      className={`ui-quick-filters ${className}`}
      role="group"
      aria-label={label}
      data-ignore-dirty
      ref={ref}
      onKeyDown={(event) => {
        if (!["ArrowLeft", "ArrowRight", "Home", "End"].includes(event.key))
          return;
        const buttons = Array.from(
          ref.current?.querySelectorAll<HTMLButtonElement>(
            "[data-quick-filter]:not(:disabled)",
          ) || [],
        );
        const index = buttons.indexOf(
          document.activeElement as HTMLButtonElement,
        );
        if (index < 0) return;
        event.preventDefault();
        const next =
          event.key === "Home"
            ? 0
            : event.key === "End"
              ? buttons.length - 1
              : (index +
                  (event.key === "ArrowRight" ? 1 : -1) +
                  buttons.length) %
                buttons.length;
        buttons[next]?.focus();
      }}
    >
      {options.map((option) => (
        <ActionButton
          key={option.id}
          type="button"
          inferAction={false}
          data-quick-filter
          aria-pressed={value === option.id}
          disabled={option.disabled}
          className={`ui-quick-filter ${buttonClassName} ${value === option.id ? "active" : ""}`}
          onClick={() => onChange(option.id)}
        >
          {option.label}
          {option.count !== undefined && (
            <span className="ui-filter-count">{option.count}</span>
          )}
        </ActionButton>
      ))}
      {children}
    </div>
  );
}

export type ProcessStep = {
  id: string;
  label: string;
  tone?: BusinessTone;
  onSelect?: () => void;
  disabled?: boolean;
};
export function ProcessSteps({
  label,
  steps,
  current,
  className = "",
}: {
  label: string;
  steps: readonly ProcessStep[];
  current?: string;
  className?: string;
}) {
  return (
    <nav className={`ui-process ${className}`} aria-label={label}>
      <ol>
        {steps.map((step, index) => (
          <li key={step.id}>
            {step.onSelect ? (
              <ActionButton
                type="button"
                inferAction={false}
                className="ui-process-step ui-tone"
                data-tone={step.tone || "info"}
                aria-current={current === step.id ? "step" : undefined}
                disabled={step.disabled}
                onClick={step.onSelect}
              >
                <b aria-hidden="true">{index + 1}</b>
                <span>{step.label}</span>
              </ActionButton>
            ) : (
              <span
                className="ui-process-step ui-tone"
                data-tone={step.tone || "info"}
                aria-current={current === step.id ? "step" : undefined}
              >
                <b aria-hidden="true">{index + 1}</b>
                <span>{step.label}</span>
              </span>
            )}
            {index < steps.length - 1 && (
              <ArrowRight size={14} aria-hidden="true" />
            )}
          </li>
        ))}
      </ol>
    </nav>
  );
}

export function ModuleOverview({
  title,
  description,
  actions,
  children,
  className = "",
}: {
  title: string;
  description?: ReactNode;
  actions?: ReactNode;
  children: ReactNode;
  className?: string;
}) {
  const id = useId();
  return (
    <section className={`ui-module-overview ${className}`} aria-labelledby={id}>
      <header>
        <div>
          <h2 id={id}>{title}</h2>
          {description && <p>{description}</p>}
        </div>
        {actions && <div className="actions">{actions}</div>}
      </header>
      {children}
    </section>
  );
}

export function ContextSuggestion({
  title,
  children,
  evidence,
  actionLabel,
  onReview,
  disabled = false,
  tone = "suggestion",
  className = "",
}: {
  title: string;
  children: ReactNode;
  evidence: readonly string[];
  actionLabel?: string;
  onReview?: () => void;
  disabled?: boolean;
  tone?: BusinessTone;
  className?: string;
}) {
  const id = useId();
  return (
    <aside
      className={`ui-context-suggestion ui-tone ${className}`}
      data-tone={tone}
      aria-labelledby={id}
    >
      <Sparkles size={21} aria-hidden="true" />
      <div>
        <h3 id={id}>{title}</h3>
        <div className="ui-suggestion-body">{children}</div>
        <details>
          <summary>Origem da sugestão</summary>
          {evidence.length ? (
            <ul>
              {evidence.map((item, index) => (
                <li key={index}>{item}</li>
              ))}
            </ul>
          ) : (
            <p>Origem indisponível. Reveja os dados antes de avançar.</p>
          )}
        </details>
        {onReview && actionLabel && (
          <ActionButton
            type="button"
            variant="suggestion"
            disabled={disabled || !evidence.length}
            onClick={onReview}
          >
            {actionLabel}
            <ArrowRight size={15} aria-hidden="true" />
          </ActionButton>
        )}
      </div>
    </aside>
  );
}
