import { useEffect, useState } from "react";
import { api } from "./lib/api";
import { ActionButton, Notice } from "./components/InteractionUI";

const CHECK_EVERY_MS = 15 * 60 * 1000;
export const DRIVE_IMPORTED_EVENT = "iazela-drive-imported";

type Pending = { configured: boolean; pending: { saft?: number; statement?: number }; error?: string };

const plural = (count: number, one: string, many: string) => `${count} ${count === 1 ? one : many}`;

/** Aviso automático de ficheiros SAF-T e extratos no Google Drive ainda por importar. */
export default function DrivePendingNotice({ onOpen }: { onOpen: (page: string) => void }) {
  const [data, setData] = useState<Pending | null>(null);
  const [dismissed, setDismissed] = useState("");
  useEffect(() => {
    let active = true;
    const check = () => api("/drive/pending-imports", undefined, { timeoutMs: 60000 })
      .then(result => { if (active) setData(result); })
      .catch(() => { /* O aviso é informativo: falhas aparecem nas páginas de importação. */ });
    void check();
    const timer = window.setInterval(check, CHECK_EVERY_MS);
    window.addEventListener(DRIVE_IMPORTED_EVENT, check);
    window.addEventListener("iazela-bank-changed", check);
    return () => {
      active = false;
      window.clearInterval(timer);
      window.removeEventListener(DRIVE_IMPORTED_EVENT, check);
      window.removeEventListener("iazela-bank-changed", check);
    };
  }, []);
  const saft = data?.pending?.saft || 0, statement = data?.pending?.statement || 0;
  const signature = `${saft}:${statement}`;
  if (!data?.configured || !(saft || statement) || dismissed === signature) return null;
  const parts = [saft && plural(saft, "ficheiro SAF-T", "ficheiros SAF-T"), statement && plural(statement, "extrato bancário", "extratos bancários")].filter(Boolean);
  return <Notice tone="info" role="status" onDismiss={() => setDismissed(signature)}>
    Novos no Google Drive por importar: {parts.join(" e ")}.{" "}
    {!!saft && <ActionButton onClick={() => onOpen("Importar SAF-T de vendas")}>Ver SAF-T</ActionButton>}{" "}
    {!!statement && <ActionButton onClick={() => onOpen("Extratos")}>Ver extratos</ActionButton>}
  </Notice>;
}
