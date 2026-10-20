import { useEffect, useState } from "react";
import { api } from "./lib/api";
import { Field } from "./components/Form";
import { ActionButton, ManagedForm, Notice, confirmAction } from "./components/InteractionUI";
import "./Legal.css";
type Row = Record<string, any>;
const labels: Row = { audience_notice: "Âmbito da oferta e destinatários", commercial_terms: "Condições comerciais, duração, renovação, cancelamento e experiência", processing_notice: "Finalidades: dados, fundamento, destinatários e conservação por finalidade", providers_notice: "Prestadores reais, serviços e contratos de tratamento confirmados", transfers_notice: "Localizações, países e garantias de transferências confirmadas", retention_policy: "Conservação por categoria, encerramento e ciclo das cópias de segurança", complaints_notice: "Reclamações, Livro de Reclamações e RAL: aplicabilidade e canais confirmados", accessibility_notice: "Acessibilidade: âmbito legal, avaliação efetuada, limitações e contacto", dpa_instructions: "Acordo de tratamento: instruções, segurança, subcontratantes, prazos e auditorias" };
labels.consumer_terms = "Consumidores: características, preço total/IVA, duração, renovação, cancelamento, livre resolução, conformidade e confirmação em suporte duradouro";
export default function LegalAdmin() {
  const [data, setData] = useState<Row | null>(null), [requests, setRequests] = useState<Row[]>([]);
  const [error, setError] = useState(""), [notice, setNotice] = useState(""), [busy, setBusy] = useState(false);
  async function load() { const [settings, list] = await Promise.all([api("/platform/legal"), api("/platform/privacy-requests")]); setData(settings); setRequests(list); }
  useEffect(() => { load().catch(e => setError(e.message)); }, []);
  async function run(task: () => Promise<void>) { if (busy) return; setBusy(true); setError(""); setNotice(""); try { await task(); } catch (e) { setError((e as Error).message); } finally { setBusy(false); } }
  function update(key: string, value: unknown) { setData(current => current ? { ...current, config: { ...current.config, [key]: value, reviewed: key === "reviewed" ? value : false } } : current); }
  async function save(publish: boolean) {
    if (!data) return;
    if (publish && !(await confirmAction({ title: "Publicar informação legal?", message: "Confirma que verificou a identidade, os contratos, localizações, conservação e a aplicabilidade legal? Esta versão ficará arquivada para suportar as aceitações. As alterações ao texto exigem uma nova versão.", confirmLabel: "Publicar versão" }))) return;
    setData(await api("/platform/legal", { ...data.config, revision: data.revision, publish })); setNotice(publish ? "Versão publicada e arquivada." : "Rascunho guardado. A versão publicada permanece disponível.");
  }
  return <section className="panel legal-admin"><h2>Informação legal e proteção de dados</h2>{error && <Notice tone="error">{error}</Notice>}{notice && <Notice tone="success">{notice}</Notice>}
    <p>Esta área prepara a informação do prestador do serviço IAZELA; não confunda os dados do prestador com os de uma empresa cliente. Não publique dados, contratos ou garantias por confirmar.</p>
    {data && <><p>Versão publicada: {data.published_version || "Ainda não publicada"} · Pendências do rascunho: {data.pending.length}</p><details><summary>Configurar e rever textos legais</summary><ManagedForm onSubmit={e => { e.preventDefault(); void run(() => save(false)); }}><fieldset disabled={busy}>
      <Field label="Versão a aprovar (por exemplo, ano-mês-dia.1)"><input required maxLength={80} value={data.config.version} onChange={e => update("version", e.target.value)} /></Field>
      <div className="legal-identity">{Object.entries({legal_name: "Denominação legal", nif: "NIF", address: "Morada do prestador", registry: "Registo comercial e número, ou fundamento de inaplicabilidade", email: "Email de contacto", privacy_email: "Email de privacidade"}).map(([key, label]) => <Field key={key} label={label}><input type={key.includes("email") ? "email" : "text"} value={data.config.identity[key] || ""} onChange={e => update("identity", { ...data.config.identity, [key]: e.target.value })} maxLength={key === "address" || key === "registry" ? 500 : 254} /></Field>)}</div>
      <Field label="Destinatários da oferta"><select value={data.config.audience} onChange={e => update("audience", e.target.value)}><option value="unconfirmed">Por confirmar</option><option value="professionals">Exclusivamente empresas/profissionais</option><option value="consumers">Consumidores</option><option value="both">Ambos</option></select></Field>
      {Object.entries(labels).map(([key, label]) => <Field key={key} label={String(label)}><textarea value={data.config[key]} onChange={e => update(key, e.target.value)} maxLength={10000} /></Field>)}
      <label className="doc-check"><input type="checkbox" checked={data.config.reviewed} onChange={e => update("reviewed", e.target.checked)} />Confirmo a revisão empresarial e jurídica aplicável, incluindo contratos, finalidades, conservação e localizações. Esta confirmação não equivale a uma certificação.</label>
      <div className="actions"><ActionButton disabled={busy}>Guardar rascunho</ActionButton><ActionButton type="button" className="primary" disabled={busy || !data.config.reviewed} onClick={() => run(() => save(true))}>Publicar versão revista</ActionButton></div>
      <p>Antes de publicar, reveja também os textos base da aplicação no painel seguinte. O site não oferece contratação ou cobrança automática. Se a oferta abranger consumidores, as condições devem contemplar preços e impostos, duração, resolução e conformidade digital; o acesso imediato não elimina automaticamente a livre resolução.</p>
    </fieldset></ManagedForm></details><details><summary>Pré-visualizar os textos do último rascunho guardado</summary>{Object.entries(data.preview).map(([key, value]) => <article key={key}><h3>{(value as Row).title}</h3>{(value as Row).sections.map((section: Row) => <section key={section.title}><h4>{section.title}</h4><p className="legal-prose">{section.text || "Informação por completar — publicação bloqueada"}</p></section>)}</article>)}</details></>}
    <details><summary>Pedidos de direitos ({requests.length})</summary><p>A receção inicia a contagem do prazo; aguardar identidade não suspende automaticamente o prazo. Este painel regista o trabalho e as comunicações efetivamente realizadas; não envia emails nem apaga dados. Verifique a identidade proporcionalmente, sem pedir cópias de identificação por defeito. Pedidos relativos aos documentos de clientes podem exigir encaminhamento ao responsável competente.</p>
      {requests.map(row => <RequestEditor key={`${row.reference}-${row.revision}`} row={row} busy={busy} save={values => run(async () => { await api(`/platform/privacy-requests/${row.reference}`, values); await load(); setNotice("Tratamento do pedido registado."); })} />)}
    </details>
  </section>;
}
function RequestEditor({ row, busy, save }: { row: Row; busy: boolean; save: (values: Row) => void }) {
  const [status, setStatus] = useState(row.status), [verified, setVerified] = useState(row.identity_verified);
  const [note, setNote] = useState(""), [extend, setExtend] = useState(false), [sent, setSent] = useState(false);
  const overdue = new Date(row.due_at).getTime() < Date.now() && !["answered", "refused"].includes(row.status);
  return <section className="privacy-request"><h3>{row.email} · {row.kind}</h3><p>Referência: {row.reference} · Recebido: {new Date(row.received_at).toLocaleDateString("pt-PT")} · Prazo: {new Date(row.due_at).toLocaleDateString("pt-PT")}{overdue ? " · PRAZO ULTRAPASSADO" : ""}</p><p>{row.company_reference}</p><p>{row.message}</p>
    <ManagedForm onSubmit={e => { e.preventDefault(); save({ revision: row.revision, status, identity_verified: verified, note, extend, communication_sent: sent }); }}><fieldset disabled={busy}>
      <Field label="Estado do pedido"><select value={status} onChange={e => setStatus(e.target.value)}>{Object.entries({received: "Recebido", identity_required: "Identidade a verificar", in_progress: "Em análise", forwarded: "Encaminhado ao responsável", answered: "Respondido", refused: "Recusado com fundamento"}).map(([key, label]) => <option key={key} value={key}>{label}</option>)}</select></Field>
      <label className="doc-check"><input type="checkbox" checked={verified} onChange={e => setVerified(e.target.checked)} />Identidade verificada proporcionalmente</label><label className="doc-check"><input type="checkbox" checked={extend} onChange={e => setExtend(e.target.checked)} />Prorrogar até dois meses adicionais (fundamentar e comunicar no primeiro mês)</label><label className="doc-check"><input type="checkbox" checked={sent} onChange={e => setSent(e.target.checked)} />A comunicação ao titular foi efetivamente enviada por canal externo adequado</label>
      <Field label="Fundamento, verificação e comunicação efetuada (sem documentos de identificação)"><textarea required minLength={5} maxLength={4000} value={note} onChange={e => setNote(e.target.value)} /></Field><ActionButton disabled={busy}>Registar tratamento</ActionButton>
    </fieldset></ManagedForm><details><summary>Histórico do pedido</summary>{row.history.map((item: Row, index: number) => <p key={index}>{item.at} · {item.status} · {item.note}</p>)}</details>
  </section>;
}
