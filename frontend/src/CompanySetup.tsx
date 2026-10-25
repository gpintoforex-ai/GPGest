import InfoIcon from "./components/InfoIcon";
import { ActionButton, AppDialog, ManagedForm, Notice } from "./components/InteractionUI";
import { useEffect, useRef, useState } from "react";
import { ArrowLeft, ArrowRight, Building2, CheckCircle, CreditCard, FileUp, Landmark, Plus, Save, Settings, Trash2, Wallet, X } from "lucide-react";
import { api } from "./lib/api";
import { Field } from "./components/Form";
import { VatRegimeFields, vatLabel } from "./FiscalSettings";
import "./CompanySetup.css";

type Row = Record<string, any>;
type Props = { boot: Row; reload: () => Promise<void>; onNavigate: (page: string, action?: string) => void; onAction: (kind: string) => Promise<void>; hasOpenSession: boolean; standalone?: boolean };
const steps = [
  ["company", "Empresa"], ["mode", "Modo de trabalho"], ["registers", "Caixas"], ["banks", "Contas bancárias"],
  ["payments", "Meios de pagamento"], ["terminals", "Terminais TPA"], ["preferences", "Preferências"], ["saft", "Faturação SAF-T"], ["summary", "Resumo"],
] as const;
const key = () => crypto.randomUUID();
const today = () => new Date().toLocaleDateString("en-CA", { timeZone: "Europe/Lisbon" });
const money = (text: string | null, currency = "EUR") => text?.trim() ? new Intl.NumberFormat("pt-PT", { style: "currency", currency }).format(Number(text.replace(",", "."))) : "Por configurar";

function Hint({ children }: { children: React.ReactNode }) {
  return <details className="setup-help"><summary><InfoIcon text="Clicar para saber mais" size={17} aria-hidden="true" />Como funciona</summary><div>{children}</div></details>;
}

export default function CompanySetupPanel(props: Props) {
  const [status, setStatus] = useState<Row | null>(null);
  const [opened, setOpened] = useState(false);
  // Destino escolhido dentro do assistente: só se navega depois de o assistente sair da página,
  // para o formulário fechado não bloquear a navegação.
  const [destination, setDestination] = useState<{ page: string; action?: string } | null>(null);
  const [error, setError] = useState("");
  const admin = props.boot.user.role === "administrador";
  async function loadStatus() { setStatus(await api("/setup/status")); }
  useEffect(() => {
    if (opened || !destination) return;
    setDestination(null);
    void Promise.resolve(props.onNavigate(destination.page)).then(() => { if (destination.action) void props.onAction(destination.action); });
  }, [opened, destination]);
  useEffect(() => { let active = true; api("/setup/status").then(result => { if (active) setStatus(result); }).catch(() => { if (active && props.standalone) setError("Não foi possível carregar a configuração. Atualize a página para tentar novamente."); }); return () => { active = false; }; }, [props.boot.company?.id]);
  if (!status && !props.standalone) return null;
  if (status?.completed && !status.pending?.length && !status.unclosed_count && !props.standalone && !opened) return null;
  const completed = Boolean(status?.completed);
  return <>
    <section className={`setup-widget surface-light ${completed ? "setup-reminder" : ""}`} aria-label="Configuração inicial da IAZELA">
      <div className="setup-widget-icon">{completed ? <CheckCircle size={26} /> : <Settings size={28} />}</div>
      <div className="setup-widget-copy">
        <h2>{props.standalone ? "Configuração inicial" : completed ? "Configuração com passos opcionais pendentes" : "Vamos preparar a sua IAZELA"}</h2>
        <p>{completed ? "A empresa está pronta a utilizar. Pode completar as integrações e os restantes parâmetros quando precisar." : "Escolha como pretende trabalhar e prepare as caixas, contas bancárias e meios de pagamento da empresa."}</p>
        {!completed && <div className="setup-widget-progress"><progress value={status?.progress || 0} max={100} aria-label="Progresso da configuração" /><span>{status?.progress || 0}%</span></div>}
        {completed && !!status?.pending?.length && <ul>{status.pending.map((item: string) => <li key={item}>{item}</li>)}</ul>}
        {!!status?.unclosed_count && <p className="setup-note">Há {status.unclosed_count} caixa(s) de dias anteriores por fechar. <ActionButton type="button" onClick={() => props.onNavigate("Folha de caixa")}>Conferir caixas</ActionButton></p>}
        {!admin && <small>A configuração é partilhada pela empresa. Peça a um administrador para a concluir.</small>}
      </div>
      {admin && <ActionButton className={completed ? "" : "primary"} onClick={() => setOpened(true)}>{completed ? "Rever configuração" : status?.started ? "Continuar configuração" : "Iniciar configuração"}<ArrowRight size={17} aria-hidden="true" /></ActionButton>}
    </section>
    {error && <Notice className="error" role="alert">{error}</Notice>}
    {opened && <SetupWizard {...props} onNavigate={(page: string, action?: string) => setDestination({ page, action })} onClose={() => { setOpened(false); loadStatus().catch(() => {}); }} onChanged={async () => { await props.reload(); await loadStatus(); }} />}
  </>;
}

