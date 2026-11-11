// Casas decimais das quantidades no ecrã (Artigos → Parâmetros; 2 por omissão). Só a apresentação:
// os valores guardados mantêm a precisão de cada artigo. Definido ao carregar o arranque e o catálogo.
let decimals = 2;

export function setQuantityDecimals(value: unknown) {
  const parsed = Number(value);
  decimals = Number.isInteger(parsed) && parsed >= 0 && parsed <= 6 ? parsed : 2;
}

export function formatQuantity(value: unknown, missing = "Sem informação") {
  if (value === null || value === undefined || value === "") return missing;
  return new Intl.NumberFormat("pt-PT", { minimumFractionDigits: decimals, maximumFractionDigits: decimals }).format(Number(value));
}
