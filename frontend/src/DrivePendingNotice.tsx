import { useEffect, useState } from "react";
import { api } from "./lib/api";

const CHECK_EVERY_MS = 15 * 60 * 1000;
export const DRIVE_IMPORTED_EVENT = "iazela-drive-imported";

export type DrivePending = { saft: number; statement: number };

/**
 * Ficheiros SAF-T e extratos no Google Drive ainda por importar (mostrados no sino, à parte dos avisos).
 * Verifica a cada 15 minutos e logo após uma importação ou alteração bancária.
 */
export function useDrivePending(enabled: boolean): DrivePending {
  const [pending, setPending] = useState<DrivePending>({ saft: 0, statement: 0 });
  useEffect(() => {
    if (!enabled) { setPending({ saft: 0, statement: 0 }); return; }
    let active = true;
    const check = () => api("/drive/pending-imports", undefined, { timeoutMs: 60000 })
      .then(result => {
        if (!active) return;
        const configured = !!result?.configured;
        setPending({
          saft: configured ? Number(result?.pending?.saft) || 0 : 0,
          statement: configured ? Number(result?.pending?.statement) || 0 : 0,
        });
      })
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
  }, [enabled]);
  return pending;
}
