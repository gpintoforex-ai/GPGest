import { useEffect, useRef, useState } from "react";
import { api, Field, Form } from "./App";
import { ASSISTANTS } from "./components/AssistantIdentity";
type Row = Record<string, any>;
export default function DocumentAssistant({ document, admin, canReadErp = true, onClose, onPrepare }: {
  document: Row; admin: boolean; canReadErp?: boolean; onClose: () => void; onPrepare: () => void;
}) {
  const dialog = useRef<HTMLDialogElement>(null);
  const [step, setStep] = useState(1), [doc, setDoc] = useState(document);
  const [files, setFiles] = useState<Row[]>([]), [nextPage, setNextPage] = useState("");
  const [file, setFile] = useState<Row | null>(null), [analysis, setAnalysis] = useState<Row | null>(null);
  const [entities, setEntities] = useState<Row[]>([]), [busy, setBusy] = useState(false);
  const [error, setError] = useState(""), [notice, setNotice] = useState("");
  const [erpChecked, setErpChecked] = useState(false), [confirmDemo, setConfirmDemo] = useState(false);
  useEffect(() => { dialog.current?.showModal(); return () => dialog.current?.close(); }, []);
  async function act(task: () => Promise<void>) {
    setBusy(true); setError(""); setNotice("");
    try { await task(); } catch(e) { setError((e as Error).message); } finally { setBusy(false); }
  }
  const suppliers = entities.filter(e => (e.tax_number || "").trim() === doc.supplier_nif.trim());
  const supplierReady = suppliers.some(e => e.is_supplier && e.active);
  async function loadEntities() { setEntities(await api("/entities")); }
  async function loadDrive(page = "") {
    const r = await api(`/documents/${doc.id}/assistant/drive?page_token=${encodeURIComponent(page)}`);
    setFiles(current => page ? [...current, ...r.files] : r.files); setNextPage(r.next_page);
  }
  return <dialog ref={dialog} className="doc-dialog" aria-label={`${ASSISTANTS.clara.name} · Conferência documental`} onCancel={e => { e.preventDefault(); onClose(); }}>
    <div className="panel-title"><h2>{ASSISTANTS.clara.name} · {doc.document}</h2><button disabled={busy} onClick={onClose}>Fechar</button></div>
    <p>{ASSISTANTS.clara.mission}</p>
    <p><b>Passo {step} de 4:</b> {['Anexo no Drive', 'Fornecedor', 'Verificação ERP', 'Revisão'][step - 1]}</p>
    <p>{doc.supplier_name} · NIF {doc.supplier_nif} · {doc.document_date}</p>
    {error && <p className="error" role="alert">{error}</p>}
    {notice && <p className="success" role="status">{notice}</p>}
    {busy && <p role="status">A consultar…</p>}
    {step === 1 && <>
      <p>Os ficheiros ficam no Drive. Ao associar, o GPGest guarda apenas a referência, nome e metadados, sem guardar o PDF ou imagem na base de dados.</p>
      <button disabled={busy} onClick={() => act(() => loadDrive())}>Procurar anexos na pasta Drive</button>
      <div className="table-wrap"><table><thead><tr><th>Ficheiro</th><th>Sugestão inicial</th><th>Escolher</th></tr></thead><tbody>{files.map(f => <tr key={f.id}>
        <td><a href={f.url} target="_blank" rel="noreferrer">{f.name}</a></td>
        <td>{f.confidence}/100 · {f.reasons.join("; ") || "Rever manualmente"}</td>
        <td><button disabled={busy} onClick={() => act(async () => {
          setFile(f); setAnalysis(null);
          setAnalysis(await api(`/documents/${doc.id}/assistant/drive-analyze`, { drive_id: f.id, confirmed: false }));
        })}>Analisar e selecionar</button></td>
      </tr>)}</tbody></table></div>
      {nextPage && <button disabled={busy} onClick={() => act(() => loadDrive(nextPage))}>Consultar mais ficheiros</button>}
      {file && analysis && <Form submitLabel="Confirmar referência ao anexo" onSubmit={async v => {
        await act(async () => {
          await api(`/documents/${doc.id}/assistant/drive-attach`, { drive_id: file.id, confirmed: v.confirmed === "on" });
          setDoc(await api(`/documents/${doc.id}/detail`));
          setNotice("Referência ao anexo guardada. O ficheiro permanece no Drive.");
        });
      }}>
        <p><b>{file.name}</b> · Confiança {analysis.confidence}/100 · {analysis.reasons.join("; ") || "Sem correspondências suficientes"}</p>
        <p>A pontuação é indicativa. Reveja o conteúdo antes de confirmar; imagens e PDFs digitalizados não têm OCR nesta versão.</p>
        <label className="checkbox"><input name="confirmed" type="checkbox" required /> Confirmei que este ficheiro pertence ao documento.</label>
      </Form>}
      <p>{doc.attachments.length} anexos associados ao documento.</p>
      <button disabled={busy} onClick={() => act(async () => { await loadEntities(); setStep(2); })}>Continuar para fornecedor</button>
    </>}
    {step === 2 && <>
      {supplierReady ? <p>Fornecedor criado e ativo: {suppliers.find(e => e.is_supplier && e.active)?.name}.</p> : <>
        <p>{suppliers.length ? "A entidade existe, mas precisa de ser identificada como fornecedor ativo." : "Não existe um fornecedor com este NIF. Pode criar um registo no GPGest."}</p>
        <Form submitLabel={suppliers.length ? "Confirmar fornecedor ativo" : "Criar fornecedor"} onSubmit={async v => {
          await act(async () => {
            await api(`/documents/${doc.id}/assistant/supplier`, { name: v.name, nif: doc.supplier_nif, email: v.email, phone: v.phone, confirmed: v.confirmed === "on" });
            await loadEntities(); setNotice("Fornecedor validado no GPGest. Esta ação não cria entidades na Wintouch.");
          });
        }}>
          <Field label="Nome do fornecedor"><input name="name" defaultValue={doc.supplier_name} required minLength={2} maxLength={300} /></Field>
          <Field label="Email do fornecedor"><input name="email" type="email" maxLength={200} /></Field>
          <Field label="Telefone do fornecedor"><input name="phone" maxLength={60} /></Field>
          <label className="checkbox"><input name="confirmed" type="checkbox" required /> Confirmo o fornecedor com o NIF {doc.supplier_nif}.</label>
        </Form>
      </>}
      <button disabled={busy} onClick={() => setStep(1)}>Anterior</button>{" "}
      <button disabled={busy} onClick={() => setStep(3)}>Continuar para ERP</button>
    </>}
    {step === 3 && <>
      <p>{erpChecked ? "Consulta ao ERP concluída para a data do documento." : "Esta informação usa o catálogo ERP já importado. Para verificar a situação atual, consulte o ERP."}</p>
      {admin && canReadErp ? <>
        <label className="checkbox"><input type="checkbox" checked={confirmDemo} onChange={e => setConfirmDemo(e.target.checked)} /> Confirmo a consulta/importação ERP, incluindo dados reais se esta empresa estiver em demonstração.</label>
        <button disabled={busy || !confirmDemo} onClick={() => act(async () => {
          await api("/documents/erp-sync", { store_id: doc.store_id, start: doc.document_date, end: doc.document_date, confirm_demo: confirmDemo });
          setDoc(await api(`/documents/${doc.id}/detail`)); setErpChecked(true);
        })}>Verificar agora no ERP</button>
      </> : <p>Um administrador pode atualizar a consulta ao ERP. Não se presume que o documento está por lançar quando não há uma consulta atual.</p>}
      {doc.erp_status === "confirmada" && <p>Correspondência ERP já confirmada.</p>}
      {doc.erp_candidates.map((c: Row) => <div key={c.id} className="panel">
        <p>{c.internal_document || c.document} · {c.differences?.length ? "Divergências: " + c.differences.join("; ") : "Correspondência sugerida"}</p>
        {!c.linked && <button disabled={busy} onClick={() => act(async () => {
          await api(`/documents/${doc.id}/erp-link`, { erp_id: c.id, reason: "Correspondência revista no assistente documental" });
          setDoc(await api(`/documents/${doc.id}/detail`));
        })}>Confirmar correspondência ERP</button>}
      </div>)}
      {!doc.erp_candidates.length && <p>{erpChecked ? "Não foi encontrada uma correspondência nesta consulta ao ERP." : "Sem correspondência no catálogo local. Isto não confirma a ausência de lançamento no ERP."} Pode preparar uma proposta de despesa para revisão.</p>}
      <button disabled={busy} onClick={() => setStep(2)}>Anterior</button>{" "}
      <button disabled={busy} onClick={() => setStep(4)}>Rever e concluir</button>
    </>}
    {step === 4 && <>
      <p>Anexos: {doc.attachments.length} referências/anexos associados.</p>
      <p>Fornecedor: {supplierReady ? "criado e ativo" : "por validar"}.</p>
      <p>ERP: {doc.erp_status === "confirmada" ? "correspondência confirmada" : "por confirmar"}.</p>
      {doc.erp_status === "sem_correspondencia" && <>
        <p>Sugestão: preparar uma proposta de lançamento, rever os dados e verificar novamente o ERP antes de exportar. Não será emitido um documento automaticamente na Wintouch.</p>
        <button disabled={busy} onClick={() => act(async () => { await onPrepare(); })}>Abrir preparação da proposta de despesa</button>
      </>}
      <button disabled={busy} onClick={() => setStep(3)}>Anterior</button>{" "}<button disabled={busy} onClick={onClose}>Concluir assistente</button>
      <p>As ações confirmadas ficam guardadas individualmente. O assistente não marca o documento como pago nem entregue à contabilidade.</p>
    </>}
  </dialog>;
}
