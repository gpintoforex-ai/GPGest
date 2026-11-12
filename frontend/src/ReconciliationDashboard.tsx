import { useState } from "react";
import Withholdings from "./Withholdings";
import { Landmark, ListChecks, ListFilter, CheckCheck, FilePlus2, CheckCircle2, Clock, Sparkles, CircleAlert, Search, SlidersHorizontal, ArrowDownLeft, ArrowUpRight, Download, Settings, X, CalendarDays, CircleHelp, Equal, EqualNot } from "lucide-react";
import { RecordIndicator, RecordGroup } from "./components/RecordSheet";
import { ActionButton, ActionLink, AppDialog, Notice } from "./components/InteractionUI";
import { Field } from "./components/Form";
import { StatusBadge } from "./components/BusinessUI";
import DatePresets from "./components/DatePresets";
import BankReconciliationPeriod from "./BankReconciliationPeriod";
import BankRules from "./BankRules";

type Row = Record<string, any>;
const money = (n: number, currency = "EUR") => new Intl.NumberFormat("pt-PT", { style: "currency", currency }).format(n / 100);
const key = (r: Row) => `${r.source}:${r.id}`;
// Valor escrito em euros ("60", "60,00", "-1.234,56 €") em cêntimos absolutos; null se não for um valor.
const amountOf = (text: string) => {
  const value = text.trim().replace(/[\s€]/g, "").replace(/^[-+]/, "").replace(/\.(?=\d{3}(?:\D|$))/g, "").replace(",", ".");
  return /^\d+(?:\.\d{1,2})?$/.test(value) ? Math.round(Number(value) * 100) : null;
};
const day = (s: string) => s ? s.split("-").reverse().join("/") : "—";
const classifications: Row = { exata: "Correspondência exata", provavel: "Correspondência provável", conflito: "Conflito", sem_correspondencia: "Sem correspondência" };
const sources: Row = { purchase: "Fatura", movement: "Movimento interno", wintouch: "Wintouch", payment: "Pagamento", book: "Registo bancário" };

/** Documento ou registo da aplicação: referência em destaque e, por baixo, origem, entidade, data e valor. */
function Identity({ item }: { item: Row }) {
  const title = item.reference || item.entity || item.description;
  const details = [sources[item.source], item.entity && item.entity !== title ? item.entity : "", day(item.date), money(Math.abs(item.amount_cents), item.currency)];
  return <span className="reconciliation-identity"><b>{title}</b><small>{details.filter(Boolean).join(" · ")}</small></span>;
}

/** Proposta agrupada: a entidade comum e cada documento (referência, data e valor a associar). */
function GroupIdentity({ proposal, internals, currency }: { proposal: Row; internals: Row[]; currency?: string }) {
  const items = proposal.lines.map((l: Row) => ({ line: l, item: internals.find((r: Row) => r.source === l.source && r.id === l.source_id) }));
  const entities: string[] = [...new Set<string>(items.map(({ item }: Row) => item?.entity).filter(Boolean))];
  return <span className="reconciliation-identity">
    <b>{entities.length === 1 ? entities[0] : `${proposal.lines.length} registo(s) agrupados`}</b>
    {items.map(({ line, item }: Row) => <small key={`${line.source}:${line.source_id}`}>{item
      ? [`${sources[item.source] || "Registo"} ${item.reference || item.description}`, entities.length === 1 ? "" : item.entity, day(item.date), money(line.amount_cents, item.currency || currency)].filter(Boolean).join(" · ")
      : `Registo indisponível · ${money(line.amount_cents, currency)}`}</small>)}
    <small className="reconciliation-identity-total">Total {money(proposal.lines.reduce((n: number, l: Row) => n + l.amount_cents, 0), currency)}
      {proposal.commission_cents > 0 && ` · Comissão ${money(proposal.commission_cents, currency)} · Líquido ${money(proposal.net_cents, currency)}`}</small>
  </span>;
}

