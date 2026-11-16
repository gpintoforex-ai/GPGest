import { useEffect, useRef, useState, type ReactNode } from "react";
import { Camera, Download, Package, ScanText, Upload } from "lucide-react";
import { ActionButton } from "./components/InteractionUI";
import { api } from "./lib/api";

type Role = "photo" | "label";
type Image = { id: string; url: string; width: number; height: number; size: number; created_at: string; public?: boolean; gallery?: boolean };
type Images = Partial<Record<Role, Image | null>>;

const roleName = { photo: "fotografia", label: "fotografia do rótulo" };

async function uploadImage(articleId: string, role: Role, file: File): Promise<Images> {
  if (file.size > 20 * 1024 * 1024) throw new Error("A imagem excede 20 MB.");
  const body = new FormData();
  body.append("file", file);
  let response: Response;
  try {
    response = await fetch(`/api/stock/articles/${articleId}/images/${role}`, { method: "PUT", body, credentials: "same-origin" });
  } catch {
    throw new Error("Não foi possível contactar o servidor. Verifique a ligação e volte a tentar.");
  }
  const data = await response.json().catch(() => ({}));
  if (response.status === 401) window.dispatchEvent(new Event("gpgest-expired"));
  if (!response.ok) throw new Error(typeof data.detail === "string" ? data.detail : `Não foi possível carregar a ${roleName[role]}.`);
  return data;
}

