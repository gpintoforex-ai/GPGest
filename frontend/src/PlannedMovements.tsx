import { useEffect, useState } from "react";
import { api, Field, Form } from "./App";
import RecordList from "./RecordList";
import WintouchSettlementForm from "./WintouchSettlementForm";
import SettlementConfirmation from "./SettlementConfirmation";
type Row = Record<string, any>;
const money = (value: number) =>
  new Intl.NumberFormat("pt-PT", { style: "currency", currency: "EUR" }).format(
    value / 100,
  );

export default function PlannedMovements({
  boot,
  sessions,
  store,
  reload,
}: {
  boot: Row;
  sessions: Row[];
  store: string;
  reload: () => Promise<void>;
}) {
  const [rows, setRows] = useState<Row[]>([]);
  const [sourceRows, setSourceRows] = useState<Row[]>([]);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const [create, setCreate] = useState(false);
  const [selected, setSelected] = useState<Row | null>(null);
  const [key, setKey] = useState(crypto.randomUUID());
  const load = async () => {
    const [local, imported] = await Promise.all([
      api("/planned-movements"),
      api("/integrations/wintouch/sales"),
    ]);
    setRows(local);
    setSourceRows(
      imported
        .filter((r: Row) => r.kind === "previsto")
        .map((r: Row) => ({
          ...r,
          id: `wintouch:${r.id}`,
          external: true,
          paid_cents: null,
          remaining_cents: r.amount_cents,
          status: "Pendente",
          settlements: [],
        })),
    );
    setError("");
  };
  useEffect(() => {
    const refresh = () => {
      load().catch((e) => setError(e.message));
    };
    refresh();
    window.addEventListener("gpgest-sales-updated", refresh);
    return () => window.removeEventListener("gpgest-sales-updated", refresh);
  }, []);
  const allRows = [
    ...rows.map<Row>((r) => ({
      ...r,
      document_group: r.direction === 1 ? "vendas" : "compras",
    })),
    ...sourceRows,
  ].sort(
    (a, b) => a.due_date.localeCompare(b.due_date) || a.id.localeCompare(b.id),
  );
  const write = boot.user.role !== "consulta";
  const open = sessions.filter(
    (s) =>
      !s.closed_at &&
      boot.registers.some(
        (r: Row) => r.id === s.register_id && r.store_id === selected?.store_id,
      ) &&
      (boot.user.role !== "operador" || s.operator_id === boot.user.id),
  );
  return (
    <>
      <div className="page-intro">
        <div>
          <h1>Movimentos previstos</h1>
          <p>
            Documentos a crédito: valores a receber ou a pagar. Só a liquidação
            gera um movimento real.
          </p>
        </div>
        {write && (
          <button
            className="primary"
            onClick={() => {
              setCreate(!create);
              setSelected(null);
            }}
          >
            Novo movimento previsto
          </button>
        )}
      </div>
      {notice && (
        <SettlementConfirmation message={notice} onClose={() => setNotice("")} />
      )}
      {error && (
        <p className="error" role="alert">
          {error}
        </p>
      )}
      {create && (
        <section className="panel">
          <h2>Novo documento a crédito</h2>
          <Form
            onSubmit={async (v) => {
              await api("/planned-movements", {
                ...v,
                direction: Number(v.direction),
              });
              await load();
              setCreate(false);
            }}
          >
            <Field label="Loja">
              <select
                name="store_id"
                defaultValue={store || boot.stores[0]?.id}
                required
              >
                {boot.stores.map((r: Row) => (
                  <option key={r.id} value={r.id}>
                    {r.name}
                  </option>
                ))}
              </select>
            </Field>
            <Field label="Tipo">
              <select name="direction">
                <option value="1">A receber de cliente</option>
                <option value="-1">A pagar a fornecedor</option>
              </select>
            </Field>
            <Field label="Entidade">
              <input name="entity" required maxLength={200} />
            </Field>
            <Field label="Documento">
              <input name="document" required maxLength={200} />
            </Field>
            <Field label="Vencimento">
              <input name="due_date" type="date" required />
            </Field>
            <Field label="Montante (€)">
              <input
                name="amount"
                type="number"
                min="0.01"
                step="0.01"
                required
              />
            </Field>
            <Field label="Categoria">
              <select name="category_id" required>
                {boot.categories.map((r: Row) => (
                  <option key={r.id} value={r.id}>
                    {r.name}
                  </option>
                ))}
              </select>
            </Field>
          </Form>
        </section>
      )}
      {selected?.external && (
        <WintouchSettlementForm
          key={selected.id}
          row={selected}
          onCancel={() => setSelected(null)}
          onDone={async (result) => {
            setNotice(
              `${result.message} ${selected.document} · ${money(Math.round(Number(result.amount.replace(",", ".")) * 100))}.`,
            );
            setSelected(null);
            try {
              await load();
              await reload();
            } catch {
              setError(
                "A liquidação foi confirmada, mas não foi possível atualizar a lista. Atualize a página.",
              );
            }
            window.dispatchEvent(new Event("gpgest-sales-updated"));
          }}
        />
      )}
      {selected && !selected.external && (
        <section className="panel" key={selected.id}>
          <h2>
            Liquidar {selected.document} · {selected.entity}
          </h2>
          <p>
            Saldo por liquidar: {money(selected.remaining_cents)}. Pode registar
            um pagamento parcial.
          </p>
          {!open.length ? (
            <p>
              Abra uma caixa desta loja para registar o pagamento ou
              recebimento.
            </p>
          ) : (
            <Form
              submitLabel="Efetivar movimento"
              onSubmit={async (v) => {
                await api(`/planned-movements/${selected.id}/settle`, {
                  ...v,
                  occurred_at: new Date().toISOString(),
                  request_key: key,
                });
                await load();
                await reload();
                setSelected(null);
              }}
            >
              <Field label="Caixa aberta">
                <select name="session_id" required>
                  {open.map((s) => (
                    <option key={s.id} value={s.id}>
                      {
                        boot.registers.find((r: Row) => r.id === s.register_id)
                          ?.name
                      }
                    </option>
                  ))}
                </select>
              </Field>
              <Field label="Meio de pagamento">
                <select name="payment">
                  <option value="numerario">Numerário</option>
                  <option value="cartao">Cartão</option>
                  <option value="transferencia">Transferência</option>
                  <option value="outro">Outro</option>
                </select>
              </Field>
              <Field label="Valor a liquidar (€)">
                <input
                  name="amount"
                  type="number"
                  min="0.01"
                  step="0.01"
                  max={selected.remaining_cents / 100}
                  defaultValue={(selected.remaining_cents / 100).toFixed(2)}
                  required
                />
              </Field>
            </Form>
          )}
          <button onClick={() => setSelected(null)}>Fechar</button>
        </section>
      )}
      <section className="panel">
        <RecordList
          plannedFilters
          startAtOldestPending
          totalField="remaining_cents"
          totalsTitle="Totais por liquidar no período"
          rows={allRows.filter((r) => !store || r.store_id === store)}
          dateField="due_date"
          dateLabel="Vencimento"
          resetKey={store}
        >
          {(pageRows) => (
            <div className="table-wrap">
              <table>
                <thead>
                  <tr>
                    <th>Vencimento</th>
                    <th>Documento</th>
                    <th>Entidade</th>
                    <th>Tipo</th>
                    <th>Total</th>
                    <th>Liquidado</th>
                    <th>Por liquidar</th>
                    <th>Estado</th>
                    <th>Origem</th>
                    <th>Ações</th>
                  </tr>
                </thead>
                <tbody>
                  {pageRows.map((r) => (
                    <tr key={r.id}>
                      <td>{r.due_date.split("-").reverse().join("/")}</td>
                      <td>{r.document}</td>
                      <td>{r.entity}</td>
                      <td>{r.direction === 1 ? "A receber" : "A pagar"}</td>
                      <td>{money(r.amount_cents)}</td>
                      <td>
                        {r.paid_cents === null ? "—" : money(r.paid_cents)}
                      </td>
                      <td>{money(r.remaining_cents)}</td>
                      <td>
                        <span className="pill">{r.status}</span>
                      </td>
                      <td>
                        <span className="pill neutral">
                          {r.external ? "Wintouch" : "GPGest"}
                        </span>
                      </td>
                      <td>
                        {r.external &&
                          (boot.user.role === "administrador" ||
                          boot.user.role === "gestor" ? (
                            <button
                              onClick={() => {
                                setSelected(r);
                                setCreate(false);
                              }}
                            >
                              Liquidar na Wintouch
                            </button>
                          ) : (
                            <small>
                              Liquidação pelo administrador ou gestor.
                            </small>
                          ))}
                        {write && !r.external && r.remaining_cents > 0 && (
                          <button
                            onClick={() => {
                              setSelected(r);
                              setKey(crypto.randomUUID());
                              setCreate(false);
                            }}
                          >
                            Liquidar
                          </button>
                        )}
                        {r.settlements.map((m: Row) => (
                          <small key={m.id}>
                            {new Date(m.occurred_at).toLocaleDateString(
                              "pt-PT",
                            )}{" "}
                            · {money(m.amount_cents)} · {m.payment}{" "}
                            {m.reversed_by ? "(estornado)" : ""}
                          </small>
                        ))}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
              {!allRows.length && <p>Sem movimentos previstos registados.</p>}
            </div>
          )}
        </RecordList>
      </section>
    </>
  );
}
