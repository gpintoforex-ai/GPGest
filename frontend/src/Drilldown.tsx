import { useEffect, useState, type ReactNode } from "react";
import { ArrowLeft, ChevronRight, Maximize, PanelRight, Square } from "lucide-react";
import { ActionButton, AppDialog, Notice } from "./components/InteractionUI";
import { api } from "./lib/api";
import "./Drilldown.css";

export type DrilldownContext = { company: string; store: string; start: string; end: string };
export type DrilldownStep = { label: string; params: Record<string, string>; expected?: number; offset?: number };
type Mode = "panel" | "modal" | "full";

export function DrilldownTrigger({ children, onOpen, label }: { children: ReactNode; onOpen: () => void; label?: string }) {
  return <ActionButton type="button" className="drilldown-trigger" aria-label={label} onClick={onOpen}>{children}<ChevronRight size={16} aria-hidden="true" /></ActionButton>;
}

export function DrilldownTable({ columns, rows, label }: { columns: string[]; rows: { id: string; cells: ReactNode[] }[]; label: string }) {
  if (!rows.length) return <p>Sem registos para estes filtros.</p>;
  return <div className="table-wrap"><table aria-label={label}><thead><tr>{columns.map(column => <th key={column} scope="col">{column}</th>)}</tr></thead>
    <tbody>{rows.map(row => <tr key={row.id}>{row.cells.map((cell, index) => <td key={index}>{cell}</td>)}</tr>)}</tbody></table></div>;
}

function DrilldownBreadcrumb({ steps, onGo }: { steps: DrilldownStep[]; onGo: (index: number) => void }) {
  return <nav aria-label="Percurso do detalhe"><ol className="drilldown-breadcrumb">{steps.map((step, index) => <li key={index}>
    {index === steps.length - 1 ? <span aria-current="location">{step.label}</span> : <button type="button" onClick={() => onGo(index)}>{step.label}</button>}
  </li>)}</ol></nav>;
}

export function DocumentPreview({ identifiers }: { identifiers: string[] }) {
  return identifiers.length > 0 && <details><summary>Documentos originais no Arquivo digital</summary><ul>{identifiers.map((id, index) =>
    <li key={id}><a href={`/api/archive/documents/${encodeURIComponent(id)}/file`} target="_blank" rel="noreferrer">Abrir original {index + 1}</a></li>)}</ul></details>;
}

export function DrilldownPanel<T>({ context, endpoint, initial, onClose, children }: {
  context: DrilldownContext; endpoint: string; initial: DrilldownStep[]; onClose: () => void;
  children: (value: T, controls: { step: DrilldownStep; open: (step: DrilldownStep) => void; paginate: (offset: number) => void }) => ReactNode;
}) {
  const [steps, setSteps] = useState(initial);
  const [mode, setMode] = useState<Mode>("panel");
  const [retry, setRetry] = useState(0);
  const [response, setResponse] = useState<{ key: string; value?: T; error?: string }>();
  const step = steps[steps.length - 1];
  const params = new URLSearchParams({ start: context.start, end: context.end, store_id: context.store, ...step.params,
    offset: String(step.offset ?? 0) });
  const url = `${endpoint}?${params}`;
  const key = `${retry}:${url}`;
  useEffect(() => {
    let active = true;
    api(url, undefined, { timeoutMs: 30_000 }).then(value => { if (active) setResponse({ key, value }); })
      .catch(error => { if (active) setResponse({ key, error: error.message }); });
    return () => { active = false; };
  }, [key, url]);
  const current = response?.key === key ? response : undefined;
  const date = (value: string) => value.split("-").reverse().join("/");
  return <AppDialog className={`drilldown-dialog drilldown-${mode}`} aria-label="Detalhe do indicador" guardChanges={false} onRequestClose={onClose}>
    <header className="drilldown-header"><h2>{step.label}</h2><p>{context.company} · {context.store ? "Estabelecimento selecionado" : "Estabelecimentos autorizados"} · {date(context.start)} a {date(context.end)}</p></header>
    <div className="drilldown-toolbar">
      {steps.length > 1 && <ActionButton type="button" onClick={() => setSteps(current => current.slice(0, -1))}><ArrowLeft size={16} />Voltar</ActionButton>}
      <DrilldownBreadcrumb steps={steps} onGo={index => setSteps(current => current.slice(0, index + 1))} />
      <div className="drilldown-modes" aria-label="Apresentação">
        <ActionButton type="button" aria-label="Painel lateral" aria-pressed={mode === "panel"} onClick={() => setMode("panel")}><PanelRight size={16} /></ActionButton>
        <ActionButton type="button" aria-label="Janela modal" aria-pressed={mode === "modal"} onClick={() => setMode("modal")}><Square size={16} /></ActionButton>
        <ActionButton type="button" aria-label="Vista de página completa" aria-pressed={mode === "full"} onClick={() => setMode("full")}><Maximize size={16} /></ActionButton>
      </div>
    </div>
    {current?.error ? <Notice tone="error">{current.error} <ActionButton type="button" onClick={() => setRetry(value => value + 1)}>Tentar novamente</ActionButton></Notice>
      : current?.value === undefined ? <p role="status">A carregar detalhe…</p>
      : children(current.value, { step, open: next => setSteps(current => [...current, next]),
        paginate: offset => setSteps(current => [...current.slice(0, -1), { ...current[current.length - 1], offset }]) })}
  </AppDialog>;
}