function SetupWizard({ boot, onClose, onChanged, onNavigate, onAction, hasOpenSession }: Props & { onClose: () => void; onChanged: () => Promise<void> }) {
  const dialog = useRef<HTMLDialogElement>(null);
  const heading = useRef<HTMLHeadingElement>(null);
  const [state, setState] = useState<Row | null>(null);
  const [data, setData] = useState<Row | null>(null);
  const [step, setStep] = useState(0);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const [summary, setSummary] = useState<Row | null>(null);
  // Associação SAF-T por gravar; é guardada com os restantes dados ao avançar, recuar ou sair.
  const saftSave = useRef<(() => Promise<void>) | null>(null);
  const [importPreview, setImportPreview] = useState<Row | null>(null);
  const [confirmDemo, setConfirmDemo] = useState(false);
  const [includeERPMethods, setIncludeERPMethods] = useState(true);
  const [done, setDone] = useState(false);
  useEffect(() => {
    const previous = document.activeElement as HTMLElement | null;
    const element = dialog.current!;
    element.showModal();
    let active = true;
    api("/setup").then(result => { if (active) { hydrate(result); setStep(Math.max(0, steps.findIndex(([id]) => id === result.step))); } }).catch(e => { if (active) setError(e.message); });
    return () => { active = false; element.close(); previous?.focus(); };
  }, []);
  useEffect(() => {
    heading.current?.focus();
    dialog.current?.querySelector(".setup-content")?.scrollTo({ top: 0 });
    const rail = dialog.current?.querySelector<HTMLElement>(".setup-step-nav");
    if (!rail) return;
    const centerStep = () => {
      const active = rail.querySelector<HTMLElement>("[aria-current=step]");
      if (active && rail.scrollWidth > rail.clientWidth) {
        const offset = active.getBoundingClientRect().left - rail.getBoundingClientRect().left;
        rail.scrollTo({ left: rail.scrollLeft + offset - (rail.clientWidth - active.offsetWidth) / 2 });
      }
    };
    const observer = new ResizeObserver(centerStep);
    observer.observe(rail);
    centerStep();
    return () => observer.disconnect();
  }, [step, Boolean(data), done]);
  function hydrate(result: Row) { setState(result); setData(result.data); }
  function change(section: string, field: string, value: any) { setData(previous => ({ ...previous, [section]: { ...previous![section], [field]: value } })); setSummary(null); if (section === "mode") setState(previous => previous && ({ ...previous, connection_tested: false })); }
  function itemChange(section: string, index: number, field: string, value: any) {
    setData(previous => ({ ...previous, [section]: previous![section].map((item: Row, i: number) => i === index ? { ...item, [field]: value } : item) }));
    setSummary(null);
  }
  function add(section: string, item: Row) { setData(previous => ({ ...previous, [section]: [...previous![section], item] })); setSummary(null); }
  function remove(section: string, index: number) { setData(previous => ({ ...previous, [section]: previous![section].filter((_: Row, i: number) => i !== index) })); setSummary(null); }
  async function run(task: () => Promise<void>) { if (busy) return; setBusy(true); setError(""); setNotice(""); try { await task(); } catch (e) { setError((e as Error).message); } finally { setBusy(false); } }
  async function persist(next = step, draft = data) {
    if (!state || !draft) throw new Error("Aguarde pelo carregamento da configuração.");
    if (saftSave.current) await saftSave.current();
    const saved = await api("/setup/draft", { revision: state.revision, step: steps[next][0], data: draft });
    hydrate(saved);
    setSummary(null);
    return saved;
  }
  async function go(next: number, override?: Row) { await run(async () => { const draft = override || { ...data, skipped: data!.skipped.filter((id: string) => id !== steps[step][0]) }; const saved = await persist(next, draft); setStep(next); if (next === 8) setSummary(await api("/setup/preview", { revision: saved.revision })); }); }
  async function navigate(page: string) { await run(async () => { if (!done) await persist(); onClose(); onNavigate(page); }); }
  const registerOptions = data?.registers || [];
  const accountOptions = data?.accounts || [];
  function storeSelect(value: string, onChange: (value: string) => void) {
    const known = state?.stores || [];
    return <select value={value} onChange={event => onChange(event.target.value)}>
      {!known.some((s: Row) => s.id === data?.company.store_id) && <option value="primary">{data?.company.store_name || "Estabelecimento principal"}</option>}
      {known.map((s: Row) => <option key={s.id} value={s.id}>{s.name}</option>)}
    </select>;
  }
  function accountSelect(value: string | null, onChange: (value: string) => void, empty = "Escolher conta") {
    return <select value={value || ""} onChange={event => onChange(event.target.value)}><option value="">{empty}</option>{accountOptions.map((a: Row) => <option key={a.key} value={a.key}>{a.bank || "Banco"} · {a.name || "Conta por nomear"}</option>)}</select>;
  }
  function deleteButton(section: string, index: number) { return <ActionButton type="button" className="setup-remove" aria-label={`Retirar ${section === "registers" ? "caixa" : section === "accounts" ? "conta" : "terminal"} ${index + 1}`} onClick={() => remove(section, index)}><Trash2 size={17} />Retirar da configuração</ActionButton>; }
  const currency = data?.company.currency || "EUR";
  return <AppDialog busy={busy} dirty={!!data && !!state && !done && JSON.stringify(data) !== JSON.stringify(state.data)} ref={dialog} className="setup-dialog surface-light" aria-labelledby="setup-title" onRequestClose={() => { if (!busy) onClose(); }}>
    <header className="setup-dialog-header"><div><small>CONFIGURAÇÃO DA EMPRESA</small><h2 id="setup-title">Vamos preparar a sua IAZELA</h2></div></header>
    <p className="footnote">Introduza apenas os dados necessários à configuração e informe as pessoas cujos acessos cria. <a href="/legal/privacidade">Informação de privacidade</a> · <a href="/legal/direitos">Direitos e contacto</a>. A configuração não subscreve publicidade.</p>
    {!data || !state ? <div className="setup-content">{error ? <Notice className="error" role="alert">{error}</Notice> : <p role="status">A carregar os dados existentes…</p>}</div> : done ? <div className="setup-content setup-completed">
      <CheckCircle size={52} aria-hidden="true" /><h2>A sua IAZELA está pronta</h2><p>A configuração foi guardada para toda a empresa.</p>
      {notice && <Notice onDismiss={() => setNotice("")} className="success" role="status">{notice}</Notice>}
      {!!state.pending?.length && <div className="setup-note"><b>Pode completar mais tarde</b><ul>{state.pending.map((item: string) => <li key={item}>{item}</li>)}</ul></div>}
      <div className="setup-next-actions"><ActionButton type="button" className="primary" onClick={() => { onClose(); onNavigate("Folha de caixa", "open"); }}><Wallet size={19} />Abrir primeira folha de caixa</ActionButton>
        <ActionButton type="button" disabled={!hasOpenSession} onClick={() => { onClose(); onNavigate("Movimentos reais", "movement"); }}>Registar primeiro movimento</ActionButton>
        <ActionButton type="button" onClick={() => { onClose(); onNavigate("Extratos"); }}><Landmark size={19} />Importar extrato</ActionButton>
        {data.mode.mode === "local" && <ActionButton type="button" onClick={() => { onClose(); onNavigate("Importar SAF-T de vendas"); }}><FileUp size={19} />Importar SAF-T de vendas</ActionButton>}
        {data.mode.mode === "integrated" && <ActionButton type="button" onClick={() => { onClose(); onNavigate("Integrações"); }}>Configurar ligação ao ERP</ActionButton>}</div>
      {!hasOpenSession && <p>Abra uma folha de caixa para registar o primeiro movimento.</p>}
      <ActionButton type="button" onClick={onClose}>Voltar ao painel</ActionButton>
    </div> : <div className="setup-layout">
      <nav className="setup-step-nav" aria-label="Etapas da configuração">{steps.map(([id, label], index) => <ActionButton type="button" key={id} disabled={busy} className={step === index ? "active" : ""} aria-current={step === index ? "step" : undefined} onClick={() => void go(index)}><span>{index + 1}</span>{label}</ActionButton>)}<p>As respostas são guardadas na empresa ao avançar ou escolher guardar.</p></nav>
      <ManagedForm className="setup-form" onSubmit={event => { event.preventDefault(); if (!busy) void (step < 8 ? go(step + 1) : run(async () => { const saved = await persist(8); const checked = await api("/setup/preview", { revision: saved.revision }); setSummary(checked); const finished = await api("/setup/finish", { revision: saved.revision, token: checked.token, confirmed: true }); hydrate(finished); setDone(true); try { await onChanged(); } catch { setNotice("Configuração guardada. Atualize a página para carregar os novos registos."); } })); }}>
        <div className="setup-content" aria-busy={busy}>
          <div className="setup-step-heading"><span>Etapa {step + 1} de {steps.length}</span><h3 ref={heading} tabIndex={-1}>{steps[step][1]}</h3></div>
          <fieldset disabled={busy} className="setup-fields">
            {step === 0 && <>
              <p>Confirme os dados já existentes e preencha o que falta. O estabelecimento identifica onde trabalham as caixas e as contas.</p>
              <div className="setup-grid"><Field label="Nome da empresa"><input value={data.company.name} maxLength={100} onChange={e => change("company", "name", e.target.value)} /></Field>
                <Field label="Nome comercial"><input value={data.company.trade_name} maxLength={100} onChange={e => change("company", "trade_name", e.target.value)} /></Field>
                <Field label="NIF"><input inputMode="numeric" value={data.company.nif} maxLength={9} onChange={e => change("company", "nif", e.target.value)} /></Field>
                <Field label="País"><select value={data.company.country} onChange={e => change("company", "country", e.target.value)}>{["PT", "ES", "FR", "DE", "GB", "BR", "AO", "CV", "MZ", "US"].map(code => <option key={code} value={code}>{new Intl.DisplayNames(["pt-PT"], { type: "region" }).of(code)}</option>)}</select></Field>
                <Field label="Moeda habitual"><select value={currency} onChange={e => change("company", "currency", e.target.value)}>{["EUR", "USD", "GBP", "BRL", "AOA", "CVE", "MZN", "CHF"].map(code => <option key={code} value={code}>{code}</option>)}</select></Field>
                <Field label="Fuso horário"><select value={data.company.timezone} onChange={e => change("company", "timezone", e.target.value)}>{Array.from(new Set([data.company.timezone, "Europe/Lisbon", "Atlantic/Azores", "Europe/Madrid", "Europe/London", "Europe/Paris", "America/Sao_Paulo", "Africa/Luanda", "Atlantic/Cape_Verde", "Africa/Maputo", "America/New_York", "UTC"])).map(value => <option key={value}>{value}</option>)}</select></Field>
                <Field label="Estabelecimento principal">{storeSelect(data.company.store_id, value => { change("company", "store_id", value); const found = state.stores.find((s: Row) => s.id === value); if (found) change("company", "store_name", found.name); })}</Field>
                <Field label="Nome do estabelecimento"><input value={data.company.store_name} maxLength={100} onChange={e => change("company", "store_name", e.target.value)} /></Field></div>
              <h4>Stock</h4>
              {(() => { const existing = (state.warehouses || []).filter((w: Row) => w.store_id === data.company.store_id); return <>
                {!!existing.length && <p>Armazéns já existentes neste estabelecimento: {existing.map((w: Row) => w.name).join(", ")}.</p>}
                <label className="setup-check"><input type="checkbox" checked={data.company.create_warehouse !== false} onChange={e => change("company", "create_warehouse", e.target.checked)} />{existing.length ? "Criar outro armazém de stock no estabelecimento principal" : "Criar um armazém de stock no estabelecimento principal (recomendado)"}</label>
                {data.company.create_warehouse !== false && <div className="setup-grid"><Field label="Nome do armazém"><input value={data.company.warehouse_name ?? "Armazém principal"} maxLength={100} onChange={e => change("company", "warehouse_name", e.target.value)} /></Field></div>}
                <p className="footnote">O armazém guarda as quantidades de stock das compras e dos inventários. Não é criado em duplicado se já existir com o mesmo nome. Pode acrescentar outros em Compras e Stock.</p>
              </>; })()}
              <h4>IVA</h4>
              <div className="setup-grid setup-vat"><VatRegimeFields regime={data.company.vat_regime || ""} period={data.company.vat_period || ""} onChange={(regime, period) => setData(previous => ({ ...previous, company: { ...previous!.company, vat_regime: regime, vat_period: period } }))} /></div>
              {currency !== "EUR" && <p className="setup-note">A moeda é aplicada às novas caixas e contas. Os saldos existentes conservam a sua moeda. A contagem por notas e moedas de euro deve ser substituída pela introdução do valor total nas restantes moedas.</p>}
            </>}
            {step === 1 && <>
              <h4>Como pretende trabalhar com a IAZELA?</h4>
              <div className="setup-mode-options">{[["local", "Modo local autónomo", "Caixas, bancos, recebimentos e pagamentos na IAZELA. Introduza manualmente o faturado diário para conferir."], ["integrated", "Ligação a ERP", "Ligue um conector disponível e escolha os dados que pretende importar."]].map(([value, label, explanation]) => <label className={data.mode.mode === value ? "selected" : ""} key={value}><input type="radio" name="setup-mode" value={value} checked={data.mode.mode === value} onChange={() => change("mode", "mode", value)} /><strong>{label}</strong><span>{explanation}</span></label>)}</div>
              <p className="setup-note">Autónomo significa independente de ERP. O acesso à IAZELA e à base de dados pode continuar a precisar de Internet.</p>
              {data.mode.mode === "integrated" && <>
                <div className="setup-grid"><Field label="Conector ERP"><select value={data.mode.provider} onChange={e => change("mode", "provider", e.target.value)}>{state.connectors.map((c: Row) => <option key={c.id} value={c.id}>{c.name}</option>)}</select></Field>
                  <Field label="Empresa no ERP"><input value={data.mode.enterprise} maxLength={100} placeholder="Identificador da empresa na ligação ERP" onChange={e => change("mode", "enterprise", e.target.value)} /></Field>
                  <Field label="Chave de acesso ERP"><input type="password" autoComplete="new-password" value={data.mode.api_key || ""} placeholder={state.has_api_key ? "Chave já guardada; preencha apenas para substituir" : "Introduza a chave da ligação"} onChange={e => change("mode", "api_key", e.target.value)} /></Field></div>
                <Hint><p>A empresa é identificada pelo código usado pelo conector Wintouch. As caixas importadas podem ser associadas a estabelecimentos da IAZELA na etapa seguinte. A chave é cifrada e nunca é devolvida pelo servidor.</p><Field label="Endereço da API ERP"><input value={data.mode.api_url} onChange={e => change("mode", "api_url", e.target.value)} /></Field></Hint>
                <div className="setup-inline-actions"><ActionButton type="button" onClick={() => void run(async () => { const saved = await persist(); const checked = await api("/setup/erp/test", { revision: saved.revision }); hydrate(checked); setNotice(checked.test_message); })}>Testar ligação</ActionButton><span className={`setup-connection ${state.connection_tested ? "confirmed" : ""}`}>{state.connection_tested ? "Ligação testada com sucesso" : "Ligação pendente de teste"}</span></div>
                <h4>Dados disponíveis para importar</h4><div className="setup-check-grid">{Object.entries(state.resource_labels).map(([id, label]) => <label key={id}><input type="checkbox" checked={data.mode.resources.includes(id)} onChange={e => change("mode", "resources", e.target.checked ? [...data.mode.resources, id] : data.mode.resources.filter((r: string) => r !== id))} />{String(label)}</label>)}</div>
                <p>O assistente pré-visualiza os catálogos de caixas, entidades e artigos. Documentos, movimentos e rubricas mantêm os mecanismos de importação e conferência na área Integrações.</p>
                {boot.mode === "demonstracao" && <label className="setup-check"><input type="checkbox" checked={confirmDemo} onChange={e => setConfirmDemo(e.target.checked)} />Confirmo a leitura de dados reais do ERP nesta empresa de demonstração.</label>}
                <label className="setup-check"><input type="checkbox" checked={includeERPMethods} onChange={e => setIncludeERPMethods(e.target.checked)} />Incluir os meios de pagamento ERP para associação</label>
                <ActionButton type="button" disabled={!state.connection_tested || (boot.mode === "demonstracao" && !confirmDemo) || !includeERPMethods && !data.mode.resources.some((r: string) => ["cash_accounts", "entities", "products"].includes(r))} onClick={() => void run(async () => { const saved = await persist(); const result = await api("/setup/erp/preview", { revision: saved.revision, resources: [...data.mode.resources.filter((r: string) => ["cash_accounts", "entities", "products"].includes(r)), ...(includeERPMethods ? ["payment_methods"] : [])], confirm_demo: confirmDemo }); setState(previous => ({ ...previous, revision: result.revision })); setImportPreview(result); })}>Pré-visualizar importação inicial</ActionButton>
                {importPreview && <section className="setup-import-preview"><h4>O que será importado</h4>{Object.entries(importPreview.resources).map(([resource, value]) => <div key={resource}><b>{state.catalog_labels[resource]}: {(value as Row).count}</b><ul>{(value as Row).examples.map((item: Row) => <li key={item.id}>{item.name}</li>)}</ul></div>)}<p>Os catálogos são atualizados pela identidade ERP. Esta importação não cria receitas, pagamentos ou saldos.</p><ActionButton type="button" className="primary" onClick={() => void run(async () => { const result = await api("/setup/erp/import", { revision: importPreview.revision, token: importPreview.token, confirmed: true }); hydrate(result); setImportPreview(null); setNotice("Catálogos importados. Pode associar as caixas existentes na etapa Caixas."); })}>Confirmar importação dos catálogos</ActionButton></section>}
                <label className="setup-check"><input type="checkbox" checked={data.mode.defer} onChange={e => change("mode", "defer", e.target.checked)} />Concluir com a ligação pendente e configurar mais tarde</label>
              </>}
            </>}
            {step === 2 && <>
              <p>Quantas caixas pretende gerir? Reutilize as caixas existentes ou acrescente as que faltam.</p>
              <div className="setup-inline-actions"><Field label="Número de caixas"><input type="number" min={0} max={100} value={data.registers.length} onChange={e => { const count = Math.max(0, Math.min(100, Number(e.target.value))); setData(previous => ({ ...previous, registers: Array.from({ length: count }, (_, i) => previous!.registers[i] || { key: key(), name: i ? `Caixa ${i + 1}` : "Caixa principal", store_id: data.company.store_id, responsible: "", start_date: today(), fund: "0,00", opening: null, count_required: true, balance_mode: "fixed", erp_id: null }) })); }} /></Field></div>
              <Hint><p>O fundo habitual é o montante que pretende manter na gaveta. O saldo inicial é todo o numerário existente na data de início. É registado uma única vez como saldo de partida, sem ser tratado como venda ou receita.</p><p>Retirar um registo existente deste assistente conserva os seus dados e o respetivo histórico na IAZELA.</p></Hint>
              {data.registers.map((r: Row, i: number) => <section className="setup-record" key={r.key}><div className="setup-record-title"><h4><Wallet size={19} />{r.name || `Caixa ${i + 1}`}</h4>{deleteButton("registers", i)}</div><div className="setup-grid">
                <Field label={`Nome da caixa ${i + 1}`}><input value={r.name} maxLength={100} onChange={e => itemChange("registers", i, "name", e.target.value)} /></Field>
                <Field label={`Estabelecimento da caixa ${i + 1}`}>{storeSelect(r.store_id, value => itemChange("registers", i, "store_id", value))}</Field>
                <Field label={`Responsável da caixa ${i + 1}`}><select value={r.responsible || ""} onChange={e => itemChange("registers", i, "responsible", e.target.value)}><option value="">Escolher mais tarde</option>{state.responsibles.map((p: Row) => <option key={p.id} value={p.id}>{p.name}</option>)}</select></Field>
                <Field label={`Data de início da caixa ${i + 1}`}><input type="date" value={r.start_date || ""} onChange={e => itemChange("registers", i, "start_date", e.target.value || null)} /></Field>
                <Field label={`Fundo habitual da caixa ${i + 1} (${currency})`}><input inputMode="decimal" value={r.fund} onChange={e => itemChange("registers", i, "fund", e.target.value)} /></Field>
                <Field label={`Saldo inicial da caixa ${i + 1} (${currency})`}><input inputMode="decimal" value={r.opening || ""} placeholder="Opcional" onChange={e => itemChange("registers", i, "opening", e.target.value || null)} /></Field>
                <Field label={`Saldo para o dia seguinte da caixa ${i + 1}`}><select value={r.balance_mode} onChange={e => itemChange("registers", i, "balance_mode", e.target.value)}><option value="fixed">Repor o fundo habitual</option><option value="carry">Transportar o último saldo contado</option></select></Field>
                {data.mode.mode === "integrated" && <Field label={`Caixa ERP associada ${i + 1}`}><select value={r.erp_id || ""} onChange={e => itemChange("registers", i, "erp_id", e.target.value || null)}><option value="">Sem associação</option>{state.erp_accounts.map((account: Row) => <option key={account.id} value={account.external_id}>{account.name}</option>)}</select></Field>}
              </div><label className="setup-check"><input type="checkbox" checked={r.count_required} onChange={e => itemChange("registers", i, "count_required", e.target.checked)} />Contagem obrigatória no fecho</label></section>)}
              {data.mode.mode === "integrated" && state.erp_accounts.filter((a: Row) => !data.registers.some((r: Row) => r.erp_id === a.external_id)).map((remote: Row) => <ActionButton key={remote.id} type="button" onClick={() => add("registers", { key: key(), name: remote.name, store_id: data.company.store_id, fund: "0,00", opening: null, start_date: today(), count_required: true, balance_mode: "fixed", erp_id: remote.external_id })}><Plus size={16} />Usar caixa ERP: {remote.name}</ActionButton>)}
            </>}
            {step === 3 && <>
              <p>Um banco pode ter várias contas da sua empresa. Crie cada conta separadamente para identificar os saldos, os extratos e os recebimentos.</p>
              <Hint><p>O IBAN é opcional, validado quando preenchido e guardado de forma cifrada. O saldo inicial é uma referência de início do dia; o saldo do extrato passa a prevalecer quando o importar. Não é uma receita.</p></Hint>
              {data.accounts.map((a: Row, i: number) => <section className="setup-record" key={a.key}><div className="setup-record-title"><h4><Landmark size={19} />{a.name || `Conta ${i + 1}`}</h4>{deleteButton("accounts", i)}</div><div className="setup-grid">
                <Field label={`Banco da conta ${i + 1}`}><input list="setup-banks" value={a.bank} maxLength={100} onChange={e => { itemChange("accounts", i, "bank", e.target.value); itemChange("accounts", i, "bank_id", state.banks.find((b: Row) => b.name === e.target.value)?.id || null); }} /></Field>
                <Field label={`Nome da conta ${i + 1}`}><input value={a.name} maxLength={100} onChange={e => itemChange("accounts", i, "name", e.target.value)} /></Field>
                <Field label={`Estabelecimento da conta ${i + 1}`}>{storeSelect(a.store_id, value => itemChange("accounts", i, "store_id", value))}</Field>
                <Field label={`IBAN da conta ${i + 1}`}><input autoComplete="off" value={a.iban || ""} placeholder={state.iban_configured.includes(a.key) ? "IBAN guardado; preencha para substituir" : "Opcional"} onChange={e => itemChange("accounts", i, "iban", e.target.value)} /></Field>
                <Field label={`Moeda da conta ${i + 1}`}><select value={a.currency} onChange={e => itemChange("accounts", i, "currency", e.target.value)}>{Array.from(new Set([a.currency, currency, "EUR", "USD", "GBP", "BRL", "AOA", "CVE", "MZN"])).map(code => <option key={code}>{code}</option>)}</select></Field>
                <Field label={`Saldo inicial da conta ${i + 1} (${a.currency})`}><input inputMode="decimal" value={a.opening || ""} placeholder="Opcional" onChange={e => itemChange("accounts", i, "opening", e.target.value || null)} /></Field>
                <Field label={`Data do saldo inicial da conta ${i + 1}`}><input type="date" value={a.opening_date || ""} onChange={e => itemChange("accounts", i, "opening_date", e.target.value || null)} /></Field>
              </div><label className="setup-check"><input type="radio" name="principal-account" checked={a.principal} onChange={() => setData(previous => ({ ...previous, accounts: previous!.accounts.map((account: Row, index: number) => ({ ...account, principal: index === i })) }))} />Conta principal</label></section>)}
              <datalist id="setup-banks">{state.banks.map((bank: Row) => <option key={bank.id} value={bank.name} />)}</datalist>
              <ActionButton type="button" onClick={() => add("accounts", { key: key(), name: "", bank: "", bank_id: null, store_id: data.company.store_id, currency, iban: "", opening: null, opening_date: null, principal: data.accounts.length === 0 })}><Plus size={17} />Adicionar conta bancária</ActionButton>
            </>}
            {step === 4 && <>
              <p>Confirme apenas os meios que utiliza. Os meios existentes que retirar da seleção ficam inativos e conservam o histórico.</p>
              {data.payments.map((p: Row, i: number) => <section className={`setup-record setup-payment ${p.selected ? "selected" : ""}`} key={p.key}>
                <label className="setup-check setup-payment-title"><input type="checkbox" checked={p.selected} disabled={p.key === "numerario"} onChange={e => itemChange("payments", i, "selected", e.target.checked)} /><b>{p.name || "Meio personalizado"}</b>{p.key === "numerario" && <small>Obrigatório para a gaveta</small>}</label>
                {p.selected && <><div className="setup-grid"><Field label={`Nome do meio de pagamento ${i + 1}`}><input value={p.name} maxLength={100} onChange={e => itemChange("payments", i, "name", e.target.value)} /></Field>
                  {p.bank_receivable && <Field label={`Conta de destino de ${p.name}`}>{accountSelect(p.account, value => itemChange("payments", i, "account", value || null), "Definir depois / por terminal")}</Field>}
                  {p.key === "mbway" && <Field label="Liquidação do MB WAY"><select value={p.mbway_via} onChange={e => itemChange("payments", i, "mbway_via", e.target.value)}><option value="account">Serviço ou conta própria</option><option value="terminal">Através de um terminal TPA</option></select></Field>}
                  {!!state.erp_methods.length && <Field label={`Meio ERP para ${p.name}`}><select value={p.erp_id || ""} onChange={e => itemChange("payments", i, "erp_id", e.target.value || null)}><option value="">Sem associação</option>{state.erp_methods.map((m: Row) => <option key={m.id} value={m.id}>{m.name}</option>)}</select></Field>}
                </div><div className="setup-check-grid"><label><input type="checkbox" checked={p.cash} disabled={["numerario", "cartao", "mbway", "transferencia"].includes(p.key)} onChange={e => { itemChange("payments", i, "cash", e.target.checked); if (e.target.checked) itemChange("payments", i, "bank_receivable", false); }} />Movimenta numerário da gaveta</label>
                  <label><input type="checkbox" checked={p.bank_receivable} disabled={p.cash} onChange={e => itemChange("payments", i, "bank_receivable", e.target.checked)} />Gera valor a receber no banco</label>
                  <label><input type="checkbox" checked={p.receipts} disabled={p.key === "numerario"} onChange={e => itemChange("payments", i, "receipts", e.target.checked)} />Recebimentos</label>
                  <label><input type="checkbox" checked={p.payments} disabled={p.key === "numerario"} onChange={e => itemChange("payments", i, "payments", e.target.checked)} />Pagamentos</label></div></>}
              </section>)}
              <ActionButton type="button" onClick={() => add("payments", { key: key(), name: "", selected: true, cash: false, bank_receivable: false, account: null, receipts: true, payments: true, erp_id: null, mbway_via: "account" })}><Plus size={17} />Adicionar meio personalizado</ActionButton>
              <Hint><p>Os meios que movimentam numerário são conferidos em conjunto na gaveta. Os recebimentos em TPA e MB WAY são conferidos separadamente da liquidação bancária, para evitar contar a mesma receita duas vezes.</p></Hint>
            </>}
            {step === 5 && <>
              <h4>Em que conta bancária recebe os pagamentos deste terminal?</h4><p>Pode configurar vários terminais com contas de destino diferentes. O prestador do terminal pode ser diferente do banco dessa conta.</p>
              {!data.payments.some((p: Row) => p.selected && (p.key === "cartao" || p.key === "mbway" && p.mbway_via === "terminal")) && <p className="setup-note">Não selecionou TPA nos meios de pagamento. Esta etapa é opcional.</p>}
              {data.terminals.map((t: Row, i: number) => <section className="setup-record" key={t.key}><div className="setup-record-title"><h4><CreditCard size={19} />{t.name || `Terminal ${i + 1}`}</h4>{deleteButton("terminals", i)}</div><div className="setup-grid">
                <Field label={`Nome do terminal ${i + 1}`}><input value={t.name} maxLength={100} onChange={e => itemChange("terminals", i, "name", e.target.value)} /></Field>
                <Field label={`Identificador do terminal ${i + 1}`}><input value={t.identifier || ""} maxLength={100} placeholder="Opcional" onChange={e => itemChange("terminals", i, "identifier", e.target.value)} /></Field>
                <Field label={`Estabelecimento do terminal ${i + 1}`}>{storeSelect(t.store_id, value => itemChange("terminals", i, "store_id", value))}</Field>
                <Field label={`Caixa do terminal ${i + 1}`}><select value={t.register_id || ""} onChange={e => itemChange("terminals", i, "register_id", e.target.value || null)}><option value="">Todas as caixas do estabelecimento</option>{registerOptions.map((r: Row) => <option key={r.key} value={r.key}>{r.name}</option>)}</select></Field>
                <Field label={`Prestador do terminal ${i + 1}`}><input value={t.provider || ""} maxLength={100} placeholder="Banco ou prestador do serviço" onChange={e => itemChange("terminals", i, "provider", e.target.value)} /></Field>
                <Field label={`Conta de liquidação do terminal ${i + 1}`}>{accountSelect(t.account, value => itemChange("terminals", i, "account", value))}</Field>
                <Field label={`Prazo de liquidação do terminal ${i + 1} (dias)`}><input type="number" min={0} max={90} value={t.settlement_days} onChange={e => itemChange("terminals", i, "settlement_days", Number(e.target.value))} /></Field>
                <Field label={`Forma de liquidação do terminal ${i + 1}`}><select value={t.settlement_mode} onChange={e => itemChange("terminals", i, "settlement_mode", e.target.value)}><option value="gross">Valor bruto</option><option value="net">Líquido de comissões</option></select></Field>
                <Field label={`Comissão percentual do terminal ${i + 1} (%)`}><input inputMode="decimal" value={t.commission_percent || ""} placeholder="Desconhecida / por configurar" onChange={e => itemChange("terminals", i, "commission_percent", e.target.value || null)} /></Field>
                <Field label={`Comissão fixa do terminal ${i + 1} (${currency})`}><input inputMode="decimal" value={t.commission_fixed || ""} placeholder="Desconhecida / por configurar" onChange={e => itemChange("terminals", i, "commission_fixed", e.target.value || null)} /></Field>
                <Field label={`Descrição habitual no extrato do terminal ${i + 1}`}><input value={t.statement_reference || ""} maxLength={200} placeholder="Opcional" onChange={e => itemChange("terminals", i, "statement_reference", e.target.value)} /></Field>
              </div><label className="setup-check"><input type="checkbox" checked={t.mbway} onChange={e => itemChange("terminals", i, "mbway", e.target.checked)} />O MB WAY também é liquidado por este terminal</label></section>)}
              <ActionButton type="button" onClick={() => add("terminals", { key: key(), name: `TPA ${data.terminals.length + 1}`, identifier: "", store_id: data.company.store_id, register_id: data.registers[0]?.key || null, provider: "", account: "", settlement_days: 1, settlement_mode: "gross", commission_percent: null, commission_fixed: null, statement_reference: "", mbway: false })}><Plus size={17} />Adicionar terminal TPA</ActionButton>
              <Hint><p>Receber do cliente e receber a liquidação no banco são momentos diferentes da mesma operação. O extrato é associado aos recebimentos existentes na reconciliação. As comissões são confirmadas nessa altura e não criam uma segunda receita.</p><p>Com vários terminais, escolha o terminal ao registar um recebimento. Um total agregado de fecho sem terminal não é atribuído automaticamente a um banco quando existem vários destinos possíveis.</p></Hint>
            </>}
            {step === 7 && <SaftSetupStep local={data.mode.mode === "local"} block={data.preferences.saft_block_unmapped !== false} onBlock={value => change("preferences", "saft_block_unmapped", value)} onNavigate={page => void navigate(page)} saveRef={saftSave} />}
            {step === 6 && <>
              <p>Escolha as preferências que ajudam no trabalho diário. As integrações facultativas podem ficar para mais tarde.</p>
              <div className="setup-check-grid setup-preferences">{[["daily_close", "Conferência e fecho diário de caixa"], ["manual_realized", "Introdução manual do faturado no modo autónomo"], ["require_reason", "Exigir justificação para diferenças"], ["unclosed_warning", "Avisar sobre caixas por fechar"]].map(([id, label]) => <label key={id}><input type="checkbox" checked={data.preferences[id]} onChange={e => change("preferences", id, e.target.checked)} />{label}</label>)}</div>
              <Field label="Categorias iniciais de entradas e saídas"><textarea value={data.preferences.categories.join("\n")} rows={5} onChange={e => change("preferences", "categories", e.target.value.split("\n"))} /></Field><small>Uma categoria por linha. As categorias existentes são reutilizadas.</small>
              <Field label="Arquivo dos documentos"><select value={data.preferences.archive} onChange={e => change("preferences", "archive", e.target.value)}><option value="later">Escolher mais tarde</option><option value="existing">Manter o arquivo já configurado</option><option value="drive">Google Drive</option></select></Field>
              {data.preferences.archive === "drive" && <ActionButton type="button" onClick={() => void navigate("Anexos")}>Configurar conta Google e pasta de arquivo</ActionButton>}
              <Hint><p>O arquivo usa as integrações já existentes de anexos e Google Drive. Os utilizadores com acesso à aplicação e os funcionários sem acesso continuam a ser geridos em Tabelas → Operadores / funcionários.</p><ActionButton type="button" onClick={() => void navigate("Operadores / funcionários")}>Gerir responsáveis e utilizadores</ActionButton></Hint>
            </>}
            {step === 8 && <>
              <p>Confirme os registos a criar ou associar. Os dados existentes e os saldos iniciais já registados são preservados.</p>
              <section className="setup-summary-section"><h4><Building2 size={18} />{data.company.trade_name || data.company.name}</h4><p>{data.company.name} · NIF {data.company.nif}</p><p>{data.mode.mode === "local" ? "Modo local autónomo" : `Ligação a ${state.connectors.find((c: Row) => c.id === data.mode.provider)?.name || "ERP"} · ${state.connection_tested && !data.mode.defer ? "Testada" : "Pendente"}`}</p><p>{data.company.country} · {currency} · {data.company.timezone}</p><p>{vatLabel(data.company.vat_regime, data.company.vat_period)}</p>{data.company.create_warehouse !== false && <p>Armazém de stock: {data.company.warehouse_name ?? "Armazém principal"}{(state.warehouses || []).some((w: Row) => w.store_id === data.company.store_id && w.name === (data.company.warehouse_name ?? "Armazém principal").trim()) ? " · Existente" : " · Novo"}</p>}</section>
              <section className="setup-summary-section"><h4>Caixas</h4>{data.registers.map((r: Row) => <p key={r.key}><b>{r.name}</b> · Fundo: {money(r.fund, currency)} · Saldo inicial: {money(r.opening, currency)}{r.erp_id ? " · Associada ao ERP" : r.id ? " · Existente" : " · Nova"}</p>)}</section>
              <section className="setup-summary-section"><h4>Contas bancárias</h4>{data.accounts.length ? data.accounts.map((a: Row) => <p key={a.key}><b>{a.bank} · {a.name}</b> · {a.currency}{a.principal ? " · Principal" : ""} · Saldo inicial: {money(a.opening, a.currency)}</p>) : <p>Pode configurar mais tarde.</p>}</section>
              <section className="setup-summary-section"><h4>Meios de pagamento</h4><p>{data.payments.filter((p: Row) => p.selected).map((p: Row) => p.name).join(" · ")}</p></section>
              <section className="setup-summary-section"><h4>Terminais e contas de liquidação</h4>{data.terminals.length ? data.terminals.map((t: Row) => <p key={t.key}><b>{t.name}</b> → {data.accounts.find((a: Row) => a.key === t.account || a.id === t.account)?.name || "Conta por escolher"} · {t.settlement_days} dia(s) · {t.settlement_mode === "net" ? "Líquido de comissões" : "Bruto"}</p>) : <p>Pode configurar mais tarde.</p>}</section>
              {!!summary?.pending?.length && <section className="setup-note"><h4>Configurações pendentes</h4><ul>{summary.pending.map((item: string) => <li key={item}>{item}</li>)}</ul></section>}
              {!summary && <ActionButton type="button" onClick={() => void run(async () => { const saved = await persist(8); setSummary(await api("/setup/preview", { revision: saved.revision })); })}>Atualizar e validar resumo</ActionButton>}
            </>}
          </fieldset>
          {notice && <Notice onDismiss={() => setNotice("")} className="success" role="status">{notice}</Notice>}
          {error && <Notice className="error" role="alert">{error}</Notice>}
        </div>
        <footer className="setup-footer"><span>{busy ? "A guardar…" : "Progresso partilhado pela empresa"}</span><div className="setup-footer-actions">
          <ActionButton type="button" disabled={busy || step === 0} onClick={() => void go(step - 1)}><ArrowLeft size={16} />Anterior</ActionButton>
          <ActionButton type="button" disabled={busy} onClick={() => void run(async () => { await persist(); onClose(); })}><Save size={16} />Guardar e continuar mais tarde</ActionButton>
          <ActionButton className="primary" disabled={busy} type="submit">{step === 8 ? "Guardar e começar" : "Guardar e continuar"}<ArrowRight size={16} /></ActionButton>
        </div></footer>
      </ManagedForm>
    </div>}
  </AppDialog>;
}

