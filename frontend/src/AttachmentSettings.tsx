import { ActionLink } from "./components/InteractionUI";
import { ActionButton, Notice } from "./components/InteractionUI";
import { useEffect, useState } from "react";
import { api } from "./lib/api";
import { Field, Form } from "./components/Form";
import GoogleInvoiceInbox from "./GoogleInvoiceInbox";
export default function AttachmentSettings({ nif }: { nif?: string }) {
  const [config, setConfig] = useState<Record<string, string> | null>(null);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const [google, setGoogle] = useState<{configured: boolean; connected: boolean; email: string} | null>(null);
  const [busy, setBusy] = useState(false);
  useEffect(() => { api("/integrations/attachments/google/status").then(setGoogle).catch(e => setError(e.message)); }, []);
  useEffect(() => { api("/settings/attachments").then(r => setConfig(r.config)).catch(e => setError(e.message)); }, []);
  const defaults = nif === "510774318" ? { outlook_email: "desafiosalutar@hotmail.com", google_email: "desafiosalutar@gmail.com", drive_folder: "1OV1fH8lEFrvlTa7P7-OzgkVxgFmMZ5pJ" } : {};
  return <section className="panel">
    <h3>Anexos de faturas · Gmail e Google Drive</h3>
    <p>Defina as contas e a pasta de destino para esta empresa.</p>
    <p className="pill neutral">{google?.connected ? "Conta ligada: " + google.email : "Conta Google ainda não ligada"}</p>
    <p>Consulte os emails, guarde os anexos selecionados no Drive e confirme a associação às faturas abaixo ou na Conferência documental.</p>
    <p>Para usar a pasta Drive existente, poderá ser necessário desligar e voltar a ligar a conta. A nova autorização solicita acesso de leitura e gestão ao Drive; a IAZELA usa a pasta configurada e não apaga ficheiros.</p>
    {google?.configured && <>
      {window.location.hostname !== "localhost" && <p>Para autorizar, <ActionLink href={"http://localhost:5174/?nif=" + encodeURIComponent(nif || "")}>abre a IAZELA em localhost</ActionLink>, entra na empresa e volta a Configurações.</p>}
      <ActionButton disabled={busy || window.location.hostname !== "localhost" && !google.connected} onClick={async () => {
        setBusy(true); setError("");
        try {
          if (google.connected) {
            await api("/integrations/attachments/google/disconnect", {}, { timeoutMs: 15000 });
            setGoogle({ ...google, connected: false, email: "" });
            setNotice("Conta Google desligada da IAZELA. Pode voltar a ligar para autorizar as novas permissões.");
          }
          else { const result = await api("/integrations/attachments/google/connect", {}, { timeoutMs: 15000 }); window.location.assign(result.url); }
        } catch(e) { setError((e as Error).name === "TimeoutError" ? "O servidor demorou a responder. Atualize a página para confirmar o estado da ligação e tente novamente." : (e as Error).message); }
        finally { setBusy(false); }
      }}>{busy ? (google.connected ? "A desligar…" : "A abrir Google…") : google.connected ? "Desligar conta Google" : "Ligar Gmail e Drive"}</ActionButton>
    </>}
    {error && <Notice className="error" role="alert">{error}</Notice>}
    {notice && <Notice onDismiss={() => setNotice("")} className="success" role="status">{notice}</Notice>}
    {config && <Form onSubmit={async v => {
      await api("/settings/attachments", v);
      setNotice("Configuração guardada para esta empresa.");
    }}>
      <input name="outlook_email" type="hidden" value="" />
      <Field label="Conta Google com acesso à pasta"><input name="google_email" type="email" required maxLength={200} defaultValue={config.google_email || defaults.google_email || ""} /></Field>
      <Field label="Link ou identificador da pasta Google Drive"><input name="drive_folder" required maxLength={300} defaultValue={config.drive_folder || defaults.drive_folder || ""} /></Field>
      <Field label="Pasta ou etiqueta Gmail (inbox ou all)"><input name="mail_folder" required maxLength={100} defaultValue={config.mail_folder || "inbox"} /></Field>
      <p>Os anexos serão associados às faturas após confirmação. Não introduza palavras-passe das contas nestes campos.</p>
    </Form>}
    {google?.connected && <GoogleInvoiceInbox key={google.email + notice} />}
  </section>;
}
