export type ArticleRow = Record<string, any>;
export const newArticle: ArticleRow = {
  code: "", name: "", reference: "", barcodes: [], description: "", photo: "",
  internal_notes: "", additional_photos: [], attachments: [],
  family: "", category: "", brand: "", supplier_id: null, supplier_reference: "",
  unit: "un", precision: 0, vat_rate: "23", sale_net: "0", minimum: "0",
  reorder: "0", location: "", lots: false, expiry: false, service: false, active: true, revision: 0,
};
export const articlePayload = (article: ArticleRow) => Object.fromEntries(
  Object.keys(newArticle).map(key => [key, article[key] ?? newArticle[key]]),
);
export function articlePrices(netValue: unknown, vatValue: unknown, costValue: unknown) {
  const number = (value: unknown) => value === null || value === undefined || value === "" || !Number.isFinite(Number(value)) ? null : Number(value);
  const net = number(netValue), vat = number(vatValue), cost = number(costValue);
  const margin = net !== null && cost !== null ? net - cost : null;
  return { net, gross: net !== null && vat !== null ? net * (1 + vat / 100) : null,
    cost, margin, marginPct: margin !== null && net !== null && net > 0 ? margin / net * 100 : null };
}
export function validateArticle(value: ArticleRow, articles: ArticleRow[], id?: string) {
  const errors: Record<string, string> = {};
  for (const key of ["code", "name", "unit"]) if (!String(value[key] || "").trim()) errors[key] = "Preencha este campo.";
  for (const [key, max] of Object.entries({ code: 100, name: 300, reference: 100, family: 100, category: 100, brand: 100, supplier_reference: 100, unit: 20, location: 200, description: 4000, internal_notes: 4000, photo: 2000 })) {
    if (String(value[key] || "").length > max) errors[key] = `Utilize até ${max} caracteres.`;
  }
  for (const [key, max] of Object.entries({ vat_rate: 100, sale_net: 1e9, minimum: 1e12, reorder: 1e12, precision: 6 })) {
    if (value[key] === "" || !Number.isFinite(Number(value[key])) || Number(value[key]) < 0 || Number(value[key]) > max) errors[key] = `Indique um valor entre 0 e ${max}.`;
  }
  if (!Number.isInteger(Number(value.precision))) errors.precision = "Indique um número inteiro de 0 a 6.";
  if (Number(value.reorder) < Number(value.minimum)) errors.reorder = "A reposição deve ser igual ou superior ao mínimo.";
  if (value.expiry && !value.lots) errors.expiry = "O controlo de validade exige lotes.";
  if (value.service && (value.lots || value.expiry)) errors.service = "Um serviço não controla lotes nem validades.";
  const validUrl = (value: string) => {
    try { const url = new URL(value); return value.length <= 2000 && url.protocol === "https:" && !!url.hostname && !url.username && !url.password; }
    catch { return false; }
  };
  if (value.photo && !validUrl(value.photo)) errors.photo = "Indique um endereço HTTPS válido, sem credenciais.";
  if (value.additional_photos.length > 10 || value.additional_photos.some((v: string) => !validUrl(v))) errors.additional_photos = "Use até 10 endereços HTTPS válidos, um por linha.";
  if (value.attachments.length > 20 || value.attachments.some((v: ArticleRow) => !validUrl(v.url) || String(v.name || "").length > 200)) errors.attachments = "Use até 20 anexos com endereço HTTPS válido e nome até 200 caracteres.";
  const codes: string[] = value.barcodes.map((v: string) => v.trim()).filter(Boolean);
  if (codes.length > 20 || codes.some(v => v.length > 100) || new Set(codes).size !== codes.length) errors.barcodes = "Use até 20 códigos distintos, com até 100 caracteres cada.";
  for (const other of articles) {
    if (other.id === id) continue;
    if (String(other.code).trim().toLocaleLowerCase("pt-PT") === String(value.code).trim().toLocaleLowerCase("pt-PT")) errors.code = `Código já utilizado por «${other.name}».`;
    if ((other.barcodes || []).some((v: string) => codes.includes(v))) errors.barcodes = `Código de barras já utilizado por «${other.name}».`;
  }
  return errors;
}
