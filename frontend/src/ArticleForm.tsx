import { formatQuantity } from "./lib/quantity";
import { ContextSuggestion } from "./components/OverviewUI";
import { useEffect, useId, useRef, useState, type FormEvent } from "react";
import { Barcode, Camera, ChevronLeft, ChevronRight, Coins, Copy, FileText, History, Home, Info, Leaf, Link2, Package, RefreshCw, ShoppingBag, ShoppingCart, StickyNote, Tag, TrendingUp, TriangleAlert, Truck, Warehouse } from "lucide-react";
import { Field } from "./components/Form";
import { ActionButton, ActionLink, ApiError, ManagedForm, Notice, fieldErrorSummary, markDialogSaved, reportFormError, useDialogWorking } from "./components/InteractionUI";
import { api } from "./lib/api";
import { articlePayload, articlePrices, validateArticle, type ArticleRow as Row } from "./articleValues";
import MarketPrices from "./MarketPrices";
import ArticleSupplierTerms from "./ArticleSupplierTerms";
import { StatusBadge, SyncStatus as SyncBadge } from "./components/BusinessUI";
import { RecordGroup, RecordHeader, RecordHistory, RecordIndicator, RecordNavigation } from "./components/RecordSheet";
import "./ArticleForm.css";

const sections = ["Geral", "Preços e rentabilidade", "Stock", "Compras e fornecedores", "Validades e lotes", "Integração ERP", "Histórico", "Descrição e anexos", "Vendas", "Compras"];
const sectionOrder = [0, 1, 2, 3, 8, 9, 7, 4, 6, 5];
const sectionLabels = ["Geral", "Preços", "Stocks", "Fornecedores", "Validades e lotes", "Integração ERP", "Histórico", "Documentos", "Vendas", "Compras"];
const sectionIcons = [Home, Coins, Warehouse, Truck, Leaf, Link2, History, FileText, ShoppingBag, ShoppingCart];
const fieldSection: Record<string, number> = { sale_net: 1, sale_gross: 1, vat_rate: 1, minimum: 2, reorder: 2, maximum: 2, location: 0, service: 0, lots: 4, expiry: 4, supplier_id: 3, supplier_reference: 0, description: 7, internal_notes: 0, additional_photos: 7, attachments: 7 };
const money = (v: any) => v === null || v === undefined ? "Sem informação" : new Intl.NumberFormat("pt-PT", { style: "currency", currency: "EUR" }).format(Number(v));
const quantity = (v: any) => formatQuantity(v);
const date = (v: any) => v ? new Intl.DateTimeFormat("pt-PT", { dateStyle: "short", timeZone: "Europe/Lisbon" }).format(new Date(v)) : "Sem informação";