/** Etapa SAF-T: associação dos códigos de pagamento, política de importação e primeiro ficheiro. */
function SaftSetupStep({ local, block, onBlock, onNavigate, saveRef }: { local: boolean; block: boolean; onBlock: (value: boolean) => void; onNavigate: (page: string) => void; saveRef: { current: (() => Promise<void>) | null } }) {
  const [mapping, setMapping] = useState<Row | null>(null), [unavailable, setUnavailable] = useState(""), [dirty, setDirty] = useState(false);
  useEffect(() => {
    if (!local) return;
    let active = true;
    api("/saft/payment-mapping").then(result => { if (active) setMapping(result); }).catch(e => { if (active) setUnavailable((e as Error).message); });
    return () => { active = false; };
  }, [local]);
  if (!local) return <><p>A importação SAF-T de vendas destina-se ao modo local autónomo. No modo integrado, a faturação vem do ERP.</p><p>Pode avançar para o resumo.</p></>;
  const safe = Object.entries(mapping?.suggestions || {}).filter(([, s]: [string, any]) => s?.safe && s.id);
  // Só grava quando houve alterações; um erro impede o avanço e aparece no assistente.
  saveRef.current = mapping && dirty ? async () => { const result = await api("/saft/payment-mapping", { mapping: mapping.mapping }); setMapping(result); setDirty(false); } : null;
  useEffect(() => () => { saveRef.current = null; }, [saveRef]);
  function edit(next: Record<string, string>) { setMapping({ ...mapping, mapping: next }); setDirty(true); }
  return <>
    <p>O SAF-T de vendas acrescenta a faturação para análise e conferência. <strong>Não cria entradas em caixa, banco nem movimentos de stock.</strong></p>
    <label className="setup-check"><input type="checkbox" checked={block} onChange={e => onBlock(e.target.checked)} /> Impedir a confirmação de importações com códigos de pagamento por associar (recomendado). Sem esta opção, a aplicação apenas avisa e pede confirmação.</label>
    <h4>Associar códigos de pagamento SAF-T aos meios de pagamento</h4>
    {!mapping && !unavailable && <p role="status">A carregar códigos SAF-T…</p>}
    {unavailable && <Notice tone="info">A associação fica disponível depois de guardar o modo local e os meios de pagamento (conclua a configuração e volte a esta etapa, ou use Tesouraria → Importar SAF-T de vendas). {unavailable}</Notice>}
    {mapping && <>
      <div className="form-grid">{mapping.codes.map((code: string) => <Field key={code} label={`${code} · ${mapping.descriptions?.[code] || "Código SAF-T"}`}><select value={mapping.mapping[code] || ""} onChange={e => { const next = { ...mapping.mapping }; if (e.target.value) next[code] = e.target.value; else delete next[code]; edit(next); }}><option value="">Por associar</option>{mapping.methods.map((m: Row) => <option key={m.id} value={m.id}>{m.name}</option>)}</select></Field>)}</div>
      {safe.length > 0 && <div className="setup-inline-actions"><ActionButton type="button" onClick={() => edit({ ...Object.fromEntries(safe.map(([code, s]: [string, any]) => [code, s.id])), ...mapping.mapping })}>Aplicar {safe.length} sugestões seguras</ActionButton></div>}
      <small>A associação é guardada com «Guardar e continuar».</small>
      <small>Só associe quando o significado for inequívoco: MB (referência Multibanco) e DE (dinheiro eletrónico) não identificam, por si só, MB WAY ou TPA.</small>
    </>}
    <h4>Primeira importação</h4>
    <p>Depois de concluir, importe o XML SAF-T em Tesouraria → Importar SAF-T de vendas. Com o Google Drive ligado, os ficheiros da pasta configurada aparecem automaticamente. A importação pode propor criar os artigos em falta.</p>
    <ActionButton type="button" onClick={() => onNavigate("Importar SAF-T de vendas")}>Importar SAF-T agora</ActionButton>
  </>;
}
