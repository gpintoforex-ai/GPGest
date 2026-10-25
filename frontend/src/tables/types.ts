// Contrato das rotas /api/tables (ver backend/app/table_framework.py).

export type FieldType =
  | "text" | "textarea" | "number" | "decimal" | "boolean" | "select" | "store"
  | "category" | "payment_term" | "payment_method" | "email" | "nif" | "color" | "icon";

export type FieldSpec = {
  name: string;
  label: string;
  type: FieldType;
  required: boolean;
  max_length: number | null;
  min_length: number | null;
  minimum: number | null;
  maximum: number | null;
  options: { value: string; label: string; active?: boolean }[];
  column: boolean;
  create_only: boolean;
  create_required?: boolean;
  tab?: string;
  hidden?: boolean;
  group?: string;
  span?: number | null;
  help: string;
};

export type ErpOperationKey = "read" | "create" | "update" | "activate" | "deactivate" | "delete";
export type ErpOperation = { label: string; supported: boolean; reason: string };
export type ConflictPolicy = "perguntar" | "erp" | "local";

export type TableInfo = {
  key: string;
  title: string;
  singular: string;
  feminine: boolean;
  description: string;
  fields: FieldSpec[];
  extra_columns: { name: string; label: string }[];
  code_field: string | null;
  description_field: string;
  has_active: boolean;
  allow_create: boolean;
  allow_remove: boolean;
  remove_reason: string;
  permissions: Record<"view" | "create" | "edit" | "status" | "remove" | "import" | "export" | "erp_remove", boolean>;
  erp: {
    integrated: boolean;
    available: boolean;
    reason: string;
    provider?: string;
    operations: Partial<Record<ErpOperationKey, ErpOperation>>;
    managed_fields?: string[];
    conflict_policy?: ConflictPolicy;
    import_options?: FieldSpec[];
  };
  local_only_reason: string;
};

export type SyncState = "apenas_local" | "sincronizado" | "pendente" | "conflito" | "erro";

export type TableRow = {
  id: string;
  values: Record<string, any>;
  labels: Record<string, string>;
  code: string | null;
  description: string;
  active: boolean;
  origin: "local" | "erp";
  erp_id: string;
  erp_code: string;
  sync_state: SyncState;
  sync_label: string;
  last_sync_at: string | null;
  last_error: string;
};

export type RecordDetails = TableRow & {
  dependencies: { label: string; count: number }[];
  managed_fields: string[];
  // Regras próprias da tabela que impedem remover só da aplicação ou de ambos os lados.
  remove_blocked?: { app: string | null; both: string | null };
};

export type Direction = "import" | "export" | "sync";

export type PlanItem = {
  key: string;
  action: string;
  action_label: string;
  summary: "criar" | "atualizar" | "desativar" | "remover" | "ignorar" | "conflito";
  label: string;
  reason: string;
  local_id: string | null;
  external_id: string | null;
  changes: { field: string; label: string; local: unknown; erp: unknown }[];
  // Proposta opcional (registo antigo só da IAZELA): desmarcada por omissão.
  optional: boolean;
};

export type Plan = {
  direction: Direction;
  ids: string[];
  options: Record<string, unknown>;
  token: string;
  summary: Record<PlanItem["summary"], number>;
  items: PlanItem[];
  conflict_policy: ConflictPolicy;
};

export type SyncResult = {
  key: string;
  label: string;
  action: string;
  action_label: string;
  ok: boolean;
  message: string;
  retry: boolean;
};

/** Sincronização em segundo plano: progresso e, no fim, o resultado por registo. */
export type SyncRun = {
  id: string;
  status: "em_curso" | "concluida" | "falhou";
  total: number;
  done: number;
  current: string;
  ok: number;
  failed: number;
  error: string;
  started_at: string;
  finished_at: string | null;
  results: SyncResult[];
  retry: string[];
};

/** Dados da aplicação usados por campos de referência (estabelecimentos, rubricas). */
export type TableContext = {
  stores: { id: string; name: string }[];
  categories: { id: string; name: string; icon?: string; color?: string; active?: boolean }[];
};

export const DIRECTION_LABELS: Record<Direction, { title: string; button: string; confirm: string; destination: string }> = {
  import: { title: "Importar do ERP", button: "Importar do ERP", confirm: "Confirmar importação", destination: "Afeta apenas a IAZELA: cria e atualiza registos locais a partir do ERP." },
  export: { title: "Enviar para o ERP", button: "Enviar para o ERP", confirm: "Confirmar envio para o ERP", destination: "Afeta o ERP: cria ou atualiza no ERP os registos da IAZELA." },
  sync: { title: "Sincronizar", button: "Sincronizar", confirm: "Confirmar sincronização", destination: "Recebe as alterações do ERP e envia as da IAZELA. Compara os dois lados e aplica só as alterações escolhidas." },
};

/** Concordância de género: «Novo banco» / «Nova caixa», «removido» / «removida». */
export function gendered(table: Pick<TableInfo, "feminine">, masculine: string) {
  if (!table.feminine) return masculine;
  if (masculine === "Novo") return "Nova";
  if (masculine === "o") return "a";
  if (masculine === "do") return "da";
  if (masculine === "lo") return "la";
  return masculine.endsWith("o") ? masculine.slice(0, -1) + "a" : masculine;
}

export function formatDateTime(value: string | null) {
  if (!value) return "—";
  return new Intl.DateTimeFormat("pt-PT", { dateStyle: "short", timeStyle: "short", timeZone: "Europe/Lisbon" }).format(new Date(value));
}

export function displayValue(field: FieldSpec | undefined, value: unknown, label?: string) {
  if (label) return label;
  if (value === null || value === undefined || value === "") return "—";
  if (field?.type === "boolean") return value ? "Sim" : "Não";
  if (field?.type === "decimal") return `${String(value).replace(".", ",")}${field.name.endsWith("percent") ? " %" : ""}`;
  if (field?.type === "select" || field?.type === "icon") return field.options.find(o => o.value === value)?.label || String(value);
  return String(value);
}
