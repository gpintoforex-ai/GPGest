/** Exact rational arithmetic; no eval, floating-point money or executable input. */
export function calculateMoney(expression: string): string | null {
  if (expression.length > 120 || !/^\d+(?:[,.]\d{0,2})?(?:[+\-*/]\d+(?:[,.]\d{0,2})?)*$/.test(expression)) return null;
  const tokens = expression.match(/\d+(?:[,.]\d{0,2})?|[+\-*/]/g)!;
  let index = 0;
  type Fraction = [bigint, bigint];
  function number(): Fraction {
    const [whole, fraction = ""] = tokens[index++].replace(",", ".").split(".");
    return [BigInt(whole) * 100n + BigInt(fraction.padEnd(2, "0")), 100n];
  }
  function term(): Fraction {
    let [n, d] = number();
    while (tokens[index] === "*" || tokens[index] === "/") {
      const operator = tokens[index++];
      const [next, divisor] = number();
      if (operator === "*") { n *= next; d *= divisor; }
      else { if (next === 0n) throw new Error("division"); n *= divisor; d *= next; }
    }
    return [n, d];
  }
  try {
    let [n, d] = term();
    while (index < tokens.length) {
      const operator = tokens[index++];
      const [next, divisor] = term();
      n = n * divisor + (operator === "+" ? next : -next) * d;
      d *= divisor;
    }
    if (n < 0n) return null;
    const cents = (n * 100n + d / 2n) / d;
    if (cents > BigInt(Number.MAX_SAFE_INTEGER)) return null;
    return `${cents / 100n},${String(cents % 100n).padStart(2, "0")}`;
  } catch { return null; }
}
