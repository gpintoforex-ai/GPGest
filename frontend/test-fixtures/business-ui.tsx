/** Development-only fixture; no API, credentials, persistence or business operations. */
import { useState } from "react";
import { createRoot } from "react-dom/client";
import { ShoppingCart } from "lucide-react";
import {
  MetricCard,
  StatusBadge,
  SyncStatus,
  type BusinessTone,
} from "../src/components/BusinessUI";
import {
  ContextSuggestion,
  KpiCard,
  ModuleOverview,
  ProcessSteps,
  ProgressGoal,
  QuickFilters,
} from "../src/components/OverviewUI";
import {
  ActionButton,
  AppDialog,
  CloseButton,
  FeedbackHost,
  ManagedForm,
} from "../src/components/InteractionUI";
import { DataTable } from "../src/components/DataTable";
import { ModuleTabs } from "../src/components/WorkspaceUI";
import ThemeSettings, { initializeTheme } from "../src/ThemeSettings";
import "../src/style.css";
import "../src/design-system.css";
import "../src/components/InteractionUI.css";
import "../src/themes.css";
import "../src/contrast.css";
import "../src/filters.css";
import "../src/page-cards.css";
import "../src/action-colours.css";
import "../src/toggle-controls.css";
import "../src/confirmation-windows.css";
import "../src/brand-navigation.css";
import "../src/menu-current.css";
import "../src/components/BusinessUI.css";
import "./business-ui.css";

initializeTheme();

type GridRow = { id: number; name: string; quantity: number };
const gridRows: GridRow[] = Array.from({ length: 12 }, (_, i) => ({
  id: i,
  name: `Artigo de teste ${i + 1}`,
  quantity: (i + 1) * 3,
}));

/** Grelha comum: pesquisa, filtro próprio, exportação e estados distintos de carregamento, erro e vazio. */
function GridFixture() {
  const [group, setGroup] = useState("");
  return (
    <>
      <DataTable<GridRow>
        label="registos de teste"
        rows={gridRows.filter((row) => !group || (row.id % 2 === 0) === (group === "par"))}
        columns={[
          { id: "name", label: "Artigo", sortValue: (row) => row.name, render: (row) => row.name, exportValue: (row) => row.name },
          { id: "quantity", label: "Quantidade", numeric: true, sortValue: (row) => row.quantity, render: (row) => row.quantity, exportValue: (row) => row.quantity },
        ]}
        rowKey={(row) => String(row.id)}
        searchText={(row) => row.name}
        exportName="registos-de-teste.csv"
        toolbar={
          <label>
            Grupo
            <select value={group} onChange={(event) => setGroup(event.target.value)}>
              <option value="">Todos</option>
              <option value="par">Pares</option>
              <option value="impar">Ímpares</option>
            </select>
          </label>
        }
      />
      <DataTable<GridRow> label="grelha a carregar" rows={[]} loading columns={[]} rowKey={(row) => String(row.id)} searchText={(row) => row.name} />
      <DataTable<GridRow> label="grelha com falha" rows={[]} error="Falha de teste na consulta." columns={[]} rowKey={(row) => String(row.id)} searchText={(row) => row.name} />
    </>
  );
}