/** Tratamento proposto por uma regra por descrição (movimento sem documento). */
/** Valor proposto face ao valor por associar do movimento: igual (verde) ou diferença (âmbar), com ícone e texto. */
function ValueMatch({ bank, proposal, internals }: { bank: Row; proposal?: Row; internals: Row[] }) {
  if (!proposal || bank.available_cents <= 0) return null;
  const internal = proposal.lines ? undefined : internals.find((r: Row) => r.source === proposal.source && r.id === proposal.source_id);
  const value: number = proposal.lines ? proposal.lines.reduce((n: number, l: Row) => n + l.amount_cents, 0) : internal?.available_cents ?? proposal.amount_cents;
  const difference = value - bank.available_cents;
  const detail = `Movimento por associar ${money(bank.available_cents, bank.currency)} · ${proposal.lines ? "soma dos documentos" : "documento"} ${money(value, bank.currency)}`;
  const discount = proposal.discount;
  if (discount && difference === discount.cents) {
    const rate = `${(discount.basis_points / 100).toLocaleString("pt-PT", { maximumFractionDigits: 2 })} %`;
    return <span className="reconciliation-value-match is-discount" title={`${detail} · possível desconto financeiro de ${rate} sobre ${discount.on === "base" ? "a base" : "o total"} de ${money(discount.basis_cents, bank.currency)} (confirme)`}>
      <Equal size={13} aria-hidden="true" />Desconto financeiro {rate}: {money(discount.cents, bank.currency)}</span>;
  }
  const withheld = proposal.withholding;
  if (withheld && difference === withheld.cents) {
    const rate = `${(withheld.basis_points / 100).toLocaleString("pt-PT", { maximumFractionDigits: 2 })} %`;
    return <span className="reconciliation-value-match is-withholding" title={`${detail} · retenção na fonte de ${rate} sobre ${money(withheld.base_cents, bank.currency)}${withheld.configured ? " (taxa da ficha do fornecedor)" : " (taxa habitual: confirme)"}`}>
      <Equal size={13} aria-hidden="true" />Retenção na fonte {rate}: {money(withheld.cents, bank.currency)}</span>;
  }
  return difference === 0
    ? <span className="reconciliation-value-match is-equal" title={detail}><Equal size={13} aria-hidden="true" />Valor igual</span>
    : <span className="reconciliation-value-match is-different" title={detail}><EqualNot size={13} aria-hidden="true" />Diferença de {money(Math.abs(difference), bank.currency)}</span>;
}

function RuleIdentity({ rule }: { rule: Row }) {
  return <span className="reconciliation-identity"><b>{rule.name}</b>
    <small>{["Regra", rule.kind_label, rule.category_name && `Rubrica ${rule.category_name}`, rule.employee_name, rule.target_account_name && `Para ${rule.target_account_name}`,
      rule.counts_as_cost ? "conta como custo" : ""].filter(Boolean).join(" · ")}</small></span>;
}

/** Retenção na fonte reconhecida na proposta: a diferença é exatamente a retenção calculada. */
function withholdingOf(row: Row, proposal?: Row, internal?: Row) {
  const withheld = proposal?.withholding;
  return withheld && internal && internal.available_cents - row.available_cents === withheld.cents ? withheld : null;
}

/** Desconto financeiro reconhecido na proposta: a diferença é exatamente o desconto calculado. */
function discountOf(row: Row, proposal?: Row, internal?: Row) {
  const discount = proposal?.discount;
  return discount && internal && internal.available_cents - row.available_cents === discount.cents ? discount : null;
}

