import { useMemo, type ReactNode } from "react";
import { ContextSuggestion, KpiCard, ModuleOverview } from "./OverviewUI";
import type { BusinessTone } from "./BusinessUI";
import "./OperationsOverview.css";

export type OperationMetric = {
  label: string;
  value: ReactNode;
  description: string;
  tone?: BusinessTone;
  onOpen?: () => void;
};
export type OperationSuggestion = {
  title: string;
  reason: ReactNode;
  evidence: string[];
  actionLabel: string;
  onReview: () => void;
  tone?: BusinessTone;
};

/** Presentation of already-authorized module data; never fetches or performs business actions. */
export default function OperationsOverview({ title, scope, metrics, loaded = true, error = false, suggestion, source }: {
  title: string;
  scope: string;
  metrics: OperationMetric[];
  loaded?: boolean;
  error?: boolean;
  suggestion?: OperationSuggestion | null;
  source?: unknown;
}) {
  const state = error ? "error" : loaded ? "ready" : "loading";
  // Only callers holding an actual query response provide a source reference.
  // Filtering or focusing controls does not change the time of that response.
  const receivedAt = useMemo(() => source ? new Date().toISOString() : null, [source]);
  return <ModuleOverview title={title} description={scope} className="operations-overview">
    {receivedAt && loaded && !error && <p className="operations-overview-time">Consulta recebida em {new Date(receivedAt).toLocaleString("pt-PT", {timeZone:"Europe/Lisbon"})} · hora de Lisboa</p>}
    <div className="operations-overview-metrics">
      {metrics.map(metric => <KpiCard key={metric.label} {...metric} state={state} />)}
    </div>
    {loaded && !error && suggestion && <ContextSuggestion
      title={suggestion.title} evidence={suggestion.evidence} actionLabel={suggestion.actionLabel}
      onReview={suggestion.onReview} tone={suggestion.tone}>
      {suggestion.reason}
    </ContextSuggestion>}
  </ModuleOverview>;
}
