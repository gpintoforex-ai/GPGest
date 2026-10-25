import { CheckCircle2, CircleAlert, CircleMinus, Clock, Cloud, GitCompareArrows, HardDrive } from "lucide-react";
import type { SyncState } from "./types";

// Estados apresentados por ícone e texto, nunca só por cor.

export function ActiveBadge({ active }: { active: boolean }) {
  const Icon = active ? CheckCircle2 : CircleMinus;
  return <span className={`status-badge table-badge${active ? " status-success" : ""}`}><Icon size={14} aria-hidden="true" />{active ? "Ativo" : "Inativo"}</span>;
}

export function OriginBadge({ origin }: { origin: "local" | "erp" }) {
  const Icon = origin === "erp" ? Cloud : HardDrive;
  return <span className="status-badge table-badge"><Icon size={14} aria-hidden="true" />{origin === "erp" ? "ERP" : "IAZELA"}</span>;
}

const SYNC: Record<SyncState, [string, typeof CheckCircle2]> = {
  apenas_local: ["", HardDrive],
  sincronizado: ["status-success", CheckCircle2],
  pendente: ["status-warning", Clock],
  conflito: ["status-danger", GitCompareArrows],
  erro: ["status-danger", CircleAlert],
};

export function SyncBadge({ state, label }: { state: SyncState; label: string }) {
  const [tone, Icon] = SYNC[state];
  return <span className={`status-badge table-badge ${tone}`}><Icon size={14} aria-hidden="true" />{label}</span>;
}
