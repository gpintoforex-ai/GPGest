import { useState } from "react";
import { ActionButton } from "./InteractionUI";

/** Dia atual em Europe/Lisbon, no formato AAAA-MM-DD. */
export function lisbonToday() {
  return new Intl.DateTimeFormat("en-CA", { timeZone: "Europe/Lisbon", year: "numeric", month: "2-digit", day: "2-digit" }).format(new Date());
}

const iso = (date: Date) => date.toISOString().slice(0, 10);
const utc = (value: string) => new Date(`${value}T12:00:00Z`);
const MONTHS = Array.from({ length: 12 }, (_, month) => new Intl.DateTimeFormat("pt-PT", { month: "long", timeZone: "UTC" }).format(new Date(Date.UTC(2000, month, 1))));

/** Primeiro e último dia dos meses `from`..`to` (0 a 11) do ano indicado. */
export function monthsRange(year: number, from: number, to: number): [string, string] {
  return [iso(new Date(Date.UTC(year, from, 1, 12))), iso(new Date(Date.UTC(year, to + 1, 0, 12)))];
}

/** Default filter period, using the company's Lisbon calendar. */
export function currentMonthRange() {
  const today = lisbonToday();
  const month = Number(today.slice(5, 7)) - 1;
  const [from, to] = monthsRange(Number(today.slice(0, 4)), month, month);
  return { from, to };
}

/** Semana atual, de segunda a domingo. */
export function weekRange(today = lisbonToday()): [string, string] {
  const day = utc(today);
  const monday = new Date(day); monday.setUTCDate(day.getUTCDate() - ((day.getUTCDay() + 6) % 7));
  const sunday = new Date(monday); sunday.setUTCDate(monday.getUTCDate() + 6);
  return [iso(monday), iso(sunday)];
}

/**
 * Períodos rápidos para filtros por datas: hoje, semana atual, mês e ano à escolha e trimestres
 * do ano escolhido. Em formulários controlados usa `onSelect`; em formulários não controlados
 * indica os `names` dos campos desde/até. `untilToday` limita o fim ao dia atual quando a
 * consulta não aceita datas futuras.
 */
export default function DatePresets({ from, to, onSelect, names, untilToday, disabled }: {
  from?: string;
  to?: string;
  onSelect?: (from: string, to: string) => void;
  names?: [string, string];
  untilToday?: boolean;
  disabled?: boolean;
}) {
  const today = lisbonToday();
  const currentYear = Number(today.slice(0, 4)), currentMonth = Number(today.slice(5, 7)) - 1;
  const [chosen, setChosen] = useState<number | null>(null);
  const [anchor, setAnchor] = useState<HTMLElement | null>(null);
  // Sem escolha explícita, o ano de referência acompanha a data inicial do filtro.
  const year = chosen ?? (from && /^\d{4}-/.test(from) ? Number(from.slice(0, 4)) : currentYear);
  const years = Array.from(new Set([...Array.from({ length: 10 }, (_, i) => currentYear - i), year])).sort((a, b) => b - a);
  const clamp = (end: string) => untilToday && end > today ? today : end;

  function apply(start: string, end: string, source?: HTMLElement | null) {
    end = clamp(end);
    if (onSelect) return onSelect(start, end);
    const form = (source || anchor)?.closest("form");
    names?.forEach((name, index) => {
      const field = form?.elements.namedItem(name);
      if (!(field instanceof HTMLInputElement)) return;
      field.value = index ? end : start;
      field.dispatchEvent(new Event("input", { bubbles: true }));
      field.dispatchEvent(new Event("change", { bubbles: true }));
    });
  }
  const isRange = (start: string, end: string) => from === start && to === clamp(end);
  const button = (label: string, [start, end]: [string, string]) => {
    const active = isRange(start, end);
    return <ActionButton key={label} type="button" className={active ? "active" : ""} aria-pressed={active} disabled={disabled}
      onClick={event => apply(start, end, event.currentTarget)}>{label}</ActionButton>;
  };
  const selectedMonth = MONTHS.findIndex((_, month) => isRange(...monthsRange(year, month, month)));
  const available = (month: number) => !untilToday || year < currentYear || month <= currentMonth;
  const quarters = [1, 2, 3, 4].filter(q => available((q - 1) * 3));
  const selectedQuarter = quarters.find(q => isRange(...monthsRange(year, (q - 1) * 3, q * 3 - 1)));
  const wholeYear = isRange(...monthsRange(year, 0, 11));

  return <div className="date-presets" role="group" aria-label="Período rápido" ref={setAnchor}>
    {button("Hoje", [today, today])}
    {button("Esta semana", weekRange(today))}
    <select aria-label="Mês" className={selectedMonth >= 0 ? "active" : ""} disabled={disabled} value={selectedMonth >= 0 ? String(selectedMonth) : ""}
      onChange={event => { if (event.target.value) apply(...monthsRange(year, Number(event.target.value), Number(event.target.value)), event.currentTarget); }}>
      <option value="">Mês…</option>
      {MONTHS.map((name, month) => available(month) && <option key={name} value={month}>{name[0].toUpperCase() + name.slice(1)} {year}</option>)}
    </select>
    <select aria-label="Trimestre" className={selectedQuarter ? "active" : ""} disabled={disabled} value={selectedQuarter ? String(selectedQuarter) : ""}
      onChange={event => { if (!event.target.value) return; const q = Number(event.target.value); apply(...monthsRange(year, (q - 1) * 3, q * 3 - 1), event.currentTarget); }}>
      <option value="">Trimestre…</option>
      {quarters.map(q => <option key={q} value={q}>{q}.º trimestre{year !== currentYear ? ` ${year}` : ""}</option>)}
    </select>
    <select aria-label="Ano" className={wholeYear ? "active" : ""} disabled={disabled} value={wholeYear ? String(year) : ""}
      onChange={event => { if (!event.target.value) return; const next = Number(event.target.value); setChosen(next); apply(...monthsRange(next, 0, 11), event.currentTarget); }}>
      {!wholeYear && <option value="">Ano…</option>}
      {years.map(value => <option key={value} value={value}>Ano {value}</option>)}
    </select>
  </div>;
}
