import { Notice } from "./components/InteractionUI";
import { useEffect, useState } from "react";
import { api } from "./lib/api";
import { Form } from "./components/Form";

const resources = [
  { id: "products", label: "Artigos" },
  { id: "entities", label: "Clientes / fornecedores (entidades)" },
  { id: "cash_accounts", label: "Caixas Wintouch" },
];

export default function SyncPanel({
  company,
  apiBase = "/integrations/wintouch",
}: {
  company: { id: string; mode: string; active: boolean; api_active: boolean; operating_mode?: string; resources?: string[] };
  apiBase?: string;
}) {
  const [selectedResources, setSelectedResources] = useState<string[]>([]);
  const [history, setHistory] = useState<any[]>([]),
    [error, setError] = useState(""),
    [notice, setNotice] = useState("");
  async function refresh() {
    setHistory(await api(apiBase + "/sync-history"));
  }
  useEffect(() => {
    setSelectedResources([]);
    refresh().catch((e) => setError(e.message));
    const updated = () => {
      refresh().catch((e) => setError(e.message));
    };
    window.addEventListener("gpgest-sales-updated", updated);
    return () => window.removeEventListener("gpgest-sales-updated", updated);
  }, [company.id, apiBase]);
  return (
    <section style={{ marginTop: 24 }}>
      <h3>Importação Wintouch</h3>
      <p>
        Importa o catálogo desta empresa por leitura. Atualiza IDs existentes
        sem duplicar registos. Os saldos de caixa IAZELA permanecem separados.
      </p>
      {error && (
        <Notice role="alert" className="error">
          {error}
        </Notice>
      )}
      {notice && <p role="status">{notice}</p>}
      {company.active && company.api_active && company.operating_mode !== "local" ? (
        <Form
          submitLabel="Importar selecionados"
          onSubmit={async (v) => {
            setNotice("");
            setError("");
            if (!selectedResources.length) throw new Error("Selecione pelo menos um tipo de dados a importar.");
            const results: string[] = [];
            const failures: string[] = [];
            for (const resource of resources.filter((item) => selectedResources.includes(item.id) && (!company.resources || company.resources.includes(item.id)))) {
              try {
                const r = await api(apiBase + "/sync", {
                  resource: resource.id,
                  confirm_demo: v.confirm_demo === "on",
                });
                if (r.status !== "concluida") throw new Error(r.error_message || "Importação falhou.");
                results.push(`${resource.label}: ${r.read_count} registos, ${r.inserted_count} novos e ${r.updated_count} atualizados.`);
              } catch (e) {
                failures.push(`${resource.label}: ${(e as Error).message}`);
              }
            }
            setNotice(results.join(" "));
            await refresh();
            if (failures.length) throw new Error(failures.join(" "));
          }}
        >
          <fieldset>
            <legend>Dados a importar</legend>
            {resources.map((resource) => (
              <label className="checkbox" key={resource.id}>
                <input type="checkbox" disabled={!!company.resources && !company.resources.includes(resource.id)} checked={selectedResources.includes(resource.id)}
                  onChange={(e) => setSelectedResources((current) => e.target.checked
                    ? [...current, resource.id] : current.filter((id) => id !== resource.id))} />
                {resource.label}
              </label>
            ))}
          </fieldset>
          {company.mode === "demonstracao" && (
            <label className="checkbox">
              <input name="confirm_demo" type="checkbox" required />
              Confirmo que pretendo importar dados Wintouch para esta empresa de
              demonstração.
            </label>
          )}
          <p className="footnote">
            A operação pode demorar alguns minutos. A classificação
            cliente/fornecedor usa os campos explícitos da origem. Para vendas,
            use o formulário de importação de vendas abaixo.
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
                      sales: "Vendas e recebimentos",
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
