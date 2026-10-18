import { ActionLink } from "./components/InteractionUI";
import { CloseButton, Notice } from "./components/InteractionUI";
import { useEffect, useState } from "react";
type Row = Record<string, any>;
export default function InvoiceSidePreview({ document, onClose }: { document: Row | null; onClose: () => void }) {
  const [attachmentId, setAttachmentId] = useState("");
  const [preview, setPreview] = useState(""), [error, setError] = useState(""), [image, setImage] = useState(false);
  const attachments = document?.attachments || [];
  const attachment = attachments.find((a: Row) => a.id === attachmentId) || attachments[0];
  const url = attachment && (attachment.view_url || `/api/documents/attachments/${attachment.id}/view`);
  useEffect(() => {
    const controller = new AbortController(); let objectUrl = "";
    setPreview(""); setError("");
    if (url) fetch(url, { credentials: "same-origin", signal: controller.signal }).then(async response => {
      if (!response.ok) { const result = await response.json().catch(() => ({})); throw new Error(result.detail || "Não foi possível carregar o anexo."); }
      const blob = await response.blob();
      if (!['application/pdf', 'image/png', 'image/jpeg'].includes(blob.type.split(';')[0])) throw new Error("O servidor não devolveu um PDF ou imagem válido.");
      if (controller.signal.aborted) return;
      objectUrl = URL.createObjectURL(blob); setImage(blob.type.startsWith("image/")); setPreview(objectUrl);
    }).catch(e => { if (!controller.signal.aborted) setError(e.message); });
    return () => { controller.abort(); if (objectUrl) URL.revokeObjectURL(objectUrl); };
  }, [url]);
  if (!document) return null;
  return <aside className="invoice-side-preview">
    <div className="panel-title"><h3>{document.document}</h3><CloseButton guard={false} onClick={onClose} /></div>
    <p>{document.supplier_name}</p>
    {attachment?.validation_status === "por_validar" && <Notice className="scanner-warning">Anexo por validar. Guardar o PDF não valida o conteúdo.</Notice>}
    {attachments.length > 1 && <label>Anexo a visualizar<select value={attachment?.id || ""} onChange={e => setAttachmentId(e.target.value)}>{attachments.map((a: Row) => <option key={a.id} value={a.id}>{a.name}</option>)}</select></label>}
    {attachment ? <><ActionLink href={url} target="_blank" rel="noreferrer">Abrir anexo numa nova janela</ActionLink>{error ? <Notice role="alert" className="error">{error}</Notice> : !preview ? <p role="status">A carregar anexo…</p> : image ? <img src={preview} alt={`Documento ${document.document}`} style={{ width: "100%", height: "auto" }} /> : <iframe key={preview} src={preview} title={`Documento ${document.document}`} />}</> : <p>Esta fatura ainda não tem um anexo associado. Associe o PDF do Drive para o visualizar aqui.</p>}
  </aside>;
}
