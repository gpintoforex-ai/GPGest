import { ModuleTabs, useWorkspaceTab } from "./components/WorkspaceUI";
import DatePresets from "./components/DatePresets";
import { ActionButton, AppDialog, Notice } from "./components/InteractionUI";
import { useEffect, useRef, useState } from "react";
import { api } from "./lib/api";
import Purchases from "./Purchases";
import AssistantIdentity from "./components/AssistantIdentity";
import "./Reconciliation.css";
import BankResolution from "./BankResolution";

type Row = Record<string, any>;
const money = (n: number,currency="EUR") =>
  new Intl.NumberFormat("pt-PT", { style: "currency", currency }).format(
    n / 100,
  );
const key = (r: Row) => `${r.source}:${r.id}`;
const labels: Record<string, string> = {
  bank: "Banco",
  purchase: "Fatura",
  movement: "Movimento interno",
  wintouch: "Wintouch",
  payment: "Pagamento identificado",
  book: "Registo bancário da aplicação",
  pendente: "Pendente",
  parcial: "Parcial",
  reconciliado: "Reconciliado",
  pago: "Pago",
  ignorado: "Ignorado",
};
function cents(text: string) {
  const value = text.trim().replace(",", ".");
  if (!/^-?\d+(?:\.\d{1,2})?$/.test(value))
    throw new Error("Indique valores em euros com até duas casas decimais.");
  return Math.round(Number(value) * 100);
}

