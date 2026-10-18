import { Notice } from "./components/InteractionUI";
import { useEffect, useState } from "react";
import { api } from "./App";
import SyncPanel from "./SyncPanel";
import { SalesImportPanel } from "./WintouchSales";

export default function TenantIntegrations({
  canManage,
}: {
  canManage: boolean;
}) {
  const [company, setCompany] = useState<any>(null),
    [error, setError] = useState("");
  useEffect(() => {
    api("/integrations/wintouch")
      .then(setCompany)
      .catch((e) => setError(e.message));
  }, []);
  return (
    <section className="panel">
      <h3>Wintouch</h3>
      {error && (
        <Notice className="error" role="alert">
          {error}
        </Notice>
      )}
      {company ? (
        <>
          <span className="pill">
            {company.api_active ? "Ligação ativada" : "Ligação desativada"}
          </span>
          {canManage && company.api_active ? (
            <>
              <SyncPanel company={company} />
              {(company.capabilities?.read_documents ?? true) && company.resources?.includes("sales") !== false && <SalesImportPanel company={company} />}
            </>
          ) : (
            <p>Consulte o modo e as permissões em Configurações &gt; Parâmetros &gt; Ligação ERP. Os dados já guardados permanecem acessíveis.</p>
          )}
        </>
      ) : (
        !error && <p>A verificar ligação…</p>
      )}
    </section>
  );
}
