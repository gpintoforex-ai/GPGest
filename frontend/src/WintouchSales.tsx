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

export function SalesImportPanel({ company }: { company: Row }) {
  const [stores, setStores] = useState<Row[]>([]);
  const [notice, setNotice] = useState("");
  const [error, setError] = useState("");
  const [initialRange] = useState(currentMonth);
  useEffect(() => {
    api("/bootstrap")
      .then((b) => setStores(b.stores))
      .catch((e) => setError(e.message));
  }, []);
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
      {company.active && company.api_active && stores.length > 0 ? (
        <Form
          submitLabel="Importar vendas"
          onSubmit={async (v) => {
            setNotice("");
            const r = await api("/integrations/wintouch/sales", {
              ...v,
              confirm_demo: v.confirm_demo === "on",
            });
            if (r.status !== "concluida") throw new Error(r.error_message);
            setNotice(
              `Importação concluída: ${r.inserted_count} registos novos e ${r.updated_count} atualizados. Consulte Movimentos reais e Movimentos previstos.`,
            );
            window.dispatchEvent(new Event("gpgest-sales-updated"));
          }}
        >
          <Field label="Loja de destino">
            <select name="store_id" required>
              {stores.map((r) => (
                <option value={r.id} key={r.id}>
                  {r.name}
                </option>
              ))}
            </select>
          </Field>
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
