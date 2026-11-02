import { useEffect, useRef, useState } from "react";
import { RefreshCw } from "lucide-react";
import { api } from "./lib/api";
import { ActionButton, AppDialog, Notice } from "./components/InteractionUI";
import { Field } from "./components/Form";
import DatePresets from "./components/DatePresets";
import { currentMonth } from "./RecordList";
import { StockOutcome } from "./WintouchSales";
import "./StockSalesSync.css";

type Row = Record<string, any>;
type Run = { id: string; status: string; stage: string; done: number; total: number; error: string; result: Row | null };

/** Progresso real da sincronização no servidor: etapa, feitos e total (total 0 = ainda por contar). */
function Progress({ run }: { run: Run }) {
  const percent = run.total ? Math.round((run.done / run.total) * 100) : null;
  return (
    <section className="stock-sync-progress" aria-label="Progresso da sincronização">
      <div className="stock-sync-progress-head">
        <b>{run.stage || "A preparar"}…</b>
        {percent !== null && <span>{percent}%</span>}
      </div>
      <div
        className={`stock-sync-progress-bar${percent === null ? " is-indeterminate" : ""}`}
        role="progressbar"
        aria-label="Sincronização de movimentos"
        aria-valuemin={0}
        aria-valuemax={run.total || undefined}
        aria-valuenow={run.total ? run.done : undefined}
        aria-valuetext={run.total ? `${run.done} de ${run.total}` : run.stage}
      >
        <span style={percent === null ? undefined : { width: `${percent}%` }} />
      </div>
      <p className="footnote">
        {run.total ? `${run.done} de ${run.total} · ` : ""}Pode fechar esta janela: a sincronização continua no servidor e o progresso volta a aparecer ao reabrir.
      </p>
    </section>
  );
}

/**
 * Sincroniza as vendas Wintouch de um período (documentos, pagamentos e, com o controlo de stock
 * ativo, as saídas de stock das linhas). Corre em segundo plano; o progresso é consultado a cada segundo.
 */