/** Fotografia principal (carregada no IAZELA ou por endereço HTTPS) e fotografia do rótulo, só para a IA. */
export default function ArticlePhotos({ articleId, name, readOnly, urlPhoto, uploadedPhoto, urlEditor, onImagesChange }: {
  articleId?: string; name: string; readOnly: boolean; urlPhoto: string; uploadedPhoto?: string;
  urlEditor: ReactNode; onImagesChange?: (images: Images) => void;
}) {
  const [images, setImages] = useState<Images>(articleId && uploadedPhoto ? { photo: { url: uploadedPhoto } as Image } : {});
  const [busy, setBusy] = useState<Role | "">(""), [error, setError] = useState("");
  const [failed, setFailed] = useState("");
  const inputs = { photo: { camera: useRef<HTMLInputElement>(null), file: useRef<HTMLInputElement>(null) },
    label: { camera: useRef<HTMLInputElement>(null), file: useRef<HTMLInputElement>(null) } };
  useEffect(() => {
    setImages(articleId && uploadedPhoto ? { photo: { url: uploadedPhoto } as Image } : {});
    setError("");
    if (!articleId) return;
    let active = true;
    api(`/stock/articles/${articleId}/images`).then(result => { if (active) setImages(result); }).catch(() => undefined);
    return () => { active = false; };
  }, [articleId]);
  const update = (next: Images) => { setImages(next); onImagesChange?.(next); };
  async function choose(role: Role, file?: File) {
    if (!file || !articleId || busy) return;
    setBusy(role); setError("");
    try { update(await uploadImage(articleId, role, file)); }
    catch (e) { setError((e as Error).message); }
    finally { setBusy(""); }
  }
  async function remove(role: Role) {
    if (!articleId || busy) return;
    setBusy(role); setError("");
    try { update(await api(`/stock/articles/${articleId}/images/${role}`, {}, { method: "DELETE" })); }
    catch (e) { setError((e as Error).message); }
    finally { setBusy(""); }
  }
  // Endereço HTTPS indicado na ficha: a IAZELA vai buscar a imagem e guarda-a (a loja e a IA deixam de depender dele).
  const [imported, setImported] = useState("");
  const importable = !!articleId && /^https:\/\/[^\s]+$/i.test(urlPhoto.trim());
  async function importUrl() {
    if (!importable || busy) return;
    setBusy("photo"); setError(""); setImported("");
    try {
      update(await api(`/stock/articles/${articleId}/images/photo/from-url`, { url: urlPhoto.trim() }, { preserveDraft: true }));
      setImported("Imagem guardada na IAZELA. Passa a ser esta a fotografia do artigo, na aplicação e na loja online.");
    } catch (e) { setError((e as Error).message); }
    finally { setBusy(""); }
  }
  // Rótulo: endereço HTTPS a guardar na IAZELA e publicação no site (confirmada: legível e em português).
  const [labelUrl, setLabelUrl] = useState(""), [labelNote, setLabelNote] = useState("");
  const labelImportable = !!articleId && /^https:\/\/[^\s]+$/i.test(labelUrl.trim());
  async function importLabel() {
    if (!labelImportable || busy) return;
    setBusy("label"); setError(""); setLabelNote("");
    try {
      update(await api(`/stock/articles/${articleId}/images/label/from-url`, { url: labelUrl.trim() }, { preserveDraft: true }));
      setLabelUrl(""); setLabelNote("Rótulo guardado na IAZELA.");
    } catch (e) { setError((e as Error).message); }
    finally { setBusy(""); }
  }
  async function publishLabel(publicLabel: boolean, gallery: boolean) {
    if (!articleId || busy) return;
    setBusy("label"); setError(""); setLabelNote("");
    try {
      update(await api(`/stock/articles/${articleId}/images/label/publication`, { public: publicLabel, gallery }, { method: "PUT", preserveDraft: true }));
      setLabelNote(publicLabel ? "O rótulo vai para o site na próxima sincronização." : "O rótulo deixa de estar no site na próxima sincronização.");
    } catch (e) { setError((e as Error).message); }
    finally { setBusy(""); }
  }
  const pickers = (role: Role, label: string) => !articleId ? null : <div className="article-photo-actions">
    <ActionButton type="button" disabled={!!busy} onClick={() => inputs[role].camera.current?.click()}><Camera size={15} aria-hidden="true" />Tirar {label}</ActionButton>
    <ActionButton type="button" disabled={!!busy} onClick={() => inputs[role].file.current?.click()}><Upload size={15} aria-hidden="true" />Escolher ficheiro</ActionButton>
  </div>;
  const shown = images.photo?.url || urlPhoto;
  const label = images.label;
  return <div className="article-photo-card">
    <div className="article-photo-main">
    <div className="article-image" aria-busy={busy === "photo" || undefined}>
      {busy === "photo" ? <span>A carregar a fotografia…</span>
        : shown && failed !== shown ? <img src={shown} alt={name ? `Fotografia de ${name}` : "Fotografia do produto"} referrerPolicy="no-referrer" onError={() => setFailed(shown)} onLoad={() => setFailed("")} />
        : <><Package size={38} aria-hidden="true" /><span>{shown ? "Fotografia indisponível" : "Fotografia do produto"}</span></>}
    </div>
    {!readOnly && <details className="article-photo-editor"><summary aria-label={shown ? "Alterar fotografia" : "Definir fotografia"}><Camera size={16} aria-hidden="true" /></summary>
      <div className="article-photo-popover">
        <strong>Fotografia do produto</strong>
        {articleId ? <>{pickers("photo", "fotografia")}<small>Fica guardada no IAZELA e segue para a loja online nas publicações. JPG, PNG ou WebP até 20 MB.</small></>
          : <small>Guarde o artigo para carregar uma fotografia. Até lá, pode indicar um endereço HTTPS.</small>}
        {images.photo && <ActionButton type="button" disabled={!!busy} confirmation={{ title: "Retirar a fotografia carregada?", message: urlPhoto ? "Passa a usar-se a fotografia do endereço HTTPS da ficha. Na loja online, a fotografia é substituída na próxima sincronização." : "O artigo fica sem fotografia no IAZELA. Na loja online, a fotografia atual mantém-se.", confirmLabel: "Retirar fotografia", tone: "warning" }} onClick={() => void remove("photo")}>Retirar fotografia carregada</ActionButton>}
        <details className="article-photo-url"><summary>Ou usar um endereço HTTPS</summary>
          {images.photo && <small>A fotografia carregada prevalece sobre o endereço.</small>}
          {urlEditor}
          {articleId && <ActionButton type="button" variant="primary" className="article-photo-import" disabled={!importable || !!busy}
            title={importable ? undefined : "Indique primeiro um endereço HTTPS"} onClick={() => void importUrl()}>
            <Download size={15} aria-hidden="true" />{busy === "photo" ? "A guardar a imagem…" : "Guardar esta imagem na IAZELA"}</ActionButton>}
          {imported && <small role="status">{imported}</small>}
        </details>
      </div></details>}
    </div>
    <div className="article-label-photo">
      {label ? <a href={label.url} target="_blank" rel="noopener noreferrer" title="Abrir a fotografia do rótulo"><img src={label.url} alt={`Rótulo de ${name || "artigo"}`} /></a>
        : <span className="article-label-placeholder" aria-hidden="true"><ScanText size={18} /></span>}
      <div title={label?.public ? "Fotografia do rótulo: lida pela IA e publicada no site." : "Fotografia do rótulo: só a IA a lê; não vai para o site."}><strong>Rótulo</strong><small>{busy === "label" ? "A carregar…" : !label ? "Por tirar" : label.public ? "No site" : "Para a IA"}</small></div>
      {!readOnly && articleId && <details className="article-photo-editor article-label-editor"><summary aria-label={label ? "Alterar fotografia do rótulo" : "Fotografar o rótulo"}><ScanText size={15} aria-hidden="true" /></summary>
        <div className="article-photo-popover">
          <strong>Fotografia do rótulo</strong>
          {pickers("label", "fotografia")}
          <small>A IA lê-a para propor um descritivo mais completo (ingredientes, dose, advertências). Fotografe o rótulo de frente, nítido e sem reflexos.</small>
          <details className="article-photo-url" open={!label || undefined}><summary>Ou usar um endereço HTTPS</summary>
            <label className="article-label-url" data-ignore-dirty>URL da fotografia (HTTPS)<input type="url" inputMode="url" value={labelUrl} placeholder="https://…" onChange={e => setLabelUrl(e.target.value)} /></label>
            <ActionButton type="button" variant="primary" className="article-photo-import" disabled={!labelImportable || !!busy} onClick={() => void importLabel()}>
              <Download size={15} aria-hidden="true" />{busy === "label" ? "A guardar a imagem…" : "Guardar esta imagem na IAZELA"}</ActionButton>
          </details>
          {label && <fieldset className="article-label-publish" disabled={!!busy} data-ignore-dirty>
            <legend>No site</legend>
            <label><input type="checkbox" checked={!!label.public} onChange={e => void publishLabel(e.target.checked, e.target.checked && !!label.gallery)} />Publicar o rótulo no site (legível e em português)</label>
            <label><input type="checkbox" checked={!!label.gallery} disabled={!label.public} onChange={e => void publishLabel(true, e.target.checked)} />Também como última imagem da galeria</label>
            <small>Vai para o campo «rótulo em português» da informação alimentar. Ao substituir a fotografia, volta a ser só para a IA até nova confirmação.</small>
          </fieldset>}
          {labelNote && <small role="status">{labelNote}</small>}
          {label && <ActionButton type="button" disabled={!!busy} confirmation={{ title: "Retirar a fotografia do rótulo?", message: "A IA deixa de a usar nas próximas propostas de descritivo.", confirmLabel: "Retirar rótulo", tone: "warning" }} onClick={() => void remove("label")}>Retirar fotografia do rótulo</ActionButton>}
        </div></details>}
    </div>
    {error && <small className="ui-field-error article-photo-error" role="alert">{error}</small>}
    {articleId && !readOnly && <span hidden data-ignore-dirty>
      {(["photo", "label"] as Role[]).map(role => <span key={role}>
        <input ref={inputs[role].camera} type="file" accept="image/*" capture="environment" tabIndex={-1} aria-hidden="true" onChange={e => { void choose(role, e.target.files?.[0]); e.target.value = ""; }} />
        <input ref={inputs[role].file} type="file" accept="image/jpeg,image/png,image/webp" tabIndex={-1} aria-hidden="true" onChange={e => { void choose(role, e.target.files?.[0]); e.target.value = ""; }} />
      </span>)}
    </span>}
  </div>;
}
