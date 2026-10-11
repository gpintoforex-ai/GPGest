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
        <p className="error" role="alert">
          {error}
        </p>
      )}
      {company ? (
        <>
          <span className="pill">
            {company.api_active ? "Ligação ativada" : "Ligação desativada"}
          </span>
          {canManage ? (
            <>
              <SyncPanel company={company} />
              <SalesImportPanel company={company} />
            </>
          ) : (
            <p>O administrador da empresa gere as sincronizações.</p>
          )}
        </>
      ) : (
        !error && <p>A verificar ligação…</p>
      )}
    </section>
  );
}
