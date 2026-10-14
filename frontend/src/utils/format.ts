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