export default function ArticleForm({ initial, catalog, readOnly = false, onSave, onNavigate, onSync, canSync, onDuplicate, onBarcodeSearch, recordNavigation, initialSection = 0 }: {
  initial: Row; catalog: Row; readOnly?: boolean; onSave: (value: Row) => Promise<void>;
  onNavigate: (tab: string, id: string) => void; onSync: () => void; canSync: boolean;
  onDuplicate?: () => Promise<void>; onBarcodeSearch?: (code: string) => Promise<void>;
  initialSection?: number;
  recordNavigation?: { position?: number; total?: number; busy: boolean; error: string; onMove: (delta: number, section: number) => Promise<void> };
}) {
  const [value, setValue] = useState<Row>(() => articlePayload(initial));
  // Ligar «Controlar lotes» num artigo já existente: o stock lançado sem lote passa para este lote.
  const [openingLot, setOpeningLot] = useState({ number: "", expiry: "" });
  const needsOpeningLot = !!initial.id && !initial.lots && !!value.lots;
  const [imageText, setImageText] = useState((initial.additional_photos || []).join("\n"));
  const [attachmentText, setAttachmentText] = useState((initial.attachments || []).map((a: Row) => a.name ? `${a.name} | ${a.url}` : a.url).join("\n"));
  const [tab, setTab] = useState(initialSection), [errors, setErrors] = useState<Record<string, string>>({});
  const [error, setError] = useState(""), [busy, setBusy] = useState(false);
  const [details, setDetails] = useState<Row | null>(null), [detailError, setDetailError] = useState("");
  const [attempt, setAttempt] = useState(0);
  const [failedPhoto, setFailedPhoto] = useState("");
  const form = useRef<HTMLFormElement>(null), saving = useRef(false);
  const id = useId();
  useDialogWorking(busy || !!recordNavigation?.busy);
  const erp = initial.origin === "ERP", rights = catalog.permissions || {};
  // Artigos da Wintouch: margens sobre o preço sem IVA da linha de preço 1 do ERP.
  const current = details?.current || initial;
  const insights = details?.insights;
  const prices = articlePrices(erp ? current.erp_price_net : value.sale_net, erp ? current.erp_vat_rate : value.vat_rate, rights.view_cost ? current.average_cost : null);
  const change = (key: string, next: any) => {
    setValue(old => ({ ...old, [key]: next }));
    setErrors(old => { const nextErrors = { ...old }; delete nextErrors[key]; return nextErrors; });
  };
  useEffect(() => {
    if (!initial.id || busy) return;
    const timer = window.setInterval(() => { if (document.visibilityState === "visible") setAttempt(v => v + 1); }, 60_000);
    return () => window.clearInterval(timer);
  }, [initial.id, busy]);
  useEffect(() => {
    if (!initial.id) return;
    let active = true;
    setDetailError("");
    api(`/stock/articles/${initial.id}/details`).then(result => { if (active) setDetails(result); })
      .catch(e => { if (active) setDetailError(e.message); });
    return () => { active = false; };
  }, [initial.id, initial.revision, attempt]);
  const revealErrors = (fields: Record<string, string>) => {
    const normalized = Object.fromEntries(Object.entries(fields).map(([key, message]) => [key.split(".")[0], message]));
    setErrors(normalized);
    const first = Object.keys(normalized)[0];
    if (first) {
      setTab(fieldSection[first] ?? 0);
      requestAnimationFrame(() => {
        const field = form.current?.querySelector<HTMLElement>(`[name="${first.replace(/[^a-z_]/g, "")}"]`);
        const details = field?.closest("details");
        if (details) details.open = true;
        field?.scrollIntoView({ block: "center", behavior: "smooth" });
        field?.focus({ preventScroll: true });
      });
    }
  };
  async function submit(event: FormEvent) {
    event.preventDefault();
    if (readOnly || saving.current || recordNavigation?.busy) return;
    const invalid = validateArticle(value, catalog.articles, initial.id);
    if (Object.keys(invalid).length) {
      revealErrors(invalid);
      const message = `Reveja: ${fieldErrorSummary(form.current, invalid)}.`;
      reportFormError(form.current, new ApiError(message, invalid));
      setError(message);
      return;
    }
    saving.current = true;
    setBusy(true); setError(""); setErrors({});
    try {
      await onSave({ ...value, code: value.code.trim(), name: value.name.trim(), precision: Number(value.precision), barcodes: value.barcodes.map((v: string) => v.trim()).filter(Boolean),
        ...(needsOpeningLot && openingLot.number.trim() ? { opening_lot: { number: openingLot.number.trim(), expiry: openingLot.expiry || null } } : {}) });
      markDialogSaved(form.current);
    } catch (e) {
      if (e instanceof ApiError) {
        const normalized = Object.fromEntries(Object.entries(e.fields).map(([key, message]) => [key.split(".")[0], message]));
        reportFormError(form.current, new ApiError(e.message, normalized)); revealErrors(normalized);
      }
      setError((e as Error).message);
    } finally { saving.current = false; setBusy(false); }
  }
  const text = (key: string, label: string, required = false, locked = false, list?: string) => <Field label={label}>
    <input name={key} value={value[key] ?? ""} required={required} readOnly={readOnly || locked} list={list} autoFocus={key === "code"}
      aria-invalid={!!errors[key] || undefined} onChange={e => change(key, e.target.value)} />
  </Field>;
  const numeric = (key: string, label: string, locked = false, max = 1e12, step = "any") => <Field label={label}>
    <input name={key} type="number" inputMode="decimal" min="0" max={max} step={step} value={value[key] ?? ""}
      readOnly={readOnly || locked} aria-invalid={!!errors[key] || undefined} onChange={e => change(key, e.target.value)} />
  </Field>;
  const gross = (label = "Preço com IVA (€)") => <Field label={label}>
    <input name="sale_gross" type="number" inputMode="decimal" min="0" step="any" readOnly={readOnly || !rights.change_price}
      value={prices.gross === null ? "" : Number(prices.gross.toFixed(6))}
      onChange={e => change("sale_net", e.target.value === "" ? "" : String(Number((Number(e.target.value) / (1 + Number(value.vat_rate) / 100)).toFixed(6))))} />
  </Field>;
  const readonly = (label: string, v: any) => <div className="article-fact"><span>{label}</span><strong>{v ?? "—"}</strong></div>;
  const navigate = (section: string) => onNavigate(section, initial.id);
  // Google Imagens num separador novo, pela designação e código de barras; o endereço da imagem escolhida é colado na ficha.
  const imageTerms = [value.name, value.barcodes.find((v: string) => v.trim())].map(v => String(v || "").trim()).filter(Boolean).join(" ");
  const imageSearch = <div className="article-image-search">
    <ActionLink className="ui-action" href={`https://www.google.com/search?tbm=isch&q=${encodeURIComponent(imageTerms)}`} target="_blank" rel="noopener noreferrer"
      aria-disabled={!imageTerms || undefined} onClick={e => { if (!imageTerms) e.preventDefault(); }}>Pesquisar imagens no Google</ActionLink>
    <small>{imageTerms ? "Abre num separador novo. Na imagem escolhida, use «Copiar endereço da imagem» e cole o endereço HTTPS aqui." : "Indique a designação ou o código de barras para pesquisar."}</small>
  </div>;
  const suppliers: Row[] = catalog.suppliers || [];
  const supplier = suppliers.find(s => s.id === value.supplier_id);
  const list = (key: string) => `${id}-${key}`;
  const unitQuantity = (v: any) => v === null || v === undefined ? "Sem dados" : `${quantity(v)} ${value.unit}`;
  const syncState = !erp ? "apenas_local" : details?.sync_state === "falhou" ? "erro" : ["pendente", "em_curso"].includes(details?.sync_state) ? "pendente" : initial.synced_at ? "sincronizado" : null;
  const periodValue = (key: string) => !initial.id ? "Por criar" : detailError ? "Indisponível" : !details ? "A carregar…" : unitQuantity(insights?.[key]);
  return <ManagedForm ref={form} onSubmit={submit} noValidate className={`article-form${readOnly ? " is-readonly" : ""}`} aria-busy={busy || undefined}
    onKeyDown={e => { if (!readOnly && (e.ctrlKey || e.metaKey) && e.key.toLowerCase() === "s") { e.preventDefault(); form.current?.requestSubmit(); } }}>
    <RecordHeader title="Ficha de artigo" icon={Package} subtitle={readOnly ? "Consulta" : initial.id ? "Editar artigo" : "Novo artigo"} actions={<>
      {recordNavigation && <div className="article-record-navigation" role="group" aria-label="Percorrer artigos">
        <ActionButton type="button" variant="quiet" aria-label="Artigo anterior" title="Artigo anterior" disabled={busy || recordNavigation.busy || recordNavigation.position == null || recordNavigation.position <= 0} onClick={() => void recordNavigation.onMove(-1, tab)}><ChevronLeft size={19} /></ActionButton>
        {recordNavigation.position != null && recordNavigation.total != null && <span aria-live="polite">{recordNavigation.position + 1} / {recordNavigation.total}</span>}
        <ActionButton type="button" variant="quiet" aria-label="Artigo seguinte" title="Artigo seguinte" disabled={busy || recordNavigation.busy || recordNavigation.position == null || recordNavigation.total == null || recordNavigation.position + 1 >= recordNavigation.total} onClick={() => void recordNavigation.onMove(1, tab)}><ChevronRight size={19} /></ActionButton>
      </div>}
      {!readOnly && <ActionButton type="submit" variant="primary" className="primary" disabled={busy}>{busy ? "A guardar…" : "Guardar"}</ActionButton>}
      {onDuplicate && rights.adjust_stock && initial.id && <ActionButton type="button" disabled={busy} onClick={() => void onDuplicate()}><Copy size={16} />Duplicar</ActionButton>}
      {initial.id && <ActionButton type="button" variant="quiet" disabled={busy} aria-label="Atualizar informação do artigo" onClick={() => setAttempt(v => v + 1)}><RefreshCw size={17} /></ActionButton>}
        <details className="article-help"><summary aria-label="Informação da ficha do artigo"><Info size={19} aria-hidden="true" /></summary>
          <div className="article-help-box" role="note"><strong>Sobre esta ficha</strong>
            <p>Os campos com * são obrigatórios. Pode guardar o artigo com os dados de Geral. Utilize a navegação lateral para consultar preços, stocks e histórico.</p>
            <p>{erp ? "Artigo da Wintouch: código, nome, estado e preço vêm do ERP e só se alteram na origem. Os restantes dados e o stock são geridos aqui." : "Artigo local: os dados são guardados nesta aplicação."}</p>
            <p>A fotografia principal acompanha todos os separadores. Defina o endereço HTTPS em Geral; as imagens adicionais e fichas técnicas ficam em Descrição e anexos.</p>
            <p>Peso, volume, prazo genérico de validade, etiquetas comerciais e tabelas adicionais de preços não estão registados nesta ficha. A rotatividade exige um histórico de stock médio que ainda não existe.</p>
          </div>
        </details>
    </>} />
    <div className="article-sheet-layout"><aside className="article-sheet-sidebar">
      <RecordNavigation prefix={id} label="Ficha do artigo" active={tab} onSelect={setTab} items={sectionOrder.map(index => ({ id: index, label: sectionLabels[index], accessibleLabel: sections[index], icon: sectionIcons[index], invalid: Object.keys(errors).some(key => (fieldSection[key] ?? 0) === index) }))} />
    </aside><fieldset className="ui-form-fields article-sheet-main" disabled={busy || !!recordNavigation?.busy}>
      {recordNavigation?.error && <Notice tone="error">{recordNavigation.error}</Notice>}
      <section className="article-identity record-sheet-group" aria-label="Identificação do artigo">
        <div className="article-photo-card">
          <div className="article-image">{value.photo && failedPhoto !== value.photo ? <img src={value.photo} alt={value.name ? `Fotografia de ${value.name}` : "Fotografia do produto"} referrerPolicy="no-referrer" onError={() => setFailedPhoto(value.photo)} onLoad={() => setFailedPhoto("")} /> : <><Package size={38} aria-hidden="true" /><span>{value.photo ? "Fotografia indisponível" : "Fotografia do produto"}</span></>}</div>
          {!readOnly && <details className="article-photo-editor"><summary aria-label={value.photo ? "Alterar fotografia" : "Definir fotografia"}><Camera size={16} aria-hidden="true" /></summary><div className="article-photo-popover">{text("photo", "URL da fotografia (HTTPS)")}<small>Imagem alojada por HTTPS.</small>{imageSearch}{value.photo && <ActionButton type="button" confirmation={{ title: "Retirar fotografia da ficha?", message: "A associação será retirada ao guardar. O ficheiro no alojamento de origem é preservado.", confirmLabel: "Retirar fotografia", tone: "warning" }} onClick={() => change("photo", "")}>Retirar fotografia da ficha</ActionButton>}</div></details>}
        </div>
        <div className="article-identity-fields"><div className="article-identity-top">
          {text("code", "Código interno", true, erp)}{text("name", "Designação", true, erp)}
          <label className="article-active-switch">Ativo<input type="checkbox" role="switch" name="active" checked={!!value.active} disabled={readOnly || erp} onChange={e => change("active", e.target.checked)} /><span aria-hidden="true" /></label>
        </div><div className="article-identity-bottom">
          {text("family", "Família", false, false, list("family"))}{text("category", "Subfamília / categoria", false, false, list("category"))}{text("brand", "Marca", false, false, list("brand"))}{text("unit", "Unidade", true, false, list("unit"))}
          <Field label="Tipo de artigo"><select name="service" disabled={readOnly} value={String(!!value.service)} onChange={e => setValue({ ...value, service: e.target.value === "true", ...(e.target.value === "true" ? { lots: false, expiry: false } : {}) })}><option value="false">Produto de stock</option><option value="true">Serviço (sem stock)</option></select></Field>
        </div><div className="article-identity-meta">{syncState ? <SyncBadge state={syncState} label={syncState === "pendente" ? "Catálogo a sincronizar" : syncState === "erro" ? "Erro no catálogo ERP" : undefined} /> : <StatusBadge tone="info">ERP · estado por consultar</StatusBadge>}<StatusBadge tone={value.active ? "success" : "neutral"}>{value.active ? "Ativo" : "Inativo"}</StatusBadge>{initial.id && <small>{value.code} · {erp ? "Wintouch" : "Local"}{details?.generated_at ? ` · Atualizado ${date(details.generated_at)}` : ""}</small>}</div></div>
      </section>
      <div className="article-indicators" aria-label="Resumo do artigo">
        <RecordIndicator label="Stock atual" value={value.service ? "Não aplicável" : initial.id ? unitQuantity(current.quantity) : "Por criar"} icon={Package} tone={current.quantity == null || value.service ? "neutral" : Number(current.quantity) < Number(value.minimum) ? "warning" : "success"} onOpen={() => setTab(2)} />
        <RecordIndicator label="Stock mínimo" value={value.service ? "Não aplicável" : unitQuantity(value.minimum)} icon={TriangleAlert} tone={!value.service && current.quantity != null && Number(current.quantity) < Number(value.minimum) ? "warning" : "neutral"} onOpen={() => setTab(2)} />
        <RecordIndicator label="Em encomenda" value={!rights.view_cost ? "Sem permissão" : periodValue("incoming_quantity")} icon={ShoppingCart} tone="info" onOpen={() => setTab(9)} />
        <RecordIndicator label="Vendido (30 dias)" value={periodValue("sold_30_days")} note="Movimentos de stock" icon={TrendingUp} tone="accent" onOpen={() => setTab(8)} />
        <RecordIndicator label="Margem comercial" value={!rights.view_cost ? "Sem permissão" : prices.marginPct == null ? "Sem dados" : `${quantity(prices.marginPct)} %`} icon={TrendingUp} tone={prices.margin == null || !rights.view_cost ? "neutral" : prices.margin < 0 ? "danger" : "success"} onOpen={() => setTab(1)} />
      </div>
      {detailError && <Notice tone="error">Não foi possível atualizar os dados do artigo. {detailError}<ActionButton type="button" onClick={() => setAttempt(v => v + 1)}>Voltar a tentar</ActionButton></Notice>}
      {rights.view_cost && prices.margin !== null && prices.margin < 0 && <ContextSuggestion title="Rever a rentabilidade do artigo" tone="warning"
        evidence={[`Artigo ${value.code || "em criação"}`,"Preço sem IVA inferior ao custo médio conhecido; cálculo da ficha atual, incluindo alterações ainda por guardar."]}
        actionLabel="Rever preços e rentabilidade" onReview={()=>setTab(1)}>O preço sem IVA está abaixo do custo médio.</ContextSuggestion>}
      {sections.map((label, index) => <section key={label} role="tabpanel" data-section={index} id={`${id}-panel-${index}`} aria-labelledby={`${id}-tab-${index}`} hidden={tab !== index} className="article-panel">
        {index === 0 && <>
          <div className="article-overview-grid">
            <RecordGroup title="Preços (IVA incluído)" icon={Coins} className="article-price-overview" action={<ActionButton type="button" variant="quiet" onClick={() => setTab(1)}>Ver preços</ActionButton>}>
              <dl className="article-price-list"><div className="is-pvp"><dt>{erp && current.erp_price_basis !== "Com IVA" ? "PVP com IVA não indicado" : "PVP"}</dt><dd>{money(erp ? current.erp_price_basis === "Com IVA" ? current.erp_price : null : prices.gross)}</dd></div>
                <div><dt>Preço sem IVA</dt><dd>{money(prices.net)}</dd></div><div><dt>IVA</dt><dd>{(erp ? current.erp_vat_rate : value.vat_rate) == null ? "Sem dados" : `${quantity(erp ? current.erp_vat_rate : value.vat_rate)} %`}</dd></div>
                {rights.view_cost && <><div><dt>Custo médio</dt><dd>{money(prices.cost)}</dd></div><div className={prices.margin != null && prices.margin < 0 ? "is-loss" : "is-margin"}><dt>Margem</dt><dd>{prices.marginPct == null ? "Não calculável" : `${quantity(prices.marginPct)} %`}</dd></div></>}
              </dl>{erp && <small className="article-source">Preço no ERP · {current.erp_price_basis || "Sem preço no ERP"}</small>}
            </RecordGroup>
            <RecordGroup title="Códigos e identificação" icon={Barcode}>
              <div className="article-identification-fields"><div className="article-barcode-input"><Field label="Códigos de barras (separados por |)"><input readOnly={readOnly} name="barcodes" value={value.barcodes.join("|")} onChange={e => change("barcodes", e.target.value.split("|"))} /></Field>{onBarcodeSearch && <ActionButton type="button" variant="quiet" disabled={!value.barcodes.some((v: string) => v.trim()) || busy} aria-label="Pesquisar artigo pelo código de barras" onClick={() => void onBarcodeSearch(value.barcodes.find((v: string) => v.trim())?.trim() || "")}><Barcode size={17} /></ActionButton>}</div>
              {text("supplier_reference", "Referência do fornecedor")}{text("location", "Localização")}</div>
            </RecordGroup>
            <RecordGroup title="Informação rápida" icon={Info}><dl className="article-quick-list">
              <div><dt>Reservado</dt><dd>{unitQuantity(current.reserved_quantity)}</dd></div><div><dt>Disponível</dt><dd>{unitQuantity(current.available_quantity)}</dd></div>
              <div><dt>Lotes</dt><dd>{value.lots ? "Controlados" : "Não controla"}</dd></div><div><dt>Validades</dt><dd>{value.expiry ? "Controladas por lote" : "Não controla"}</dd></div>
              <div><dt>Última compra</dt><dd>{!rights.view_cost ? "Sem permissão" : date(insights?.last_purchase || details?.last_purchase?.date)}</dd></div>
            </dl><ActionButton type="button" variant="quiet" onClick={() => setTab(4)}>Consultar lotes e validades</ActionButton></RecordGroup>
          </div>
          <div className="article-bottom-grid"><RecordGroup title="Observações" icon={StickyNote}><Field label="Observações internas"><textarea readOnly={readOnly} name="internal_notes" value={value.internal_notes} maxLength={4000} onChange={e => change("internal_notes", e.target.value)} placeholder="Informações úteis para a equipa…" /></Field></RecordGroup>
            <RecordGroup title="Estado e origem" icon={Tag}><div className="article-labels"><StatusBadge tone={value.active ? "success" : "neutral"}>{value.active ? "Ativo" : "Inativo"}</StatusBadge><StatusBadge tone="accent">{value.service ? "Serviço" : "Produto de stock"}</StatusBadge><StatusBadge tone="info">{erp ? "Wintouch" : "Artigo local"}</StatusBadge>{value.lots && <StatusBadge tone="neutral">Controlo de lotes</StatusBadge>}</div><p className="footnote">Classificações da ficha atual. Etiquetas comerciais personalizadas ainda não disponíveis.</p></RecordGroup></div>
          <details><summary>Mais detalhes</summary><div className="form-grid">{text("reference", "Referência")}{numeric("precision", "Casas decimais (0 a 6)", false, 6, "1")}</div><p className="footnote">Código manual; não existe numeração automática configurada. O catálogo atual gere artigos de stock. A unidade e o controlo de lotes ficam protegidos depois de existirem movimentos.</p></details>
          {["family", "category", "brand", "unit"].map(key => <datalist key={key} id={list(key)}>{[...new Set<string>((catalog.facets?.[key] || catalog.articles.map((a: Row) => a[key]).filter(Boolean)))].map(v => <option key={v} value={v} />)}</datalist>)}
        </>}
        {index === 1 && <>
          <h3>Preço de venda e margem bruta</h3>
          <p>A margem utiliza o preço sem IVA e o custo médio ponderado dos movimentos. Os valores da venda são editáveis quando o seu perfil o permite; os custos e as margens são calculados.</p>
          {!erp && <div className="form-grid">{numeric("sale_net", "Venda sem IVA (€)", !rights.change_price, 1e9)}{gross("Venda com IVA (€)")}{numeric("vat_rate", "Taxa de IVA (%)", !rights.change_price, 100)}</div>}
          {/* Nos artigos locais o preço já está nos campos acima: aqui só o que é calculado. */}
          <div className="article-facts">{erp && <>{readonly("Preço no ERP com IVA", money(current.erp_price_basis === "Com IVA" ? current.erp_price : null))}{readonly("Preço no ERP sem IVA", money(current.erp_price_net))}{readonly("IVA", current.erp_vat_rate == null ? "Não indicado pelo ERP" : `${quantity(current.erp_vat_rate)} %`)}</>}
            {rights.view_cost && <>{readonly("Custo médio ponderado", money(current.average_cost))}{readonly("Último custo de compra", money(current.last_cost))}{readonly("Margem bruta por unidade", money(prices.margin))}{readonly("Margem sobre a venda sem IVA", prices.marginPct === null ? "Não calculável" : `${quantity(prices.marginPct)} %`)}{readonly("Markup sobre o custo", prices.cost && prices.cost > 0 && prices.net !== null ? `${quantity((prices.net - prices.cost) / prices.cost * 100)} %` : "Não calculável")}</>}
          </div>
          {!rights.view_cost && <p>O seu perfil não permite consultar custos e margens.</p>}
          {erp && (current.erp_price_net == null || current.erp_vat_rate == null) && <p>A base de IVA do preço não está disponível no ERP; os valores em falta não são estimados.</p>}
          {!erp && initial.id && <section className="article-subsection" aria-labelledby={`${id}-price-history`}>
            <h4 id={`${id}-price-history`}>Histórico do preço de venda</h4>
            {details?.price_history?.length > 0
              ? <div className="table-wrap"><table className="responsive-data-table article-compact-table" aria-label="Histórico do preço de venda">
                <thead><tr><th>Data</th><th>Sem IVA</th><th>IVA</th><th>Com IVA</th><th>Alterado por</th></tr></thead>
                <tbody>{details!.price_history.map((p: Row, i: number) => <tr key={`${p.created_at}-${i}`}>
                  <td data-label="Data"><div>{date(p.created_at)}</div></td>
                  <td data-label="Sem IVA"><div>{money(p.sale_net)}</div></td>
                  <td data-label="IVA"><div>{quantity(p.vat_rate)} %</div></td>
                  <td data-label="Com IVA"><div>{money(Number(p.sale_net) * (1 + Number(p.vat_rate) / 100))}</div></td>
                  <td data-label="Alterado por"><div>{p.user_name || "Sem informação"}</div></td>
                </tr>)}</tbody>
              </table></div>
              : details ? <p>Sem alterações de preço registadas.</p> : null}
          </section>}
          <section className="article-subsection" aria-labelledby={`${id}-market`}>
            <h4 id={`${id}-market`}>Preços de mercado (KuantoKusta e outras lojas)</h4>
            {initial.id
              ? <MarketPrices articleId={initial.id} vatRate={Number(value.vat_rate) || 0} cost={rights.view_cost && !erp ? prices.cost : null} readOnly={readOnly} />
              : <p>Guarde o artigo para registar e comparar preços de mercado.</p>}
          </section>
          <p className="footnote">A ficha usa o preço local ou a linha de preço 1 do ERP. Não existem tabelas adicionais de preços configuradas nesta ficha.</p>
        </>}
        {index === 2 && <>
          <h3>Quantidades e controlo</h3>
          <p>O stock atual é a soma dos movimentos nos armazéns a que tem acesso. O reservado corresponde aos pedidos de clientes; o disponível considera armazéns ativos e exclui essas reservas e os lotes expirados, sem compensar faltas entre armazéns.</p>
          <div className="article-facts">{readonly("Stock atual", initial.id ? quantity(current.quantity) : "Sem movimentos")}{readonly("Stock reservado", quantity(current.reserved_quantity))}{readonly("Disponível para saída", quantity(current.available_quantity))}</div>
          <div className="form-grid">{numeric("minimum", "Stock mínimo")}{numeric("reorder", "Nível de reposição")}{numeric("maximum", "Stock máximo (0 = sem máximo)")}{readonly("Localização", value.location || "Sem localização")}</div>
          <p className="footnote">A localização é editável em Geral. O tipo de artigo, no cabeçalho, determina se movimenta stock.</p>
          {details?.warehouses?.length > 0 && <div className="article-warehouse-list"><h4>Stock por armazém autorizado</h4>{details?.warehouses.map((w: Row) => <div key={w.id}><span>{w.name}</span><strong>{quantity(w.quantity)} {value.unit}</strong></div>)}</div>}
          {initial.id && <div className="stock-actions"><ActionButton type="button" onClick={() => navigate("Movimentos")}>Consultar movimentos</ActionButton><ActionButton type="button" onClick={() => navigate("Lotes e validades")}>Consultar lotes e validades</ActionButton></div>}
          <p className="footnote">As correções usam «Registar movimento» ou um inventário, com motivo e histórico. As quantidades não se alteram nesta ficha.</p>
        </>}
        {index === 4 && <>
          <h3>Validades e lotes</h3>
          {[["lots", "Controlar lotes"], ["expiry", "Controlar validades"]].map(([key, title]) => <label key={key} className="stock-check"><input name={key} type="checkbox" checked={value[key]} disabled={readOnly || !!value.service} onChange={e => change(key, e.target.checked)} />{title}</label>)}
          {needsOpeningLot && <div className="article-opening-lot">
            <Notice tone="info">Se este artigo já tiver stock lançado sem lote, indique o lote (e a validade) que fica com esse stock. Ao guardar, o stock sem lote passa para este lote com dois ajustes registados (saída sem lote e entrada no lote, ao custo médio). A quantidade e o valor do stock não mudam e o histórico mantém-se.</Notice>
            <div className="form-grid">
              <Field label="Lote do stock existente"><input name="opening_lot" maxLength={100} disabled={readOnly} value={openingLot.number} onChange={e => setOpeningLot({ ...openingLot, number: e.target.value })} /></Field>
              {value.expiry && <Field label="Validade desse lote"><input type="date" disabled={readOnly} value={openingLot.expiry} onChange={e => setOpeningLot({ ...openingLot, expiry: e.target.value })} /></Field>}
            </div>
          </div>}
          <p className="footnote">Alertas de validade: {(catalog.settings?.alerts || []).join(", ")} dias antes da validade. O nível de reposição é a quantidade de referência para repor stock.</p>
          {details?.expiry_alerts?.length > 0 && <Notice tone="warning">Lotes com validade próxima ou ultrapassada: {details?.expiry_alerts.map((lot: Row) => `${lot.number} · ${date(lot.expiry)}`).join("; ")}. Consulte os lotes antes de registar saídas.</Notice>}
          {initial.id && <ActionButton type="button" onClick={() => navigate("Lotes e validades")}>Consultar lotes e validades</ActionButton>}
          <p>As datas e quantidades dos lotes são geridas no módulo de lotes, com as permissões e justificações existentes.</p>
        </>}
        {index === 3 && <>
          <h3>Dados de compra do artigo</h3>
          <div className="form-grid"><Field label="Fornecedor principal"><select disabled={readOnly} name="supplier_id" value={value.supplier_id || ""} onChange={e => change("supplier_id", e.target.value || null)}><option value="">Sem fornecedor definido</option>{suppliers.map(s => <option key={s.id} value={s.id}>{s.name}</option>)}</select></Field>{readonly("Referência do fornecedor", value.supplier_reference || "Sem referência")}</div>
          {supplier && <p>Fornecedor associado: <strong>{supplier.name}</strong>. As condições gerais do fornecedor são geridas na respetiva ficha, em Entidades.</p>}
          <div className="article-facts">{rights.view_cost && readonly("Último custo de compra registado", money(current.last_cost))}{readonly("Última compra nos armazéns autorizados", date(details?.last_purchase?.date))}{readonly("Documento de compra", details?.last_purchase?.document || "Sem informação")}</div>
          {initial.id && <section className="article-subsection" aria-labelledby={`${id}-suppliers`}>
            <h4 id={`${id}-suppliers`}>Compras por fornecedor</h4>
            {details?.purchase_suppliers?.length > 0
              ? <div className="table-wrap"><table className="responsive-data-table article-compact-table" aria-label="Compras por fornecedor">
                <thead><tr><th>Fornecedor</th><th>Compras</th><th>Quantidade</th>{rights.view_cost && <><th>Último custo</th><th>Mínimo / máximo</th><th>Custo médio</th></>}<th>Última compra</th></tr></thead>
                <tbody>{details!.purchase_suppliers.map((s: Row) => <tr key={s.supplier_id || "none"}>
                  <td data-label="Fornecedor"><div>{s.supplier_name}{s.main && <small> · principal</small>}</div></td>
                  <td data-label="Compras"><div>{s.purchases}</div></td>
                  <td data-label="Quantidade"><div>{quantity(s.quantity)} {value.unit}</div></td>
                  {rights.view_cost && <>
                    <td data-label="Último custo"><div>{money(s.last_cost)}</div></td>
                    <td data-label="Mínimo / máximo"><div>{s.min_cost === null ? "Sem informação" : `${money(s.min_cost)} / ${money(s.max_cost)}`}</div></td>
                    <td data-label="Custo médio"><div>{money(s.average_cost)}</div></td>
                  </>}
                  <td data-label="Última compra"><div>{date(s.last_date)}{s.last_document && <small> · {s.last_document}</small>}</div></td>
                </tr>)}</tbody>
              </table></div>
              : details ? <p>Ainda não há compras registadas deste artigo nos armazéns a que tem acesso.</p> : null}
          </section>}
          <section className="article-subsection" aria-labelledby={`${id}-terms`}>
            <h4 id={`${id}-terms`}>Condições por fornecedor</h4>
            {initial.id
              ? <ArticleSupplierTerms articleId={initial.id} suppliers={suppliers} purchases={details?.purchase_suppliers || []} unit={value.unit} readOnly={readOnly} />
              : <p>Guarde o artigo para registar condições por fornecedor.</p>}
          </section>
          <p className="footnote">A referência e o fornecedor principal pertencem a este artigo. As compras por fornecedor vêm das receções e dos movimentos de compra; entradas substituídas ou anuladas não contam. Custos sem IVA, por unidade.</p>
          {initial.id && <ActionButton type="button" onClick={() => navigate("Movimentos")}>Consultar histórico de compras e movimentos</ActionButton>}
        </>}
        {index === 7 && <>
          <h3>Descrição do artigo</h3><Field label="Descrição"><textarea readOnly={readOnly} name="description" value={value.description} maxLength={4000} onChange={e => change("description", e.target.value)} /></Field>
          <p className="footnote">As observações internas são editáveis em Geral e mantêm-se separadas da descrição do artigo.</p>
          {!readOnly && imageSearch}
          <Field label="Imagens adicionais — endereço HTTPS por linha"><textarea readOnly={readOnly} name="additional_photos" value={imageText} onChange={e => { setImageText(e.target.value); change("additional_photos", e.target.value.split("\n").map(v => v.trim()).filter(Boolean)); }} /></Field>
          <Field label="Anexos — nome | endereço HTTPS, um por linha"><textarea readOnly={readOnly} name="attachments" value={attachmentText} onChange={e => {
            setAttachmentText(e.target.value);
            change("attachments", e.target.value.split("\n").filter(v => v.trim()).map(line => {
              const separator = line.indexOf("|");
              return separator < 0 ? { name: "", url: line.trim() } : { name: line.slice(0, separator).trim(), url: line.slice(separator + 1).trim() };
            }));
          }} /></Field>
          <p className="footnote">Ligue imagens e fichas técnicas já alojadas por HTTPS. Até 10 imagens e 20 anexos; os ficheiros mantêm-se no alojamento de origem.</p>
          {!!value.additional_photos.length && <div className="article-gallery">{value.additional_photos.filter((url: string) => /^https:\/\//.test(url)).map((url: string, i: number) => <img key={`${i}-${url}`} src={url} alt={`Imagem adicional ${i + 1}`} referrerPolicy="no-referrer" loading="lazy" />)}</div>}
          {!!value.attachments.length && <ul>{value.attachments.filter((a: Row) => /^https:\/\//.test(a.url)).map((a: Row, i: number) => <li key={i}><a href={a.url} target="_blank" rel="noopener noreferrer">{a.name || "Consultar anexo"}</a></li>)}</ul>}
          <p className="footnote">A fotografia principal pode ser alterada no ícone da câmara, no cabeçalho da ficha.</p>
        </>}
        {index === 5 && <>
          <h3>{erp ? "Ligação ao ERP" : "Origem local"}</h3>
          <div className="article-facts">{readonly("Criado em", date(details?.created?.created_at))}{readonly("Criado por", details?.created?.user_name || "Sem informação")}{readonly("Última alteração em", date(details?.updated?.created_at))}{readonly("Alterado por", details?.updated?.user_name || "Sem informação")}</div>
          <div className="article-facts">{readonly("Origem", erp ? "Wintouch" : "Local")}{readonly("Empresa", details?.company_name || "Sem informação")}{readonly("Código no ERP", erp ? initial.code : "Não se aplica")}{erp && <>{readonly("Identificador no ERP", details?.erp?.external_id || "Sem informação")}{readonly("Empresa no ERP", details?.erp?.enterprise_id || "Sem informação")}{readonly("Última sincronização do catálogo", date(initial.synced_at))}{readonly("Estado de sincronização", initial.synced_at ? "Catálogo recebido" : "Sem informação")}</>}{readonly("Revisão da ficha", initial.revision ?? 0)}</div>
          {erp && <p>Os dados locais são guardados nesta aplicação. O conector atual importa o catálogo e não permite criar ou alterar artigos na Wintouch.</p>}
          {erp && details?.sync_state === "falhou" && <Notice tone="warning">A última sincronização do catálogo falhou. Consulte Integrações antes de atualizar os artigos.</Notice>}
          {erp && ["pendente", "em_curso"].includes(details?.sync_state) && <p role="status">Existe uma sincronização do catálogo pendente ou em curso.</p>}
          {!readOnly && canSync && <ActionButton type="button" onClick={onSync}>Importar / atualizar catálogo do ERP</ActionButton>}
          {!erp && <p>Não precisa de configurar um ERP para criar e gerir este artigo.</p>}

        </>}
        {index === 6 && <><RecordGroup title="Histórico de alterações" icon={History}><p className="footnote">Últimas 30 alterações da ficha e do preço, com responsável e data.</p><RecordHistory rows={(details?.history || []).map((entry: Row) => ({ id: entry.id, title: entry.label, detail: `${date(entry.created_at)} · ${entry.user_name || "Sem informação"}` }))} /></RecordGroup>
          {erp && <RecordGroup title="Sincronização do catálogo ERP" icon={RefreshCw}><RecordHistory rows={(details?.synchronizations || []).map((entry: Row) => ({ id: entry.id, title: ({ concluida: "Concluída", falhou: "Erro de sincronização", em_curso: "Em curso", pendente: "Pendente" } as Row)[entry.status] || entry.status, detail: `${date(entry.finished_at || entry.started_at)} · ${entry.read_count} registos lidos · ${entry.updated_count} atualizados` }))} empty="Sem histórico de sincronização disponível." /></RecordGroup>}</>}
        {[8, 9].includes(index) && <>
          {index === 9 && !rights.view_cost ? <Notice>O seu perfil não permite consultar compras e custos.</Notice> : <>
            <RecordGroup title={index === 8 ? "Vendas do artigo" : "Compras do artigo"} icon={index === 8 ? ShoppingBag : ShoppingCart}>
              <div className="article-facts">{readonly(index === 8 ? "Vendido nos últimos 30 dias" : "Quantidade em encomenda", periodValue(index === 8 ? "sold_30_days" : "incoming_quantity"))}{readonly(index === 8 ? "Última venda" : "Última compra", date(insights?.[index === 8 ? "last_sale" : "last_purchase"]))}{index === 8 && readonly("Rotatividade", "Indisponível: sem histórico de stock médio")}</div>
              <p className="footnote">{insights?.source || "Os indicadores usam os movimentos de stock nos armazéns autorizados. Guarde o artigo para consultar o histórico."}</p>
              {initial.id && !details && !detailError && <p role="status">A carregar os movimentos do artigo…</p>}
              <div className="article-mini-chart" aria-label={index === 8 ? "Evolução das vendas em quantidade" : "Evolução das compras em quantidade"}>{(insights?.monthly || []).map((month: Row) => {
                const key = index === 8 ? "sold" : "purchased", max = Math.max(1, ...(insights?.monthly || []).map((m: Row) => Math.abs(Number(m[key]) || 0)));
                return <div key={month.month}><span>{month.month.split("-").reverse().join("/")}</span><span className="article-mini-track"><i style={{ width: `${month[key] == null ? 0 : Math.abs(Number(month[key])) / max * 100}%` }} /></span><strong>{unitQuantity(month[key])}</strong></div>;
              })}</div>
            </RecordGroup>
            <RecordGroup title="Histórico documental" icon={FileText}><RecordHistory rows={(insights?.documents || []).filter((entry: Row) => index === 8 ? ["sale", "customer_return"].includes(entry.kind) : ["purchase", "supplier_return"].includes(entry.kind)).map((entry: Row) => ({ id: entry.id, title: entry.document || "Movimento sem referência documental", detail: `${date(entry.date)} · ${({ sale: "Venda", customer_return: "Devolução de cliente", purchase: "Compra", supplier_return: "Devolução a fornecedor" } as Row)[entry.kind]} · ${unitQuantity(Math.abs(Number(entry.quantity)))}` }))} empty="Sem movimentos documentais disponíveis nesta consulta." />
              {initial.id && <ActionButton type="button" onClick={() => navigate("Movimentos")}>Consultar todos os movimentos</ActionButton>}
            </RecordGroup>
          </>}
        </>}
        {initial.id && [1, 2, 3, 4, 5, 6].includes(index) && <>{detailError ? <Notice tone="error">{detailError}<ActionButton type="button" onClick={() => setAttempt(v => v + 1)}>Voltar a tentar</ActionButton></Notice> : !details && <p role="status">A carregar informação do artigo…</p>}</>}
      </section>)}
    </fieldset></div>
    {error && <Notice tone="error" role="alert">{error}</Notice>}
  </ManagedForm>;
}
