import { useId, useState, type ReactNode } from "react";
import type { LucideIcon } from "lucide-react";
import { RecordHeader, RecordNavigation } from "./RecordSheet";
import "./RecordWorkspace.css";

export type WorkspaceSection = { label: string; icon: LucideIcon; content: ReactNode };

/** Keeps controls mounted: switching tabs must not discard drafts or skip validation. */
export function RecordWorkspace({ title, subtitle, icon, actions, indicators, sections, active, onSelect }: {
  title: string; subtitle?: ReactNode; icon: LucideIcon; actions?: ReactNode;
  indicators?: ReactNode; sections: WorkspaceSection[]; active?: number; onSelect?: (index: number) => void;
}) {
  const prefix = useId();
  const [selected, select] = useState(0);
  const current = active ?? selected;
  const change = onSelect ?? select;
  return <div className="business-record-workspace" onInvalidCapture={event => {
    const target = event.target as HTMLElement;
    const index = target.closest<HTMLElement>("[data-record-section]")?.dataset.recordSection;
    if (index !== undefined && Number(index) !== current) {
      change(Number(index));
      window.setTimeout(() => target.focus(), 0);
    }
  }}>
    <RecordHeader title={title} subtitle={subtitle} icon={icon} actions={actions} />
    <div className="business-record-layout">
      <aside><RecordNavigation prefix={prefix} label={`Separadores — ${title}`} active={current} onSelect={change} items={sections.map((section, id) => ({ id, label: section.label, icon: section.icon }))} /></aside>
      <div className="business-record-content">
        {indicators && <div className="business-record-indicators">{indicators}</div>}
        {sections.map((section, index) => <div key={section.label} role="tabpanel" id={`${prefix}-panel-${index}`} aria-labelledby={`${prefix}-tab-${index}`} data-record-section={index} hidden={current !== index} inert={current !== index}>{section.content}</div>)}
      </div>
    </div>
  </div>;
}
