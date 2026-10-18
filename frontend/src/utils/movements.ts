/** Rótulos de tipos de movimento e meios de pagamento, e data/hora em Europe/Lisbon. */
export const formatDateTime = (v: string) =>
  new Intl.DateTimeFormat("pt-PT", {
    day: "2-digit",
    month: "2-digit",
    year: "numeric",
    hour: "2-digit",
    minute: "2-digit",
    timeZone: "Europe/Lisbon",
  }).format(new Date(v));
export const movementLabels: Record<string, string> = {
  venda: "Recebimento de venda",
  cliente: "Recebimento de cliente",
  entrada_extra: "Entrada extraordinária",
  entrada_caixa: "Entrada de caixa",
  saida_caixa: "Saída de caixa",
  fornecedor: "Pagamento a fornecedor",
  despesa: "Pequena despesa",
  reembolso: "Reembolso",
  diferenca_caixa: "Diferença de caixa",
  deposito: "Retirada para depósito",
  reforco: "Reforço de fundo",
  transferencia_caixa: "Transferência entre caixas",
  numerario: "Numerário",
  cartao: "Multibanco / cartão",
  mbway: "MB WAY",
  transferencia: "Transferência",
  outro: "Outro",
  cheque: "Cheques",
  misto: "Numerário e cheques",
};
