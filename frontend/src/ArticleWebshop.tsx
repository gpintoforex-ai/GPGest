import { useEffect, useState } from "react";
import { Globe } from "lucide-react";
import { ActionButton, Notice } from "./components/InteractionUI";
import { StatusBadge } from "./components/BusinessUI";
import { RecordGroup } from "./components/RecordSheet";
import { api } from "./lib/api";

type Row = Record<string, any>;

const TONES: Record<string, "success" | "info" | "warning" | "danger" | "neutral"> = {
  synced: "success", pending: "info", error: "danger", blocked: "warning", withdrawn: "neutral",
};
const SHORT: Record<string, string> = {
  synced: "No site", pending: "A enviar", error: "Erro", blocked: "Bloqueado", withdrawn: "Retirado",
};
const PRESERVED_LABELS: Record<string, string> = { description: "descrição", short_description: "resumo", images: "imagens", price: "preço",
  seo_keyword: "palavra-chave SEO", seo_title: "título SEO", seo_description: "meta-descrição SEO", label: "rótulo" };
const money = (v: any) => v == null ? "Sem preço" : new Intl.NumberFormat("pt-PT", { style: "currency", currency: "EUR" }).format(Number(v));
const when = (v: any) => v ? new Intl.DateTimeFormat("pt-PT", { dateStyle: "short", timeStyle: "short", timeZone: "Europe/Lisbon" }).format(new Date(v)) : "Ainda não confirmada";

/** Estado do artigo numa loja online, curto (grelha) ou completo (ficha). */
export function WebBadge({ state, long = false }: { state: Row; long?: boolean }) {
  const status = state.status as string;
  return <StatusBadge compact tone={TONES[status] || "neutral"} icon={<Globe size={12} />}
    title={state.error || (state.blockers || []).join(" ") || state.status_label}>{long ? state.status_label : SHORT[status] || state.status_label}</StatusBadge>;
}

/** Marca o artigo para a loja: cria a publicação ou volta a publicar uma retirada. */
export async function markForShop(connectionId: string, articleId: string, publication?: Row | null, allowNew = false) {
  if (publication) return api(`/webshop/publications/${publication.id}/publish`, { revision: publication.revision });
  return api("/webshop/publications", { connection_id: connectionId, article_id: articleId, ...(allowNew ? { allow_new: true } : {}) });
}

/** Retira da loja: o produto fica em rascunho e sem stock (não é apagado). */
export function withdrawFromShop(publication: Row) {
  return api(`/webshop/publications/${publication.id}/withdraw`, { revision: publication.revision });
}

export const WITHDRAW_CONFIRMATION = {
  title: "Retirar da loja online?",
  message: "O produto fica em rascunho e sem stock na loja na próxima sincronização. Não é apagado: o histórico, as encomendas e a informação alimentar mantêm-se, e pode voltar a marcá-lo.",
  confirmLabel: "Retirar da loja", tone: "warning" as const,
};

