import { useEffect, useRef, useState } from "react";
import { api } from "./lib/api";
import { Field } from "./components/Form";
import {
  ActionButton,
  ActionLink,
  AppDialog,
  ManagedForm,
  Notice,
  confirmAction,
} from "./components/InteractionUI";
import { DataTable } from "./components/DataTable";
import { DetailDrawer } from "./components/WorkspaceUI";
import "./OrderCycle.css";

type Row = Record<string, any>;
const money = (n: number) =>
  new Intl.NumberFormat("pt-PT", { style: "currency", currency: "EUR" }).format(
    (n || 0) / 100,
  );
const quantity = (n: unknown) =>
  new Intl.NumberFormat("pt-PT", { maximumFractionDigits: 6 }).format(
    Number(n || 0),
  );
const states: Row = {
  draft: "Por conferir",
  reviewed: "Dados revistos",
  approved: "Conferência aprovada",
  proposed: "Proposta ERP pendente",
  posted_local: "Compra local lançada",
  posted_erp: "Compra confirmada no Wintouch",
  pending: "Pendente",
  confirmed: "Cloud confirmada",
  not_configured: "Cloud por configurar",
};

function useCycleAction() {
  const keys = useRef(new Map<string, string>());
  const [busy, setBusy] = useState(false),
    [error, setError] = useState("");
  async function mutate(
    path: string,
    body: Row,
    method: "POST" | "PUT" = "POST",
  ) {
    const signature = path + JSON.stringify(body);
    const request_key = keys.current.get(signature) || crypto.randomUUID();
    keys.current.set(signature, request_key);
    const result = await api(path, { ...body, request_key }, { method });
    keys.current.delete(signature);
    return result;
  }
  async function run(action: () => Promise<void>) {
    if (busy) return;
    setBusy(true);
    setError("");
    try {
      await action();
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  }
  return { mutate, run, busy, error, setError };
}

export function ERPCatalogPicker({
  onSelect,
  canAttach,
}: {
  onSelect: (r: Row) => void;
  canAttach: boolean;
}) {
  const [rows, setRows] = useState<Row[] | null>(null);
  const action = useCycleAction();
  async function choose(row: Row) {
    let fresh = await api("/stock/catalog");
    let article = fresh.articles.find(
      (a: Row) => a.origin === "ERP" && a.code === row.code,
    );
    if (!article) {
      const attached = await api("/stock/erp-products", { erp_id: row.id });
      fresh = await api("/stock/catalog");
      article = fresh.articles.find((a: Row) => a.id === attached.id);
    }
    if (!article)
      throw new Error("Não foi possível carregar a referência associada.");
    onSelect(article);
  }
  return (
    <details>
      <summary>Pesquisar no catálogo ERP sincronizado</summary>
      {action.error && <Notice role="alert">{action.error}</Notice>}
      <ActionButton
        disabled={action.busy}
        onClick={() =>
          void action.run(async () =>
            setRows((await api("/stock/erp-products")).rows),
          )
        }
      >
        Consultar artigos Wintouch existentes
      </ActionButton>
      {rows && (
        <DataTable<Row>
          label="artigos ERP para encomenda"
          rows={rows}
          rowKey={(r) => r.id}
          searchText={(r) => `${r.code} ${r.name}`}
          columns={[
            {
              id: "article",
              label: "Artigo",
              render: (r) => (
                <>
                  {r.code} · {r.name}
                </>
              ),
            },
            {
              id: "actions",
              label: "Ação",
              render: (r) => (
                <ActionButton
                  disabled={action.busy || (!r.linked && !canAttach)}
                  onClick={() => void action.run(async () => choose(r))}
                >
                  Selecionar referência ERP
                </ActionButton>
              ),
            },
          ]}
        />
      )}
      <small>
        Reutiliza o catálogo já sincronizado; não cria artigos no ERP. A
        associação exige as permissões atuais de artigos.
      </small>
    </details>
  );
}

export function StockAvailability({
  storeId,
  articleIds,
}: {
  storeId: string;
  articleIds: string[];
}) {
  const [rows, setRows] = useState<Row[]>([]),
    [error, setError] = useState("");
  useEffect(() => {
    let alive = true;
    void api("/order-cycle/stock?store_id=" + encodeURIComponent(storeId))
      .then((r) => {
        if (alive) setRows(r.items);
      })
      .catch((e) => {
        if (alive) setError(e.message);
      });
    return () => {
      alive = false;
    };
  }, [storeId]);
  return (
    <details>
      <summary>Stock livre e compras em trânsito</summary>
      {error && <Notice role="alert">{error}</Notice>}
      {rows
        .filter((r) => articleIds.includes(r.article_id))
        .map((r) => (
          <p key={r.article_id}>
            <strong>
              {r.code} {r.name}
            </strong>
            <br />
            Stock livre: {quantity(r.free)} · Em trânsito:{" "}
            {quantity(r.incoming)} · Procura por satisfazer:{" "}
            {quantity(r.to_order)}
          </p>
        ))}
      <small>
        Stock físico local, depois das reservas de outros clientes. Quantidades
        ERP exigem um conector de stock disponível.
      </small>
    </details>
  );
}

export function ReplenishmentRecommendations({
  storeId,
  supplierId = "",
  onAdd,
}: {
  storeId: string;
  supplierId?: string;
  onAdd?: (row: Row) => void;
}) {
  const [rows, setRows] = useState<Row[]>([]),
    [selected, setSelected] = useState<Row | null>(null);
  const action = useCycleAction();
  async function load() {
    const result = await api(
      "/order-cycle/recommendations?store_id=" +
        encodeURIComponent(storeId) +
        "&supplier_id=" +
        encodeURIComponent(supplierId),
    );
    setRows(result.items);
  }
  useEffect(() => {
    if (storeId) void load().catch((e) => action.setError(e.message));
  }, [storeId, supplierId]);
  if (!storeId)
    return (
      <Notice>Escolha uma loja para calcular necessidades de compra.</Notice>
    );
  return (
    <section aria-label="Recomendações de reposição">
      <h3>Reposição recomendada</h3>
      <p>
        Propostas justificadas pela procura, mínimos, cobertura e validade. A
        seleção é sempre sua.
      </p>
      {action.error && <Notice role="alert">{action.error}</Notice>}
      <ActionButton
        disabled={action.busy}
        onClick={() => void action.run(load)}
      >
        Atualizar recomendações
      </ActionButton>
      <DataTable<Row>
        rows={rows}
        rowKey={(r) => r.article_id}
        label="recomendações de reposição"
        searchText={(r) => `${r.code} ${r.description}`}
        columns={[
          {
            id: "article",
            label: "Artigo",
            render: (r) => (
              <>
                <strong>{r.code}</strong>
                <p>{r.description}</p>
              </>
            ),
          },
          {
            id: "stock",
            label: "Disponibilidade",
            render: (r) => (
              <>
                Livre {quantity(r.free)} · Trânsito {quantity(r.incoming)}
                <br />
                Pedidos {quantity(r.customer_demand)}
              </>
            ),
          },
          {
            id: "quantity",
            label: "Proposta",
            render: (r) => (
              <>
                {quantity(r.quantity)} un. ·{" "}
                {Number(r.unit_cost)
                  ? money(r.capital_cents)
                  : "Custo por confirmar"}
                <br />
                <small>
                  Margem unitária estimada:{" "}
                  {r.margin == null
                    ? "sem custo"
                    : money(Number(r.margin) * 100)}
                </small>
              </>
            ),
          },
          {
            id: "why",
            label: "Justificação",
            render: (r) => (
              <details>
                <summary>Razão e riscos</summary>
                {r.reasons.map((v: string) => (
                  <p key={v}>{v}</p>
                ))}
                {r.warnings.map((v: string) => (
                  <p key={v} role="note">
                    {v}
                  </p>
                ))}
              </details>
            ),
          },
          {
            id: "actions",
            label: "Ações",
            render: (r) => (
              <div className="actions">
                {onAdd && (
                  <ActionButton
                    disabled={Number(r.quantity) <= 0 || !r.supplier_id}
                    onClick={() => onAdd(r)}
                  >
                    Selecionar para simulação
                  </ActionButton>
                )}
                <ActionButton
                  onClick={() => setSelected({ ...r, policy: { ...r.policy } })}
                >
                  Condições de reposição
                </ActionButton>
              </div>
            ),
          },
        ]}
      />
      {selected && (
        <AppDialog
          open
          aria-label="Condições de reposição"
          onRequestClose={() => setSelected(null)}
          busy={action.busy}
        >
          <h2>Condições de reposição · {selected.description}</h2>
          {action.error && <Notice role="alert">{action.error}</Notice>}
          <ManagedForm
            onSubmit={(e) => {
              e.preventDefault();
              void action.run(async () => {
                const {
                  request_key: _key,
                  revision: _revision,
                  ...policy
                } = selected.policy;
                await action.mutate("/order-cycle/replenishment", {
                  ...policy,
                  revision: selected.revision,
                  store_id: storeId,
                  article_id: selected.article_id,
                });
                await load();
                setSelected(null);
              });
            }}
          >
            <fieldset disabled={action.busy} className="order-cycle-grid">
              {[
                ["safety", "Stock de segurança", 0, 1000000000],
                ["coverage_days", "Dias de cobertura", 1, 365],
                ["history_days", "Dias de histórico", 7, 730],
                ["shelf_days", "Validade prevista (dias)", 1, 3650],
                ["seasonal_factor", "Fator sazonal", 0, 10],
                [
                  "budget_cents",
                  "Teto de capital (cêntimos)",
                  0,
                  1000000000000,
                ],
              ].map(([key, label, min, max]) => (
                <Field key={key} label={String(label)}>
                  <input
                    type="number"
                    min={min}
                    max={max}
                    step={
                      ["safety", "seasonal_factor"].includes(String(key))
                        ? "0.001"
                        : "1"
                    }
                    value={selected.policy[key] ?? ""}
                    onChange={(e) =>
                      setSelected({
                        ...selected,
                        policy: {
                          ...selected.policy,
                          [key]:
                            e.target.value === ""
                              ? null
                              : Number(e.target.value),
                        },
                      })
                    }
                    required={
                      !["shelf_days", "budget_cents"].includes(String(key))
                    }
                  />
                </Field>
              ))}
            </fieldset>
            <div className="actions">
              <ActionButton type="submit" disabled={action.busy}>
                Guardar condições
              </ActionButton>
              <ActionButton onClick={() => setSelected(null)}>
                Fechar
              </ActionButton>
            </div>
          </ManagedForm>
        </AppDialog>
      )}
    </section>
  );
}

function ReviewEditor({
  row,
  catalog,
  busy,
  save,
}: {
  row: Row;
  catalog: Row;
  busy: boolean;
  save: (value: Row) => Promise<void>;
}) {
  const articles = [...catalog.articles];
  for (const proposal of row.article_proposals || []) {
    if (
      proposal.article_id &&
      !articles.some((a: Row) => a.id === proposal.article_id)
    ) {
      articles.push({
        ...proposal.article,
        id: proposal.article_id,
        active: true,
      });
    }
  }
  const [draft, setDraft] = useState<Row>({
    ...row.review,
    lines:
      row.review.lines ||
      row.comparison.rows.map((r: Row) => ({
        article_id: r.article_id || "",
        reference: r.reference || "",
        description: r.description,
        quantity: r.quantity,
        price: r.price,
        discount: r.discount,
        vat: r.vat,
      })),
    note: row.review.note || "",
  });
  function edit(index: number, change: Row) {
    setDraft({
      ...draft,
      lines: draft.lines.map((r: Row, i: number) =>
        i === index ? { ...r, ...change } : r,
      ),
    });
  }
  return (
    <ManagedForm
      onSubmit={(e) => {
        e.preventDefault();
        void save(draft);
      }}
    >
      <fieldset disabled={busy}>
        <div className="order-cycle-grid">
          {[
            ["supplier_nif", "NIF do fornecedor", "text"],
            ["document", "Número do documento", "text"],
            ["document_date", "Data de emissão", "date"],
            ["due_date", "Vencimento", "date"],
            ["total_cents", "Total fiscal (cêntimos)", "number"],
            ["tax_cents", "IVA fiscal (cêntimos)", "number"],
          ].map(([key, label, type]) => (
            <Field key={key} label={label}>
              <input
                type={type}
                min={type === "number" ? 0 : undefined}
                required
                value={draft[key] ?? ""}
                onChange={(e) =>
                  setDraft({
                    ...draft,
                    [key]:
                      type === "number"
                        ? Number(e.target.value)
                        : e.target.value,
                  })
                }
              />
            </Field>
          ))}
          <Field label="Tipo fiscal">
            <select
              value={draft.document_type}
              onChange={(e) =>
                setDraft({ ...draft, document_type: e.target.value })
              }
            >
              {["FT", "FR", "FS", "ND"].map((v) => (
                <option key={v}>{v}</option>
              ))}
            </select>
          </Field>
          <Field label="Moeda">
            <input
              required
              maxLength={3}
              pattern="[A-Z]{3}"
              value={draft.currency}
              onChange={(e) =>
                setDraft({ ...draft, currency: e.target.value.toUpperCase() })
              }
            />
          </Field>
        </div>
        {draft.lines.map((r: Row, i: number) => (
          <details
            key={i}
            open={draft.lines.length === 1}
            className="order-cycle-line"
          >
            <summary>
              Linha {i + 1} · {r.description || "Artigo por identificar"}
            </summary>
            <div className="order-cycle-grid">
              <Field label={`Artigo da linha ${i + 1}`}>
                <select
                  value={r.article_id || ""}
                  onChange={(e) =>
                    edit(i, { article_id: e.target.value || null })
                  }
                >
                  <option value="">
                    Por identificar — propor artigo ou escolher existente
                  </option>
                  {articles
                    .filter((a: Row) => a.active)
                    .map((a: Row) => (
                      <option value={a.id} key={a.id}>
                        {a.code} · {a.name}
                      </option>
                    ))}
                </select>
              </Field>
              <Field label={`Referência do fornecedor ${i + 1}`}>
                <input
                  value={r.reference}
                  onChange={(e) => edit(i, { reference: e.target.value })}
                />
              </Field>
              <Field label={`Descrição faturada ${i + 1}`}>
                <input
                  required
                  value={r.description}
                  onChange={(e) => edit(i, { description: e.target.value })}
                />
              </Field>
              {[
                ["quantity", "Quantidade", "0.000001", "0.000001"],
                ["price", "Preço unitário", "0", "0.000001"],
                ["discount", "Desconto (%)", "0", "0.0001"],
                ["vat", "IVA (%)", "0", "0.001"],
              ].map(([key, label, min, step]) => (
                <Field key={key} label={`${label} da linha ${i + 1}`}>
                  <input
                    type="number"
                    required
                    min={min}
                    max={["vat", "discount"].includes(key) ? 100 : 1000000000}
                    step={step}
                    value={r[key]}
                    onChange={(e) => edit(i, { [key]: e.target.value })}
                  />
                </Field>
              ))}
            </div>
            <ActionButton
              onClick={() =>
                setDraft({
                  ...draft,
                  lines: draft.lines.filter((_: Row, n: number) => i !== n),
                })
              }
            >
              Remover linha {i + 1}
            </ActionButton>
          </details>
        ))}
        <div className="actions">
          <ActionButton
            onClick={() =>
              setDraft({
                ...draft,
                lines: [
                  ...draft.lines,
                  {
                    article_id: null,
                    reference: "",
                    description: "",
                    quantity: "1",
                    price: "0",
                    discount: "0",
                    vat: "23",
                  },
                ],
              })
            }
          >
            Adicionar linha faturada
          </ActionButton>
        </div>
        <Field label="Observações da conferência">
          <textarea
            value={draft.note}
            onChange={(e) => setDraft({ ...draft, note: e.target.value })}
          />
        </Field>
        <ActionButton type="submit" disabled={!draft.lines.length}>
          Guardar dados conferidos
        </ActionButton>
      </fieldset>
    </ManagedForm>
  );
}

function ArticleProposalForm({
  row,
  action,
  done,
}: {
  row: Row;
  action: ReturnType<typeof useCycleAction>;
  done: (value: Row) => void;
}) {
  const [draft, setDraft] = useState<Row>({
    line_index: 0,
    create_local: false,
    article: {
      code: "",
      name: row.comparison.rows[0]?.description || "",
      category: "",
      unit: "un",
      vat_rate: "23",
      supplier_id: row.supplier_id,
      supplier_reference: row.comparison.rows[0]?.reference || "",
      barcodes: [],
      sale_net: "0",
    },
  });
  return (
    <details>
      <summary>Propor criação de artigo desconhecido</summary>
      <p>
        A referência do fornecedor não é usada automaticamente como código
        interno. Uma proposta ERP fica pendente até existir integração de
        escrita validada. A criação local exige modo local e permissões de
        artigos.
      </p>
      <ManagedForm
        onSubmit={(e) => {
          e.preventDefault();
          void action.run(async () => {
            const result = await action.mutate(
              `/order-cycle/reviews/${row.id}/article-proposal`,
              { ...draft, revision: row.revision },
            );
            done(result);
          });
        }}
      >
        <fieldset disabled={action.busy}>
          <Field label="Linha extraída do original">
            <select
              value={draft.line_index}
              onChange={(e) => {
                const i = Number(e.target.value),
                  line = row.comparison.rows[i];
                setDraft({
                  ...draft,
                  line_index: i,
                  article: {
                    ...draft.article,
                    name: line.description,
                    supplier_reference: line.reference || "",
                  },
                });
              }}
            >
              {row.comparison.rows.map((line: Row, i: number) => (
                <option key={i} value={i}>
                  {i + 1} · {line.description}
                </option>
              ))}
            </select>
          </Field>
          <div className="order-cycle-grid">
            {[
              ["code", "Código interno proposto"],
              ["name", "Nome do artigo"],
              ["category", "Categoria"],
              ["unit", "Unidade"],
              ["supplier_reference", "Referência do fornecedor"],
            ].map(([key, label]) => (
              <Field key={key} label={label}>
                <input
                  required={key !== "supplier_reference"}
                  value={draft.article[key]}
                  onChange={(e) =>
                    setDraft({
                      ...draft,
                      article: { ...draft.article, [key]: e.target.value },
                    })
                  }
                />
              </Field>
            ))}
            <Field label="EAN do artigo proposto">
              <input
                value={draft.article.barcodes[0] || ""}
                onChange={(e) =>
                  setDraft({
                    ...draft,
                    article: {
                      ...draft.article,
                      barcodes: e.target.value ? [e.target.value] : [],
                    },
                  })
                }
              />
            </Field>
            <Field label="IVA do artigo proposto">
              <input
                required
                type="number"
                min="0"
                max="100"
                step="0.001"
                value={draft.article.vat_rate}
                onChange={(e) =>
                  setDraft({
                    ...draft,
                    article: { ...draft.article, vat_rate: e.target.value },
                  })
                }
              />
            </Field>
            <Field label="Preço de venda estimado sem IVA">
              <input
                required
                type="number"
                min="0"
                step="0.000001"
                value={draft.article.sale_net}
                onChange={(e) =>
                  setDraft({
                    ...draft,
                    article: { ...draft.article, sale_net: e.target.value },
                  })
                }
              />
            </Field>
          </div>
          <label>
            <input
              type="checkbox"
              checked={draft.create_local}
              onChange={(e) =>
                setDraft({ ...draft, create_local: e.target.checked })
              }
            />{" "}
            Confirmar criação de artigo local (apenas em modo local)
          </label>
          <div className="actions">
            <ActionButton type="submit" disabled={!row.comparison.rows.length}>
              Guardar proposta de artigo
            </ActionButton>
          </div>
        </fieldset>
      </ManagedForm>
      {row.article_proposals?.map((p: Row, i: number) => (
        <p key={i}>
          {p.article.code} · {p.article.name} ·{" "}
          {p.state === "created_local"
            ? "Artigo local criado"
            : p.state === "confirmed_erp"
              ? "Artigo confirmado no Wintouch"
              : "Proposta ERP pendente"}
        </p>
      ))}
    </details>
  );
}

function WintouchWritePanel({
  row,
  action,
  done,
}: {
  row: Row;
  action: ReturnType<typeof useCycleAction>;
  done: (r: Row) => void;
}) {
  const [options, setOptions] = useState<Row | null>(null),
    [mapping, setMapping] = useState<Row>({
      kind: "purchase",
      proposal_index: 0,
      reason: "Conferido e autorizado para Wintouch",
    });
  const labels: Row = {
    prepared: "Preparada, sem envio",
    sending: "Envio em curso",
    uncertain: "Resultado incerto — consultar o identificador",
    failed: "Pedido recusado",
    confirmed: "Confirmada pelo ERP",
    cancelled: "Preparação substituída",
  };
  const selectors =
    mapping.kind === "product"
      ? [
          ["unit_id", "stock_units", "Unidade Wintouch"],
          ["vat_id", "vats", "IVA Wintouch"],
          ["category_id", "product_1st_categories", "Categoria Wintouch"],
          ["price_policy_id", "price_policies", "Tabela de preços Wintouch"],
        ]
      : [
          ["document_type_id", "document_types", "Tipo de compra Wintouch"],
          ["document_serie_id", "document_series", "Série de compra Wintouch"],
          ["warehouse_id", "warehouses", "Armazém Wintouch"],
          ["sector_id", "sectors", "Setor Wintouch"],
          ["currency_id", "currencies", "Moeda Wintouch"],
        ];
  return (
    <details>
      <summary>Preparar e confirmar escrita no Wintouch Cloud</summary>
      <p>
        Administrador com escrita ERP autorizada. O envio não é repetido
        automaticamente; uma resposta incerta exige consulta do mesmo
        identificador.
      </p>
      <ActionButton
        disabled={action.busy}
        onClick={() =>
          void action.run(async () =>
            setOptions(
              await api(`/order-cycle/reviews/${row.id}/wintouch-options`),
            ),
          )
        }
      >
        Consultar opções atuais do Wintouch
      </ActionButton>
      {options && (
        <ManagedForm
          onSubmit={(e) => {
            e.preventDefault();
            void action.run(async () => {
              const result = await action.mutate(
                `/order-cycle/reviews/${row.id}/erp-prepare`,
                {
                  ...mapping,
                  revision: row.revision,
                  fingerprint: row.comparison.fingerprint,
                  confirmed: true,
                },
              );
              done(result);
            });
          }}
        >
          <fieldset disabled={action.busy} className="order-cycle-grid">
            <label>
              <input
                type="checkbox"
                checked={!!mapping.confirm_demo}
                onChange={(e) =>
                  setMapping({ ...mapping, confirm_demo: e.target.checked })
                }
              />{" "}
              Confirmo a escrita no ERP externo caso esta instalação esteja em
              demonstração
            </label>
            <Field label="Operação Wintouch">
              <select
                value={mapping.kind}
                onChange={(e) =>
                  setMapping({ ...mapping, kind: e.target.value })
                }
              >
                <option value="purchase">
                  Criar fatura de compra conferida
                </option>
                <option value="product">Criar artigo proposto</option>
              </select>
            </Field>
            {mapping.kind === "product" && (
              <Field label="Proposta de artigo para o ERP">
                <select
                  required
                  value={mapping.proposal_index}
                  onChange={(e) =>
                    setMapping({
                      ...mapping,
                      proposal_index: Number(e.target.value),
                    })
                  }
                >
                  {(row.article_proposals || []).map((p: Row, i: number) => (
                    <option
                      disabled={p.state !== "blocked_connector"}
                      key={i}
                      value={i}
                    >
                      {p.article.code} · {p.article.name}
                    </option>
                  ))}
                </select>
              </Field>
            )}
            {selectors.map(([key, resource, label]) => (
              <Field key={key} label={label}>
                <select
                  required
                  value={mapping[key] || ""}
                  onChange={(e) =>
                    setMapping({ ...mapping, [key]: e.target.value })
                  }
                >
                  <option value="">Escolha no ERP</option>
                  {(options[resource] || [])
                    .filter(
                      (r: Row) =>
                        resource !== "document_types" || r.class === 32,
                    )
                    .filter(
                      (r: Row) =>
                        resource !== "document_series" ||
                        r.type_id === mapping.document_type_id,
                    )
                    .map((r: Row) => (
                      <option key={r.id} value={r.id}>
                        {r.name}
                        {r.rate != null ? ` · ${r.rate}%` : ""}
                      </option>
                    ))}
                </select>
              </Field>
            ))}
            <Field label="Justificação da operação ERP">
              <input
                required
                minLength={3}
                value={mapping.reason}
                onChange={(e) =>
                  setMapping({ ...mapping, reason: e.target.value })
                }
              />
            </Field>
            <ActionButton type="submit">
              Preparar operação sem enviar
            </ActionButton>
          </fieldset>
        </ManagedForm>
      )}
      {(row.erp_operations || []).map((op: Row) => (
        <section key={op.id}>
          <h4>
            {op.kind === "product" ? "Artigo" : "Compra"} · {labels[op.state]}
          </h4>
          <p>Identificador: {op.external_id || op.id}</p>
          {op.error && <Notice role="alert">{op.error}</Notice>}
          <details>
            <summary>Dados preparados para confirmação</summary>
            <pre className="co-message">
              {JSON.stringify(op.payload, null, 2)}
            </pre>
          </details>
          <ActionButton
            disabled={
              action.busy || ["confirmed", "cancelled"].includes(op.state)
            }
            onClick={() =>
              void action.run(async () => {
                if (
                  op.state === "prepared" &&
                  !(await confirmAction({
                    title: "Confirmar criação no Wintouch Cloud?",
                    message:
                      "Esta operação escreve na empresa ERP configurada. Reveja os dados preparados antes de confirmar.",
                    confirmLabel: "Confirmar criação no Wintouch",
                  }))
                )
                  return;
                const result = await action.mutate(
                  `/order-cycle/reviews/${row.id}/erp-send/${op.id}`,
                  {
                    revision: row.revision,
                    fingerprint: row.comparison.fingerprint,
                    reason: "Operação Wintouch confirmada pelo administrador",
                    confirmed: true,
                    confirm_demo: !!op.confirm_demo,
                  },
                );
                done(result);
              })
            }
          >
            {op.state === "prepared"
              ? "Confirmar criação no Wintouch"
              : "Verificar resultado no Wintouch"}
          </ActionButton>
        </section>
      ))}
    </details>
  );
}

export function OrderCyclePanel({
  mode,
  stores,
  store = "",
  simulations,
  catalog,
  navigate,
}: {
  mode: "receiving" | "review";
  stores: Row[];
  store?: string;
  simulations: Row[];
  catalog: Row;
  navigate: (page: string) => void;
}) {
  const action = useCycleAction();
  const [shop, setShop] = useState(store || stores[0]?.id || ""),
    [reviews, setReviews] = useState<Row[]>([]),
    [documents, setDocuments] = useState<Row[]>([]);
  const [selected, setSelected] = useState<Row | null>(null),
    [link, setLink] = useState<Row | null>(null),
    [reason, setReason] = useState("");
  const [automation, setAutomation] = useState<Row | null>(null);
  useEffect(() => {
    if (store) setShop(store);
  }, [store]);
  async function load() {
    const result = await api(
      "/order-cycle/reviews?store_id=" + encodeURIComponent(shop),
    );
    setReviews(result.items);
    const files = await api(
      "/archive/documents?limit=200&store_id=" + encodeURIComponent(shop),
    );
    setDocuments(files.documents);
    setSelected((old) =>
      old ? result.items.find((r: Row) => r.id === old.id) || null : null,
    );
  }
  useEffect(() => {
    if (shop) void load().catch((e) => action.setError(e.message));
  }, [shop]);
  async function decision(kind: string, label: string) {
    if (!selected) return;
    if (
      !(await confirmAction({
        title: label + "?",
        message:
          kind === "post-local"
            ? "Confirme apenas os artigos, impostos e receções conferidos. O lançamento local não envia documentos ao ERP."
            : "A decisão e as divergências atuais ficam no histórico.",
        confirmLabel: label,
      }))
    )
      return;
    await action.run(async () => {
      const result = await action.mutate(
        `/order-cycle/reviews/${selected.id}/${kind}`,
        {
          revision: selected.revision,
          fingerprint: selected.comparison.fingerprint,
          reason,
          confirmed: true,
        },
      );
      setSelected(result);
      await load();
    });
  }
  return (
    <section
      className="order-cycle-panel"
      aria-label={mode === "review" ? "Conferência ERP" : "Receção e OCR"}
    >
      <div className="filter-bar">
        <Field label="Loja do ciclo">
          <select value={shop} onChange={(e) => setShop(e.target.value)}>
            {stores.map((s) => (
              <option key={s.id} value={s.id}>
                {s.name}
              </option>
            ))}
          </select>
        </Field>
        <ActionButton
          disabled={action.busy}
          onClick={() => void action.run(load)}
        >
          Atualizar conferências
        </ActionButton>
        {mode === "receiving" && (
          <>
            <ActionButton
              onClick={() =>
                setLink({ simulation_id: "", document_id: "", receipt_id: "" })
              }
            >
              Associar ou importar original
            </ActionButton>
            <ActionButton onClick={() => navigate("Digitalizar documento")}>
              Digitalizar documento
            </ActionButton>
          </>
        )}
      </div>
      {action.error && <Notice role="alert">{action.error}</Notice>}
      <DataTable<Row>
        label="conferências de encomendas"
        rows={reviews}
        rowKey={(r) => r.id}
        searchText={(r) => `${r.document_name} ${r.supplier_name}`}
        columns={[
          {
            id: "name",
            label: "Original",
            render: (r) => (
              <>
                <strong>{r.document_name}</strong>
                <p>{r.supplier_name}</p>
              </>
            ),
          },
          {
            id: "state",
            label: "Estado",
            render: (r) => (
              <>
                {states[r.status]}
                <br />
                <small>
                  OCR: {r.processing_status} ·{" "}
                  {states[r.cloud_status] || r.cloud_status}
                </small>
              </>
            ),
          },
          {
            id: "issues",
            label: "Pendências",
            render: (r) => (
              <>
                {r.comparison.discrepancies.length} divergências
                {r.cloud_error && <p>{r.cloud_error}</p>}
                {r.jobs
                  .filter((j: Row) => j.error)
                  .map((j: Row) => (
                    <p key={j.kind}>{j.error}</p>
                  ))}
              </>
            ),
          },
          {
            id: "actions",
            label: "Ações",
            render: (r) => (
              <ActionButton
                onClick={() => {
                  setSelected(r);
                  setReason("");
                }}
              >
                Conferir documento
              </ActionButton>
            ),
          },
        ]}
      />
      {mode === "receiving" && (
        <details>
          <summary>Automatização de notificações por email</summary>
          <p>
            Apenas administradores podem autorizar o envio automático por loja.
            Só são contactados clientes que autorizaram email e têm produtos
            fisicamente disponíveis. WhatsApp permanece manual.
          </p>
          <ActionButton
            onClick={() =>
              void action.run(async () =>
                setAutomation(await api(`/order-cycle/automation/${shop}`)),
              )
            }
          >
            Consultar configuração de notificações
          </ActionButton>
        </details>
      )}
      {selected && (
        <DetailDrawer
          aria-label="Conferência de encomenda"
          busy={action.busy}
          onRequestClose={() => setSelected(null)}
        >
          <h2>Encomendado · Recebido · Faturado</h2>
          <p>
            {selected.document_name} · {selected.supplier_name}
          </p>
          {action.error && <Notice role="alert">{action.error}</Notice>}
          <ol
            className="order-cycle-progress"
            aria-label="Progresso administrativo"
          >
            {[
              "Original guardado",
              "OCR e revisão",
              "Conferência",
              "Proposta ou compra",
            ].map((s, i) => (
              <li
                key={s}
                aria-current={
                  i ===
                  (selected.procurement_id
                    ? 3
                    : selected.comparison.approved
                      ? 2
                      : 1)
                    ? "step"
                    : undefined
                }
              >
                {s}
              </li>
            ))}
          </ol>
          <ActionLink href={selected.file_url} target="_blank" rel="noreferrer">
            Consultar original
          </ActionLink>
          <DataTable<Row>
            label="encomendado recebido faturado"
            rows={[
              ...selected.comparison.rows,
              ...selected.comparison.missing,
            ].map((r, i) => ({ ...r, __key: String(i) }))}
            rowKey={(r) => r.__key}
            searchText={(r) => `${r.description} ${r.reference || ""}`}
            columns={[
              {
                id: "description",
                label: "Artigo",
                render: (r) => (
                  <>
                    {r.description}
                    <small>{r.reference}</small>
                    {r.candidates?.length > 0 && (
                      <details>
                        <summary>Correspondências propostas</summary>
                        {r.candidates.map((c: Row) => (
                          <p key={c.article_id}>
                            {c.code} · {c.name} ·{" "}
                            {c.exact
                              ? "referência exata por confirmar"
                              : "semelhança"}
                          </p>
                        ))}
                      </details>
                    )}
                  </>
                ),
              },
              {
                id: "ordered",
                label: "Encomendado",
                render: (r) => quantity(r.ordered),
              },
              {
                id: "received",
                label: "Recebido",
                render: (r) => quantity(r.received),
              },
              {
                id: "billed",
                label: "Faturado",
                render: (r) => quantity(r.billed),
              },
            ]}
          />
          <details open={selected.comparison.discrepancies.length > 0}>
            <summary>Divergências e avisos</summary>
            {selected.comparison.discrepancies.map((v: string) => (
              <p key={v}>{v}</p>
            ))}
            {selected.comparison.warnings.map((v: string) => (
              <p key={v}>{v}</p>
            ))}
          </details>
          <p>
            Receção física e disponibilidade do cliente são independentes da
            conferência administrativa.
          </p>
          {!selected.procurement_id && (
            <details>
              <summary>Rever dados e identificar artigos</summary>
              <ReviewEditor
                key={`${selected.id}:${selected.revision}`}
                row={selected}
                catalog={catalog}
                busy={action.busy}
                save={async (draft) => {
                  await action.run(async () => {
                    const result = await action.mutate(
                      `/order-cycle/reviews/${selected.id}`,
                      {
                        ...draft,
                        lines: draft.lines.map((r: Row) => ({
                          ...r,
                          article_id: r.article_id || null,
                        })),
                        revision: selected.revision,
                      },
                      "PUT",
                    );
                    setSelected(result);
                    await load();
                  });
                }}
              />
            </details>
          )}
          <Field label="Motivo da decisão">
            <textarea
              value={reason}
              onChange={(e) => setReason(e.target.value)}
              minLength={3}
            />
          </Field>
          <div className="actions">
            <ActionButton
              disabled={
                action.busy ||
                reason.trim().length < 3 ||
                !!selected.procurement_id
              }
              onClick={() => void decision("approve", "Aprovar conferência")}
            >
              Aprovar conferência
            </ActionButton>
            <ActionButton
              disabled={
                action.busy ||
                !selected.comparison.approved ||
                reason.trim().length < 3 ||
                !!selected.procurement_id
              }
              onClick={() => void decision("proposal", "Preparar proposta ERP")}
            >
              Preparar proposta ERP
            </ActionButton>
            <ActionButton
              disabled={
                action.busy ||
                !selected.comparison.approved ||
                reason.trim().length < 3 ||
                !!selected.procurement_id
              }
              onClick={() => void decision("post-local", "Lançar compra local")}
            >
              Lançar compra local
            </ActionButton>
            <ActionButton
              disabled={action.busy}
              onClick={() =>
                void action.run(async () => {
                  const r = await action.mutate(
                    `/order-cycle/reviews/${selected.id}/retry`,
                    { revision: selected.revision },
                  );
                  setSelected(r);
                  await load();
                })
              }
            >
              Repetir OCR / envio cloud
            </ActionButton>
          </div>
          <Notice>{selected.erp_message}</Notice>
          <WintouchWritePanel
            key={`erp:${selected.id}`}
            row={selected}
            action={action}
            done={setSelected}
          />
          {selected.proposal && (
            <ActionLink
              href={`/api/order-cycle/reviews/${selected.id}/proposal`}
              download
            >
              Exportar proposta ERP
            </ActionLink>
          )}
          <div className="actions">
            <ActionButton onClick={() => navigate("Artigos e validades")}>
              Completar ou associar artigo real
            </ActionButton>
            <ActionButton onClick={() => navigate("Integrações")}>
              Consultar integração ERP
            </ActionButton>
          </div>
          {!selected.procurement_id && (
            <ArticleProposalForm
              key={`article:${selected.id}:${selected.revision}`}
              row={selected}
              action={action}
              done={setSelected}
            />
          )}
          <ActionButton onClick={() => setSelected(null)}>
            Fechar conferência
          </ActionButton>
        </DetailDrawer>
      )}
      {link && (
        <AppDialog
          open
          aria-label="Associar original à encomenda"
          busy={action.busy}
          onRequestClose={() => setLink(null)}
        >
          <h2>Associar original à encomenda</h2>
          {action.error && <Notice role="alert">{action.error}</Notice>}
          <ManagedForm
            onSubmit={(e) => {
              e.preventDefault();
              void action.run(async () => {
                await action.mutate("/order-cycle/reviews", {
                  ...link,
                  receipt_id: link.receipt_id || null,
                });
                await load();
                setLink(null);
              });
            }}
          >
            <fieldset disabled={action.busy}>
              <Field label="Encomenda ao fornecedor">
                <select
                  required
                  value={link.simulation_id}
                  onChange={(e) =>
                    setLink({ ...link, simulation_id: e.target.value })
                  }
                >
                  <option value="">Escolha a encomenda</option>
                  {simulations
                    .filter(
                      (s) =>
                        s.store_id === shop &&
                        s.procurement_id &&
                        s.procurement?.status !== "cancelled",
                    )
                    .map((s) => (
                      <option key={s.id} value={s.id}>
                        {s.supplier_name} ·{" "}
                        {s.procurement?.document || s.id.slice(0, 8)}
                      </option>
                    ))}
                </select>
              </Field>
              <Field label="Original no arquivo">
                <select
                  required
                  value={link.document_id}
                  onChange={(e) =>
                    setLink({ ...link, document_id: e.target.value })
                  }
                >
                  <option value="">Escolha um original</option>
                  {documents.map((d) => (
                    <option key={d.id} value={d.id}>
                      {d.original_name} · {d.document_number}
                    </option>
                  ))}
                </select>
              </Field>
              <Field label="Importar PDF ou fotografia">
                <input
                  type="file"
                  accept="application/pdf,image/jpeg,image/png,image/webp"
                  onChange={(e) => {
                    const file = e.target.files?.[0];
                    if (!file) return;
                    void action.run(async () => {
                      const body = new FormData();
                      body.append("files", file);
                      body.append("store_id", shop);
                      const response = await fetch("/api/archive/upload", {
                        method: "POST",
                        body,
                        credentials: "same-origin",
                      });
                      const result = await response.json();
                      if (!response.ok)
                        throw new Error(
                          typeof result.detail === "string"
                            ? result.detail
                            : "Não foi possível importar o original.",
                        );
                      const item = result.results?.[0];
                      if (!item?.id)
                        throw new Error(
                          item?.message ||
                            "O ficheiro não ficou disponível no arquivo.",
                        );
                      const files = await api(
                        "/archive/documents?limit=200&store_id=" +
                          encodeURIComponent(shop),
                      );
                      setDocuments(files.documents);
                      setLink((old) =>
                        old ? { ...old, document_id: item.id } : old,
                      );
                    });
                  }}
                />
              </Field>
              <p>
                O original é conservado no Arquivo Digital. O OCR e a cópia para
                a cloud configurada são processados pela fila recuperável.
              </p>
              <div className="actions">
                <ActionButton type="submit">
                  Associar e iniciar processamento
                </ActionButton>
                <ActionButton onClick={() => setLink(null)}>
                  Fechar
                </ActionButton>
              </div>
            </fieldset>
          </ManagedForm>
        </AppDialog>
      )}
      {automation && (
        <AppDialog
          open
          aria-label="Notificações automáticas"
          busy={action.busy}
          onRequestClose={() => setAutomation(null)}
        >
          <h2>Notificações automáticas</h2>
          {action.error && <Notice role="alert">{action.error}</Notice>}
          <ManagedForm
            onSubmit={(e) => {
              e.preventDefault();
              void action.run(async () => {
                if (
                  !(await confirmAction({
                    title: "Guardar autorização de envio automático?",
                    message:
                      "Ao ativar, autoriza o agendador a enviar email aos clientes desta loja que consentiram e têm produtos disponíveis.",
                    confirmLabel: "Guardar autorização",
                  }))
                )
                  return;
                await action.mutate(`/order-cycle/automation/${shop}`, {
                  enabled: automation.enabled,
                  auto_erp_purchases: !!automation.auto_erp_purchases,
                  template: automation.template,
                  revision: automation.revision,
                });
                setAutomation(null);
              });
            }}
          >
            <label>
              <input
                type="checkbox"
                checked={automation.enabled}
                onChange={(e) =>
                  setAutomation({ ...automation, enabled: e.target.checked })
                }
              />{" "}
              Autorizar envio automático por SMTP nesta loja
            </label>
            <label>
              <input
                type="checkbox"
                checked={!!automation.auto_erp_purchases}
                onChange={(e) =>
                  setAutomation({
                    ...automation,
                    auto_erp_purchases: e.target.checked,
                  })
                }
              />{" "}
              Autorizar envio automático de compras Wintouch já preparadas,
              aprovadas e sem divergências
            </label>
            <Field label="Mensagem automática">
              <textarea
                required
                minLength={10}
                value={automation.template}
                onChange={(e) =>
                  setAutomation({ ...automation, template: e.target.value })
                }
              />
            </Field>
            <p>
              Campos disponíveis: {"{nome}"} e {"{numero}"}. Falhas e resultados
              incertos exigem revisão manual no histórico de Email.
            </p>
            <div className="actions">
              <ActionButton type="submit" disabled={action.busy}>
                Guardar autorização
              </ActionButton>
              <ActionButton onClick={() => setAutomation(null)}>
                Fechar
              </ActionButton>
            </div>
          </ManagedForm>
        </AppDialog>
      )}
    </section>
  );
}
