/** Indicação visível de que algo está a ser processado: texto e três pontos animados. */
export default function Processing({ label = "A processar…", inline = false }: { label?: string; inline?: boolean }) {
  return <span className={`table-processing${inline ? " is-inline" : ""}`} role="status" aria-live="polite">
    <span className="table-processing-label">{label}</span>
    <span className="table-processing-dots" aria-hidden="true"><i /><i /><i /></span>
  </span>;
}
