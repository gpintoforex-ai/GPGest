import { useEffect, useRef, useState } from "react";
import { CheckCircle2, Link2, MinusCircle, PlusCircle, RefreshCw } from "lucide-react";
import { api } from "./lib/api";
import { ActionButton, AppDialog, Notice } from "./components/InteractionUI";
import Processing from "./tables/Processing";
import "./tables/TablePage.css";

type Row = Record<string, any>;

/**
 * Sincronizar artigos (como nas tabelas): recebe o catálogo da Wintouch e, com pré-visualização,
 * liga as fichas locais pelo código e cria as que faltam. O stock continua registado na IAZELA.
 */
export default function InventorySync({ onClose, onDone }: { onClose: () => void; onDone: (message: string) => void }) {
  const [step, setStep] = useState<"catalogue" | "plan" | "saving">("catalogue");
  const [imported, setImported] = useState<Row | null>(null);
  const [plan, setPlan] = useState<Row | null>(null);
  const [createMissing, setCreateMissing] = useState(true);
  const [error, setError] = useState("");

  // Um só pedido ao ERP por abertura (o modo estrito do React monta o efeito duas vezes).
  const started = useRef(false);
  useEffect(() => {
    if (started.current) return;
    started.current = true;
    (async () => {
      try {
        const run: Row = await api("/integrations/wintouch/sync", { resource: "products", confirm_demo: true });
        setImported(run);
        if (run.status !== "concluida") {
          setError(run.error_message || "A importação do catálogo falhou. Nada foi alterado nas fichas.");
          return;
        }
        setPlan(await api("/stock/erp-products/link-by-code", {}));
        setStep("plan");
      } catch (e) {
        setError((e as Error).message);
      }
    })();
  }, []);

  const changes = plan ? plan.link + (createMissing ? plan.create : 0) : 0;

  async function confirm() {
    setStep("saving");
    setError("");
    try {
      const r: Row = await api("/stock/erp-products/link-by-code", { confirmed: true, create_missing: createMissing });
      onDone(`Artigos sincronizados: ${r.linked} fichas associadas ao ERP; ${r.created} fichas criadas.`);
    } catch (e) {
      setError((e as Error).message);
      setStep("plan");
    }
  }

  return <AppDialog autoOpen busy={step === "saving"} className="modal table-dialog table-sync-dialog" onRequestClose={onClose} aria-labelledby="inventory-sync-title">
    <div className="panel-title"><h2 id="inventory-sync-title">Sincronizar — Artigos</h2></div>
    <Notice className="info" role="status"><b>Destino:</b> recebe o catálogo da Wintouch (código, nome, estado e preço) e liga-o às fichas da IAZELA. O stock continua registado aqui.</Notice>
    {error && <Notice className="error" role="alert">{error}</Notice>}
    {step === "catalogue" && !error && <Processing label="A receber o catálogo da Wintouch" />}
    {imported && imported.status === "concluida" && <p className="footnote">
      Catálogo recebido: {imported.read_count} artigos ({imported.inserted_count} novos, {imported.updated_count} atualizados). Nada mais é alterado antes de confirmar.
    </p>}
    {plan && <>
      <ul className="table-sync-summary" aria-label="Resumo">
        <li className={plan.link ? "has-items" : ""}><Link2 size={16} aria-hidden="true" /><b>{plan.link}</b> A associar</li>
        <li className={plan.create ? "has-items" : ""}><PlusCircle size={16} aria-hidden="true" /><b>{plan.create}</b> A criar</li>
        <li className={plan.already_linked ? "has-items" : ""}><CheckCircle2 size={16} aria-hidden="true" /><b>{plan.already_linked}</b> Já associados</li>
        <li className={plan.ambiguous ? "has-items" : ""}><MinusCircle size={16} aria-hidden="true" /><b>{plan.ambiguous}</b> Ignorados</li>
      </ul>
      <ul className="inventory-sync-notes">
        <li><b>A associar:</b> fichas locais com o mesmo código de um artigo da Wintouch. Mantêm o stock e o histórico; código, nome e preço passam a vir do ERP.</li>
        {plan.ambiguous > 0 && <li><b>Ignorados:</b> códigos repetidos ou já usados por outra ficha: {plan.ambiguous_codes.join(", ")}{plan.ambiguous > plan.ambiguous_codes.length ? "…" : ""}</li>}
        <li><b>{plan.local_only}</b> fichas só locais (sem artigo na Wintouch) ficam como estão.</li>
      </ul>
      {plan.create > 0 && <label className={`table-sync-select${createMissing ? " is-on" : ""}`}>
        <input type="checkbox" aria-label="Criar fichas em falta" checked={createMissing} disabled={step === "saving"} onChange={e => setCreateMissing(e.target.checked)} />
        <b>Criar fichas para os {plan.create} artigos ativos da Wintouch sem ficha</b>
      </label>}
      {!changes && <p>Não há alterações a aplicar.</p>}
    </>}
    {step === "saving" && <Processing label="A sincronizar" />}
    <div className="ui-dialog-actions">
      <ActionButton type="button" disabled={step === "saving"} onClick={onClose}>Fechar</ActionButton>
      {plan && <ActionButton type="button" className="primary" disabled={step === "saving" || !changes} onClick={confirm}>
        <RefreshCw size={16} aria-hidden="true" />Confirmar sincronização ({changes})
      </ActionButton>}
    </div>
  </AppDialog>;
}