/** Bloco «Loja online» da ficha do artigo. */
export function ArticleWebshop({ articleId, readOnly }: { articleId?: string; readOnly: boolean }) {
  const [data, setData] = useState<Row | null>(null), [error, setError] = useState(""), [notice, setNotice] = useState("");
  const [busy, setBusy] = useState(""), [ean, setEan] = useState("");
  const load = () => articleId ? api(`/webshop/article-publications/${articleId}`).then(setData) : Promise.resolve();
  useEffect(() => { setData(null); setError(""); setNotice(""); setEan(""); load()?.catch(e => setError((e as Error).message)); }, [articleId]);
  if (!articleId) return null;
  const connections: Row[] = data?.connections || [];
  if (data && !connections.length) return null;
  async function run(key: string, action: () => Promise<unknown>, done: string) {
    setBusy(key); setError(""); setNotice(""); setEan("");
    try { await action(); await load(); setNotice(done); }
    catch (e) {
      const message = (e as Error).message;
      if (/mesmo EAN/.test(message)) setEan(key);
      setError(message);
    } finally { setBusy(""); }
  }
  return <RecordGroup title="Loja online" icon={Globe} className="article-webshop">
    {!data && !error && <p className="footnote">A carregar…</p>}
    {connections.map(item => {
      const pub = item.publication;
      const manage = item.can_manage && !readOnly;
      const mark = (allowNew = false) => run(item.connection_id, () => markForShop(item.connection_id, articleId, pub, allowNew),
        "Artigo marcado para o site. A loja recebe-o na próxima sincronização.");
      return <div key={item.connection_id} className="article-webshop-row">
        <div className="article-webshop-head">
          <strong>{item.connection}</strong>
          {pub ? <WebBadge state={pub} long /> : <StatusBadge compact tone="neutral">Fora da loja</StatusBadge>}
        </div>
        {pub ? <dl className="article-webshop-facts">
          <div><dt>SKU</dt><dd>{pub.sku}</dd></div>
          <div><dt>Preço no site</dt><dd>{money(pub.price_gross)}{pub.channel_price ? " (preço do canal)" : ""}</dd></div>
          <div><dt>Última sincronização</dt><dd>{when(pub.synced_at)}</dd></div>
          {pub.woo_product_id && <div><dt>Produto na loja</dt><dd>#{pub.woo_product_id}{pub.woo_status === "draft" ? " · rascunho" : ""}</dd></div>}
        </dl> : <p className="footnote">{item.blockers.length ? `Não pode ir para o site: ${item.blockers.join(" ")}` : "Este artigo não está na lista de publicação desta loja."}</p>}
        {pub?.error && <Notice tone="error">{pub.error}</Notice>}
        {manage && pub?.product_missing && <ActionButton type="button" variant="primary" disabled={!!busy}
          confirmation={{ title: "Criar um produto novo na loja?", message: `O produto #${pub.woo_product_id} foi apagado na loja. Na próxima sincronização, a loja cria um produto novo em rascunho. A informação alimentar e as edições do produto apagado não são recuperadas.`, confirmLabel: "Criar produto novo", tone: "warning" }}
          onClick={() => void run(item.connection_id, () => api(`/webshop/publications/${pub.id}/recreate`, { revision: pub.revision }), "Produto novo pedido: a loja cria-o em rascunho na próxima sincronização.")}>
          Criar produto novo na loja</ActionButton>}
        {pub?.status === "blocked" && <Notice tone="warning">{pub.blockers.join(" ")}</Notice>}
        {pub?.food_missing?.length > 0 && <p className="footnote">Informação alimentar em falta na loja: {pub.food_missing.join(", ")}.</p>}
        {pub?.preserved?.length > 0 && <div className="article-webshop-preserved">
          <p className="footnote">A loja manteve o que foi editado lá: {pub.preserved.map((key: string) => PRESERVED_LABELS[key] || key).join(", ")}.{pub.override_pending ? " Texto da IAZELA pedido: aplicado na próxima sincronização." : ""}</p>
          {manage && !pub.override_pending && <ActionButton type="button" disabled={!!busy}
            confirmation={{ title: "Usar o texto da IAZELA na loja?", message: "Na próxima sincronização, a loja substitui o que tinha preservado pelo conteúdo atual da IAZELA. As alterações feitas na loja a esses campos perdem-se.", confirmLabel: "Usar o texto da IAZELA", tone: "warning" }}
            onClick={() => void run(item.connection_id, () => api(`/webshop/publications/${pub.id}/override`, { revision: pub.revision }), "Pedido enviado: a loja usa o texto da IAZELA na próxima sincronização.")}>
            Usar o texto da IAZELA</ActionButton>}
        </div>}
        {manage && <div className="article-webshop-actions">
          {(!pub || pub.state === "withdrawn") && <ActionButton type="button" variant="primary" disabled={!!busy || (!pub && item.blockers.length > 0)} onClick={() => void mark()}>
            {busy === item.connection_id ? "A marcar…" : pub ? "Voltar a publicar no site" : "Marcar para o site"}</ActionButton>}
          {ean === item.connection_id && !pub && <ActionButton type="button" disabled={!!busy} confirmation={{ title: "Criar um produto novo na loja?", message: "Já existe na loja um produto com o mesmo EAN. O recomendado é associá-lo em Integrações → Publicação online. Criar outro pode duplicar o produto.", confirmLabel: "Criar produto novo", tone: "warning" }} onClick={() => void mark(true)}>Criar produto novo mesmo assim</ActionButton>}
          {pub?.state === "published" && <ActionButton type="button" disabled={!!busy} confirmation={WITHDRAW_CONFIRMATION}
            onClick={() => void run(item.connection_id, () => withdrawFromShop(pub), "Artigo retirado do site na próxima sincronização.")}>Retirar do site</ActionButton>}
          {pub && <ActionButton type="button" variant="quiet" disabled={!!busy} onClick={() => void run(item.connection_id, () => api(`/webshop/publications/${pub.id}/resync`, { revision: pub.revision }), "O conteúdo atual volta a ser enviado na próxima sincronização.")}>Enviar de novo</ActionButton>}
        </div>}
      </div>;
    })}
    {error && <Notice tone="error">{error}</Notice>}
    {notice && <p className="footnote" role="status">{notice}</p>}
    <p className="footnote">SKU, preço do canal, associações e histórico em Integrações → Publicação online.</p>
  </RecordGroup>;
}
