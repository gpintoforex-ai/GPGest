import { Notice } from "./components/InteractionUI";
import { useEffect, useState } from "react";
import { api } from "./lib/api";
import { Form } from "./components/Form";
import { DataTable } from "./components/DataTable";

const resourceLabel = (resource: string) =>
  ({
    products: "Artigos",
    entities: "Entidades",
    cash_accounts: "Caixas",
    sales: "Vendas e recebimentos",
  })[resource] || resource;
const syncDate = (r: any) =>
  new Date(r.started_at).toLocaleString("pt-PT", { timeZone: "Europe/Lisbon" });

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
      <DataTable<any>
        label="últimas sincronizações"
        rows={history}
        rowKey={(r) => String(r.id)}
        emptyText="Ainda não existem importações."
        exportName="sincronizacoes.csv"
        searchText={(r) => `${resourceLabel(r.resource)} ${r.status} ${r.error_message || ""}`}
        columns={[
          { id: "data", label: "Data", render: (r) => syncDate(r), sortValue: (r) => r.started_at || "", exportValue: syncDate },
          { id: "recurso", label: "Recurso", render: (r) => resourceLabel(r.resource), sortValue: (r) => resourceLabel(r.resource), exportValue: (r) => resourceLabel(r.resource) },
          {
            id: "estado",
            label: "Estado",
            sortValue: (r) => r.status,
            exportValue: (r) => [r.status, r.error_message].filter(Boolean).join(" — "),
            render: (r) => (
              <>
                {r.status}
                {r.error_message && <p>{r.error_message}</p>}
              </>
            ),
          },
          { id: "lidos", label: "Lidos", numeric: true, render: (r) => r.read_count, sortValue: (r) => r.read_count || 0, exportValue: (r) => r.read_count },
          { id: "novos", label: "Novos", numeric: true, render: (r) => r.inserted_count, sortValue: (r) => r.inserted_count || 0, exportValue: (r) => r.inserted_count },
          { id: "atualizados", label: "Atualizados", numeric: true, render: (r) => r.updated_count, sortValue: (r) => r.updated_count || 0, exportValue: (r) => r.updated_count },
        ]}
      />
    </section>
  );
}
