import type { ReactNode } from "react";
import { Landmark, Undo2 } from "lucide-react";
import "./MovementIdentity.css";

export default function MovementIdentity({ reversal = false, children }: { reversal?: boolean; children: ReactNode }) {
  const Icon = reversal ? Undo2 : Landmark;
  return <span className={`movement-identity ${reversal ? "movement-identity-reversal" : "movement-identity-deposit"}`}>
    <Icon size={13} aria-hidden="true" />{children}
  </span>;
}

export function movementIdentity(row: Record<string, any>) {
  if (row.reversal_of || row.kind === "estorno" || (row.origin === "deposito_banco" && row.direction < 0)) return "reversal";
  if (row.kind === "deposito" || ["deposito_banco", "deposito_modulo"].includes(row.origin)) return "deposit";
  return null;
}
