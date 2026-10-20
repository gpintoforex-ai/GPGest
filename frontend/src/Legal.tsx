import { useEffect, useState } from "react";
import { api } from "./lib/api";
import { ActionButton, ActionLink, Notice, ManagedForm } from "./components/InteractionUI";
import { Field } from "./components/Form";
import "./Legal.css";

type Row = Record<string, any>;
export const legalLinks = [["prestador", "Prestador e contactos"], ["termos", "Termos do serviço"], ["privacidade", "Privacidade"], ["cookies", "Cookies e preferências"], ["direitos", "Exercer direitos"], ["subcontratacao", "Tratamento de dados"], ["reclamacoes", "Reclamações"], ["acessibilidade", "Acessibilidade"]];
export function LegalFooter() {
  return <footer className="legal-footer"><nav aria-label="Informação legal">{legalLinks.map(([slug, label]) => <a key={slug} href={`/legal/${slug}`}>{label}</a>)}</nav></footer>;
}
export function PrivacyNotice() {
  return <p className="footnote">Os dados introduzidos são usados para gerir o acesso e o serviço. <a href="/legal/privacidade">Informação de privacidade</a> · <a href="/legal/direitos">Direitos e contacto</a>. Não está a subscrever publicidade.</p>;
}
function downloadJSON(value: unknown, name: string) {
  const url = URL.createObjectURL(new Blob([JSON.stringify(value, null, 2)], { type: "application/json" }));
  const link = document.createElement("a"); link.href = url; link.download = name; link.click();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
}
const kinds = [["access", "Acesso aos dados pessoais"], ["rectification", "Retificação"], ["erasure", "Apagamento"], ["restriction", "Limitação"], ["objection", "Oposição"], ["portability", "Portabilidade, quando aplicável"], ["closure", "Encerramento da empresa/contrato"], ["company_export", "Exportação contratual da empresa"]];

