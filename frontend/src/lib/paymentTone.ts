// Classe de cor por meio de pagamento (tokens --payment-* em design-system.css e themes.css).
const tones: Record<string, string> = {
  numerario: "numerario",
  cartao: "cartao",
  mbway: "mbway",
  transferencia: "transferencia",
};

export function paymentTone(key: string): string {
  const tone = tones[key] ?? (key.startsWith("cheque") ? "cheque" : "outro");
  return `payment-tone payment-tone-${tone}`;
}
