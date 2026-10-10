import { useEffect, useState } from "react";
import { api, Form, Field } from "./App";

export default function SyncPanel({
  company,
  apiBase = "/integrations/wintouch",
}: {
  company: { id: string; mode: string; active: boolean; api_active: boolean };
  apiBase?: string;
}) {
  const [history, setHistory] = useState<any[]>([]),
    [error, setError] = useState(""),
    [notice, setNotice] = useState("");
  async function refresh() {
    setHistory(await api(apiBase + "/sync-history"));
  }
  useEffect(() => {
    refresh().catch((e) => setError(e.message));
  }, [company.id]);
  return (
    <section style={{ marginTop: 24 }}>
      <h3>Importação Wintouch</h3>
      <p>
        Importa o catálogo desta empresa por leitura. Atualiza IDs existentes
        sem duplicar registos. Os saldos de caixa GPGest permanecem separados.
      </p>
      {error && (
        <p role="alert" className="error">
          {error}
        </p>
      )}
      {notice && <p role="status">{notice}</p>}
      {company.active && company.api_active ? (
        <Form
          submitLabel="Sincronizar agora"
          onSubmit={async (v) => {
            setNotice("");
            const r = await api(apiBase + "/sync", {
              resource: v.resource,
              confirm_demo: v.confirm_demo === "on",
            });
            await refresh();
            if (r.status !== "concluida") throw new Error(r.error_message);
            setNotice(
              `Sincronização concluída: ${r.read_count} registos, ${r.inserted_count} novos e ${r.updated_count} atualizados.`,
            );
          }}
        >
          <Field label="Dados a importar">
            <select name="resource">
              <option value="products">Artigos</option>
              <option value="entities">
                Clientes / fornecedores (entidades)
              </option>
              <option value="cash_accounts">Caixas Wintouch</option>
            </select>
          </Field>
          {company.mode === "demonstracao" && (
            <label className="checkbox">
              <input name="confirm_demo" type="checkbox" required />
              Confirmo que pretendo importar dados Wintouch para esta empresa de
              demonstração.
            </label>
          )}
          <p className="footnote">
            A operação pode demorar alguns minutos. A classificação
            cliente/fornecedor usa os campos explícitos da origem. A importação
            de movimentos financeiros será tratada separadamente.
          </p>
        </Form>
      ) : (
        <p>Ative e guarde a ligação para importar.</p>
      )}
      <h4>Últimas sincronizações</h4>
      {history.length === 0 ? (
        <p>Ainda não existem importações.</p>
      ) : (
        <div className="table-wrap">
          <table>
            <thead>
              <tr>
                <th>Data</th>
                <th>Recurso</th>
                <th>Estado</th>
                <th>Lidos</th>
                <th>Novos</th>
                <th>Atualizados</th>
              </tr>
            </thead>
            <tbody>
              {history.map((r) => (
                <tr key={r.id}>
                  <td>
                    {new Date(r.started_at).toLocaleString("pt-PT", {
                      timeZone: "Europe/Lisbon",
                    })}
                  </td>
                  <td>
                    {{
                      products: "Artigos",
                      entities: "Entidades",
                      cash_accounts: "Caixas",
                    }[r.resource as string] || r.resource}
                  </td>
                  <td>
                    {r.status}
                    {r.error_message && <p>{r.error_message}</p>}
                  </td>
                  <td>{r.read_count}</td>
                  <td>{r.inserted_count}</td>
                  <td>{r.updated_count}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </section>
  );
}