const tones: BusinessTone[] = [
  "neutral",
  "info",
  "success",
  "warning",
  "danger",
  "suggestion",
  "accent",
];
function Catalog() {
  const [filter, setFilter] = useState("all"),
    [tab, setTab] = useState("overview"),
    [events, setEvents] = useState<string[]>([]),
    [dialog, setDialog] = useState(false);
  const record = (value: string) =>
    setEvents((previous) => [...previous, value]);
  return (
    <div className="workspace ui-catalog">
      <main>
        <header className="ui-catalog-header">
          <div>
            <p className="eyebrow">IAZELA · Fase B</p>
            <h1>Componentes comuns</h1>
            <p>
              Catálogo de validação — valores fictícios de teste, sem ligação à
              empresa.
            </p>
          </div>
          <ThemeSettings compact />
        </header>
        <ModuleOverview
          title="Cartões e indicadores"
          description="Valor, estado e ação separados; ausência de informação não equivale a zero."
        >
          <div className="ui-catalog-grid">
            {tones.map((tone, index) => (
              <KpiCard
                key={tone}
                label={`Indicador ${tone}`}
                value={index === 0 ? 0 : index * 3}
                description="Dados fictícios · loja de teste"
                icon={<ShoppingCart size={24} />}
                tone={tone}
                onOpen={() => record(`indicador:${tone}`)}
              />
            ))}
            <KpiCard
              label="Valor em falta"
              value={null}
              description="Origem sem valor disponível"
            />
            {(["loading", "empty", "error", "restricted"] as const).map(
              (state) => (
                <KpiCard
                  key={state}
                  label={`Estado ${state}`}
                  value={999}
                  state={state}
                  onOpen={() => record(state)}
                  description="Não substituir indisponibilidade por um zero."
                />
              ),
            )}
          </div>
        </ModuleOverview>
        <section className="panel">
          <h2>Compatibilidade dos módulos existentes</h2>
          <div className="doc-overview-grid">
            {tones.map((tone) => (
              <MetricCard
                key={tone}
                label={`Resumo ${tone}`}
                value="12"
                tone={tone}
                description="Definição do indicador e fonte preservadas."
              />
            ))}
          </div>
        </section>
        <section className="panel">
          <h2>Estados com texto e ícone</h2>
          <div className="ui-catalog-row">
            {tones.map((tone) => (
              <StatusBadge key={tone} tone={tone}>
                Estado {tone}
              </StatusBadge>
            ))}
          </div>
          <div className="ui-catalog-row">
            {(
              [
                "apenas_local",
                "sincronizado",
                "pendente",
                "conflito",
                "erro",
              ] as const
            ).map((state) => (
              <SyncStatus key={state} state={state} />
            ))}
          </div>
        </section>
        <section className="panel">
          <h2>Filtros e seleção</h2>
          <QuickFilters
            label="Filtros de teste"
            value={filter}
            onChange={setFilter}
            options={[
              { id: "all", label: "Todos", count: 0 },
              { id: "pending", label: "Pendentes", count: 3 },
              { id: "blocked", label: "Indisponível", disabled: true },
              { id: "ready", label: "Prontos", count: 2 },
            ]}
          />
          <p role="status">Filtro atual: {filter}</p>
          <ModuleTabs
            label="Áreas de teste"
            value={tab}
            onChange={setTab}
            tabs={[
              { id: "overview", label: "Resumo" },
              { id: "detail", label: "Detalhe" },
            ]}
          />
        </section>
        <section className="panel">
          <h2>Objetivos e progresso</h2>
          <div className="ui-catalog-grid">
            <ProgressGoal
              label="Mínimo configurado"
              value={18550}
              target={25000}
              valueText="185,50 € / 250,00 €"
              detail="Valores fictícios. Base e moeda fornecidas pelo módulo."
              tone="warning"
            />
            <ProgressGoal
              label="Objetivo atingido"
              value={300}
              target={250}
              tone="success"
            />
            <ProgressGoal
              label="Objetivo por configurar"
              value={10}
              target={null}
            />
            <ProgressGoal label="Cálculo pendente" value={null} target={250} />
            <ProgressGoal label="Limite zero" value={0} target={0} />
          </div>
        </section>
        <section className="panel">
          <h2>Etapas do processo</h2>
          <ProcessSteps
            label="Processo de teste"
            current="needs"
            steps={[
              {
                id: "client",
                label: "Cliente",
                onSelect: () => record("cliente"),
              },
              {
                id: "needs",
                label: "Necessidades",
                tone: "warning",
                onSelect: () => record("necessidades"),
              },
              {
                id: "supplier",
                label: "Fornecedor",
                tone: "suggestion",
                onSelect: () => record("fornecedor"),
              },
              {
                id: "receipt",
                label: "Receção",
                tone: "success",
                disabled: true,
                onSelect: () => record("rececao"),
              },
              { id: "delivery", label: "Entrega", tone: "accent" },
            ]}
          />
        </section>
        <section className="panel">
          <h2>Sugestões com origem e revisão</h2>
          <div className="ui-catalog-grid">
            <ContextSuggestion
              title="Rever composição"
              evidence={[
                "Dados fictícios: mercadorias de 185,50 €.",
                "Mínimo contratual de teste: 250,00 €; faltam 64,50 €.",
              ]}
              actionLabel="Rever sugestão"
              onReview={() => record("rever")}
            >
              A sugestão explica os dados antes de propor uma ação.
            </ContextSuggestion>
            <ContextSuggestion
              title="Origem por confirmar"
              evidence={[]}
              actionLabel="Rever sem evidência"
              onReview={() => record("sem-evidencia")}
            >
              O componente não apresenta uma recomendação sem origem como uma
              ação pronta a executar.
            </ContextSuggestion>
          </div>
        </section>
        <section className="panel">
          <h2>Grelha e formulário existentes</h2>
          <GridFixture />
          <ManagedForm
            onSubmit={(event) => {
              event.preventDefault();
              record("formulario");
            }}
          >
            <label>
              Campo de teste
              <input name="field" defaultValue="Branco no tema claro" />
            </label>
            <div className="ui-catalog-row">
              <label>
                <input type="checkbox" name="column" defaultChecked />
                Mostrar coluna
              </label>
              <ActionButton type="submit" variant="primary">
                Guardar teste
              </ActionButton>
              <ActionButton
                type="button"
                variant="secondary"
                onClick={() => setDialog(true)}
              >
                Abrir janela de teste
              </ActionButton>
            </div>
          </ManagedForm>
        </section>
        <output aria-label="Ações executadas no teste">
          {events.join(" · ") || "Nenhuma ação executada"}
        </output>
        {dialog && (
          <AppDialog
            aria-label="Janela de teste"
            onRequestClose={() => setDialog(false)}
          >
            <CloseButton onClick={() => setDialog(false)} />
            <h2>Janela de teste</h2>
            <ManagedForm onSubmit={(event) => event.preventDefault()}>
              <label>
                Observações
                <input name="notes" defaultValue="Original" />
              </label>
            </ManagedForm>
            <ActionButton type="button" onClick={() => setDialog(false)}>
              Cancelar
            </ActionButton>
          </AppDialog>
        )}
      </main>
    </div>
  );
}
createRoot(document.getElementById("root")!).render(
  <>
    <FeedbackHost />
    <Catalog />
  </>,
);
