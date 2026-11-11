import type { ReactNode } from "react";
import { Sparkles } from "lucide-react";
import { ActionButton } from "./components/InteractionUI";
import ManagementDashboard, { useDashboardVersion } from "./ManagementDashboard";
import WorkOverview from "./WorkOverview";

type Navigate = (page: string, params?: Record<string, string>) => void;

/**
 * Página Dashboard: versão atual (visão de trabalho com o painel financeiro dentro) ou versão nova
 * (painel modernizado com a visão de trabalho em «Encomendas e abastecimento»). As duas usam os mesmos
 * componentes, dados e filtros; a escolha fica no navegador, por empresa e utilizador.
 */
export default function DashboardHome({ store, scopeKey, companyName, mode, role, navigate, onAction, operation }: {
  store: string; scopeKey: string; companyName: string; mode: string; role: string;
  navigate: Navigate; onAction: (kind: string) => void; operation: ReactNode;
}) {
  const [variant, setVariant] = useDashboardVersion(scopeKey);
  const dashboard = (work?: ReactNode) => <ManagementDashboard store={store} scopeKey={scopeKey} companyName={companyName} mode={mode} role={role}
    navigate={navigate} onAction={onAction} operation={operation} variant={variant} onVersion={setVariant} work={work} />;
  if (variant === "nova") return dashboard(<WorkOverview store={store} role={role} mode={mode} navigate={navigate} embedded />);
  return <>
    {/* Acesso visível à nova versão (lado a lado até ser validada); a escolha fica neste navegador. */}
    <section className="dashboard-version-banner" aria-label="Nova versão do Dashboard">
      <Sparkles size={22} aria-hidden="true" />
      <div><strong>Há uma nova versão do Dashboard</strong><p>Os mesmos dados, filtros e análises, com cartões, gráficos e previsão de IVA reorganizados. Pode voltar a esta versão a qualquer momento.</p></div>
      <ActionButton type="button" variant="primary" inferAction={false} onClick={() => setVariant("nova")}>Abrir a nova versão</ActionButton>
    </section>
    <WorkOverview store={store} role={role} mode={mode} navigate={navigate} financial={dashboard()} />
  </>;
}
