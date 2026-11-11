import DatePresets from "./components/DatePresets";
import { ActionButton, Notice } from "./components/InteractionUI";
import { useEffect, useState } from "react";
import { api } from "./lib/api";
import { Field, Form } from "./components/Form";
import RecordList, { calendarDay, currentMonth } from "./RecordList";
type Row = Record<string, any>;
const money = (v: number) =>
  new Intl.NumberFormat("pt-PT", { style: "currency", currency: "EUR" }).format(
    v / 100,
  );
const day = (v: string) => calendarDay(v).split("-").reverse().join("/") || "—";

/** Resumo das saídas de stock geradas pelas vendas importadas (só quando o controlo de stock está ativo). */
export function StockOutcome({ stock }: { stock: Row }) {
  if (stock.status !== "aplicado")
    return (
      <Notice className={stock.status === "erro" ? "error" : "warning"} role="status">
        <strong>O stock não foi alterado.</strong> As vendas foram importadas para os movimentos financeiros, mas não geraram
        movimentos de stock: {stock.message}
      </Notice>
    );
  const issues = [...(stock.conflicts || []), ...(stock.pending || [])];
  return (
    <Notice className={stock.pending_count || stock.conflict_count ? "warning" : "success"} role="status">
      <strong>Stock:</strong> {[
        `${stock.applied} saída(s) de venda`,
        stock.entries ? `${stock.entries} entrada(s) de stock` : "",
        stock.already_received_count ? `${stock.already_received_count} compra(s) já recebida(s) na aplicação (sem nova entrada)` : "",
        stock.exits ? `${stock.exits} saída(s) de stock` : "",
        stock.transfers ? `${stock.transfers} transferência(s)` : "",
        `${stock.returns} devolução(ões)`,
        stock.reversed ? `${stock.reversed} estorno(s) de documentos anulados` : "",
      ].filter(Boolean).join(", ")}.
      {issues.length > 0 && (
        <details>
          <summary>
            {stock.pending_count} linha(s) por aplicar{stock.conflict_count ? ` e ${stock.conflict_count} conflito(s)` : ""}
          </summary>
          <ul>
            {issues.map((item: Row, index: number) => (
              <li key={index}>
                <b>{item.document}</b>{item.article ? ` · ${item.article}` : ""}: {item.reason}
              </li>
            ))}
          </ul>
        </details>
      )}
    </Notice>
  );
}

export function SalesImportPanel({ company }: { company: Row }) {
  const [stores, setStores] = useState<Row[]>([]);
  const [warehouses, setWarehouses] = useState<Row[]>([]);
  const [store, setStore] = useState("");
  const [notice, setNotice] = useState("");
  const [stock, setStock] = useState<Row | null>(null);
  const [error, setError] = useState("");
  const [initialRange] = useState(currentMonth);
  useEffect(() => {
    api("/bootstrap")
      .then((b) => { setStores(b.stores); setStore(b.stores[0]?.id || ""); })
      .catch((e) => setError(e.message));
    // Sem acesso aos armazéns, a escolha automática (ligação ao ERP ou armazém único) continua disponível.
    api("/tables/warehouses/records?status=active&size=100")
      .then((r) => setWarehouses(r.rows || []))
      .catch(() => setWarehouses([]));
  }, []);
  const storeWarehouses = warehouses.filter((w) => w.values?.store_id === store);
  return (
    <section className="panel">
      <h3>Importar vendas e movimentos de caixa Wintouch</h3>
      <p>
        Escolha o período e a loja a que pertence a ligação. Importa faturas,
        faturas-recibo, faturas simplificadas, notas de crédito, pagamentos
        identificados, entradas e retiradas manuais de caixa e saldos a crédito.
      </p>
      <p>
        Os recebimentos Wintouch ficam nos movimentos reais e os saldos em
        aberto nos previstos, identificados pela origem. Não alteram o saldo das
        caixas locais. As datas sem fuso horário mantêm a data indicada pela
        Wintouch.
      </p>
      {error && (
        <Notice className="error" role="alert">
          {error}
        </Notice>
      )}
      {notice && <p role="status">{notice}</p>}
      {stock && <StockOutcome stock={stock} />}
      {company.active && company.api_active && stores.length > 0 ? (
        <Form
          submitLabel="Importar vendas"
          onSubmit={async (v) => {
            setNotice("");
            setStock(null);
            const r = await api("/integrations/wintouch/sales", {
              ...v,
              warehouse_id: v.warehouse_id || null,
              confirm_demo: v.confirm_demo === "on",
            });
            if (r.status !== "concluida") throw new Error(r.error_message);
            setStock(r.stock || null);
            setNotice(
              `Importação concluída: ${r.inserted_count} registos novos e ${r.updated_count} atualizados. Consulte Movimentos reais e Movimentos previstos.`,
            );
            window.dispatchEvent(new Event("gpgest-sales-updated"));
          }}
        >
          <Field label="Loja de destino">
            <select name="store_id" required value={store} onChange={(e) => setStore(e.target.value)}>
              {stores.map((r) => (
                <option value={r.id} key={r.id}>
                  {r.name}
                </option>
              ))}
            </select>
          </Field>
          {storeWarehouses.length > 1 && (
            <Field label="Armazém das saídas de stock">
              <select name="warehouse_id" defaultValue="">
                <option value="">Pela ligação dos armazéns ao ERP</option>
                {storeWarehouses.map((w) => (
                  <option value={w.id} key={w.id}>{w.description}</option>
                ))}
              </select>
            </Field>
          )}
          <DatePresets names={["start_date", "end_date"]} />
          <Field label="Vendas desde">
            <input
              type="date"
              name="start_date"
              defaultValue={initialRange.from}
              required
            />
          </Field>
          <Field label="Até">
            <input
              type="date"
              name="end_date"
              defaultValue={initialRange.to}
              required
            />
          </Field>
          {company.mode === "demonstracao" && (
            <label className="checkbox">
              <input type="checkbox" name="confirm_demo" required />
              Confirmo a importação Wintouch para esta empresa de demonstração.
            </label>
          )}
          <p className="footnote">
            Pode repetir o período sem duplicar registos. Pendentes são
            atualizados pela conta corrente Wintouch. Confirme que todos os
            documentos desta ligação pertencem à loja escolhida. Período máximo:
            um ano.
          </p>
          <p className="footnote">
            Com o controlo de stock ativo, os documentos que a Wintouch marca como movimentando stock
            (vendas, compras, entradas, saídas, transferências e devoluções) desde a data de início geram
            os movimentos de stock dos artigos associados, uma vez por linha e com o lote indicado. As
            linhas que não podem ser aplicadas ficam listadas.
          </p>
        </Form>
      ) : (
        <p>Ative a ligação Wintouch para importar.</p>
      )}
    </section>
  );
}

