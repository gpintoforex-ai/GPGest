import { useEffect, useState } from "react";
import { api } from "../lib/api";
import { ActionButton, AppDialog, Notice, confirmAction } from "../components/InteractionUI";
import Processing from "./Processing";
import { gendered, type RecordDetails, type TableInfo, type TableRow } from "./types";

type Scope = "app" | "erp" | "erp_deactivate" | "both";

/** Remoção com destino explícito: aplicação, ERP ou ambos. Nunca propaga para o ERP sem escolha. */
export default function RemoveDialog({ table, row, onClose, onDone, onDeactivate }: {
  table: TableInfo;
  row: TableRow;
  onClose: () => void;
  onDone: (message: string) => void;
  onDeactivate: (row: TableRow) => void;
}) {
  const [details, setDetails] = useState<RecordDetails | null>(null);
  const [scope, setScope] = useState<Scope | "">("");
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const label = row.code ? `${row.code} — ${row.description}` : row.description;

  useEffect(() => {
    api(`/tables/${table.key}/records/${encodeURIComponent(row.id)}/details`).then(setDetails).catch(e => setError(e.message));
  }, [table, row]);

  const ops = table.erp.operations;
  const linked = !!details?.erp_id;
  const dependencies = details?.dependencies || [];
  const appReason = !table.allow_remove ? table.remove_reason || "Esta tabela não permite remover registos."
    : !table.permissions.remove ? "O seu perfil não permite remover."
    : dependencies.length ? "Tem registos dependentes."
    : details?.remove_blocked?.app || "";
  const bothRule = details?.remove_blocked?.both || "";
  const erpDelete = !!ops.delete?.supported;
  const erpDeactivate = !!ops.deactivate?.supported;
  const erpAction: "delete" | "deactivate" = erpDelete ? "delete" : "deactivate";
  const erpReason = !table.erp.integrated ? "" : !linked ? "Não está ligado a nenhum registo do ERP."
    : !table.permissions.erp_remove ? "O seu perfil não permite remover no ERP."
    : !erpDelete && !erpDeactivate ? (ops.delete?.reason || "O conector não permite eliminar nem desativar.") : "";
  const erpLabel = erpAction === "delete" ? "Remover do ERP" : "Desativar no ERP";
  const bothAppReason = appReason === details?.remove_blocked?.app && details?.remove_blocked?.app ? bothRule : appReason;
  const options: { value: Scope; title: string; detail: string; reason: string }[] = [
    { value: "app", title: "Remover da aplicação", detail: linked ? "Remove o registo da IAZELA e a ligação. O registo no ERP mantém-se." : "Remove o registo da IAZELA.", reason: appReason },
    ...(table.erp.integrated ? [
      { value: "erp" as Scope, title: erpLabel, detail: erpAction === "delete" ? "Elimina o registo no ERP. O registo da IAZELA mantém-se, como «Apenas local»." : `O ERP não permite eliminar (${ops.delete?.reason || "operação não suportada"}). O registo fica inativo no ERP e mantém-se na IAZELA.`, reason: erpReason },
      ...(erpDelete && erpDeactivate ? [{ value: "erp_deactivate" as Scope, title: "Desativar no ERP", detail: "Mantém o registo no ERP, inativo (por exemplo, quando já tem documentos). O registo da IAZELA mantém-se.", reason: erpReason }] : []),
      { value: "both" as Scope, title: erpAction === "delete" ? "Remover de ambos" : "Remover da aplicação e desativar no ERP", detail: "Aplica as duas operações. O ERP é tratado primeiro; se falhar, nada é removido na IAZELA.", reason: bothAppReason || erpReason },
    ] : []),
  ];
  const destination = scope === "app" ? "IAZELA (aplicação)" : scope === "erp" || scope === "erp_deactivate" ? "ERP" : "IAZELA e ERP";

  async function confirm() {
    if (!scope) return;
    const chosen = options.find(o => o.value === scope)!;
    if (!(await confirmAction({
      title: `${chosen.title}?`,
      message: <>Registo: <b>{label}</b><br />Destino: <b>{destination}</b><br />{chosen.detail}{scope !== "erp_deactivate" ? " Esta ação não pode ser anulada." : ""}</>,
      confirmLabel: chosen.title,
      tone: "danger",
    }))) return;
    setBusy(true); setError("");
    try {
      const body = scope === "erp_deactivate" ? { scope: "erp", erp_action: "deactivate" } : { scope, erp_action: erpAction };
      const result = await api(`/tables/${table.key}/records/${encodeURIComponent(row.id)}/remove`, { ...body, confirmed: true, confirm_demo: true });
      onDone([result.removed ? `«${label}» ${gendered(table, "removido")} da aplicação.` : "", result.erp || ""].filter(Boolean).join(" "));
    } catch (e) { setError((e as Error).message); }
    finally { setBusy(false); }
  }

  return <AppDialog autoOpen busy={busy} initialFocus="cancel" className="modal table-dialog" onRequestClose={onClose} aria-labelledby="table-remove-title">
    <div className="panel-title"><h2 id="table-remove-title">Remover {table.singular}</h2></div>
    <p>Registo: <b>{label}</b></p>
    {error && <Notice className="error" role="alert">{error}</Notice>}
    {!details && !error && <Processing label="A verificar dependências" />}
    {busy && <Processing label="A remover" />}
    {details && <>
      {dependencies.length > 0 && <Notice className="warning" role="status">
        <p><b>Não pode ser removido da aplicação:</b> tem {dependencies.map(d => `${d.count} ${d.label}`).join(", ")}. O histórico é preservado.</p>
        {table.has_active && row.active && table.permissions.status && <p>Pode desativá-{gendered(table, "lo")} para impedir novas utilizações. <ActionButton type="button" disabled={busy} onClick={() => onDeactivate(row)}>Desativar</ActionButton></p>}
      </Notice>}
      <fieldset className="table-remove-options" disabled={busy}>
        <legend>Onde remover</legend>
        {options.map(o => <label key={o.value} className={`table-remove-option${o.reason ? " is-disabled" : ""}`}>
          <input type="radio" name="scope" value={o.value} checked={scope === o.value} disabled={!!o.reason} onChange={() => setScope(o.value)} />
          <span><b>{o.title}</b><small>{o.reason ? `Indisponível: ${o.reason}` : o.detail}</small></span>
        </label>)}
      </fieldset>
    </>}
    <div className="ui-dialog-actions">
      <ActionButton type="button" disabled={busy} onClick={onClose}>Cancelar</ActionButton>
      <ActionButton type="button" className="danger" disabled={busy || !scope} onClick={confirm}>{busy ? "A remover…" : scope ? options.find(o => o.value === scope)!.title : "Remover"}</ActionButton>
    </div>
  </AppDialog>;
}
