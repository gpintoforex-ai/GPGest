/** Display-only formatting; financial calculations stay in integer cents. */
export function formatEuro(cents: number | null | undefined): string {
  if (cents == null || !Number.isFinite(cents)) return "—";
  const parts = new Intl.NumberFormat("pt-PT", {
    minimumFractionDigits: 2, maximumFractionDigits: 2, useGrouping: false,
  }).formatToParts(cents / 100);
  return parts.map(part => part.type === "integer"
    ? part.value.replace(/\B(?=(\d{3})+(?!\d))/g, ".")
    : part.value).join("") + " €";
}

/** Euros escritos («1.234,56» ou «1234.56») para cêntimos, só com aritmética inteira; NaN se inválido. */
export function euroToCents(text: string): number | null {
  const value = text.trim().replace(/\s|€/g, "");
  if (!value) return null;
  const normalized = value.includes(",") ? value.replace(/\./g, "").replace(",", ".") : value;
  const match = /^(\d+)(?:\.(\d{1,2}))?$/.exec(normalized);
  if (!match) return NaN;
  return Number(match[1]) * 100 + Number((match[2] || "").padEnd(2, "0"));
}

/** Cêntimos para texto editável («1234,50»). */
export const centsInput = (cents: number | null | undefined) => cents == null ? "" : `${Math.floor(cents / 100)},${String(cents % 100).padStart(2, "0")}`;
