import { useEffect, useRef, useState } from "react";
import { Mail, Save, Plug } from "lucide-react";
import { api, Field } from "./App";
import { ActionButton, ManagedForm, Notice } from "./components/InteractionUI";
import { AccountingEmailDialog, EmailHistory } from "./AccountingEmail";

type Row = Record<string, any>;
const addresses = (text: string) => text.split(/[;,\n]+/).map(value => value.trim()).filter(Boolean);

export default function EmailSettings({ admin, company, stores }: { admin: boolean; company: Row; stores: Row[] }) {
  const [data, setData] = useState<Row | null>(null), [password, setPassword] = useState(""), [clearPassword, setClearPassword] = useState(false);
  const [additional, setAdditional] = useState(""), [cc, setCc] = useState(""), [busy, setBusy] = useState(false);
  const [error, setError] = useState(""), [notice, setNotice] = useState(""), [opened, setOpened] = useState<Row | null>(null), [historyTick, setHistoryTick] = useState(0);
  const running = useRef(false);
  useEffect(() => { if (!admin) return; let active = true; api("/settings/email").then(result => {
    if (active) { setData(result); setAdditional(result.additional_to.join("; ")); setCc(result.cc.join("; ")); }
  }).catch(e => { if (active) setError(e.message); }); return () => { active = false; }; }, [admin]);
  async function run(task: () => Promise<void>) { if (running.current) return; running.current = true; setBusy(true); setError(""); setNotice(""); try { await task(); } catch(e) { setError((e as Error).message); } finally { setBusy(false); running.current = false; } }
  if (!admin) return <section className="panel"><p>A configuração de email e o histórico de envios estão reservados ao administrador desta empresa.</p></section>;
  return <><div className="page-intro"><div><h1>Email</h1><p>SMTP, destinatários da contabilidade e histórico da empresa {company.name}.</p></div></div>
    {error && <Notice tone="error">{error}</Notice>}{notice && <Notice tone="success" onDismiss={() => setNotice("")}>{notice}</Notice>}
    {!data && !error && <p role="status">A carregar configuração de email…</p>}
    {data && <section className="panel"><ManagedForm onSubmit={e => { e.preventDefault(); void run(async () => {
      const { has_password, manual_only, ...saved } = data;
      const result = await api("/settings/email", { ...saved, password, clear_password: clearPassword, additional_to: addresses(additional), cc: addresses(cc) });
      setData(result); setPassword(""); setClearPassword(false); setAdditional(result.additional_to.join("; ")); setCc(result.cc.join("; "));
      setNotice("Configuração de email guardada nesta empresa. O envio continua manual.");
    }); }}><fieldset disabled={busy} className="ui-form-fields">
      <h2>Servidor SMTP</h2><label className="doc-check"><input type="checkbox" checked={data.enabled} onChange={e => setData({ ...data, enabled: e.target.checked })}/>Ativar o envio manual por SMTP</label>
      <div className="form-grid"><Field label="Servidor SMTP"><input name="host" maxLength={250} required={data.enabled} value={data.host} onChange={e => setData({ ...data, host: e.target.value })}/></Field>
        <Field label="Porta"><input name="port" type="number" min={1} max={65535} required value={data.port} onChange={e => setData({ ...data, port: Number(e.target.value) })}/></Field>
        <Field label="Segurança"><select name="security" value={data.security} onChange={e => setData({ ...data, security: e.target.value })}><option value="starttls">STARTTLS</option><option value="ssl">SSL / TLS</option></select></Field>
        <Field label="Utilizador SMTP"><input name="username" autoComplete="off" value={data.username} maxLength={250} onChange={e => setData({ ...data, username: e.target.value })}/></Field>
        <Field label="Palavra-passe SMTP"><input name="password" type="password" autoComplete="new-password" value={password} maxLength={500} placeholder={data.has_password ? "Guardada; deixe vazio para manter" : "Sem palavra-passe guardada"} onChange={e => setPassword(e.target.value)}/></Field>
        <Field label="Limite máximo da mensagem (MB)"><input type="number" min={0.064} max={100} step={0.001} value={data.max_message_bytes / 1_000_000} onChange={e => setData({ ...data, max_message_bytes: Math.round(Number(e.target.value) * 1_000_000) })}/></Field>
        <Field label="Nome do remetente"><input name="sender_name" maxLength={200} value={data.sender_name} onChange={e => setData({ ...data, sender_name: e.target.value })}/></Field>
        <Field label="Email do remetente"><input name="sender_email" type="email" required={data.enabled} value={data.sender_email} onChange={e => setData({ ...data, sender_email: e.target.value })}/></Field>
      </div>
      {data.has_password && <label className="doc-check"><input type="checkbox" checked={clearPassword} onChange={e => setClearPassword(e.target.checked)}/>Remover a palavra-passe guardada ao gravar</label>}
      <p className="footnote">O limite inclui cabeçalhos e codificação dos anexos. Antes do envio, é respeitado o menor limite entre este valor e o anunciado pelo servidor SMTP. O teste da ligação usa a configuração já guardada e não envia emails.</p>
      <h2>Contabilidade</h2><div className="form-grid">
        <Field label="Nome do contabilista ou gabinete"><input name="accountant_name" maxLength={200} value={data.accountant_name} onChange={e => setData({ ...data, accountant_name: e.target.value })}/></Field>
        <Field label="Email do contabilista ou gabinete"><input name="accountant_email" type="email" value={data.accountant_email} onChange={e => setData({ ...data, accountant_email: e.target.value })}/></Field>
        <Field label="Destinatários adicionais (opcional)"><input value={additional} onChange={e => setAdditional(e.target.value)} placeholder="Separar por ponto e vírgula"/></Field>
        <Field label="CC predefinido (opcional)"><input value={cc} onChange={e => setCc(e.target.value)}/></Field>
        <Field label="Periodicidade habitual"><select value={data.periodicity} onChange={e => setData({ ...data, periodicity: e.target.value })}><option value="mensal">Mensal</option><option value="trimestral">Trimestral</option><option value="personalizada">Personalizada</option></select></Field>
        {data.periodicity === "personalizada" && <Field label="Organização e lembretes do período personalizado"><input maxLength={1000} value={data.custom_period_note} onChange={e => setData({ ...data, custom_period_note: e.target.value })}/></Field>}
      </div>
      <Field label="Assunto predefinido"><input required maxLength={500} value={data.subject_template} onChange={e => setData({ ...data, subject_template: e.target.value })}/></Field>
      <Field label="Mensagem predefinida"><textarea required rows={12} maxLength={20000} value={data.message_template} onChange={e => setData({ ...data, message_template: e.target.value })}/></Field>
      <Field label="Assinatura da empresa"><textarea maxLength={5000} value={data.signature} onChange={e => setData({ ...data, signature: e.target.value })}/></Field>
      <p className="footnote">Campos disponíveis: {"{empresa}, {periodo}, {resumo_documentacao}, {resumo_pendencias_ou_sem_pendencias}, {assinatura_empresa}"}. A periodicidade organiza o trabalho; não ativa envios automáticos.</p>
      <div className="ui-dialog-actions"><ActionButton type="button" disabled={busy || !data.revision} onClick={() => run(async () => { const result = await api("/settings/email/test", {}); setNotice(result.message + (result.server_limit_bytes ? ` Limite anunciado: ${result.server_limit_bytes} bytes.` : " O servidor não anunciou um limite; será usado o limite configurado.")); })}><Plug size={16}/>Testar ligação guardada</ActionButton><ActionButton className="primary" disabled={busy}><Save size={16}/>{busy ? "A guardar…" : "Guardar configuração de email"}</ActionButton></div>
    </fieldset></ManagedForm></section>}
    <EmailHistory refreshKey={historyTick} onOpen={setOpened}/>
    {opened && <AccountingEmailDialog company={company} stores={stores} initial={opened} onClose={() => { setOpened(null); setHistoryTick(value => value + 1); }} onChanged={() => setHistoryTick(value => value + 1)}/>}
    <p className="footnote"><Mail size={15}/> A aceitação SMTP e a confirmação de receção são estados diferentes. A receção pode ser registada manualmente no histórico.</p>
  </>;
}