export default function WintouchSales({
  kind,
  store,
}: {
  kind: "real" | "previsto";
  store: string;
}) {
  const [rows, setRows] = useState<Row[]>([]);
  const [error, setError] = useState("");
  const [documents, setDocuments] = useState(false);
  useEffect(() => {
    const load = () =>
      api("/integrations/wintouch/sales")
        .then(setRows)
        .catch((e) => setError(e.message));
    load();
    window.addEventListener("gpgest-sales-updated", load);
    return () => window.removeEventListener("gpgest-sales-updated", load);
  }, []);
  const visible = rows.filter(
    (r) =>
      (!store || r.store_id === store) &&
      r.kind === (documents ? "documento" : kind),
  );
  return (
    <section className="panel">
      <div className="page-intro">
        <div>
          <h2>
            {documents
              ? "Documentos de venda Wintouch"
              : kind === "real"
                ? "Recebimentos Wintouch"
                : "Saldos a crédito Wintouch"}
          </h2>
          <p>
            {kind === "real"
              ? "Pagamentos confirmados pela origem. As faturas são consultáveis separadamente; emitir uma fatura não prova um recebimento."
              : "Saldo em aberto na conta corrente Wintouch. Atualize a importação depois de efetivar o recebimento na Wintouch."}
          </p>
        </div>
        {kind === "real" && (
          <ActionButton onClick={() => setDocuments(!documents)}>
            {documents ? "Ver recebimentos" : "Ver documentos de venda"}
          </ActionButton>
        )}
      </div>
      {error && (
        <Notice className="error" role="alert">
          {error}
        </Notice>
      )}
      <RecordList
        totalField={documents ? undefined : "amount_cents"}
        totalsTitle={
          kind === "previsto"
            ? "Totais por liquidar no período"
            : "Totais do período selecionado"
        }
        rows={visible}
        dateField={kind === "previsto" ? "due_date" : "occurred_at"}
        dateLabel={kind === "previsto" ? "Vencimento" : "Data Wintouch"}
        resetKey={`${store}-${documents}`}
      >
        {(pageRows) => (
          <div className="table-wrap">
            <table>
              <thead>
                <tr>
                  <th>Data</th>
                  <th>Documento</th>
                  <th>Entidade</th>
                  <th>
                    {kind === "previsto" ? "Vencimento" : "Meio de pagamento"}
                  </th>
                  <th>{kind === "previsto" ? "Por liquidar" : "Montante"}</th>
                  <th>Origem / estado</th>
                </tr>
              </thead>
              <tbody>
                {pageRows.map((r) => (
                  <tr key={r.id}>
                    <td>{day(r.occurred_at)}</td>
                    <td>{r.document}</td>
                    <td>{r.entity}</td>
                    <td>
                      {kind === "previsto"
                        ? day(r.due_date)
                        : r.payment || "Documento de venda"}
                    </td>
                    <td className={["numeric", r.direction > 0 ? "positive" : "negative"].filter(Boolean).join(" ")}>
                      {r.direction < 0 ? "−" : "+"}
                      {money(r.amount_cents)}
                    </td>
                    <td>
                      <span className="pill neutral">
                        Wintouch{r.cancelled ? " · Anulado" : ""}
                      </span>
                      <small>
                        Atualizado:{" "}
                        {new Date(r.synced_at).toLocaleString("pt-PT")}
                      </small>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </RecordList>
      {!visible.length && (
        <p>
          Sem{" "}
          {documents
            ? "documentos"
            : kind === "real"
              ? "recebimentos"
              : "saldos em aberto"}{" "}
          importados. Use Integrações → Importar vendas Wintouch.
        </p>
      )}
    </section>
  );
}
