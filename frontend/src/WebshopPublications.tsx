import { useCallback, useEffect, useState } from "react";
import { History, Pencil, RefreshCw, Search, Upload, XCircle } from "lucide-react";
import { api } from "./lib/api";
import { ActionButton, ManagedForm, Notice, confirmAction } from "./components/InteractionUI";
import { MetricCard, StatusBadge, type BusinessTone } from "./components/BusinessUI";
import { DataTable } from "./components/DataTable";
import "./WebshopPublications.css";

type Connection = { id: string; name: string; active: boolean };
type Publication = {
  id: string;
  article_id: string;
  code: string;
  erp_code: string;
  name: string;
  sku: string;
  barcodes: string[];
  gtin: string;
  base_price_net: string | null;
  channel_price_net: string | null;
  price_net: string | null;
  price_gross: string | null;
  vat_rate: string | null;
  average_cost: string | null;
  margin_percent: string | null;
  state: "published" | "withdrawn";
  shop_state: "published" | "withdrawn";
  status: "synced" | "pending" | "error" | "blocked";
  status_label: string;
  blockers: string[];
  woo_product_id: number | null;
  woo_status: string;
  synced_at: string | null;
  retry_at: string | null;
  attempts: number;
  error: string;
  product_missing: boolean;
  food_missing: string[];
  preserved: string[];
  override_pending: boolean;
  revision: number;
};
type Listing = { cost_visible: boolean; counts: Record<string, number>; items: Publication[] };
type Candidate = { article_id: string; code: string; erp_code: string; name: string; barcodes: string[]; price_net: string | null; blockers: string[] };
type Suggestion = {
  woo_product_id: number;
  sku: string;
  gtin: string;
  name: string;
  woo_status: string;
  status: "suggested" | "ambiguous" | "conflict" | "none";
  note: string;
  candidates: { article_id: string; code: string; name: string; by: string[]; already_published: boolean }[];
};
type HistoryItem = { action: string; at: string; user: string; data: Record<string, unknown> };

const euro = (value: string | null) =>
  value === null ? "—" : new Intl.NumberFormat("pt-PT", { style: "currency", currency: "EUR" }).format(Number(value));
const when = (value: string | null) =>
  value ? new Date(value).toLocaleString("pt-PT", { timeZone: "Europe/Lisbon", dateStyle: "short", timeStyle: "short" }) : "—";
const percent = (value: string | null) =>
  value === null ? "—" : `${new Intl.NumberFormat("pt-PT", { maximumFractionDigits: 2 }).format(Number(value))} %`;
/** Endereços longos nos erros: domínio e nome do ficheiro (o texto completo fica na dica). */
const shortUrls = (text: string) =>
  text.replace(/https?:\/\/\S+?(?=[.,;]?(\s|$))/g, (url) => {
    try {
      const parsed = new URL(url);
      const file = parsed.pathname.split("/").filter(Boolean).pop();
      return file ? `${parsed.hostname}/…/${file}` : parsed.hostname;
    } catch {
      return url;
    }
  });
const PRESERVED: Record<string, string> = { description: "descrição", short_description: "resumo", images: "imagens", price: "preço",
  seo_keyword: "palavra-chave SEO", seo_title: "título SEO", seo_description: "meta-descrição SEO", label: "rótulo" };
const STATUS_TONE: Record<Publication["status"], BusinessTone> = {
  synced: "success",
  pending: "info",
  error: "danger",
  blocked: "warning",
};
const ACTIONS: Record<string, string> = {
  published: "Publicado",
  withdrawn: "Retirado da loja",
  resync: "Atualização pedida",
  edited: "SKU/preço alterado",
  associated: "Associado a produto da loja",
  linked: "Associação reconhecida",
  imported: "Importado da loja",
  synced: "Sincronizado pela loja",
  sync_error: "Erro na loja",
  recreated: "Produto novo pedido (o anterior foi apagado na loja)",
  override: "Texto da IAZELA pedido (substitui o preservado na loja)",
};
const SUGGESTION_TONE: Record<Suggestion["status"], BusinessTone> = {
  suggested: "suggestion",
  ambiguous: "warning",
  conflict: "danger",
  none: "neutral",
};

