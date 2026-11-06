import { useEffect, useState, type ReactNode } from "react";
import type { LucideIcon } from "lucide-react";
import { ActionButton } from "./InteractionUI";
import type { BusinessTone } from "./BusinessUI";
import "./RecordSheet.css";

/** Presentation primitives for record dialogs; data, rights and mutations stay with their callers. */
export function RecordHeader({ title, subtitle, icon: Icon, actions }: { title: string; subtitle?: ReactNode; icon: LucideIcon; actions: ReactNode }) {
  return <header className="record-sheet-header"><div className="record-sheet-heading"><Icon size={29} aria-hidden="true" /><div><h2>{title}</h2>{subtitle && <small>{subtitle}</small>}</div></div><div className="record-sheet-actions">{actions}</div></header>;
}

export type RecordSection = { id: number; label: string; accessibleLabel?: string; icon: LucideIcon; invalid?: boolean };
export function RecordNavigation({ items, active, onSelect, prefix, label }: { items: RecordSection[]; active: number; onSelect: (id: number) => void; prefix: string; label: string }) {
  const [compact, setCompact] = useState(() => window.matchMedia("(max-width: 760px)").matches);
  useEffect(() => { const media = window.matchMedia("(max-width: 760px)"); const change = () => setCompact(media.matches); media.addEventListener("change", change); return () => media.removeEventListener("change", change); }, []);
  return <div className="record-sheet-nav" role="tablist" aria-label={label} aria-orientation={compact ? "horizontal" : "vertical"} onKeyDown={event => {
    if (!["ArrowUp", "ArrowDown", "ArrowLeft", "ArrowRight", "Home", "End"].includes(event.key)) return;
    event.preventDefault();
    const index = items.findIndex(item => item.id === active);
    const next = event.key === "Home" ? 0 : event.key === "End" ? items.length - 1 : (index + (["ArrowDown", "ArrowRight"].includes(event.key) ? 1 : -1) + items.length) % items.length;
    onSelect(items[next].id); document.getElementById(`${prefix}-tab-${items[next].id}`)?.focus();
  }}>{items.map(({ id, label, accessibleLabel, icon: Icon, invalid }) => <ActionButton key={id} type="button" role="tab" id={`${prefix}-tab-${id}`} aria-label={accessibleLabel} aria-selected={active === id} aria-controls={`${prefix}-panel-${id}`} tabIndex={active === id ? 0 : -1} onClick={() => onSelect(id)}><Icon size={18} aria-hidden="true" /><span>{label}</span>{invalid && <b aria-label="Precisa de correção">!</b>}</ActionButton>)}</div>;
}

export function RecordGroup({ title, icon: Icon, children, className = "", action }: { title: string; icon: LucideIcon; children: ReactNode; className?: string; action?: ReactNode }) {
  return <section className={`record-sheet-group ${className}`}><div className="record-sheet-group-title"><h3><Icon size={20} aria-hidden="true" />{title}</h3>{action}</div>{children}</section>;
}

export function RecordIndicator({ label, value, note, icon: Icon, tone = "neutral", onOpen }: { label: string; value: ReactNode; note?: string; icon: LucideIcon; tone?: BusinessTone; onOpen?: () => void }) {
  const content = <><Icon size={25} aria-hidden="true" /><span><span className="record-indicator-label">{label}</span><strong>{value}</strong>{note && <small>{note}</small>}</span></>;
  return onOpen ? <button type="button" className="record-indicator" data-tone={tone} onClick={onOpen}>{content}</button> : <div className="record-indicator" data-tone={tone}>{content}</div>;
}

export function RecordHistory({ rows, empty = "Sem histórico disponível." }: { rows: { id: string; title: string; detail: ReactNode }[]; empty?: string }) {
  return rows.length ? <ol className="record-sheet-history">{rows.map(row => <li key={row.id}><strong>{row.title}</strong><span>{row.detail}</span></li>)}</ol> : <p className="record-sheet-empty">{empty}</p>;
}
