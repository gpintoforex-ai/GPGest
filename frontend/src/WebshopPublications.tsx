import { useCallback, useEffect, useState } from "react";
import { History, RefreshCw, Search, Upload, XCircle } from "lucide-react";
import { api } from "./lib/api";
import { ActionButton, ManagedForm, Notice, confirmAction } from "./components/InteractionUI";
import { MetricCard, StatusBadge, type BusinessTone } from "./components/BusinessUI";
import { DataTable } from "./components/DataTable";

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
  food_missing: string[];
  preserved: string[];
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

  async function act(row: Publication, action: "publish" | "withdraw" | "resync") {
    if (action === "withdraw") {
      const proceed = await confirmAction({
        title: "Retirar da loja?",
        message: `«${row.name}» fica em rascunho na loja, sem stock à venda. O produto, as encomendas e o histórico mantêm-se.`,
        confirmLabel: "Retirar da loja",
        tone: "danger",
      });
      if (!proceed) return;
    }
    const labels = { publish: "Publicação pedida.", withdraw: "Retirada pedida.", resync: "Nova sincronização pedida." };
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
            sortValue: (r) => r.name,
            exportValue: (r) => r.name,
            render: (r) => (
              <>
                {r.name}
                <small>
                  IAZELA {r.code}
                  {r.erp_code ? ` · Wintouch ${r.erp_code}` : ""}
                  {r.woo_product_id ? ` · WooCommerce #${r.woo_product_id}` : " · ainda sem produto na loja"}
                </small>
              </>
            ),
          },
          {
            id: "sku",
            label: "SKU / EAN",
            exportValue: (r) => `${r.sku} ${r.gtin}`,
            render: (r) => (
              <>
                {r.sku}
                <small>{r.gtin || r.barcodes[0] || "Sem EAN"}</small>
              </>
            ),
          },
          {
            id: "price",
            label: "Preço",
            numeric: true,
            sortValue: (r) => Number(r.price_net ?? 0),
            exportValue: (r) => r.price_net,
            render: (r) => (
              <>
                {euro(r.price_gross)}
                <small>
                  {euro(r.price_net)} s/ IVA · IVA {r.vat_rate ?? "—"}%{r.channel_price_net ? " · preço do canal" : ""}
                </small>
              </>
            ),
          },
          {
            id: "margin",
            label: "Margem",
            numeric: true,
            sortValue: (r) => Number(r.margin_percent ?? -999),
            exportValue: (r) => r.margin_percent,
            render: (r) =>
              !data?.cost_visible ? (
                <small>Sem acesso a custos</small>
              ) : r.margin_percent === null ? (
                <small>Custo desconhecido</small>
              ) : (
                <>
                  {r.margin_percent}%<small>custo médio {euro(r.average_cost)}</small>
                </>
              ),
          },
          {
            id: "state",
            label: "Estado de publicação",
            sortValue: (r) => r.status,
            exportValue: (r) => `${r.state === "published" ? "Publicado" : "Retirado"} · ${r.status_label}`,
            render: (r) => (
              <>
                <StatusBadge tone={r.state === "published" ? "success" : "neutral"}>
                  {r.state === "published" ? "Publicado" : "Retirado"}
                </StatusBadge>{" "}
                <StatusBadge tone={STATUS_TONE[r.status]}>{r.status_label}</StatusBadge>
                {r.state === "published" && r.woo_status && r.woo_status !== "publish" && (
                  <small>Na loja: {r.woo_status === "draft" ? "rascunho" : r.woo_status}</small>
                )}
                {r.food_missing.length > 0 && <small>Informação alimentar em falta: {r.food_missing.join("; ")}</small>}
                {r.preserved.length > 0 && <small>Conteúdo editado na loja preservado: {r.preserved.join(", ")}</small>}
              </>
            ),
          },
          {
            id: "synced",
            label: "Última sincronização",
            sortValue: (r) => r.synced_at ?? "",
            exportValue: (r) => r.synced_at,
            render: (r) => when(r.synced_at),
          },
          {
            id: "errors",
            label: "Erros",
            exportValue: (r) => [...r.blockers, r.error].filter(Boolean).join(" "),
            render: (r) =>
              r.blockers.length || r.error ? (
                <>
                  {r.blockers.join(" ")} {r.error}
                  {r.retry_at && <small>Nova tentativa: {when(r.retry_at)} ({r.attempts}.ª falha)</small>}
                </>
              ) : (
                "—"
              ),
          },
          {
            id: "actions",
            label: "Ações",
            render: (r) => (
              <div className="actions">
                {r.state === "published" ? (
                  <>
                    <ActionButton disabled={busy} onClick={() => act(r, "resync")}>
                      <RefreshCw size={16} aria-hidden="true" /> Atualizar
                    </ActionButton>
                    <ActionButton className="danger" disabled={busy} onClick={() => act(r, "withdraw")}>
                      <XCircle size={16} aria-hidden="true" /> Retirar da loja
                    </ActionButton>
                  </>
                ) : (
                  <ActionButton className="primary" disabled={busy} onClick={() => act(r, "publish")}>
                    <Upload size={16} aria-hidden="true" /> Publicar
                  </ActionButton>
                )}
                <ActionButton disabled={busy} onClick={() => setEditing({ row: r, sku: r.sku, price: r.channel_price_net ?? "" })}>
                  SKU e preço
                </ActionButton>
                <ActionButton onClick={() => showHistory(r)}>
                  <History size={16} aria-hidden="true" /> Histórico
                </ActionButton>
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