export default function Reconciliation({
  stores,
  store,
  manager,
  admin,
  accounts = [],
}: {
  stores: Row[];
  store: string;
  manager: boolean;
  admin: boolean;
  accounts?: Row[];
}) {
  const [tab, setTab] = useWorkspaceTab("reconciliacao", "movimentos", ["movimentos", "faturas", "historico"]);
  // Vista dentro de «Movimentos»: sugestões automáticas ou seleção manual nas duas colunas. Sem escolha
  // explícita (URL), abre nas sugestões quando existem e na seleção manual quando não há nenhuma.
  const [chosenView, setView] = useWorkspaceTab("reconciliacao-modo", "", ["", "sugestoes", "manual"]);
  const [suggestionLimit, setSuggestionLimit] = useState(10);
  const confirmPanel = useRef<HTMLElement>(null);
  const reviewSelection = () => setTimeout(() => confirmPanel.current?.scrollIntoView({ block: "start", behavior: "smooth" }), 60);
  const [documentsVisited, setDocumentsVisited] = useState(false);
  useEffect(() => { if (tab === "faturas") setDocumentsVisited(true); }, [tab]);
  const [data, setData] = useState<Row>({
    banks: [],
    internals: [],
    suggestions: [],
    card_suggestions: [],
    groups: [],
  });
  const [selected, setSelected] = useState<Record<string, string>>({});
  const [filter, setFilter] = useState("abertos");
  const [search, setSearch] = useState("");
  const [from, setFrom] = useState("");
  const [to, setTo] = useState("");
  const [account,setAccount]=useState(()=>{const saved=sessionStorage.getItem("iazela-bank-reconciliation-account")||"";return accounts.some(a=>a.id===saved&&(!store||a.store_id===store))?saved:"";});
  const [minimum,setMinimum]=useState(""),[maximum,setMaximum]=useState("");
  const [postCommission,setPostCommission]=useState(false);
  const [resolution,setResolution]=useState<Row|null>(null);
  const [kind, setKind] = useState("pagamento");
  const [commission, setCommission] = useState("0");
  const [difference, setDifference] = useState("0");
  const [reason, setReason] = useState("");
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const [busy, setBusy] = useState(false);
  const [loaded, setLoaded] = useState(false);
  const [action, setAction] = useState<Row | null>(null);
  const [actionReason, setActionReason] = useState("");
  const [limits, setLimits] = useState({ bank: 50, internal: 50 });
  const all: Row[] = [...data.banks, ...data.internals];
  async function load() {
    setData(await api("/reconciliation"));
    window.dispatchEvent(new Event("iazela-bank-changed"));
    setLoaded(true);
  }
  useEffect(() => {
    let active = true;
    api("/reconciliation")
      .then((r) => {
        if (active) {
          setData(r);
          setLoaded(true);
        }
      })
      .catch((e) => {
        if (active) setError(e.message);
      });
    return () => {
      active = false;
    };
  }, [tab]);
  useEffect(() => {
    if(store&&account&&accounts.find(a=>a.id===account)?.store_id!==store)setAccount("");
    setPostCommission(false);
    setSelected({});
    setNotice("");
    setAction(null);
  }, [store]);
  useEffect(() => {
    setLimits({ bank: 50, internal: 50 });
  }, [store, filter, search, from, to]);
  async function act(task: () => Promise<void>) {
    setBusy(true);
    setError("");
    setNotice("");
    try {
      await task();
    } catch (e) {
      setError(
        e instanceof Error
          ? e.message
          : "Não foi possível concluir a operação.",
      );
    } finally {
      setBusy(false);
    }
  }
  const chosen = all.filter((r) => selected[key(r)] !== undefined);
  const selectedCurrencies=new Set<string>(chosen.map((r:Row)=>r.currency||"EUR"));
  const selectedCurrency=selectedCurrencies.size===1?[...selectedCurrencies][0]:accounts.find(a=>a.id===account)?.currency||"EUR";
  const bankTotal = chosen
    .filter((r) => r.source === "bank")
    .reduce(
      (n, r) =>
        n +
        centsSafe(selected[key(r)]) *
          Math.sign(r.amount_cents),
      0,
    );
  const internalTotal = chosen
    .filter((r) => r.source !== "bank")
    .reduce(
      (n, r) =>
        n +
        centsSafe(selected[key(r)]) *
          Math.sign(r.amount_cents),
      0,
    );
  const unexplainedDifference=bankTotal-(internalTotal-centsSafe(commission)+centsSafe(difference));
  const selectionMoney=(value:number)=>selectedCurrencies.size>1?"Moedas diferentes":money(value,selectedCurrency);
  const visible = (rows: Row[]) =>
    rows.filter(
      (r) =>
        (!store || r.store_id === store) &&
        (!account || r.account_id === account || r.source !== "bank" && !r.account_id && r.store_id === accounts.find(a=>a.id===account)?.store_id) &&
        (!minimum || Math.abs(r.amount_cents)>=centsSafe(minimum)) &&
        (!maximum || Math.abs(r.amount_cents)<=centsSafe(maximum)) &&
        (!from || r.date >= from) &&
        (!to || r.date <= to) &&
        (filter === "todos" ||
          (filter === "abertos"
            ? ["pendente", "parcial"].includes(r.status)
            : r.status === filter)) &&
        `${r.description} ${r.entity} ${r.reference} ${r.account}`
          .toLocaleLowerCase("pt-PT")
          .includes(search.toLocaleLowerCase("pt-PT")),
    );
  const banks = visible(data.banks),
    internals = visible(data.internals);
  // Sugestões cujos movimentos estão todos visíveis com os filtros atuais (critério anterior, calculado uma vez).
  const visibleCards: Row[] = (data.card_suggestions || []).filter((s: Row) => banks.some((b) => b.id === s.bank_id)
    && s.lines.every((l: Row) => internals.some((i) => i.source === l.source && i.id === l.source_id)));
  const visibleSuggestions: Row[] = data.suggestions.filter((s: Row) => banks.some((b) => b.id === s.bank_id)
    && internals.some((i) => i.source === s.source && i.id === s.source_id));
  const view = chosenView || (!loaded || visibleCards.length + visibleSuggestions.length ? "sugestoes" : "manual");
  function toggle(r: Row) {
    setPostCommission(false);
    // Depósitos e transferências próprias são sempre associados como transferência.
    if (r.source !== "bank" && r.transfer && !(key(r) in selected)) setKind("transferencia");
    setSelected((previous) => {
      const next = { ...previous };
      if (key(r) in next) delete next[key(r)];
      else next[key(r)] = (r.available_cents / 100).toFixed(2);
      return next;
    });
  }
  function chooseSuggestion(s: Row) {
    const bank = all.find((r) => r.source === "bank" && r.id === s.bank_id);
    const internal = all.find(
      (r) => r.source === s.source && r.id === s.source_id,
    );
    if (!bank || !internal) return;
    const amount = (s.amount_cents / 100).toFixed(2);
    setSelected({ [key(bank)]: amount, [key(internal)]: amount });
    setView("manual"); reviewSelection();
    setCommission("0");
    setPostCommission(false);
    setDifference("0");
    setReason("");
    setKind(internal.transfer ? "transferencia" : "pagamento");
    setNotice(
      "Sugestão selecionada. Confira os movimentos e os valores antes de confirmar.",
    );
  }
  function chooseCard(s: Row) {
    setPostCommission(false);
    const next: Record<string, string> = {
      [`bank:${s.bank_id}`]: (s.net_cents / 100).toFixed(2),
    };
    for (const l of s.lines)
      next[`${l.source}:${l.source_id}`] = (l.amount_cents / 100).toFixed(2);
    setSelected(next);
    setKind("cartao");
    setView("manual"); reviewSelection();
    setCommission((s.commission_cents / 100).toFixed(2));
    setDifference("0");
    setReason("");
    setNotice(
      "Agrupamento selecionado. Confira a comissão no comprovativo bancário e justifique antes de confirmar.",
    );
  }
  const renderRows = (rows: Row[], side: "bank" | "internal") => (
    <>
      <div className="purchase-table">
        <table>
          <thead>
            <tr>
              <th>Selecionar</th>
              <th>Movimento</th>
              <th className="numeric">Valor / disponível</th>
              <th>Estado</th>
            </tr>
          </thead>
          <tbody>
            {rows.slice(0, limits[side]).map((r) => (
              <tr
                key={key(r)}
                className={
                  selected[key(r)] !== undefined
                    ? "reconciliation-selected"
                    : ""
                }
              >
                <td>
                  {manager&&r.source==="bank"&&r.available_cents>0&&<ActionButton disabled={busy} onClick={()=>setResolution(r)}>Resolver movimento em falta</ActionButton>}
                  <input
                    type="checkbox"
                    aria-label={`Selecionar ${r.description}`}
                    disabled={
                      !manager ||
                      busy ||
                      !r.available_cents ||
                      r.status === "ignorado"
                    }
                    checked={selected[key(r)] !== undefined}
                    onChange={() => toggle(r)}
                  />
                </td>
                <td>
                  <strong>{r.date.split("-").reverse().join("/")}</strong>
                  <br />
                  {r.description}
                  <br />
                  <small>
                    {r.entity} {r.reference}
                  </small>
                  <br />
                  <small>
                    {labels[r.source]} {r.account} {r.payment}
                  </small>
                </td>
                <td className="numeric">
                  {money(r.amount_cents,r.currency)}
                  <br />
                  <small>Associado: {money(r.associated_cents,r.currency)}</small>
                  <br />
                  <strong>Por reconciliar: {money(r.available_cents,r.currency)}</strong>
                  {selected[key(r)] !== undefined && (
                    <label>
                      Associar (€)
                      <input
                        aria-label={`Valor a associar: ${r.description}`}
                        inputMode="decimal"
                        disabled={busy}
                        value={selected[key(r)]}
                        onChange={(e) =>
                          setSelected({ ...selected, [key(r)]: e.target.value })
                        }
                      />
                    </label>
                  )}
                </td>
                <td>
                  <span className={`bank-state ${r.status==="reconciliado"?"bank-state-matched":"bank-state-pending"}`}>{r.status==="reconciliado"?"✓":"◷"} {labels[r.status]}</span>
                  {r.ignore_reason && <p>{r.ignore_reason}</p>}
                  {manager && (
                    <div>
                      {r.status === "ignorado" ? (
                        <ActionButton
                          disabled={busy}
                          onClick={() =>
                            act(async () => {
                              await api(
                                `/reconciliation/restore/${r.source}/${r.id}`,
                                {},
                              );
                              await load();
                            })
                          }
                        >
                          Reativar
                        </ActionButton>
                      ) : (
                        !r.associated_cents && (
                          <ActionButton
                            disabled={busy}
                            onClick={() => {
                              setAction({ type: "ignore", row: r });
                              setActionReason("");
                            }}
                          >
                            Ignorar
                          </ActionButton>
                        )
                      )}
                    </div>
                  )}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      {!rows.length && (
        <p>
          {loaded
            ? "Sem movimentos para os filtros selecionados."
            : "A carregar…"}
        </p>
      )}
      {rows.length > limits[side] && (
        <ActionButton
          onClick={() => setLimits({ ...limits, [side]: limits[side] + 50 })}
        >
          Mostrar mais ({rows.length - limits[side]})
        </ActionButton>
      )}
    </>
  );
  return (
    <>
      <div className="page-intro">
        <div>
          <h1>Reconciliação</h1>
          <p>
            Analise as sugestões automáticas de correspondência entre extratos,
            movimentos internos e faturas. Cada associação exige confirmação.
          </p>
        </div>
      </div>
      <AssistantIdentity assistant="nuno" />
      <ModuleTabs label="Áreas da reconciliação" value={tab} tabs={[{"id": "movimentos", "label": "Movimentos e sugestões"}, {"id": "faturas", "label": "Faturas e documentos"}, {"id": "historico", "label": "Histórico"}]} onChange={value => { setTab(value); setError(""); }} />
      {error && (
        <Notice className="error" role="alert">
          {error}
        </Notice>
      )}
      {notice && <Notice onDismiss={() => setNotice("")} tone={notice.includes("selecionad") ? "info" : "success"}>{notice}</Notice>}
      {(tab === "faturas" || documentsVisited) && (
        <div hidden={tab !== "faturas"}>
        <Purchases
          stores={stores}
          store={store}
          manager={manager}
          admin={admin}
          embedded
          onOpenMovements={() => setTab("movimentos")}
          onOpenHistory={() => setTab("historico")}
        />
        </div>
      )}
      {tab === "movimentos" && (
        <>
          <section className="panel reconciliation-filters">
            <div className="purchase-filters filter-bar">
              <label>Conta bancária<select aria-label="Conta bancária" value={account} onChange={e=>{setAccount(e.target.value);setSelected({});setLimits({bank:50,internal:50});}}><option value="">Todas as contas</option>{accounts.filter(a=>!store||a.store_id===store).map(a=><option value={a.id} key={a.id}>{a.bank} · {a.name} · {a.currency||"EUR"}</option>)}</select></label>
              <label>Valor absoluto mínimo<input inputMode="decimal" value={minimum} onChange={e=>{setMinimum(e.target.value);setLimits({bank:50,internal:50});}}/></label>
              <label>Valor absoluto máximo<input inputMode="decimal" value={maximum} onChange={e=>{setMaximum(e.target.value);setLimits({bank:50,internal:50});}}/></label>
              <label>
                Estado
                <select
                  value={filter}
                  onChange={(e) => setFilter(e.target.value)}
                >
                  <option value="abertos">Pendentes e parciais</option>
                  <option value="todos">Todos</option>
                  <option value="reconciliado">Reconciliados</option>
                  <option value="pago">Faturas com pagamento identificado</option>
                  <option value="ignorado">Ignorados</option>
                </select>
              </label>
              <DatePresets from={from} to={to} onSelect={(start, end) => { setFrom(start); setTo(end); }} />
              <label>
                Desde
                <input
                  type="date"
                  value={from}
                  onChange={(e) => setFrom(e.target.value)}
                />
              </label>
              <label>
                Até
                <input
                  type="date"
                  value={to}
                  onChange={(e) => setTo(e.target.value)}
                />
              </label>
              <label>
                Pesquisar
                <input
                  value={search}
                  onChange={(e) => setSearch(e.target.value)}
                  placeholder="Referência, entidade ou descrição"
                />
              </label>
            </div>
          </section>
          <ModuleTabs label="Modo de reconciliação" value={view} onChange={setView} tabs={[
            { id: "sugestoes", label: "Sugestões", count: visibleCards.length + visibleSuggestions.length },
            { id: "manual", label: "Seleção manual", count: chosen.length || undefined },
          ]} />
          {view === "manual" && <>
          <div className="reconciliation-columns">
            <section className="panel">
              <h2>Movimentos bancários ({banks.length})</h2>
              {renderRows(banks, "bank")}
            </section>
            <section className="panel">
              <h2>Movimentos da aplicação e documentos ({internals.length})</h2>
              <p>Os registos sem conta identificada podem ser associados após conferência; a associação não altera o seu saldo contabilístico nem atribui retroativamente uma conta.</p>
              {renderRows(internals, "internal")}
            </section>
          </div>
          {manager && chosen.length === 0 && <p className="reconciliation-hint">Selecione movimentos nas duas colunas (ou escolha uma sugestão) para rever e confirmar a associação.</p>}
          {manager && chosen.length > 0 && (
            <section className="panel reconciliation-confirm" ref={confirmPanel}>
              <h2>Confirmar associação</h2>
              <p>
                {chosen.length} movimentos selecionados · Bruto interno:{" "}
                <strong>{selectionMoney(internalTotal)}</strong> · Líquido bancário:{" "}
                <strong>{selectionMoney(bankTotal)}</strong>
                {" · Diferença selecionada: "}<strong>{selectionMoney(internalTotal-bankTotal)}</strong>
              </p>
              {chosen.length>0&&selectedCurrencies.size===1&&<p className={`bank-state ${unexplainedDifference?"bank-state-divergence":"bank-state-matched"}`}>{unexplainedDifference?"✕ Diferença por explicar":"✓ Valores equilibrados após comissão e diferença justificada"}: {selectionMoney(unexplainedDifference)}</p>}
              {selectedCurrencies.size>1&&<Notice tone="error">Selecione movimentos na mesma moeda. Os totais de moedas diferentes não são comparáveis.</Notice>}
              {chosen.length > 0 && (
                <p>
                  {chosen
                    .map(
                      (r) =>
                        `${labels[r.source]}: ${r.description} (${money(centsSafe(selected[key(r)]),r.currency)})`,
                    )
                    .join(" · ")}
                </p>
              )}
              <div className="purchase-filters filter-bar">
                <label>
                  Tratamento
                  <select
                    disabled={busy}
                    value={kind}
                    onChange={(e) => setKind(e.target.value)}
                  >
                    <option value="pagamento">Pagamento / recebimento</option>
                    <option value="cartao">
                      Recebimentos de cartão agrupados
                    </option>
                    <option value="transferencia">
                      Depósito próprio / transferência
                    </option>
                  </select>
                </label>
                <label>
                  Comissão (€)
                  <input
                    inputMode="decimal"
                    disabled={busy}
                    value={commission}
                    onChange={(e) => {setCommission(e.target.value);setPostCommission(false);}}
                  />
                </label>
                <label>
                  Diferença justificada (€)
                  <input
                    inputMode="decimal"
                    disabled={busy}
                    value={difference}
                    onChange={(e) => setDifference(e.target.value)}
                  />
                </label>
              </div>
              <p>
                Bruto − comissão + diferença = líquido. Para pagamentos
                parciais, ajuste o valor a associar em cada movimento.
              </p>
              <label className="bank-resolution-check"><input type="checkbox" checked={postCommission} disabled={busy||centsSafe(commission)<=0} onChange={e=>setPostCommission(e.target.checked)}/>Aprovo criar um movimento próprio de comissão na aplicação, na conta do extrato</label>
              <p className="footnote">A comissão indicada é uma explicação da diferença. Só cria um movimento com esta aprovação explícita. A reconciliação, por si só, preserva os saldos registados.</p>
              <label>
                Justificação
                <input
                  disabled={busy}
                  value={reason}
                  maxLength={2000}
                  onChange={(e) => setReason(e.target.value)}
                  placeholder="Obrigatória para comissões, diferenças e transferências (dispensada em depósitos registados)"
                />
              </label>
              <div className="actions">
                <ActionButton
                  className="primary"
                  disabled={busy || chosen.length < 2 || selectedCurrencies.size > 1}
                  onClick={() =>
                    act(async () => {
                      const lines = chosen.map((r) => ({
                        source: r.source,
                        source_id: r.id,
                        amount_cents: cents(selected[key(r)]),
                      }));
                      await api("/reconciliation/confirm", {
                        lines,
                        kind,
                        commission_cents: cents(commission),
                        difference_cents: cents(difference),
                        reason,
                        post_commission: postCommission,
                      });
                      setSelected({});
                      setCommission("0");
                      setPostCommission(false);
                      setDifference("0");
                      setReason("");
                      await load();
                      setNotice(
                        "Reconciliação confirmada e registada na auditoria.",
                      );
                    })
                  }
                >
                  {busy ? "A processar…" : "Confirmar reconciliação"}
                </ActionButton>
                <ActionButton disabled={busy} onClick={() => setSelected({})}>
                  Limpar seleção
                </ActionButton>
              </div>
            </section>
          )}
          </>}
          {view === "sugestoes" && <section className="panel">
            <h2>Sugestões de correspondência</h2>
            <p>
              O grau de confiança resulta do montante, proximidade das datas e
              termos da referência, entidade e descrição. Uma sugestão não
              associa valores automaticamente.
            </p>
            {visibleCards
              .slice(0, 10)
              .map((s: Row) => (
                <div
                  className="setting-row"
                  key={`card:${s.bank_id}:${s.date}`}
                >
                  <div>
                    <strong>
                      Cartão agrupado · {s.date.split("-").reverse().join("/")}
                    </strong>
                    <p>
                      Bruto: {money(s.gross_cents)} · Comissão sugerida:{" "}
                      {money(s.commission_cents)} · Líquido:{" "}
                      {money(s.net_cents)}
                    </p>
                    <p>
                      Confiança {s.confidence.toLowerCase()} ({s.score}/100)
                    </p>
                    <small>{s.reasons.join(" · ")}</small>
                  </div>
                  {manager && (
                    <ActionButton disabled={busy} onClick={() => chooseCard(s)}>
                      Selecionar agrupamento
                    </ActionButton>
                  )}
                </div>
              ))}
            {visibleSuggestions
              .slice(0, suggestionLimit)
              .map((s: Row) => {
                const b = all.find(
                  (r) => r.source === "bank" && r.id === s.bank_id,
                )!;
                const i = all.find(
                  (r) => r.source === s.source && r.id === s.source_id,
                )!;
                return (
                  <div
                    className="setting-row"
                    key={`${s.bank_id}:${s.source}:${s.source_id}`}
                  >
                    <div>
                      <strong>
                        {b.description} ↔ {i.entity || i.description}
                      </strong>
                      <p>
                        {i.reference} · {money(s.amount_cents)} · Confiança{" "}
                        {s.confidence.toLowerCase()} ({s.score}/100)
                      </p>
                      <small>{s.reasons.join(" · ")}</small>
                    </div>
                    {manager && (
                      <ActionButton
                        disabled={busy}
                        onClick={() => chooseSuggestion(s)}
                      >
                        Selecionar sugestão
                      </ActionButton>
                    )}
                  </div>
                );
              })}
            {visibleSuggestions.length > suggestionLimit && <ActionButton type="button" onClick={() => setSuggestionLimit(suggestionLimit + 20)}>Mostrar mais sugestões ({visibleSuggestions.length - suggestionLimit})</ActionButton>}
            {!visibleSuggestions.length && !visibleCards.length && (
                <p>
                  Sem sugestões para estes filtros. Pode selecionar movimentos
                  manualmente.
                </p>
              )}
          </section>}
          {manager && chosen.length > 0 && <div className="reconciliation-selection-bar" aria-label="Seleção para reconciliar">
            <span aria-live="polite"><b>{chosen.length}</b> selecionado(s) · diferença {selectionMoney(internalTotal - bankTotal)}</span>
            <ActionButton type="button" className="primary" onClick={() => { setView("manual"); reviewSelection(); }}>Rever e confirmar</ActionButton>
            <ActionButton type="button" onClick={() => setSelected({})}>Limpar</ActionButton>
          </div>}
        </>
      )}
      {tab === "historico" && (
        <section className="panel">
          <h2>Associações e reversões</h2>
          {data.groups
            .filter((g: Row) => !store || g.store_id === store)
            .map((g: Row) => (
              <div className="setting-row" key={g.id}>
                <div>
                  <strong>
                    {g.kind === "cartao"
                      ? "Recebimentos de cartão"
                      : g.kind === "transferencia"
                        ? "Transferência"
                        : "Pagamento / recebimento"}{" "}
                    · {g.undone_at ? "Desfeita" : "Confirmada"}
                  </strong>
                  <p>
                    {new Date(g.created_at).toLocaleString("pt-PT")} · Comissão:{" "}
                    {money(g.commission_cents)} · Diferença:{" "}
                    {money(g.difference_cents)}
                  </p>
                  <p>{g.reason}</p>
                  <p>Confirmado por: {g.created_by?.name || "Utilizador registado na auditoria"}{g.undone_by&&` · Desfeito por: ${g.undone_by.name}`}</p>
                  <p>
                    Bruto interno: {money(g.gross_cents)} · Líquido bancário:{" "}
                    {money(g.net_cents)}
                  </p>
                  {g.undo_reason && <p>Motivo da reversão: {g.undo_reason}</p>}
                  {g.lines.map((l: Row) => {
                    const r = all.find(
                      (i) => i.source === l.source && i.id === l.source_id,
                    );
                    return (
                      <p key={l.id}>
                        {labels[l.source]}: {r?.description || l.source_id} ·{" "}
                        {money(l.amount_cents)}
                      </p>
                    );
                  })}
                </div>
                {manager && !g.undone_at && (
                  <ActionButton
                    disabled={busy}
                    onClick={() => {
                      setAction({ type: "undo", group: g });
                      setActionReason("");
                    }}
                  >
                    Desfazer
                  </ActionButton>
                )}
              </div>
            ))}
          {!data.groups.some((g: Row) => !store || g.store_id === store) && (
            <p>
              Sem reconciliações neste histórico. As associações anteriores de
              compras estão disponíveis em Faturas e documentos.
            </p>
          )}
        </section>
      )}
      {resolution&&<BankResolution row={resolution} accounts={accounts} onClose={()=>setResolution(null)} onSaved={load} onExisting={candidate=>{
        const internal=data.internals.find((r:Row)=>(r.source===candidate.source&&r.id===candidate.id)||(r.origin_source===candidate.source&&r.origin_source_id===candidate.id));
        if(!internal){setError("O registo pertence a um circuito próprio, como os depósitos. Consulte o documento de origem para associar o extrato.");setResolution(null);return;}
        const amount=(Math.min(internal.available_cents,resolution.available_cents)/100).toFixed(2);
        setSelected({[key(resolution)]:amount,[key(internal)]:amount});setKind(internal.transfer?"transferencia":"pagamento");setCommission("0");setDifference("0");setPostCommission(false);setReason("");setResolution(null);setNotice("Registo existente selecionado. Confira os valores antes de confirmar a associação.");
      }}/>}
      {action && (
        <AppDialog autoOpen busy={busy} initialFocus="cancel" onRequestClose={() => setAction(null)}
          className="doc-dialog reconciliation-action"
          aria-label="Justificar ação"
        >
          <h2>
            {action.type === "undo"
              ? "Desfazer reconciliação"
              : `Ignorar ${action.row.description}`}
          </h2>
          {error && <Notice tone="error">{error}</Notice>}
          <p>{action.type === "undo" ? `As associações da reconciliação «${action.group.id}» serão desfeitas. Os documentos e movimentos originais serão preservados e poderão ser novamente reconciliados. O motivo ficará na auditoria.` : `«${action.row.description}» deixará de ser sugerido nesta reconciliação. O movimento original será preservado e o motivo ficará na auditoria.`}</p>
          <label>
            Justificação obrigatória
            <input
              autoFocus
              value={actionReason}
              maxLength={2000}
              onChange={(e) => setActionReason(e.target.value)}
            />
          </label>
          <div className="actions">
            <ActionButton
              disabled={busy || actionReason.trim().length < 3}
              onClick={() =>
                act(async () => {
                  const path =
                    action.type === "undo"
                      ? `/reconciliation/${action.group.id}/undo`
                      : `/reconciliation/ignore/${action.row.source}/${action.row.id}`;
                  await api(path, { reason: actionReason });
                  setAction(null);
                  setSelected({});
                  await load();
                  setNotice("Alteração registada na auditoria.");
                })
              }
            >
              Confirmar
            </ActionButton>
            <ActionButton disabled={busy} onClick={() => setAction(null)}>
              Cancelar
            </ActionButton>
          </div>
        </AppDialog>
      )}
    </>
  );
}
function centsSafe(value: string) {
  try {
    return cents(value);
  } catch {
    return 0;
  }
}
