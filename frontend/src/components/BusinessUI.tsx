import type { ReactNode } from "react";

export function MetricCard({ label, value, description, tone = "neutral", className = "" }: {
  label: string; value: ReactNode; description: string;
  tone?: "neutral" | "success" | "warning" | "danger";
  className?: string;
}) {
  return <section className={`metric-card metric-${tone} ${className}`} title={description}>
    <h3>{label}</h3><strong>{value}</strong><p>{description}</p>
  </section>;
}

export function StatusBadge({ children, tone = "neutral" }: {
  children: ReactNode; tone?: "neutral" | "success" | "warning" | "danger" | "info";
}) {
  return <span className={`status-badge status-${tone}`}>{children}</span>;
}
