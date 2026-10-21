import { ActionButton } from "./components/InteractionUI";
import { useId, useState, type ReactNode } from "react";
import { formatEuro } from "./utils/format";

/** Gráficos SVG do dashboard: legenda, tooltip (rato, toque e teclado) e tabela de detalhe. */

const W = 640, H = 220, L = 58, R = 12, T = 14, B = 30;

export function compactEuro(cents: number) {
  const value = cents / 100, abs = Math.abs(value);
  const text = abs >= 1_000_000 ? `${(value / 1_000_000).toFixed(1)} M` : abs >= 10_000 ? `${Math.round(value / 1000)} k` : abs >= 1000 ? `${(value / 1000).toFixed(1)} k` : `${Math.round(value)}`;
  return text.replace(".", ",") + " €";
}

function scale(values: number[]) {
  const max = Math.max(0, ...values), min = Math.min(0, ...values);
  const span = max - min || 1;
  return { max, min, y: (v: number) => T + (H - T - B) * (1 - (v - min) / span) };
}

type Series = { key: string; label: string; form: "bar" | "line"; dashed?: boolean };

export function Legend({ series }: { series: Series[] }) {
  return <ul className="dash-legend">{series.map((s, i) => <li key={s.key}>
    <svg width="18" height="10" aria-hidden="true">{s.form === "bar" ? <rect x="2" y="1" width="14" height="8" rx="2" className={`dash-fill-${i + 1}`} /> : <line x1="1" x2="17" y1="5" y2="5" className={`dash-stroke-${i + 1}`} strokeDasharray={s.dashed ? "4 3" : undefined} />}</svg>{s.label}</li>)}</ul>;
}

export function ChartFrame({ title, legend, children, table, footnote }: { title: string; legend?: ReactNode; children: ReactNode; table: { columns: string[]; rows: (string | number)[][] }; footnote?: ReactNode }) {
  const [showTable, setShowTable] = useState(false);
  return <figure className="dash-chart">
    <figcaption><span>{title}</span><ActionButton type="button" className="dash-table-toggle" aria-pressed={showTable} onClick={() => setShowTable(!showTable)}>{showTable ? "Ver gráfico" : "Ver tabela"}</ActionButton></figcaption>
    {legend}
    {showTable ? <div className="table-wrap"><table><thead><tr>{table.columns.map(c => <th key={c}>{c}</th>)}</tr></thead><tbody>{table.rows.map((row, i) => <tr key={i}>{row.map((cell, j) => <td key={j} className={j ? "numeric" : ""}>{cell}</td>)}</tr>)}</tbody></table></div> : children}
    {footnote && <small className="dash-chart-note">{footnote}</small>}
  </figure>;
}

function Tooltip({ x, lines, title }: { x: number; title: string; lines: { label: string; value: string; index: number; form: "bar" | "line" }[] }) {
  return <div className="dash-tooltip" style={{ left: `${Math.min(Math.max(x / W * 100, 12), 88)}%` }} role="status">
    <strong>{title}</strong>
    {lines.map(line => <span key={line.label}><svg width="14" height="8" aria-hidden="true">{line.form === "bar" ? <rect width="12" height="8" rx="2" className={`dash-fill-${line.index}`} /> : <line x1="0" x2="14" y1="4" y2="4" className={`dash-stroke-${line.index}`} />}</svg><b>{line.value}</b> {line.label}</span>)}
  </div>;
}

function Axis({ min, max, y }: { min: number; max: number; y: (v: number) => number }) {
  const ticks = Array.from(new Set([min, 0, max, (max + min) / 2].filter(v => Number.isFinite(v)))).sort((a, b) => a - b);
  return <g className="dash-axis">{ticks.map(t => <g key={t}><line x1={L} x2={W - R} y1={y(t)} y2={y(t)} className={t === 0 ? "dash-zero" : "dash-grid"} /><text x={L - 6} y={y(t) + 4} textAnchor="end">{compactEuro(t)}</text></g>)}</g>;
}

function XLabels({ labels, x }: { labels: string[]; x: (i: number) => number }) {
  const step = Math.max(1, Math.ceil(labels.length / 7));
  return <g className="dash-axis">{labels.map((label, i) => i % step === 0 || i === labels.length - 1 ? <text key={i} x={x(i)} y={H - 10} textAnchor="middle">{label}</text> : null)}</g>;
}

