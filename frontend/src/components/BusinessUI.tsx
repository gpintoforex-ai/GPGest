import type { ReactNode } from "react";
import {
  CheckCircle2,
  CircleAlert,
  Clock,
  GitCompareArrows,
  HardDrive,
} from "lucide-react";

/** Presentation only: callers provide authorized values and states. */
export type BusinessTone =
  | "neutral"
  | "info"
  | "success"
  | "warning"
  | "danger"
  | "suggestion"
  | "accent";
export function MetricCard({
  label,
  value,
  description,
  tone = "neutral",
  className = "",
  children,
}: {
  label: string;
  value: ReactNode;
  description: string;
  tone?: BusinessTone;
  className?: string;
  children?: ReactNode;
}) {
  return (
    <section
      className={`metric-card metric-${tone} ui-metric ui-tone ${className}`}
      data-tone={tone}
      title={description}
    >
      <h3>{label}</h3>
      <strong>{value ?? "—"}</strong>
      <p>{description}</p>
      {children}
    </section>
  );
}

export function StatusBadge({
  children,
  tone = "neutral",
  icon,
  className = "",
  title,
  compact = false,
}: {
  children: ReactNode;
  tone?: BusinessTone;
  icon?: ReactNode;
  className?: string;
  title?: string;
  compact?: boolean;
}) {
  return (
    <span
      className={`status-badge status-${tone} ui-status ui-tone ${className}`}
      data-tone={tone}
      data-density={compact ? "compact" : undefined}
      title={title}
    >
      {icon && (
        <span className="ui-status-icon" aria-hidden="true">
          {icon}
        </span>
      )}
      {children}
    </span>
  );
}

export type SyncState =
  "apenas_local" | "sincronizado" | "pendente" | "conflito" | "erro";
const syncStates: Record<
  SyncState,
  { label: string; tone: BusinessTone; icon: typeof Clock }
> = {
  apenas_local: { label: "Apenas local", tone: "neutral", icon: HardDrive },
  sincronizado: { label: "Sincronizado", tone: "success", icon: CheckCircle2 },
  pendente: { label: "Alterações pendentes", tone: "warning", icon: Clock },
  conflito: { label: "Conflito", tone: "danger", icon: GitCompareArrows },
  erro: { label: "Erro de sincronização", tone: "danger", icon: CircleAlert },
};
export function SyncStatus({
  state,
  label,
  lastSuccessfulAt,
  className = "",
}: {
  state: SyncState;
  label?: string;
  lastSuccessfulAt?: string | null;
  className?: string;
}) {
  const status = syncStates[state],
    Icon = status.icon;
  const date = lastSuccessfulAt ? new Date(lastSuccessfulAt) : null;
  const title =
    date && Number.isFinite(date.getTime())
      ? `Última sincronização bem-sucedida: ${date.toLocaleString("pt-PT", { timeZone: "Europe/Lisbon" })}`
      : undefined;
  return (
    <StatusBadge
      tone={status.tone}
      icon={<Icon size={14} />}
      className={className}
      title={title}
    >
      {label || status.label}
    </StatusBadge>
  );
}