export default function StockSalesSync({ stores, warehouses, onClose, onDone }: {
  stores: Row[];
  warehouses: Row[];
  onClose: () => void;
  onDone: () => void;
}) {
  const initial = currentMonth();
  const [from, setFrom] = useState(initial.from);
  const [to, setTo] = useState(initial.to);
  const [store, setStore] = useState(stores[0]?.id || "");
  const [warehouse, setWarehouse] = useState("");
  const [demo, setDemo] = useState(false);
  const [confirmed, setConfirmed] = useState(false);
  const [run, setRun] = useState<Run | null>(null);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const following = useRef("");
  const storeWarehouses = warehouses.filter((w) => w.store_id === store && w.active !== false);
  const running = run?.status === "em_curso";
  // Só uma execução concluída mostra resultado; uma falha mostra apenas o motivo.
  const result = run?.status === "concluida" ? run.result : null;
  const failed = run?.status === "falhou";

  function follow(current: Run) {
    following.current = current.id;
    setRun(current);
    const poll = async () => {
      if (following.current !== current.id) return;
      try {
        const next: Run = await api(`/integrations/wintouch/sales/runs/${current.id}`);
        if (following.current !== current.id) return;
        setRun(next);
        if (next.status === "em_curso") { window.setTimeout(poll, 1000); return; }
        following.current = "";
        if (next.status === "falhou") setError(next.error || "A sincronização não terminou.");
        onDone();
      } catch {
        window.setTimeout(poll, 3000); // Falha momentânea de rede: volta a consultar, sem repetir a execução.
      }
    };
    window.setTimeout(poll, 1000);
  }

  useEffect(() => {
    // Ao abrir: retoma o acompanhamento de uma sincronização ainda em curso.
    api("/integrations/wintouch/sales/running").then((current: Run | null) => { if (current?.id) follow(current); }).catch(() => undefined);
    api("/bootstrap").then((b) => setDemo(b.mode === "demonstracao")).catch(() => undefined);
    return () => { following.current = ""; };
  }, []);

  async function start() {
    setBusy(true);
    setError("");
    try {
      follow(await api("/integrations/wintouch/sales/start", {
        store_id: store, start_date: from, end_date: to, warehouse_id: warehouse || null, confirm_demo: confirmed,
      }));
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  }

  return (
    <AppDialog autoOpen busy={busy} className="modal stock-sync-dialog" onRequestClose={onClose} aria-labelledby="stock-sync-title">
      <div className="panel-title"><h2 id="stock-sync-title">Sincronizar movimentos Wintouch</h2></div>
      <p className="footnote">
        Importa os documentos do período: vendas, pagamentos e saldos, e os documentos que a Wintouch marca como
        movimentando stock (compras, entradas, saídas, transferências e devoluções). Com o controlo de stock ativo, cada
        linha de artigo gera o movimento de stock correspondente, uma vez e com o lote indicado pela Wintouch.
      </p>
      {!running && !result && !failed && (
        <div className="stock-sync-form">
          <DatePresets from={from} to={to} onSelect={(start, end) => { setFrom(start); setTo(end); }} disabled={busy} />
          <div className="stock-sync-fields">
            <Field label="De"><input type="date" value={from} max={to} onChange={(e) => setFrom(e.target.value)} disabled={busy} /></Field>
            <Field label="Até"><input type="date" value={to} min={from} onChange={(e) => setTo(e.target.value)} disabled={busy} /></Field>
            {stores.length > 1 && (
              <Field label="Loja">
                <select value={store} onChange={(e) => { setStore(e.target.value); setWarehouse(""); }} disabled={busy}>
                  {stores.map((s) => <option key={s.id} value={s.id}>{s.name}</option>)}
                </select>
              </Field>
            )}
            {storeWarehouses.length > 1 && (
              <Field label="Armazém das saídas">
                <select value={warehouse} onChange={(e) => setWarehouse(e.target.value)} disabled={busy}>
                  <option value="">Pela ligação dos armazéns ao ERP</option>
                  {storeWarehouses.map((w) => <option key={w.id} value={w.id}>{w.name}</option>)}
                </select>
              </Field>
            )}
          </div>
          {demo && (
            <label className="checkbox">
              <input type="checkbox" checked={confirmed} onChange={(e) => setConfirmed(e.target.checked)} disabled={busy} />
              Confirmo a importação Wintouch para esta empresa de demonstração.
            </label>
          )}
          <p className="footnote">Período máximo: um ano. Repetir um período não duplica documentos nem movimentos de stock.</p>
        </div>
      )}
      {error && <Notice className="error" role="alert">{error}</Notice>}
      {running && <Progress run={run!} />}
      {result && (
        <>
          <Notice className="success" role="status">
            Sincronização concluída: {result.inserted_count} registo(s) novo(s) e {result.updated_count} atualizado(s).
          </Notice>
          {result.stock && <StockOutcome stock={result.stock} />}
        </>
      )}
      <div className="actions">
        <ActionButton type="button" onClick={onClose}>
          {running ? "Fechar (continua em segundo plano)" : "Fechar"}
        </ActionButton>
        {(result || failed) && (
          <ActionButton type="button" onClick={() => { setRun(null); setError(""); }}>
            {failed ? "Voltar a tentar" : "Sincronizar outro período"}
          </ActionButton>
        )}
        {!running && !result && !failed && (
          <ActionButton
            type="button"
            className="primary"
            disabled={busy || !store || !from || !to || from > to || (demo && !confirmed)}
            onClick={() => void start()}
          >
            <RefreshCw size={16} aria-hidden="true" />
            {busy ? "A iniciar…" : "Sincronizar"}
          </ActionButton>
        )}
      </div>
    </AppDialog>
  );
}