/** Colunas da série principal e, opcionalmente, linha da série de comparação (formas distintas). */
export function ColumnsChart({ labels, series, values, ariaLabel }: { labels: string[]; series: Series[]; values: (number | null)[][]; ariaLabel: string }) {
  const id = useId();
  const [active, setActive] = useState<number | null>(null);
  const all = values.flat().filter((v): v is number => v !== null);
  const { min, max, y } = scale(all);
  const band = (W - L - R) / Math.max(labels.length, 1);
  const x = (i: number) => L + band * (i + 0.5);
  const bars = series.map((s, i) => ({ ...s, i })).filter(s => s.form === "bar");
  const barWidth = Math.max(2, Math.min(28, band * (bars.length > 1 ? 0.36 : 0.62)));
  return <div className="dash-plot" onPointerLeave={() => setActive(null)}>
    <svg viewBox={`0 0 ${W} ${H}`} role="img" aria-labelledby={id}><title id={id}>{ariaLabel}</title>
      <Axis min={min} max={max} y={y} />
      {bars.map((s, order) => values[s.i].map((v, i) => v === null ? null : <rect key={`${s.key}-${i}`} className={`dash-fill-${s.i + 1}${active === i ? " is-active" : ""}`}
        x={x(i) - (bars.length > 1 ? barWidth + 1 : barWidth / 2) + order * (barWidth + 2)} width={barWidth} y={Math.min(y(v), y(0))} height={Math.max(1, Math.abs(y(v) - y(0)))} rx="2" />))}
      {series.map((s, si) => s.form === "line" ? <g key={s.key}>
        <polyline fill="none" className={`dash-stroke-${si + 1}`} strokeDasharray={s.dashed ? "5 4" : undefined} points={values[si].map((v, i) => v === null ? "" : `${x(i)},${y(v)}`).filter(Boolean).join(" ")} />
        {values[si].map((v, i) => v === null ? null : <circle key={i} cx={x(i)} cy={y(v)} r={active === i ? 4.5 : 3} className={`dash-dot-${si + 1}`} />)}
      </g> : null)}
      <XLabels labels={labels} x={x} />
      {labels.map((label, i) => <rect key={label + i} x={L + band * i} y={T} width={band} height={H - T - B} className="dash-hit" tabIndex={0}
        aria-label={`${label}: ${series.map((s, si) => `${s.label} ${values[si][i] === null ? "sem dados" : formatEuro(values[si][i] as number)}`).join(", ")}`}
        onPointerEnter={() => setActive(i)} onPointerDown={() => setActive(i)} onFocus={() => setActive(i)} onBlur={() => setActive(null)} />)}
      {active !== null && <line x1={x(active)} x2={x(active)} y1={T} y2={H - B} className="dash-crosshair" />}
    </svg>
    {active !== null && <Tooltip x={x(active)} title={labels[active]} lines={series.map((s, si) => ({ label: s.label, value: values[si][active] === null ? "Sem dados" : formatEuro(values[si][active] as number), index: si + 1, form: s.form }))} />}
  </div>;
}

/** Saldo previsto: ponto de partida conhecido (realizado) e projeção tracejada; reserva como referência. */
export function ForecastChart({ points, start, reserve, ariaLabel }: { points: { label: string; value: number }[]; start: number | null; reserve: number | null; ariaLabel: string }) {
  const id = useId();
  const [active, setActive] = useState<number | null>(null);
  const values = points.map(p => p.value).concat(start === null ? [] : [start]).concat(reserve ? [reserve] : []);
  const { min, max, y } = scale(values);
  const step = (W - L - R) / Math.max(points.length - 1, 1);
  const x = (i: number) => L + step * i;
  return <div className="dash-plot" onPointerLeave={() => setActive(null)}>
    <svg viewBox={`0 0 ${W} ${H}`} role="img" aria-labelledby={id}><title id={id}>{ariaLabel}</title>
      <Axis min={min} max={max} y={y} />
      {!!reserve && <g><line x1={L} x2={W - R} y1={y(reserve)} y2={y(reserve)} className="dash-reserve" /><text x={W - R - 2} y={y(reserve) - 4} textAnchor="end" className="dash-axis-label">Reserva mínima</text></g>}
      <polyline fill="none" className="dash-stroke-1" strokeDasharray="6 4" points={points.map((p, i) => `${x(i)},${y(p.value)}`).join(" ")} />
      {start !== null && <g><circle cx={x(0)} cy={y(start)} r="5" className="dash-dot-1 dash-realized" /><text x={x(0) + 8} y={y(start) - 8} className="dash-axis-label">Saldo atual</text></g>}
      <XLabels labels={points.map(p => p.label)} x={x} />
      {points.map((p, i) => <rect key={p.label} x={x(i) - step / 2} y={T} width={step} height={H - T - B} className="dash-hit" tabIndex={0} aria-label={`${p.label}: saldo previsto ${formatEuro(p.value)}`}
        onPointerEnter={() => setActive(i)} onPointerDown={() => setActive(i)} onFocus={() => setActive(i)} onBlur={() => setActive(null)} />)}
      {active !== null && <><line x1={x(active)} x2={x(active)} y1={T} y2={H - B} className="dash-crosshair" /><circle cx={x(active)} cy={y(points[active].value)} r="4.5" className="dash-dot-1" /></>}
    </svg>
    {active !== null && <Tooltip x={x(active)} title={points[active].label} lines={[{ label: active === 0 ? "saldo de abertura (realizado)" : "saldo previsto", value: formatEuro(points[active].value), index: 1, form: "line" }]} />}
  </div>;
}

/** Barras horizontais ordenadas por valor, com rótulos diretos (sem depender da cor). */
export function BarList({ rows, ariaLabel }: { rows: { label: string; value: number; note?: string }[]; ariaLabel: string }) {
  const max = Math.max(1, ...rows.map(r => Math.abs(r.value)));
  return <ul className="dash-barlist" aria-label={ariaLabel}>{rows.map(row => <li key={row.label} tabIndex={0} aria-label={`${row.label}: ${formatEuro(row.value)}${row.note ? ` · ${row.note}` : ""}`}>
    <span className="dash-barlist-label">{row.label}</span>
    <span className="dash-barlist-track"><span className="dash-fill-1" style={{ width: `${Math.max(1, Math.abs(row.value) / max * 100)}%` }} /></span>
    <span className="dash-barlist-value">{formatEuro(row.value)}{row.note && <small>{row.note}</small>}</span>
  </li>)}</ul>;
}