export default function WebshopPublications() {
  const [connections, setConnections] = useState<Connection[]>([]);
  const [connection, setConnection] = useState("");
  const [data, setData] = useState<Listing | null>(null);
  const [suggestions, setSuggestions] = useState<Suggestion[]>([]);
  const [query, setQuery] = useState("");
  const [found, setFound] = useState<Candidate[] | null>(null);
  const [editing, setEditing] = useState<{ row: Publication; sku: string; price: string } | null>(null);
  const [history, setHistory] = useState<{ row: Publication; items: HistoryItem[] } | null>(null);
  const [error, setError] = useState("");
  const [message, setMessage] = useState("");
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    api("/webshop/connections")
      .then((rows: Connection[]) => {
        const active = rows.filter((r) => r.active);
        setConnections(active);
        if (active.length) setConnection(active[0].id);
      })
      .catch((e) => setError((e as Error).message));
  }, []);

  const load = useCallback(async () => {
    if (!connection) return;
    const params = `connection_id=${encodeURIComponent(connection)}`;
    const [listing, matches] = await Promise.all([
      api(`/webshop/publications?${params}`),
      api(`/webshop/publications-suggestions?${params}`),
    ]);
    setData(listing);
    setSuggestions(matches);
  }, [connection]);

  useEffect(() => {
    setData(null);
    load().catch((e) => setError((e as Error).message));
  }, [load]);

  async function run(work: () => Promise<unknown>, done: string) {
    setBusy(true);
    setError("");
    setMessage("");
    try {
      await work();
      setMessage(done);
      await load();
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  }

  async function search() {
    setError("");
    try {
      setFound(await api(`/webshop/publications/articles?connection_id=${encodeURIComponent(connection)}&q=${encodeURIComponent(query)}`));
    } catch (e) {
      setError((e as Error).message);
    }
  }

  async function publishNew(candidate: Candidate) {
    const body = { connection_id: connection, article_id: candidate.article_id };
    await run(async () => {
      try {
        await api("/webshop/publications", body);
      } catch (e) {
        const text = (e as Error).message;
        if (!text.includes("mesmo EAN")) throw e;
        const proceed = await confirmAction({
          title: "Criar um produto novo na loja?",
          message: `${text} Associar é o recomendado para não duplicar o produto.`,
          confirmLabel: "Criar produto novo",
          tone: "danger",
        });
        if (!proceed) throw new Error("Publicação cancelada: associe o produto existente nas sugestões.");
        await api("/webshop/publications", { ...body, allow_new: true });
      }
      setFound((rows) => rows?.filter((r) => r.article_id !== candidate.article_id) ?? null);
    }, `«${candidate.name}» marcado para publicação. A loja recebe-o na próxima sincronização.`);
  }

  async function act(row: Publication, action: "publish" | "withdraw" | "resync" | "recreate" | "override") {
    if (action === "override") {
      const proceed = await confirmAction({
        title: "Usar o texto da IAZELA na loja?",
        message: `Na próxima sincronização, a loja substitui o que tinha preservado em «${row.name}» (${row.preserved.map((key) => PRESERVED[key] || key).join(", ")}) pelo conteúdo atual da IAZELA. As alterações feitas na loja a esses campos perdem-se.`,
        confirmLabel: "Usar o texto da IAZELA",
        tone: "warning",
      });
      if (!proceed) return;
    }
    if (action === "recreate") {
      const proceed = await confirmAction({
        title: "Criar um produto novo na loja?",
        message: `O produto #${row.woo_product_id} foi apagado na loja. Na próxima sincronização, a loja cria um produto novo em rascunho para «${row.name}». A informação alimentar e as edições do produto apagado não são recuperadas: reveja-o na loja antes de o pôr à venda.`,
        confirmLabel: "Criar produto novo",
        tone: "warning",
      });
      if (!proceed) return;
    }
    if (action === "withdraw") {
      const proceed = await confirmAction({
        title: "Retirar da loja?",
        message: `«${row.name}» fica em rascunho na loja, sem stock à venda. O produto, as encomendas e o histórico mantêm-se.`,
        confirmLabel: "Retirar da loja",
        tone: "danger",
      });
      if (!proceed) return;
    }
    const labels = { publish: "Publicação pedida.", withdraw: "Retirada pedida.", resync: "Nova sincronização pedida.",
      recreate: "Produto novo pedido: a loja cria-o em rascunho na próxima sincronização.",
      override: "Pedido enviado: a loja usa o texto da IAZELA na próxima sincronização." };
    await run(() => api(`/webshop/publications/${row.id}/${action}`, { revision: row.revision }), labels[action]);
  }

  async function saveEdit() {
    if (!editing) return;
    const { row, sku, price } = editing;
    await run(
      () => api(`/webshop/publications/${row.id}`, { revision: row.revision, sku, price_net: price.trim() ? price.trim().replace(",", ".") : null }, { method: "PUT" }),
      "SKU e preço do canal guardados.",
    );
    setEditing(null);
  }

  async function associate(item: Suggestion, articleId: string) {
    await run(
      () => api("/webshop/publications/associate", { connection_id: connection, article_id: articleId, woo_product_id: item.woo_product_id }),
      `Produto da loja «${item.name}» associado.`,
    );
  }

  async function showHistory(row: Publication) {
    try {
      setHistory({ row, items: await api(`/webshop/publications/${row.id}/history`) });
    } catch (e) {
      setError((e as Error).message);
    }
  }

  if (!connections.length)
    return (
      <section className="panel">
        <h3>Publicação online</h3>
        {error ? <Notice tone="error">{error}</Notice> : <p>Crie primeiro uma ligação Raizpura em Conectores.</p>}
      </section>
    );

  const counts = data?.counts ?? {};
  return (
    <section className="panel webshop-publications">
      <h3>Publicação online</h3>
      <p>
        Escolha os artigos que vão para a loja online, o SKU e o preço do canal. A loja recebe só o que mudou e
        devolve o resultado. A informação alimentar (rótulo, ingredientes, dose e revisão) é preenchida e verificada
        na loja: um produto sem ela fica em rascunho e o que falta aparece aqui.
      </p>
      {connections.length > 1 && (
        <label>
          Ligação
          <select value={connection} onChange={(e) => setConnection(e.target.value)}>
            {connections.map((c) => (
              <option key={c.id} value={c.id}>
                {c.name}
              </option>
            ))}
          </select>
        </label>
      )}
      {error && <Notice tone="error" onDismiss={() => setError("")}>{error}</Notice>}
      {message && <Notice tone="success" onDismiss={() => setMessage("")}>{message}</Notice>}
      <div className="cash-grid">
        <MetricCard label="Sincronizados" value={counts.synced ?? 0} description="Conteúdo confirmado pela loja" tone="success" />
        <MetricCard label="A aguardar a loja" value={counts.pending ?? 0} description="Enviados na próxima sincronização" tone="info" />
        <MetricCard label="Com erro" value={counts.error ?? 0} description="Nova tentativa agendada automaticamente" tone="danger" />
        <MetricCard label="Bloqueados" value={counts.blocked ?? 0} description="Retirados até corrigir o artigo" tone="warning" />
      </div>

      <h4>Adicionar artigos à loja</h4>
      <ManagedForm
        onSubmit={(e) => {
          e.preventDefault();
          search();
        }}
      >
        <label>
          Artigo (código, nome ou EAN)
          <input value={query} maxLength={100} onChange={(e) => setQuery(e.target.value)} />
        </label>
        <ActionButton type="submit">
          <Search size={16} aria-hidden="true" /> Procurar
        </ActionButton>
      </ManagedForm>
      {found && (
        <DataTable<Candidate>
          label="artigos por publicar"
          rows={found}
          rowKey={(r) => r.article_id}
          searchText={(r) => `${r.code} ${r.erp_code} ${r.name} ${r.barcodes.join(" ")}`}
          emptyText="Nenhum artigo por publicar com esta pesquisa."
          columns={[
            { id: "article", label: "Artigo", render: (r) => (<>{r.name}<small>{r.code}{r.erp_code && r.erp_code !== r.code ? ` · Wintouch ${r.erp_code}` : ""}</small></>) },
            { id: "ean", label: "EAN", render: (r) => r.barcodes.join(", ") || "—" },
            { id: "price", label: "Preço base s/ IVA", numeric: true, render: (r) => euro(r.price_net) },
            {
              id: "action",
              label: "Ação",
              render: (r) =>
                r.blockers.length ? (
                  <small>{r.blockers.join(" ")}</small>
                ) : (
                  <ActionButton className="primary" disabled={busy} onClick={() => publishNew(r)}>
                    <Upload size={16} aria-hidden="true" /> Publicar
                  </ActionButton>
                ),
            },
          ]}
        />
      )}

      {editing && (
        <ManagedForm
          className="panel"
          onSubmit={(e) => {
            e.preventDefault();
            saveEdit();
          }}
        >
          <h4>SKU e preço do canal · {editing.row.name}</h4>
          <label>
            SKU na loja
            <input required maxLength={100} value={editing.sku} onChange={(e) => setEditing({ ...editing, sku: e.target.value })} />
          </label>
          <label>
            Preço do canal sem IVA (vazio = preço base {euro(editing.row.base_price_net)})
            <input inputMode="decimal" value={editing.price} onChange={(e) => setEditing({ ...editing, price: e.target.value })} />
          </label>
          <div className="actions">
            <ActionButton type="submit" className="primary" disabled={busy}>
              Guardar
            </ActionButton>
            <ActionButton onClick={() => setEditing(null)}>Cancelar</ActionButton>
          </div>
        </ManagedForm>
      )}

      {history && (
        <section className="panel">
          <h4>Histórico de publicação · {history.row.name}</h4>
          <ul>
            {history.items.map((h, i) => (
              <li key={i}>
                <strong>{when(h.at)}</strong> · {ACTIONS[h.action] || h.action} · {h.user}
                {typeof h.data.error === "string" && h.data.error ? ` · ${h.data.error}` : ""}
              </li>
            ))}
          </ul>
          <ActionButton onClick={() => setHistory(null)}>Fechar histórico</ActionButton>
        </section>
      )}

      <h4>Artigos na loja</h4>
      <DataTable<Publication>
        label="publicações na loja online"
        rows={data?.items ?? []}
        loading={!data}
        rowKey={(r) => r.id}
        searchText={(r) => `${r.code} ${r.erp_code} ${r.name} ${r.sku} ${r.barcodes.join(" ")}`}
        exportName="publicacao-online"
        emptyText="Ainda não há artigos publicados nesta loja."
        columns={[
          {
            id: "article",
            label: "Artigo",
            className: "wp-col-article",
            sortValue: (r) => r.name,
            exportValue: (r) => r.name,
            render: (r) => (
              <>
                <strong className="wp-name">{r.name}</strong>
                <small>
                  SKU {r.sku}
                  {r.erp_code && r.erp_code !== r.sku ? ` · Wintouch ${r.erp_code}` : ""}
                  {r.code !== r.sku && r.code !== r.erp_code ? ` · IAZELA ${r.code}` : ""}
                  {` · ${r.gtin || r.barcodes[0] || "sem EAN"}`}
                </small>
                <small>{r.woo_product_id ? `Produto na loja #${r.woo_product_id}` : "Ainda sem produto na loja"}</small>
              </>
            ),
          },
          {
            id: "price",
            label: "Preço",
            numeric: true,
            className: "wp-col-price",
            sortValue: (r) => Number(r.price_net ?? 0),
            exportValue: (r) => r.price_net,
            render: (r) => (
              <>
                <strong>{euro(r.price_gross)}</strong>
                <small>{euro(r.price_net)} s/ IVA · IVA {percent(r.vat_rate)}</small>
                {r.channel_price_net && <small>Preço do canal</small>}
              </>
            ),
          },
          {
            id: "margin",
            label: "Margem",
            numeric: true,
            className: "wp-col-margin",
            sortValue: (r) => Number(r.margin_percent ?? -999),
            exportValue: (r) => r.margin_percent,
            render: (r) =>
              !data?.cost_visible ? (
                <small>Sem acesso</small>
              ) : r.margin_percent === null ? (
                <><span aria-hidden="true">—</span><small>Custo desconhecido</small></>
              ) : (
                <>
                  <strong>{percent(r.margin_percent)}</strong>
                  <small>custo {euro(r.average_cost)}</small>
                </>
              ),
          },
          {
            id: "state",
            label: "Estado",
            className: "wp-col-state",
            sortValue: (r) => r.status,
            exportValue: (r) => [r.sku, r.gtin, r.state === "published" ? "Publicado" : "Retirado", r.status_label, r.synced_at, ...r.blockers, r.error]
              .filter(Boolean).join(" · "),
            render: (r) => (
              <div className="wp-state">
                <div className="wp-badges">
                  <StatusBadge compact tone={r.state === "published" ? "success" : "neutral"}>
                    {r.state === "published" ? "Publicado" : "Retirado"}
                  </StatusBadge>
                  <StatusBadge compact tone={STATUS_TONE[r.status]}>{r.status_label}</StatusBadge>
                  {r.state === "published" && r.woo_status && r.woo_status !== "publish" && (
                    <StatusBadge compact tone="warning">Na loja: {r.woo_status === "draft" ? "rascunho" : r.woo_status}</StatusBadge>
                  )}
                </div>
                <small>{r.synced_at ? `Sincronizado a ${when(r.synced_at)}` : "Ainda sem confirmação da loja"}</small>
                {(r.blockers.length > 0 || r.error) && (
                  <p className={`wp-error tone-${r.status === "blocked" ? "warning" : "danger"}`} title={[...r.blockers, r.error].join(" ")}>
                    {shortUrls([...r.blockers, r.error].filter(Boolean).join(" "))}
                    {r.retry_at && <small>Nova tentativa a {when(r.retry_at)} · {r.attempts}.ª falha</small>}
                    {r.product_missing && (
                      <ActionButton variant="primary" className="wp-fix" disabled={busy} onClick={() => act(r, "recreate")}>
                        Criar produto novo na loja
                      </ActionButton>
                    )}
                  </p>
                )}
                {r.food_missing.length > 0 && (
                  <details className="wp-more">
                    <summary>Informação alimentar em falta ({r.food_missing.length})</summary>
                    <ul>{r.food_missing.map((item) => <li key={item}>{item}</li>)}</ul>
                  </details>
                )}
                {r.preserved.length > 0 && (
                  <div className="wp-preserved">
                    <small>Editado na loja e preservado: {r.preserved.map((key) => PRESERVED[key] || key).join(", ")}</small>
                    {r.override_pending ? <small>Texto da IAZELA pedido: aplicado na próxima sincronização.</small> : (
                      <ActionButton variant="quiet" disabled={busy} onClick={() => act(r, "override")}>Usar o texto da IAZELA</ActionButton>
                    )}
                  </div>
                )}
              </div>
            ),
          },
          {
            id: "actions",
            label: "Ações",
            className: "wp-col-actions",
            render: (r) => (
              <div className="wp-actions" role="group" aria-label={`Ações de ${r.name}`}>
                {r.state === "published" ? (
                  <ActionButton variant="quiet" disabled={busy} aria-label="Atualizar" title="Atualizar: enviar de novo o conteúdo atual" onClick={() => act(r, "resync")}>
                    <RefreshCw size={16} aria-hidden="true" />
                  </ActionButton>
                ) : (
                  <ActionButton variant="primary" disabled={busy} onClick={() => act(r, "publish")}>
                    <Upload size={16} aria-hidden="true" /> Publicar
                  </ActionButton>
                )}
                <ActionButton variant="quiet" disabled={busy} aria-label="SKU e preço" title="SKU e preço do canal" onClick={() => setEditing({ row: r, sku: r.sku, price: r.channel_price_net ?? "" })}>
                  <Pencil size={16} aria-hidden="true" />
                </ActionButton>
                <ActionButton variant="quiet" aria-label="Histórico" title="Histórico da publicação" onClick={() => showHistory(r)}>
                  <History size={16} aria-hidden="true" />
                </ActionButton>
                {r.state === "published" && (
                  <ActionButton variant="quiet" className="wp-danger" disabled={busy} aria-label="Retirar da loja" title="Retirar da loja" onClick={() => act(r, "withdraw")}>
                    <XCircle size={16} aria-hidden="true" />
                  </ActionButton>
                )}
              </div>
            ),
          },
        ]}
      />

      {suggestions.length > 0 && (
        <>
          <h4>Produtos da loja sem associação</h4>
          <p>
            Sugestões pelo mesmo SKU ou EAN. Nada é associado sem confirmação; com várias correspondências, escolha o
            artigo certo ou corrija os códigos.
          </p>
          <DataTable<Suggestion>
            label="produtos da loja sem associação"
            rows={suggestions}
            rowKey={(r) => String(r.woo_product_id)}
            searchText={(r) => `${r.name} ${r.sku} ${r.gtin}`}
            columns={[
              { id: "remote", label: "Produto na loja", render: (r) => (<>{r.name || "Sem nome"}<small>#{r.woo_product_id} · {r.woo_status || "—"}</small></>) },
              { id: "codes", label: "SKU / EAN", render: (r) => (<>{r.sku || "—"}<small>{r.gtin || "Sem EAN"}</small></>) },
              { id: "status", label: "Correspondência", render: (r) => (<><StatusBadge tone={SUGGESTION_TONE[r.status]}>{r.note}</StatusBadge></>) },
              {
                id: "candidates",
                label: "Artigos IAZELA",
                render: (r) =>
                  r.candidates.length ? (
                    <div className="actions">
                      {r.candidates.map((c) => (
                        <ActionButton
                          key={c.article_id}
                          disabled={busy || c.already_published}
                          title={c.already_published ? "Este artigo já está publicado noutro produto da loja." : undefined}
                          onClick={() => associate(r, c.article_id)}
                        >
                          Associar a {c.code} · {c.name} ({c.by.join(" e ")})
                        </ActionButton>
                      ))}
                    </div>
                  ) : (
                    "—"
                  ),
              },
            ]}
          />
        </>
      )}
    </section>
  );
}
