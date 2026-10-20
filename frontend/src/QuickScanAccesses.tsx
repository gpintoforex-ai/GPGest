import { useEffect, useState } from "react";
import { Copy, QrCode, ScanLine } from "lucide-react";
import { api } from "./lib/api";
import { ActionButton, Notice, confirmAction } from "./components/InteractionUI";
import { Field } from "./components/Form";
import "./DocumentScanner.css";

type Row = Record<string, any>;
const when = (value?: string | null) => value ? new Date(value).toLocaleString("pt-PT", { timeZone: "Europe/Lisbon", dateStyle: "short", timeStyle: "short" }) : "—";
const STATUS: Row = { cloud_saved: "Guardado na cloud", upload_error: "Erro no envio", uploading: "A enviar", pdf_generated: "PDF gerado (não enviado)" };

function state(item: Row) {
  if (item.revoked) return "Revogado";
  if (!item.active) return "Inativo";
  if (item.locked_until) return `Bloqueado até ${when(item.locked_until)}`;
  if (!item.paired) return item.pairing_pending ? "A aguardar emparelhamento" : "Sem dispositivo emparelhado";
  return "Ativo";
}

/** Administração dos acessos de digitalização rápida por PIN (dispositivo, loja, pasta cloud e PIN). */
export default function QuickScanAccesses() {
  const [data, setData] = useState<Row | null>(null), [error, setError] = useState(""), [notice, setNotice] = useState("");
  const [form, setForm] = useState<Row | null>(null), [pairing, setPairing] = useState<Row | null>(null);
  const load = async () => setData(await api("/quick-scan/accesses"));
  useEffect(() => { load().catch(e => setError((e as Error).message)); }, []);

  async function run(task: () => Promise<void>) {
    setError(""); setNotice("");
    try { await task(); } catch (e) { setError((e as Error).message); }
  }

  async function showPairing(name: string, result: Row) {
    const link = `${window.location.origin}/entrar?digitalizar=${encodeURIComponent(result.code)}`;
    const response = await fetch("/api/quick-scan/qr", { method: "POST", credentials: "same-origin", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ text: link }) });
    const qr = response.ok ? "data:image/svg+xml;charset=utf-8," + encodeURIComponent(await response.text()) : "";
    setPairing({ name, link, qr, expires_at: result.expires_at });
  }

  if (!data) return <section className="panel"><h3><ScanLine size={19} aria-hidden="true" /> Acessos de digitalização rápida</h3>{error ? <Notice tone="error">{error}</Notice> : <p role="status">A carregar…</p>}</section>;
  const editing = form && form.id;
  return <section className="panel">
    <h3><ScanLine size={19} aria-hidden="true" /> Acessos de digitalização rápida</h3>
    <p>Permite fotografar documentos a partir do ecrã de entrada, com um PIN, num dispositivo emparelhado (por exemplo, o telemóvel ou tablet da loja). O acesso só digitaliza e guarda na pasta cloud escolhida: não mostra dados, documentos anteriores nem configurações, e não cria compras, pagamentos ou stock. A sessão termina após {data.idle_minutes} minutos sem atividade.</p>
    {error && <Notice tone="error">{error}</Notice>}
    {notice && <Notice tone="success">{notice}</Notice>}
    {!data.cloud_connected && <Notice tone="warning">A conta Google não está ligada. Ligue-a em Anexos para que os envios possam ser guardados.</Notice>}
    {!data.folders.length && <Notice tone="warning">Não há pastas cloud configuradas. Configure a pasta em Anexos ou as pastas de anexos da Conferência documental.</Notice>}
    {pairing && <div className="quick-pairing" role="region" aria-label="Emparelhamento">
      <h4><QrCode size={18} aria-hidden="true" /> Emparelhar «{pairing.name}»</h4>
      <p>Leia o QR code com a câmara do dispositivo (ou abra nele o link). O código é de uso único e expira às {when(pairing.expires_at)}. Emparelhar desliga qualquer dispositivo anterior deste acesso.</p>
      {pairing.qr && <img src={pairing.qr} alt="QR code de emparelhamento" width={220} height={220} />}
      <div className="quick-pairing-link"><input readOnly value={pairing.link} aria-label="Link de emparelhamento" onFocus={e => e.target.select()} />
        <ActionButton type="button" onClick={() => void run(async () => { await navigator.clipboard.writeText(pairing.link); setNotice("Link copiado. Envie-o apenas para o dispositivo a emparelhar."); })}><Copy size={17} aria-hidden="true" />Copiar link</ActionButton></div>
      <ActionButton type="button" onClick={() => { setPairing(null); void load(); }}>Fechar</ActionButton>
    </div>}
    {data.accesses.length ? <div className="table-wrap"><table>
      <thead><tr><th>Nome / dispositivo</th><th>Loja</th><th>Pasta de destino</th><th>Estado</th><th>Último uso</th><th>Ações</th></tr></thead>
      <tbody>{data.accesses.map((item: Row) => <tr key={item.id}>
        <td>{item.name}</td><td>{item.store}</td><td>{item.folder_name}</td><td>{state(item)}</td><td>{when(item.last_used_at)}</td>
        <td>{!item.revoked && <div className="quick-actions">
          <ActionButton type="button" onClick={() => { setPairing(null); setForm({ id: item.id, name: item.name, store_id: item.store_id, folder_id: item.folder_id, active: item.active, pin: "", confirm: "" }); }}>Editar</ActionButton>
          <ActionButton type="button" action="Emparelhar" onClick={() => void run(async () => {
            if (item.paired && !(await confirmAction({ title: "Emparelhar novo dispositivo?", message: "O dispositivo atual deixa de ter acesso e as sessões abertas terminam.", confirmLabel: "Emparelhar novo" }))) return;
            await showPairing(item.name, await api(`/quick-scan/accesses/${item.id}/pairing`, {}));
          })}><QrCode size={17} aria-hidden="true" />{item.paired ? "Novo emparelhamento" : "Emparelhar"}</ActionButton>
          {item.locked_until && <ActionButton type="button" onClick={() => void run(async () => { await api(`/quick-scan/accesses/${item.id}/unlock`, {}); await load(); setNotice("Acesso desbloqueado."); })}>Desbloquear</ActionButton>}
          <ActionButton type="button" className="ui-action-danger" onClick={() => void run(async () => {
            if (!(await confirmAction({ title: "Revogar este acesso?", message: "O dispositivo deixa de funcionar de imediato e o acesso não pode ser reativado. Os documentos já guardados mantêm-se.", confirmLabel: "Revogar" }))) return;
            await api(`/quick-scan/accesses/${item.id}/revoke`, {}); await load(); setNotice("Acesso revogado.");
          })}>Revogar</ActionButton>
        </div>}</td>
      </tr>)}</tbody>
    </table></div> : <p>Ainda não existem acessos rápidos.</p>}
    {!form && <ActionButton type="button" className="primary" disabled={!data.folders.length || !data.stores.length} onClick={() => { setPairing(null); setForm({ name: "", store_id: data.stores[0]?.id || "", folder_id: data.folders[0]?.id || "", active: true, pin: "", confirm: "" }); }}>Novo acesso rápido</ActionButton>}
    {form && <form className="quick-form" onSubmit={event => { event.preventDefault(); void run(async () => {
      if ((form.pin || !editing) && form.pin !== form.confirm) throw new Error("Os dois PIN não coincidem.");
      const body = { name: form.name, store_id: form.store_id, folder_id: form.folder_id, active: form.active, pin: form.pin };
      if (editing) { await api(`/quick-scan/accesses/${form.id}`, body, { method: "PUT" }); setNotice("Acesso atualizado. As sessões abertas foram terminadas."); }
      else { const result = await api("/quick-scan/accesses", body); await showPairing(result.access.name, result.pairing); }
      setForm(null); await load();
    }); }}>
      <h4>{editing ? "Editar acesso" : "Novo acesso rápido"}</h4>
      <div className="form-grid">
        <Field label="Nome / dispositivo"><input value={form.name} required minLength={2} maxLength={100} placeholder="Ex.: Telemóvel da loja Centro" onChange={e => setForm({ ...form, name: e.target.value })} /></Field>
        <Field label="Loja"><select value={form.store_id} onChange={e => setForm({ ...form, store_id: e.target.value })}>{data.stores.map((s: Row) => <option key={s.id} value={s.id}>{s.name}</option>)}</select></Field>
        <Field label="Pasta cloud de destino"><select value={form.folder_id} onChange={e => setForm({ ...form, folder_id: e.target.value })}>{data.folders.map((f: Row) => <option key={f.id} value={f.id}>{f.name}</option>)}</select></Field>
        <Field label={editing ? "Novo PIN (vazio mantém o atual)" : "PIN (6 a 12 algarismos)"}><input type="password" inputMode="numeric" autoComplete="new-password" pattern="[0-9]{6,12}" required={!editing} value={form.pin} onChange={e => setForm({ ...form, pin: e.target.value.replace(/\D/g, "") })} /></Field>
        <Field label="Confirmar PIN"><input type="password" inputMode="numeric" autoComplete="new-password" pattern="[0-9]{6,12}" required={!editing || !!form.pin} value={form.confirm} onChange={e => setForm({ ...form, confirm: e.target.value.replace(/\D/g, "") })} /></Field>
      </div>
      <label className="quick-check"><input type="checkbox" checked={form.active} onChange={e => setForm({ ...form, active: e.target.checked })} /> Acesso ativo</label>
      <small>Evite PIN previsíveis (123456, 111111). O PIN é guardado apenas como hash; nunca é mostrado nem registado.</small>
      <div className="quick-actions">
        <ActionButton className="primary">{editing ? "Guardar alterações" : "Criar e emparelhar"}</ActionButton>
        <ActionButton type="button" dismiss onClick={() => setForm(null)}>Cancelar</ActionButton>
      </div>
    </form>}
    {data.recent.length > 0 && <>
      <h4>Últimas digitalizações rápidas</h4>
      <p>Ficam na pasta de destino de cada acesso e são encontradas pela conferência documental. Não criam compras automaticamente.</p>
      <div className="table-wrap"><table>
        <thead><tr><th>Data</th><th>Ficheiro</th><th>Tipo</th><th>Acesso</th><th>Loja</th><th className="numeric">Páginas</th><th>Estado</th></tr></thead>
        <tbody>{data.recent.map((r: Row) => <tr key={r.id}><td>{when(r.created_at)}</td><td>{r.name}</td><td>{r.type}</td><td>{r.access}</td><td>{r.store}</td><td className="numeric">{r.pages}</td><td>{STATUS[r.status] || r.status}</td></tr>)}</tbody>
      </table></div>
    </>}
  </section>;
}
