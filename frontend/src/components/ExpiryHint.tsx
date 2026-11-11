import { StatusBadge } from "./BusinessUI";

// Mesmas regras do servidor (procurement.EXPIRY_WARN_DAYS / EXPIRY_CLAIM_DAYS), a contar da data do documento.
const WARN_DAYS = 730, CLAIM_DAYS = 365;

/** Estado da validade de uma linha de receção: obrigatória em artigos com lote, curta ou a reclamar. */
export function expiryState(expiry: string | null | undefined, documentDate: string, required: boolean) {
  if (!expiry) return required ? { tone: "danger" as const, text: "Validade obrigatória: o artigo tem lote." } : null;
  const days = Math.round((Date.parse(expiry.slice(0, 10)) - Date.parse(documentDate.slice(0, 10))) / 86400000);
  if (days < CLAIM_DAYS) return { tone: "danger" as const, text: `Validade a menos de 1 ano (${days} dias): reclamar ao fornecedor.` };
  if (days < WARN_DAYS) return { tone: "warning" as const, text: `Validade a menos de 2 anos (${Math.floor(days / 30.4)} meses).` };
  return null;
}

export default function ExpiryHint({ expiry, documentDate, required }: {
  expiry: string | null | undefined;
  documentDate: string;
  required: boolean;
}) {
  const state = documentDate ? expiryState(expiry, documentDate, required) : null;
  return state ? <StatusBadge tone={state.tone}>{state.text}</StatusBadge> : null;
}