export default function LegalPage() {
  const slug = window.location.pathname.split("/")[2] || "prestador";
  const [data, setData] = useState<Row | null>(null), [account, setAccount] = useState<Row | null>(null);
  const [error, setError] = useState(""), [notice, setNotice] = useState(""), [busy, setBusy] = useState(false);
  const [receipt, setReceipt] = useState<Row | null>(null), [status, setStatus] = useState<Row | null>(null);
  const [receiptKey, setReceiptKey] = useState(""), [accepted, setAccepted] = useState(false);
  const publication = data?.publication;
  const doc = publication?.documents?.[slug];
  useEffect(() => {
    let active = true;
    document.title = `${legalLinks.find(([key]) => key === slug)?.[1] || "Informação legal"} · IAZELA`;
    api("/legal/public").then(result => { if (active) setData(result); }).catch(e => { if (active) setError(e.message); });
    fetch("/api/privacy/me", { credentials: "same-origin" }).then(async response => response.ok ? response.json() : null).then(result => { if (active) setAccount(result); }).catch(() => {});
    return () => { active = false; };
  }, [slug]);
  async function run(task: () => Promise<void>) {
    if (busy) return;
    setBusy(true); setError(""); setNotice("");
    try { await task(); } catch (e) { setError((e as Error).message); } finally { setBusy(false); }
  }
  return <main className="legal-page" id="legal-content"><a href="/">IAZELA · Início</a><h1>{legalLinks.find(([key]) => key === slug)?.[1] || "Informação legal"}</h1>
    {error && <Notice tone="error">{error}</Notice>}{notice && <Notice tone="success">{notice}</Notice>}
    {!data && !error && <p role="status">A carregar informação…</p>}
    {data && !data.published && <Notice tone="warning">A identificação do prestador e os textos contratuais estão em revisão e ainda não foram publicados. Não existe contratação automática neste site. A informação técnica de cookies e os direitos da sua conta podem ser consultados abaixo.</Notice>}
    {doc && <article><p>Versão {publication.version} · <ActionLink href={`/api/legal/documents/${encodeURIComponent(publication.version)}/${slug}`} download>Guardar uma cópia</ActionLink></p>
      {doc.sections.map((section: Row) => <section key={section.title}><h2>{section.title}</h2><p className="legal-prose">{section.text}</p></section>)}
      {slug === "privacidade" && <p><a href="https://www.cnpd.pt/cidadaos/direitos/" target="_blank" rel="noopener noreferrer">Direitos e reclamação à CNPD</a></p>}
      {slug === "termos" && account?.profile.role === "administrador" && <ManagedForm onSubmit={e => { e.preventDefault(); void run(async () => {
        const result = await api("/privacy/accept-terms", { version: publication.version, digest: publication.digest, confirmed: true });
        downloadJSON(result, "iazela-aceitacao-termos.json"); setNotice("Aceitação registada. Guarde o comprovativo e a cópia dos termos.");
      }); }}><label className="doc-check"><input type="checkbox" required checked={accepted} onChange={e => setAccepted(e.target.checked)} />Li e aceito esta versão dos termos e confirmo que tenho poderes para representar a empresa. Esta aceitação não autoriza publicidade.</label><ActionButton className="primary" disabled={busy || !accepted}>Registar aceitação</ActionButton></ManagedForm>}
    </article>}
    {slug === "cookies" && data && <section><h2>Tecnologias em uso</h2><p>Versão do inventário: {data.cookie_version}. Não estão implementadas categorias opcionais de analítica ou publicidade; por isso não é apresentado um banner de aceitação. As preferências abaixo são guardadas quando solicita a respetiva funcionalidade.</p>
      <div className="table-wrap"><table><caption>Cookies e armazenamento do navegador</caption><thead><tr><th>Nome e tecnologia</th><th>Finalidade e fornecedor</th><th>Duração</th></tr></thead><tbody>{data.cookies.map((item: Row) => <tr key={item.name}><td>{item.name}<small>{item.storage}</small></td><td>{item.purpose}<small>{item.provider}</small></td><td>{item.duration}</td></tr>)}</tbody></table></div>
      <h2>Gerir preferências</h2><p>Não há consentimentos opcionais ativos para aceitar, rejeitar ou retirar. Pode apagar as suas escolhas de apresentação e leitura. Esta ação mantém a autenticação. Pode terminar a sessão na plataforma ou limpar os cookies nas definições do navegador.</p>
      <ActionButton onClick={() => {
        try { for (const key of Object.keys(localStorage)) if (key === "iazela-theme" || key === "gpgest.read-aloud.preferences" || key.startsWith("gpgest.cash.preferences.")) localStorage.removeItem(key); sessionStorage.removeItem("iazela-bank-reconciliation-account"); document.documentElement.dataset.theme = "current"; window.dispatchEvent(new Event("iazela-theme-change")); setNotice("Preferências locais removidas."); } catch { setError("O navegador não permitiu limpar as preferências. Use as suas definições de armazenamento."); }
      }}>Limpar preferências guardadas</ActionButton>
      <p>Ligações externas, como abrir um documento no Drive, contactam o respetivo fornecedor quando são utilizadas. Os cookies desse serviço são geridos nas suas próprias definições. Novas tecnologias opcionais exigem atualização do inventário e bloqueio prévio até consentimento válido.</p></section>}
    {slug === "direitos" && data && <>
      <p>Pedidos de direitos não apagam automaticamente contas, documentos ou cópias de segurança. A portabilidade depende dos pressupostos legais; a exportação contratual da empresa é distinta. A resposta é devida, em regra, num mês, com eventual prorrogação fundamentada e comunicada no primeiro mês. Não envie documentos de identificação ou anexos financeiros neste formulário.</p>
      {publication && <p>Contacto do prestador: <a href={`mailto:${publication.identity.privacy_email}`}>{publication.identity.privacy_email}</a>. <a href="/legal/privacidade">Finalidades, conservação e destinatários</a>.</p>}
      {account && <section><h2>A minha conta</h2><p>Exportação limitada ao perfil, aos comprovativos de aceitação e às referências dos próprios pedidos. Documentos empresariais e dados de terceiros exigem análise do âmbito e das permissões.</p><ActionButton onClick={() => run(async () => downloadJSON(await api("/privacy/me"), "iazela-minha-conta.json"))} disabled={busy}>Exportar dados da minha conta</ActionButton></section>}
      {(publication || account) && <ManagedForm onSubmit={e => { e.preventDefault(); const values = Object.fromEntries(new FormData(e.currentTarget)); void run(async () => {
        const result = await api(account ? "/privacy/requests" : "/legal/rights", values); setReceipt(result); setReceiptKey(result.receipt); setNotice(result.message);
      }); }}><fieldset disabled={busy}><legend>Apresentar um pedido</legend><Field label="Email para resposta"><input name="email" type="email" maxLength={254} required defaultValue={account?.profile.email || ""} /></Field>
        <Field label="Tipo de pedido"><select name="kind">{kinds.filter(([key]) => !["closure", "company_export"].includes(key) || account?.profile.role === "administrador").map(([key, label]) => <option key={key} value={key}>{label}</option>)}</select></Field>
        <Field label="Empresa ou relação com o serviço (opcional)"><input name="company_reference" maxLength={200} /></Field><Field label="Descrição do pedido (sem anexos nem dados desnecessários)"><textarea name="message" maxLength={4000} rows={5} /></Field><p>A informação será utilizada para analisar e responder ao pedido, não para marketing. Antes de divulgar dados ou executar alterações, será verificada a identidade de forma proporcional.</p><ActionButton className="primary" disabled={busy}>Registar pedido</ActionButton></fieldset></ManagedForm>}
      {receipt && <section><h2>Comprovativo privado</h2><p>Referência: {receipt.reference} · Prazo inicial: {new Date(receipt.due_at).toLocaleDateString("pt-PT")}</p><p>Chave privada: <code className="legal-receipt">{receipt.receipt}</code></p><ActionButton onClick={() => downloadJSON(receipt, "iazela-pedido-direitos.json")}>Guardar comprovativo</ActionButton></section>}
      <ManagedForm onSubmit={e => { e.preventDefault(); void run(async () => setStatus(await api("/legal/rights/status", { receipt: receiptKey }))); }}><Field label="Chave privada do comprovativo"><input value={receiptKey} onChange={e => setReceiptKey(e.target.value)} required minLength={30} maxLength={100} autoComplete="off" /></Field><ActionButton disabled={busy}>Consultar estado do pedido</ActionButton></ManagedForm>
      {status && <p role="status">Referência {status.reference} · Estado: {({received: "Recebido", identity_required: "Aguarda verificação de identidade", in_progress: "Em análise", forwarded: "Encaminhado ao responsável", answered: "Respondido", refused: "Recusado com comunicação"} as Row)[status.status] || status.status} · Prazo registado: {new Date(status.due_at).toLocaleDateString("pt-PT")}</p>}
    </>}
  </main>;
}
