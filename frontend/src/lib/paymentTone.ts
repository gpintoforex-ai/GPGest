// Classe de cor por meio de pagamento (tokens --payment-* em design-system.css e themes.css).
const tones: Record<string, string> = {
  numerario: "numerario",
  cartao: "cartao",
  mbway: "mbway",
  transferencia: "transferencia",
};

export function paymentTone(key: string): string {
  const name = key.normalize("NFD").replace(/[\u0300-\u036f]/g, "").toLowerCase().trim();
  const aliases: Record<string, string> = { nu: "numerario", mo: "numerario", cc: "cartao", cd: "cartao", tb: "transferencia", ch: "cheque", "mb way": "mbway", "multibanco / tpa": "cartao", "multibanco / cartao": "cartao" };
  const normalized = aliases[name] || name;
  const tone = tones[normalized] ?? (normalized.startsWith("cheque") ? "cheque" : "outro");
  return `payment-tone payment-tone-${tone}`;
}
