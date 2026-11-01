import { CheckCircle2, CircleMinus, Cloud, HardDrive } from "lucide-react";
import type { SyncState } from "./types";
import { StatusBadge, SyncStatus } from "../components/BusinessUI";

// Estados apresentados por ícone e texto, nunca só por cor.

export function ActiveBadge({ active }: { active: boolean }) {
  const Icon = active ? CheckCircle2 : CircleMinus;
  return (
    <StatusBadge
      tone={active ? "success" : "neutral"}
      className="table-badge"
      icon={<Icon size={14} />}
    >
      {active ? "Ativo" : "Inativo"}
    </StatusBadge>
  );
}

export function OriginBadge({ origin }: { origin: "local" | "erp" }) {
  const Icon = origin === "erp" ? Cloud : HardDrive;
  return (
    <StatusBadge className="table-badge" icon={<Icon size={14} />}>
      {origin === "erp" ? "ERP" : "IAZELA"}
    </StatusBadge>
  );
}

export function SyncBadge({
  state,
  label,
}: {
  state: SyncState;
  label: string;
}) {
  return <SyncStatus state={state} label={label} className="table-badge" />;
}