export default function ReconciliationDashboard({ data, accounts, categories = [], store, account, from, to, loaded, busy, manager, admin, onAccount, onDates, onReview, onSuggestions, onReject, onManual, onReviewMultiple, onAcceptMany, onResolve }: {
  data: Row; accounts: Row[]; categories?: Row[]; store: string; account: string; from: string; to: string; loaded: boolean; busy: boolean; manager: boolean; admin: boolean;
  onAccount: (id: string) => void; onDates: (from: string, to: string) => void;
  onReview: (bank: Row, proposal?: Row) => void; onSuggestions: () => void; onReject: (proposal: Row) => void;
  onManual: () => void; onReviewMultiple: (banks: Row[]) => void;
  onAcceptMany: (items: Row[], progress: (done: number) => void) => Promise<void>;
  onResolve: (bank: Row, rule?: Row) => void;
}) {
  const [periodSummary, setPeriodSummary] = useState<Row | null>(null);
  // Pesquisa inicial vinda de outra página (ex.: «Associar a documento» nos movimentos da rubrica).
  const [filter, setFilter] = useState("todos"), [direction, setDirection] = useState("todos"),
    [search, setSearch] = useState(() => { try { return new URLSearchParams(window.location.search).get("pesquisa") || ""; } catch { return ""; } });
  const [minimum, setMinimum] = useState(""), [maximum, setMaximum] = useState(""), [equalOnly, setEqualOnly] = useState(false);
  const searched = amountOf(search), low = amountOf(minimum), high = amountOf(maximum);
  const [limit, setLimit] = useState(50), [expanded, setExpanded] = useState("");
  const [selection, setSelection] = useState<string[]>([]), [periodOpen, setPeriodOpen] = useState(false), [rulesOpen, setRulesOpen] = useState(false);
  const [withholdingsOpen, setWithholdingsOpen] = useState(false);
  const scope = (r: Row) => (!store || r.store_id === store) && (!account || r.account_id === account);
  const rows: Row[] = data.banks.filter((r: Row) => scope(r) && (!from || r.date >= from) && (!to || r.date <= to));
  const proposals: Row[] = [...data.suggestions, ...(data.card_suggestions || [])].filter(p => (p.bank_lines || [{ source_id: p.bank_id }]).every((l: Row) => rows.some(r => r.id === l.source_id)));
  const forBank = (id: string) => proposals.filter(p => p.bank_id === id || p.bank_lines?.some((l: Row) => l.source_id === id)).sort((a, b) => b.score - a.score);
  // Regra por descrição: só para movimentos sem proposta de documento.
  const ruleFor = (id: string): Row | undefined => forBank(id).length ? undefined : (data.rule_suggestions || []).find((s: Row) => s.bank_id === id)?.rule;
  const internalOf = (p?: Row) => p && !p.lines ? data.internals.find((r: Row) => r.source === p.source && r.id === p.source_id) : undefined;
  // Aceitação em lote: só a melhor proposta 1:1 de valor igual, sem comissão, diferença ou justificação.
  // Cada uma segue para a mesma confirmação do servidor (validações e auditoria), uma a uma.
  function candidate(row: Row): Row {
    const proposal = forBank(row.id)[0], internal = internalOf(proposal), alternatives = forBank(row.id).length - 1;
    const blocked = (reason: string) => ({ row, proposal, internal, blocked: reason });
    if (row.available_cents <= 0 || row.status === "ignorado") return blocked("Sem proposta: rever individualmente");
    const rule = ruleFor(row.id);
    // Regra: registo pelo circuito do «Resolver»; com registos parecidos na aplicação, fica para revisão individual.
    if (!proposal && rule) return { row, rule, internals: [], alternatives: 0 };
    if (!proposal) return blocked("Sem proposta: rever individualmente");
    if ((proposal.bank_lines || []).length > 1) return blocked("Proposta com vários movimentos do extrato: rever individualmente");
    if (proposal.lines) {
      // Agrupamento (ex.: débito direto de duas faturas): só sem comissão e com a soma igual ao movimento.
      const items = proposal.lines.map((l: Row) => data.internals.find((r: Row) => r.source === l.source && r.id === l.source_id));
      if (proposal.commission_cents || proposal.net_cents !== row.available_cents
          || proposal.lines.reduce((n: number, l: Row) => n + l.amount_cents, 0) !== row.available_cents)
        return blocked("Agrupamento com comissão ou diferença: rever individualmente");
      if (items.some((item: Row | undefined, i: number) => !item || item.transfer || item.available_cents < proposal.lines[i].amount_cents))
        return blocked("Registos do agrupamento indisponíveis: rever individualmente");
      return { row, proposal, internals: items, kind: proposal.kind === "cartao" ? "cartao" : "pagamento", alternatives,
        lines: [{ source: "bank", source_id: row.id, amount_cents: row.available_cents }, ...proposal.lines.map((l: Row) => ({ source: l.source, source_id: l.source_id, amount_cents: l.amount_cents }))] };
    }
    if (!internal) return blocked("Sem proposta simples: rever individualmente");
    const discount = discountOf(row, proposal, internal);
    if (discount) {
      // Desconto financeiro: a fatura fica paga na totalidade; o desconto é a diferença justificada.
      const rate = `${(discount.basis_points / 100).toLocaleString("pt-PT", { maximumFractionDigits: 2 })} %`;
      return { row, proposal, internal, internals: [internal], kind: "pagamento", alternatives,
        difference_cents: Math.sign(row.amount_cents) * (row.available_cents - internal.available_cents), discount_cents: discount.cents,
        reason: `Desconto financeiro de ${rate} sobre ${discount.on === "base" ? "a base" : "o total"} de ${(discount.basis_cents / 100).toFixed(2).replace(".", ",")} €`,
        lines: [{ source: "bank", source_id: row.id, amount_cents: row.available_cents }, { source: internal.source, source_id: internal.id, amount_cents: internal.available_cents }] };
    }
    const withheld = withholdingOf(row, proposal, internal);
    if (withheld) {
      // Retenção na fonte: a fatura fica paga na totalidade; a retenção é a diferença justificada.
      const rate = `${(withheld.basis_points / 100).toLocaleString("pt-PT", { maximumFractionDigits: 2 })} %`;
      return { row, proposal, internal, internals: [internal], kind: "pagamento", alternatives,
        difference_cents: Math.sign(row.amount_cents) * (row.available_cents - internal.available_cents), withholding_cents: withheld.cents,
        reason: `Retenção na fonte de ${rate} sobre ${(withheld.base_cents / 100).toFixed(2).replace(".", ",")} €${row.amount_cents < 0 ? " (a entregar ao Estado)" : " (retida pelo cliente)"}`,
        lines: [{ source: "bank", source_id: row.id, amount_cents: row.available_cents }, { source: internal.source, source_id: internal.id, amount_cents: internal.available_cents }] };
    }
    if (proposal.amount_cents !== row.available_cents || internal.available_cents !== row.available_cents) return blocked("Valores diferentes (parcial ou comissão): rever individualmente");
    if (internal.transfer && !internal.deposit_id) return blocked("Transferência sem depósito registado exige justificação: rever individualmente");
    return { row, proposal, internal, internals: [internal], kind: internal.transfer ? "transferencia" : "pagamento", alternatives,
      lines: [{ source: "bank", source_id: row.id, amount_cents: proposal.amount_cents }, { source: internal.source, source_id: internal.id, amount_cents: proposal.amount_cents }] };
  }
  // Valor igual: a melhor proposta de documento tem exatamente o valor por associar do movimento
  // (registo único com o mesmo disponível, ou agrupamento sem comissão cuja soma é igual).
  function equalValue(row: Row) {
    const proposal = forBank(row.id)[0];
    if (!proposal || row.available_cents <= 0 || (proposal.bank_lines || []).length > 1) return false;
    if (proposal.lines) return !proposal.commission_cents && proposal.lines.reduce((n: number, l: Row) => n + l.amount_cents, 0) === row.available_cents;
    const internal = internalOf(proposal);
    return !!internal && (internal.available_cents === row.available_cents && proposal.amount_cents === row.available_cents
      || !!withholdingOf(row, proposal, internal) || !!discountOf(row, proposal, internal));
  }
  const tops = new Map<string, number>();
  for (const r of rows) { const internal = internalOf(forBank(r.id)[0]); if (internal) tops.set(key(internal), (tops.get(key(internal)) || 0) + 1); }
  // Fiável: exata, ou confiança alta (limites da conta: Regras de correspondência) claramente acima da alternativa
  // seguinte e documento não disputado por outro movimento.
  const limits = (r: Row) => ({ score: Number(data.rules?.[r.account_id]?.safe_score ?? 80), gap: Number(data.rules?.[r.account_id]?.safe_gap ?? 20) });
  const reliable = (c: Row) => !c.blocked && (!!c.rule || c.proposal.classification === "exata" || c.proposal.score >= limits(c.row).score
    && (forBank(c.row.id)[1]?.score ?? 0) <= c.proposal.score - limits(c.row).gap && !!c.internal && tops.get(key(c.internal)) === 1);
  const [batch, setBatch] = useState<Row[] | null>(null), [skipped, setSkipped] = useState<string[]>([]), [done, setDone] = useState(0);
  function openBatch(banks: Row[]) {
    const seen = new Set<string>();
    setBatch(banks.map(candidate).map(c => {
      if (c.blocked) return c;
      if (c.internals.some((i: Row) => seen.has(key(i)))) return { ...c, blocked: "O mesmo documento está proposto para outro movimento selecionado" };
      c.internals.forEach((i: Row) => seen.add(key(i))); return c;
    }));
    setSkipped([]); setDone(0);
  }
  const accepting = (batch || []).filter(c => !c.blocked && !skipped.includes(c.row.id));
  const reconciled = rows.filter(r => r.status === "reconciliado").length;
  const pending = rows.filter(r => r.available_cents > 0).length;
  const proposedFor = (id: string) => forBank(id).length > 0 || !!ruleFor(id);
  const suggested = rows.filter(r => proposedFor(r.id) && r.available_cents > 0).length;
  const conflicts = rows.filter(r => r.match_classification === "conflito").length;
  const unmatched = rows.filter(r => r.available_cents > 0 && !proposedFor(r.id)).length;
  const equalCount = rows.filter(equalValue).length;
  const visible = rows.filter(r => (filter === "todos" || filter === "conciliados" && r.status === "reconciliado" || filter === "pendentes" && r.available_cents > 0 || filter === "sugestoes" && r.available_cents > 0 && proposedFor(r.id) || filter === "sem" && r.available_cents > 0 && !proposedFor(r.id) || filter === "conflitos" && r.match_classification === "conflito")
    && (direction === "todos" || direction === "entradas" && r.amount_cents > 0 || direction === "saidas" && r.amount_cents < 0)
    && (low == null || Math.abs(r.amount_cents) >= low) && (high == null || Math.abs(r.amount_cents) <= high)
    && (!equalOnly || equalValue(r))
    // A pesquisa encontra também o valor do movimento ou o valor por associar.
    && (searched != null && (Math.abs(r.amount_cents) === searched || r.available_cents === searched)
      || `${r.description} ${r.reference || ""}`.toLocaleLowerCase("pt-PT").includes(search.toLocaleLowerCase("pt-PT"))));
  const eligible = visible.slice(0, limit).filter(r => r.available_cents > 0 && r.status !== "ignorado");
  const selected = rows.filter(r => selection.includes(r.id) && r.available_cents > 0 && r.status !== "ignorado");
  const reliableRows = visible.filter(r => reliable(candidate(r)));
  const acceptable = selected.filter(r => !candidate(r).blocked).length;
  const percent = rows.length ? Math.round(reconciled / rows.length * 100) : null;
  const months = Array.from({ length: 6 }, (_, index) => {
    const anchor = new Date((to || new Date().toISOString().slice(0, 10)).slice(0, 7) + "-15T12:00:00Z");
    anchor.setUTCMonth(anchor.getUTCMonth() - 5 + index);
    const key = anchor.toISOString().slice(0, 7);
    const monthRows = data.banks.filter((r: Row) => scope(r) && r.date.startsWith(key));
    return { key, label: anchor.toLocaleDateString("pt-PT", { month: "short", timeZone: "Europe/Lisbon" }), value: monthRows.length ? Math.round(monthRows.filter((r: Row) => r.status === "reconciliado").length / monthRows.length * 100) : null };
  });
  const report = account && from && to ? `/api/reconciliation/accounts/${encodeURIComponent(account)}/report?${new URLSearchParams({ start: from, end: to })}` : "";
  const selectedAccount = accounts.find(a => a.id === account);
  function showPeriod() { setPeriodOpen(true); setTimeout(() => document.getElementById("reconciliation-period")?.scrollIntoView({ behavior: "smooth", block: "start" }), 0); }
  const filters = [
    { id: "todos", label: "Todos", count: rows.length, icon: ListChecks, tone: "info" },
    { id: "conciliados", label: "Conciliados", count: reconciled, icon: CheckCircle2, tone: "success" },
    { id: "pendentes", label: "Por reconciliar", count: pending, icon: Clock, tone: "warning" },
    { id: "sugestoes", label: "Sugestões", count: suggested, icon: Sparkles, tone: "suggestion" },
    { id: "sem", label: "Sem correspondência", count: unmatched, icon: CircleHelp, tone: "danger" },
    { id: "conflitos", label: "Conflitos", count: conflicts, icon: CircleAlert, tone: "danger" },
  ];
  return <section className="reconciliation-center reconciliation-dashboard" aria-label="Centro de reconciliação bancária">
    <div className="reconciliation-dashboard-period"><details className="reconciliation-date-picker"><summary><CalendarDays size={17} />Período: {day(from)} — {day(to)}</summary><div><Field label="Período desde"><input type="date" value={from} onChange={e => onDates(e.target.value, to)} /></Field><Field label="Período até"><input type="date" value={to} onChange={e => onDates(from, e.target.value)} /></Field><DatePresets from={from} to={to} onSelect={onDates} /></div></details><ActionButton variant="quiet" onClick={onManual}><Search size={15} />Seleção manual</ActionButton></div>
    <div className="reconciliation-summary-strip">
      <div className="reconciliation-account-card"><Landmark size={30} /><div className="reconciliation-account-info"><Field label="Conta da reconciliação"><select value={account} title={selectedAccount ? `${selectedAccount.bank} · ${selectedAccount.name}` : "Todas as contas autorizadas"} onChange={e => { onAccount(e.target.value); setLimit(50); setSelection([]); }}><option value="">Todas as contas autorizadas</option>{accounts.filter(a => !store || a.store_id === store).map(a => <option key={a.id} value={a.id}>{a.bank} · {a.name}</option>)}</select></Field><small>{selectedAccount ? `${selectedAccount.currency || "EUR"}${selectedAccount.iban_last4 ? ` · Conta …${selectedAccount.iban_last4}` : ""}` : "Consulta das contas com acesso autorizado"}</small></div></div>
      <RecordIndicator label="Movimentos no extrato" value={loaded ? rows.length : "—"} icon={ListChecks} tone="info" />
      <RecordIndicator label="Conciliados" value={loaded ? reconciled : "—"} icon={CheckCircle2} tone="success" onOpen={() => setFilter("conciliados")} />
      <RecordIndicator label="Por reconciliar" value={loaded ? pending : "—"} icon={Clock} tone="warning" onOpen={() => setFilter("pendentes")} />
      <RecordIndicator label="Diferenças por resolver" value={periodSummary?.unresolved_difference_cents == null ? "—" : money(periodSummary.unresolved_difference_cents, periodSummary.currency)} note={account ? undefined : "Selecione uma conta"} icon={CircleAlert} tone={periodSummary?.unresolved_difference_cents ? "danger" : "info"} />
    </div>
    {data.group_search_limited && <details className="reconciliation-search-note"><summary>Pesquisa de agrupamentos limitada · consultar âmbito</summary><p>A pesquisa considera grupos de dois ou três registos e os primeiros candidatos elegíveis. A seleção manual permite conferir os restantes movimentos.</p></details>}
    <div className="reconciliation-center-body">
      <aside className="panel reconciliation-center-filters" aria-label="Filtrar movimentos do extrato"><h3><SlidersHorizontal size={17} />Filtrar movimentos</h3>{filters.map(({ id, label, count, icon: Icon, tone }) => <ActionButton inferAction={false} key={id} data-filter-tone={tone} aria-pressed={filter === id} onClick={() => { setFilter(id); setLimit(50); }}><Icon size={17} /><span>{label} ({count})</span></ActionButton>)}
        <div className="reconciliation-type-filter"><Field label="Tipo de movimento"><select value={direction} onChange={e => setDirection(e.target.value)}><option value="todos">Todos os movimentos</option><option value="entradas">Recebimentos / entradas</option><option value="saidas">Pagamentos / saídas</option></select></Field><small><ArrowDownLeft size={13} /> Entradas <ArrowUpRight size={13} /> Saídas</small>
          <div className="reconciliation-amount-filter"><Field label="Valor mínimo (€)"><input inputMode="decimal" value={minimum} placeholder="0,00" aria-invalid={!!minimum.trim() && low == null} onChange={e => { setMinimum(e.target.value); setLimit(50); }} /></Field><Field label="Valor máximo (€)"><input inputMode="decimal" value={maximum} placeholder="0,00" aria-invalid={!!maximum.trim() && high == null} onChange={e => { setMaximum(e.target.value); setLimit(50); }} /></Field></div>
          <label className="reconciliation-equal-filter" title="Proposta de documento com exatamente o mesmo valor do movimento por associar, ou cuja diferença é a retenção na fonte ou o desconto financeiro reconhecidos"><input type="checkbox" checked={equalOnly} onChange={e => { setEqualOnly(e.target.checked); setLimit(50); }} />Só valores iguais ({equalCount})</label></div>
        <ActionButton variant="suggestion" onClick={onSuggestions}><Sparkles size={17} />Rever propostas</ActionButton>
      </aside>
      <section className="panel reconciliation-statement-panel" aria-label="Movimentos do extrato">
        <div className="reconciliation-grid-toolbar"><div className="reconciliation-grid-tabs" role="group" aria-label="Vistas do extrato"><ActionButton inferAction={false} aria-pressed={filter !== "sugestoes"} onClick={() => setFilter("todos")}><Landmark size={16} />Movimentos do extrato</ActionButton><ActionButton inferAction={false} aria-pressed={filter === "sugestoes"} onClick={() => setFilter("sugestoes")}><Sparkles size={16} />Sugestões da ZELA <b>{suggested}</b></ActionButton></div><Field label="Pesquisar movimento"><input value={search} placeholder="Descrição, referência ou valor…" onChange={e => { setSearch(e.target.value); setLimit(50); }} /></Field></div>
        <h2 className="sr-only">Movimentos do extrato</h2>
        {selected.length > 0 && <div className="reconciliation-grid-selection"><span>{selected.length} movimento(s) selecionado(s)</span><ActionButton variant="suggestion" disabled={busy || !acceptable} onClick={() => openBatch(selected)}><CheckCheck size={15} />Aceitar sugestões ({acceptable})</ActionButton><ActionButton disabled={busy} onClick={() => onReviewMultiple(selected)}>Rever seleção</ActionButton><ActionButton onClick={() => setSelection([])}>Limpar seleção</ActionButton></div>}
        {manager && !selected.length && reliableRows.length > 0 && <div className="reconciliation-grid-selection"><span>{reliableRows.length} sugestão(ões) fiável(eis): valor igual e sem alternativa próxima.</span><ActionButton variant="suggestion" disabled={busy} onClick={() => openBatch(reliableRows)}><CheckCheck size={15} />Aceitar sugestões fiáveis</ActionButton></div>}
        <div className="table-wrap"><table><caption className="sr-only">Movimentos e correspondências bancárias</caption><thead><tr><th className="reconciliation-checkbox">{manager && <input type="checkbox" aria-label="Selecionar movimentos visíveis" disabled={busy || !eligible.length} checked={!!eligible.length && eligible.every(r => selection.includes(r.id))} onChange={e => setSelection(e.target.checked ? [...new Set([...selection, ...eligible.map(r => r.id)])] : selection.filter(id => !eligible.some(r => r.id === id)))} />}</th><th>Data</th><th>Descrição (extrato)</th><th>Valor</th><th>Correspondência (ERP / ZELA)</th><th>Estado</th></tr></thead><tbody>{visible.slice(0, limit).map(row => {
          const proposed = forBank(row.id), suggestion = proposed[0], rule = ruleFor(row.id);
          const internal = suggestion && data.internals.find((r: Row) => r.source === suggestion.source && r.id === suggestion.source_id);
          const confirmedGroup = data.groups.find((g: Row) => !g.undone_at && g.lines.some((l: Row) => l.source === "bank" && l.source_id === row.id));
          const confirmed = confirmedGroup?.lines.filter((l: Row) => l.source !== "bank").map((l: Row) => data.internals.find((i: Row) => i.source === l.source && i.id === l.source_id)).filter(Boolean);
          const canSelect = manager && row.available_cents > 0 && row.status !== "ignorado";
          return <tr key={row.id} className={row.status === "reconciliado" ? "reconciliation-grid-matched" : proposed.length || rule ? "reconciliation-grid-suggested" : undefined}>
            <td className="reconciliation-checkbox">{manager && <input type="checkbox" aria-label={`Selecionar ${row.description}`} checked={selection.includes(row.id)} disabled={!canSelect || busy} onChange={e => setSelection(e.target.checked ? [...selection, row.id] : selection.filter(id => id !== row.id))} />}</td>
            <td className="reconciliation-grid-date">{day(row.date)}</td><td><span>{row.description}</span>{row.reference && <small>{row.reference}</small>}{!account && <small>{row.account}</small>}</td>
            <td className={row.amount_cents < 0 ? "reconciliation-value-negative" : "reconciliation-value-positive"}>{row.amount_cents > 0 ? "+ " : ""}{money(row.amount_cents, row.currency)}{row.status === "parcial" && <small>Por associar: {money(row.available_cents, row.currency)}</small>}</td>
            <td>{row.status === "reconciliado" ? confirmed?.length ? confirmed.map((i: Row) => <Identity key={key(i)} item={i} />) : "Associação confirmada" : <span className={suggestion || rule ? "reconciliation-match-label" : "reconciliation-no-match"}>{(suggestion || rule) && <Sparkles size={14} />}{internal ? <Identity item={internal} /> : suggestion?.lines ? <GroupIdentity proposal={suggestion} internals={data.internals} currency={row.currency} /> : rule ? <RuleIdentity rule={rule} /> : "Sem correspondência"}</span>}
              {row.status !== "reconciliado" && <ValueMatch bank={row} proposal={suggestion} internals={data.internals} />}
              {proposed.length > 0 && <ActionButton variant="quiet" className="reconciliation-proposal-toggle" onClick={() => setExpanded(expanded === row.id ? "" : row.id)} aria-expanded={expanded === row.id}>{expanded === row.id ? "Ocultar propostas" : `Ver propostas (${proposed.length})`}</ActionButton>}
              {expanded === row.id && proposed.map((p: Row) => <div className="reconciliation-proposal" key={p.proposal_id || p.source + p.source_id}><StatusBadge tone={p.classification === "conflito" ? "danger" : p.classification === "exata" ? "success" : "suggestion"}>{classifications[p.classification] || "Sugestão"}</StatusBadge>{internalOf(p) ? <Identity item={internalOf(p)!} /> : p.lines && <GroupIdentity proposal={p} internals={data.internals} currency={row.currency} />}<ValueMatch bank={row} proposal={p} internals={data.internals} /><p>Confiança {String(p.confidence || "").toLowerCase()} ({p.score}/100) · {p.reasons.join(" · ")}</p>{manager && <div className="actions"><ActionButton disabled={busy} variant="suggestion" onClick={() => onReview(row, p)}>Rever e aceitar</ActionButton>{p.proposal_id && <ActionButton disabled={busy} onClick={() => onReject(p)}>Rejeitar proposta</ActionButton>}</div>}</div>)}
            </td><td><div className="reconciliation-row-actions">{suggestion && manager && row.status !== "ignorado" ? <><ActionButton variant="suggestion" disabled={busy} onClick={() => onReview(row, suggestion)} aria-label={`Rever e aceitar proposta: ${row.description}`}><Sparkles size={13} />Aceitar</ActionButton>{suggestion.proposal_id && <ActionButton variant="quiet" disabled={busy} onClick={() => onReject(suggestion)} aria-label={`Rejeitar proposta: ${row.description}`}><X size={14} /></ActionButton>}</> : rule && canSelect ? <ActionButton variant="suggestion" disabled={busy} onClick={() => onResolve(row, rule)} aria-label={`Registar pela regra «${rule.name}»: ${row.description}`}><Sparkles size={13} />Registar</ActionButton> : <StatusBadge tone={row.status === "reconciliado" ? "success" : row.match_classification === "conflito" ? "danger" : "warning"}>{row.status === "reconciliado" && <CheckCircle2 size={13} />}{row.status === "reconciliado" ? "Conciliado" : row.status === "parcial" ? "Parcial" : row.status === "ignorado" ? "Ignorado com motivo" : "Por reconciliar"}</StatusBadge>}{manager && canSelect && !suggestion && <ActionButton variant="quiet" disabled={busy} onClick={() => onReview(row)} aria-label={`Procurar lançamento: ${row.description}`}><Search size={14} /></ActionButton>}{manager && canSelect && !suggestion && !rule && <ActionButton variant="quiet" disabled={busy} onClick={() => onResolve(row)} aria-label={`Resolver sem documento: ${row.description}`} title="Registar sem documento (impostos, vencimentos, encargos, transferências)"><FilePlus2 size={14} />Resolver</ActionButton>}</div>{row.match_classification === "conflito" && <small className="reconciliation-conflict">Conflito · conferir alternativas</small>}</td>
          </tr>;
        })}</tbody></table></div>
        {!visible.length && <p className="reconciliation-empty">{loaded ? "Sem movimentos para os filtros selecionados." : "A carregar movimentos…"}</p>}{visible.length > limit && <ActionButton onClick={() => setLimit(limit + 50)}>Mostrar mais movimentos</ActionButton>}
      </section>
    </div>
    <div className="reconciliation-center-bottom">
      <RecordGroup title="Progresso da reconciliação" icon={CheckCircle2}><div className="reconciliation-progress-content"><div className="reconciliation-donut" style={{ background: `conic-gradient(var(--context-success) 0 ${percent || 0}%, var(--context-warning-bg) ${percent || 0}% 100%)` }} role="img" aria-label={`Progresso da reconciliação: ${percent == null ? "sem movimentos" : `${percent}%`}`}><strong>{percent == null ? "—" : `${percent}%`}</strong></div><dl className="reconciliation-progress-legend"><div><dt><i data-tone="success" />Conciliados</dt><dd>{reconciled}</dd></div><div><dt><i data-tone="warning" />Por reconciliar</dt><dd>{pending}</dd></div><div><dt><i data-tone="suggestion" />Com sugestões</dt><dd>{suggested}</dd></div><div><dt><i data-tone="danger" />Sem correspondência</dt><dd>{unmatched}</dd></div></dl></div><p className="footnote">Sugestões incluídas nos pendentes; requerem validação.</p></RecordGroup>
      <RecordGroup title="Evolução mensal" icon={ListChecks}><div className="reconciliation-chart"><div className="reconciliation-chart-axis"><span>100%</span><span>50%</span><span>0%</span></div><div className="reconciliation-months">{months.map(m => <div key={m.key}><span>{m.value == null ? "—" : `${m.value}%`}</span><div style={{ height: `${m.value || 0}%` }} /><small>{m.label}</small></div>)}</div></div><p className="footnote">Movimentos importados conciliados. Sem extrato: —.</p></RecordGroup>
      <RecordGroup title="Ações rápidas" icon={Sparkles}><div className="reconciliation-quick-actions"><ActionButton variant="suggestion" onClick={onSuggestions}><Sparkles size={17} />Rever sugestões ({suggested})</ActionButton><ActionButton inferAction={false} onClick={() => setFilter("sem")}><CircleHelp size={17} />Tratar sem correspondência</ActionButton>{report ? <ActionLink href={report + "&format=pdf"} target="_blank" rel="noopener noreferrer"><Download size={17} />Exportar reconciliação (PDF)</ActionLink> : <ActionButton disabled><Download size={17} />Exportar · selecione uma conta</ActionButton>}{manager && <ActionButton inferAction={false} onClick={() => setRulesOpen(true)}><ListFilter size={17} />Regras por descrição</ActionButton>}<ActionButton inferAction={false} onClick={showPeriod}><Settings size={17} />Regras, relatórios e fecho</ActionButton></div></RecordGroup>
    </div>
    <details className="reconciliation-period-details" onToggle={e => setWithholdingsOpen(e.currentTarget.open)}><summary>Retenções na fonte e descontos financeiros</summary>{withholdingsOpen && <Withholdings revision={data} />}</details>
    <details className="reconciliation-period-details" id="reconciliation-period" open={periodOpen} onToggle={e => setPeriodOpen(e.currentTarget.open)}><summary>Fecho, saldos e histórico da reconciliação</summary><BankReconciliationPeriod account={account} from={from} to={to} revision={data} manager={manager} admin={admin} onSummary={setPeriodSummary} /></details>
    {!account && <Notice>Selecione uma conta para comparar saldos e preparar o fecho. A grelha apresenta os dados importados disponíveis.</Notice>}
    {rulesOpen && <BankRules accounts={accounts.filter(a => !store || a.store_id === store)} categories={categories} onClose={() => setRulesOpen(false)}
      onChanged={() => window.dispatchEvent(new Event("iazela-reconciliation-rules-changed"))} />}
    {batch && <AppDialog autoOpen busy={busy} className="reconciliation-batch" aria-labelledby="reconciliation-batch-title" onRequestClose={() => setBatch(null)}>
      <h2 id="reconciliation-batch-title"><CheckCheck size={20} />Aceitar sugestões em conjunto</h2>
      <p>Confira cada correspondência. Cada uma é confirmada como se fosse aceite individualmente: o servidor valida os valores disponíveis e regista a auditoria. Desmarque as que não pretende aceitar.</p>
      <div className="table-wrap"><table><caption className="sr-only">Correspondências a aceitar</caption><thead><tr><th className="reconciliation-checkbox"><span className="sr-only">Aceitar</span></th><th>Movimento do extrato</th><th>Documento proposto</th><th>Confiança</th></tr></thead><tbody>{batch.map(c => <tr key={c.row.id} className={c.blocked ? "is-blocked" : undefined}>
        <td className="reconciliation-checkbox"><input type="checkbox" aria-label={`Aceitar ${c.row.description}`} disabled={!!c.blocked || busy} checked={!c.blocked && !skipped.includes(c.row.id)} onChange={e => setSkipped(e.target.checked ? skipped.filter(id => id !== c.row.id) : [...skipped, c.row.id])} /></td>
        <td><span className="reconciliation-identity"><b>{c.row.description}</b><small>{day(c.row.date)} · {money(c.row.amount_cents, c.row.currency)}</small></span></td>
        <td>{c.rule ? <RuleIdentity rule={c.rule} /> : c.internal ? <Identity item={c.internal} /> : c.proposal?.lines ? <GroupIdentity proposal={c.proposal} internals={data.internals} currency={c.row.currency} /> : "—"}{!c.rule && <ValueMatch bank={c.row} proposal={c.proposal} internals={data.internals} />}{c.blocked && <small className="reconciliation-conflict">{c.blocked}</small>}</td>
        <td>{c.rule ? "Regra por descrição" : c.proposal ? <>{c.proposal.confidence} ({c.proposal.score}/100){c.alternatives > 0 && <small className="reconciliation-conflict">{c.alternatives} alternativa(s) por conferir</small>}</> : "—"}</td>
      </tr>)}</tbody></table></div>
      {busy && done > 0 && <p role="status">{done < accepting.length ? `A aceitar ${done} de ${accepting.length}…` : `${done} aceite(s). A atualizar a reconciliação…`}</p>}
      <div className="actions"><ActionButton variant="suggestion" disabled={busy || !accepting.length} onClick={async () => { await onAcceptMany(accepting, setDone); setBatch(null); setSelection([]); }}><CheckCheck size={16} />{busy ? "A aceitar…" : `Aceitar ${accepting.length} correspondência(s)`}</ActionButton><ActionButton disabled={busy} onClick={() => setBatch(null)}>Cancelar</ActionButton></div>
    </AppDialog>}
  </section>;
}
