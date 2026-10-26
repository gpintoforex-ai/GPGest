type Row = Record<string, any>;
export type ProcurementAttachment = { name: string; url: string };

/** Original invoice, explicit links, and files on the associated purchase. */
export function procurementAttachments(document: Row, purchases: Row[]): ProcurementAttachment[] {
  const links: ProcurementAttachment[] = (document.attachments || []).map((url: string, index: number) => ({ name: `Anexo ${index + 1}`, url }));
  if (document.archive_document_id) links.push({ name: "Fatura original · Arquivo digital", url: `/api/archive/documents/${encodeURIComponent(document.archive_document_id)}/file` });
  const purchase = purchases.find(row => row.id === document.purchase_id);
  for (const attachment of purchase?.attachments || []) {
    const url = attachment.view_url || (attachment.id && !String(attachment.id).startsWith("drive:") ? `/api/purchases/attachments/${encodeURIComponent(attachment.id)}` : "");
    if (url) links.push({ name: attachment.name || "Anexo da compra", url });
  }
  return [...new Map(links.filter(link => link.url).map(link => [link.url, link])).values()];
}
