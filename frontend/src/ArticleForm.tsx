import { WebContentAssistant } from "./ArticleWebContent";
import ArticlePhotos from "./ArticlePhotos";
import { ArticleWebshop } from "./ArticleWebshop";
import { formatQuantity } from "./lib/quantity";
import { ContextSuggestion } from "./components/OverviewUI";
import { useEffect, useId, useRef, useState, type FormEvent } from "react";
import { Barcode, ChevronLeft, ChevronRight, Coins, Copy, FileText, History, Home, Info, Leaf, Link2, Package, Pencil, Plus, RefreshCw, ShoppingBag, ShoppingCart, StickyNote, Tag, TrendingUp, TriangleAlert, Truck, Warehouse } from "lucide-react";
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
const fieldSection: Record<string, number> = { web_summary: 7, web_highlights: 7, seo_keyword: 7, seo_title: 7, seo_description: 7, sale_net: 1, sale_gross: 1, erp_gross: 1, vat_rate: 1, minimum: 2, reorder: 2, maximum: 2, location: 0, service: 0, lots: 4, expiry: 4, supplier_id: 3, supplier_reference: 0, description: 7, internal_notes: 0, additional_photos: 7, attachments: 7 };
const money = (v: any) => v === null || v === undefined ? "Sem informação" : new Intl.NumberFormat("pt-PT", { style: "currency", currency: "EUR" }).format(Number(v));
const quantity = (v: any) => formatQuantity(v);
const date = (v: any) => v ? new Intl.DateTimeFormat("pt-PT", { dateStyle: "short", timeZone: "Europe/Lisbon" }).format(new Date(v)) : "Sem informação";
type ClassificationKey = "family" | "category" | "brand";
const classificationTypes = {
  family: { table: "article_families", singular: "família" },
  category: { table: "article_subfamilies", singular: "subfamília" },
  brand: { table: "article_brands", singular: "marca" },
};

export default function ArticleForm({ initial, catalog, readOnly = false, onSave, onNavigate, onSync, canSync, onDuplicate, onBarcodeSearch, recordNavigation, initialSection = 0 }: {
  initial: Row; catalog: Row; readOnly?: boolean; onSave: (value: Row, note?: string) => Promise<void>;
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
  // PVP de um artigo da Wintouch: editável na ficha e enviado à Wintouch ao gravar (só com escrita autorizada).
  const [erpPrice, setErpPrice] = useState<Row | null>(null), [erpGross, setErpGross] = useState("");
  const [classifications, setClassifications] = useState<Row>(catalog.classifications || {});
  const [classificationEditor, setClassificationEditor] = useState<{ key: ClassificationKey; record?: Row; name: string; familyId: string } | null>(null);
  const [classificationBusy, setClassificationBusy] = useState(false);
  const [classificationError, setClassificationError] = useState("");
  const [classificationNotice, setClassificationNotice] = useState("");
  const classificationSaving = useRef(false);
  useEffect(() => { setClassifications(catalog.classifications || {}); }, [catalog.classifications]);
  const form = useRef<HTMLFormElement>(null), saving = useRef(false);
  const id = useId();
  useDialogWorking(busy || classificationBusy || !!recordNavigation?.busy);
  const erp = initial.origin === "ERP", rights = catalog.permissions || {};
  // Artigos da Wintouch: margens sobre o preço sem IVA da linha de preço 1 do ERP.
  const current = details?.current || initial;
  const insights = details?.insights;
  const erpEditable = erp && !readOnly && !!erpPrice?.available;
  const erpOriginal = erpPrice?.gross == null ? "" : Number(erpPrice.gross).toFixed(2);
  const erpChanged = erpEditable && erpGross !== "" && Number(erpGross).toFixed(2) !== erpOriginal;
  const erpNet = erpChanged && current.erp_vat_rate != null ? Number(erpGross) / (1 + Number(current.erp_vat_rate) / 100) : null;
  const prices = articlePrices(erp ? erpNet ?? current.erp_price_net : value.sale_net, erp ? current.erp_vat_rate : value.vat_rate, rights.view_cost ? current.average_cost : null);
  useEffect(() => {
    setErpPrice(null); setErpGross("");
    if (!erp || !initial.id || readOnly || !rights.change_price) return;
    let active = true;
    api(`/stock/articles/${initial.id}/erp-price`).then(result => {
      if (!active) return;
      setErpPrice(result); setErpGross(result.gross == null ? "" : Number(result.gross).toFixed(2));
    }).catch(() => undefined);
    return () => { active = false; };
  }, [erp, initial.id, readOnly, rights.change_price]);
  const change = (key: string, next: any) => {
    setValue(old => ({ ...old, [key]: next, ...(key === "family" && next !== old.family ? { category: "" } : {}) }));
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
    if (readOnly || saving.current || recordNavigation?.busy || classificationEditor || classificationSaving.current) return;
    const invalid = validateArticle(value, catalog.articles, initial.id);
    if (Object.keys(invalid).length) {
      revealErrors(invalid);
      const message = `Reveja: ${fieldErrorSummary(form.current, invalid)}.`;
      reportFormError(form.current, new ApiError(message, invalid));
      setError(message);
      return;
    }
    if (erpChanged && !(Number(erpGross) > 0 && Number(erpGross) <= 1e7)) {
      revealErrors({ erp_gross: "Indique um PVP maior que zero." });
      setError("Reveja: PVP com IVA.");
      return;
    }
    saving.current = true;
    setBusy(true); setError(""); setErrors({});
    try {
      let note = "";
      if (erpChanged) {
        // Primeiro a Wintouch: só com o preço confirmado por leitura se grava a ficha.
        const sent = await api(`/stock/articles/${initial.id}/erp-price`, { gross: Number(erpGross).toFixed(2), expected_gross: erpPrice?.gross ?? null });
        setErpPrice(sent);
        if (sent.state !== "confirmed") {
          const message = sent.state === "failed" ? `A Wintouch recusou o preço: ${sent.error}` : `${sent.error} Pode gravar de novo com segurança.`;
          revealErrors({ erp_gross: message }); setError(message);
          return;
        }
        const own = (sent.channels || []).filter((ch: Row) => ch.own_price);
        note = `PVP de ${money(sent.gross)} gravado na Wintouch.${(sent.channels || []).length > own.length ? " A loja online recebe-o na próxima sincronização." : ""}${own.length ? ` Na loja ${own.map((ch: Row) => ch.connection).join(", ")} mantém-se o preço próprio do canal.` : ""}`;
      }
      await onSave({ ...value, code: value.code.trim(), name: value.name.trim(), precision: Number(value.precision), barcodes: value.barcodes.map((v: string) => v.trim()).filter(Boolean),
        web_highlights: (value.web_highlights || []).map((v: string) => v.trim()).filter(Boolean),
        ...(needsOpeningLot && openingLot.number.trim() ? { opening_lot: { number: openingLot.number.trim(), expiry: openingLot.expiry || null } } : {}) }, note || undefined);
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
  const finishClassificationEditor = (key: ClassificationKey) => {
    setClassificationEditor(null); setClassificationError("");
    requestAnimationFrame(() => form.current?.querySelector<HTMLSelectElement>(`select[name="${key}"]`)?.focus());
  };
  async function saveClassification() {
    const editor = classificationEditor;
    if (!editor || classificationSaving.current || readOnly) return;
    const name = editor.name.trim();
    if (!name || name.length > 100) { setClassificationError("Indique uma designação entre 1 e 100 caracteres."); return; }
    const parent = classifications.family?.find((r: Row) => r.id === editor.familyId);
    if (editor.key === "category" && !parent) { setClassificationError("Selecione uma família."); return; }
    classificationSaving.current = true; setClassificationBusy(true); setClassificationError("");
    try {
      const result = await api(`/tables/${classificationTypes[editor.key].table}/records${editor.record ? `/${editor.record.id}` : ""}`, {
        values: { name, ...(editor.key === "category" ? { family_id: editor.familyId } : {}) },
      });
      const updated = { ...(editor.record || { active: true }), id: result.id, name,
        ...(editor.key === "category" ? { family_id: editor.familyId } : {}) };
      setClassifications(old => ({ ...old, [editor.key]: [...(old[editor.key] || []).filter((r: Row) => r.id !== updated.id), updated]
        .sort((a: Row, b: Row) => a.name.localeCompare(b.name, "pt-PT")) }));
      setValue(old => {
        const next = { ...old };
        const sameSelection = !editor.record || (old[editor.key] === editor.record.name &&
          (editor.key !== "category" || classifications.family?.find((r: Row) => r.name === old.family)?.id === editor.record.family_id));
        if (sameSelection) {
          next[editor.key] = name;
          if (editor.key === "family" && !editor.record) next.category = "";
          if (editor.key === "category") next.family = parent.name;
        }
        const revision = result.article_revisions?.find((r: Row) => r.id === initial.id);
        if (revision && next.revision === revision.before) next.revision = revision.after;
        return next;
      });
      setClassificationNotice(`${classificationTypes[editor.key].singular === "marca" ? "Marca" : editor.key === "family" ? "Família" : "Subfamília"} guardada. Continue a preencher a ficha e guarde o artigo.`);
      finishClassificationEditor(editor.key);
    } catch (e) { setClassificationError((e as Error).message); }
    finally { classificationSaving.current = false; setClassificationBusy(false); }
  }
  const classification = (key: ClassificationKey, label: string) => {
    const parent = classifications.family?.find((r: Row) => r.name === value.family);
    const options: Row[] = (classifications[key] || []).filter((r: Row) =>
      (r.active || r.name === value[key]) && (key !== "category" || r.family_id === parent?.id));
    const current = value[key] || "";
    const selected = options.find(r => r.name === current);
    const startEditor = (record?: Row) => {
      setClassificationError(""); setClassificationNotice("");
      setClassificationEditor({ key, record, name: record?.name || "", familyId: record?.family_id || parent?.id || "" });
    };
    return <div className="field article-classification-field"><div className="article-classification-label"><label htmlFor={`${id}-${key}-select`}>{label}</label>
      {!readOnly && catalog.can_manage_classifications && <span className="article-classification-actions">
        <ActionButton type="button" variant="quiet" aria-label={`Nova ${classificationTypes[key].singular}`} title={key === "category" && !parent?.active ? "Escolha primeiro uma família ativa" : `Nova ${classificationTypes[key].singular}`} disabled={!!classificationEditor || (key === "category" && !parent?.active)} onClick={() => startEditor()}><Plus size={14} /></ActionButton>
        {selected && <ActionButton type="button" variant="quiet" aria-label={`Editar ${classificationTypes[key].singular}`} title={`Editar ${classificationTypes[key].singular}`} disabled={!!classificationEditor} onClick={() => startEditor(selected)}><Pencil size={14} /></ActionButton>}
      </span>}</div><select id={`${id}-${key}-select`} name={key} value={current} disabled={readOnly || !!classificationEditor || (key === "category" && !value.family && !current)}
      aria-invalid={!!errors[key] || undefined} onChange={e => change(key, e.target.value)}>
      <option value="">{key === "category" && !value.family ? "Escolha primeiro a família" : "Por indicar"}</option>
      {current && !options.some(r => r.name === current) && <option value={current}>{current} (atual)</option>}
      {options.map(r => <option key={r.id} value={r.name}>{r.name}{r.active ? "" : " (inativa)"}</option>)}
    </select>{errors[key] && <small className="ui-field-error">{errors[key]}</small>}</div>;
  };
  const unitQuantity = (v: any) => v === null || v === undefined ? "Sem dados" : `${quantity(v)} ${value.unit}`;
  const syncState = !erp ? "apenas_local" : details?.sync_state === "falhou" ? "erro" : ["pendente", "em_curso"].includes(details?.sync_state) ? "pendente" : initial.synced_at ? "sincronizado" : null;
  const periodValue = (key: string) => !initial.id ? "Por criar" : detailError ? "Indisponível" : !details ? "A carregar…" : unitQuantity(insights?.[key]);
  return <ManagedForm ref={form} onSubmit={submit} noValidate className={`article-form${readOnly ? " is-readonly" : ""}`} aria-busy={busy || undefined}
    onKeyDown={e => { if (!readOnly && (e.ctrlKey || e.metaKey) && e.key.toLowerCase() === "s") { e.preventDefault(); if (classificationEditor) void saveClassification(); else form.current?.requestSubmit(); } }}>
    <RecordHeader title="Ficha de artigo" icon={Package} subtitle={readOnly ? "Consulta" : initial.id ? "Editar artigo" : "Novo artigo"} actions={<>
      {recordNavigation && <div className="article-record-navigation" role="group" aria-label="Percorrer artigos">
        <ActionButton type="button" variant="quiet" aria-label="Artigo anterior" title="Artigo anterior" disabled={busy || !!classificationEditor || recordNavigation.busy || recordNavigation.position == null || recordNavigation.position <= 0} onClick={() => void recordNavigation.onMove(-1, tab)}><ChevronLeft size={19} /></ActionButton>
        {recordNavigation.position != null && recordNavigation.total != null && <span aria-live="polite">{recordNavigation.position + 1} / {recordNavigation.total}</span>}
        <ActionButton type="button" variant="quiet" aria-label="Artigo seguinte" title="Artigo seguinte" disabled={busy || !!classificationEditor || recordNavigation.busy || recordNavigation.position == null || recordNavigation.total == null || recordNavigation.position + 1 >= recordNavigation.total} onClick={() => void recordNavigation.onMove(1, tab)}><ChevronRight size={19} /></ActionButton>
      </div>}
      {!readOnly && <ActionButton type="submit" variant="primary" className="primary" disabled={busy || !!classificationEditor}>{busy ? "A guardar…" : "Guardar"}</ActionButton>}
      {onDuplicate && rights.adjust_stock && initial.id && <ActionButton type="button" disabled={busy || !!classificationEditor} onClick={() => void onDuplicate()}><Copy size={16} />Duplicar</ActionButton>}
      {initial.id && <ActionButton type="button" variant="quiet" disabled={busy} aria-label="Atualizar informação do artigo" onClick={() => setAttempt(v => v + 1)}><RefreshCw size={17} /></ActionButton>}
        <details className="article-help"><summary aria-label="Informação da ficha do artigo"><Info size={19} aria-hidden="true" /></summary>
          <div className="article-help-box" role="note"><strong>Sobre esta ficha</strong>
            <p>Os campos com * são obrigatórios. Pode guardar o artigo com os dados de Geral. Utilize a navegação lateral para consultar preços, stocks e histórico.</p>
            <p>{erp ? "Artigo da Wintouch: código, nome, estado e preço vêm do ERP e só se alteram na origem. Os restantes dados e o stock são geridos aqui." : "Artigo local: os dados são guardados nesta aplicação."}</p>
            <p>A fotografia principal acompanha todos os separadores: carregue-a (câmara ou ficheiro) ou indique um endereço HTTPS. A fotografia do rótulo serve só para a IA propor o descritivo. As imagens adicionais e fichas técnicas ficam em Descrição e anexos.</p>
            <p>Peso, volume, prazo genérico de validade, etiquetas comerciais e tabelas adicionais de preços não estão registados nesta ficha. A rotatividade exige um histórico de stock médio que ainda não existe.</p>
          </div>
        </details>
    </>} />
    <div className="article-sheet-layout"><aside className="article-sheet-sidebar">
      <RecordNavigation prefix={id} label="Ficha do artigo" active={tab} onSelect={setTab} items={sectionOrder.map(index => ({ id: index, label: sectionLabels[index], accessibleLabel: sections[index], icon: sectionIcons[index], invalid: Object.keys(errors).some(key => (fieldSection[key] ?? 0) === index) }))} />
    </aside><fieldset className="ui-form-fields article-sheet-main" disabled={busy || classificationBusy || !!recordNavigation?.busy}>
      {recordNavigation?.error && <Notice tone="error">{recordNavigation.error}</Notice>}
      <section className="article-identity record-sheet-group" aria-label="Identificação do artigo">
        <ArticlePhotos articleId={initial.id} name={value.name} readOnly={readOnly} urlPhoto={value.photo} uploadedPhoto={initial.image_photo}
          urlEditor={<>{text("photo", "URL da fotografia (HTTPS)")}<small>Imagem alojada por HTTPS.</small>{imageSearch}{value.photo && <ActionButton type="button" confirmation={{ title: "Retirar o endereço da fotografia?", message: "A associação será retirada ao guardar. O ficheiro no alojamento de origem é preservado.", confirmLabel: "Retirar endereço", tone: "warning" }} onClick={() => change("photo", "")}>Retirar endereço da fotografia</ActionButton>}</>} />
        <div className="article-identity-fields"><div className="article-identity-top">
          {text("code", "Código interno", true, erp)}{text("name", "Designação", true, erp)}
          <label className="article-active-switch">Ativo<input type="checkbox" role="switch" name="active" checked={!!value.active} disabled={readOnly || erp} onChange={e => change("active", e.target.checked)} /><span aria-hidden="true" /></label>
        </div><div className="article-identity-bottom">
          {classification("family", "Família")}{classification("category", "Subfamília / categoria")}{classification("brand", "Marca")}{text("unit", "Unidade", true, false, list("unit"))}
          <Field label="Tipo de artigo"><select name="service" disabled={readOnly} value={String(!!value.service)} onChange={e => setValue({ ...value, service: e.target.value === "true", ...(e.target.value === "true" ? { lots: false, expiry: false } : {}) })}><option value="false">Produto de stock</option><option value="true">Serviço (sem stock)</option></select></Field>
        </div><div className="article-identity-meta">{syncState ? <SyncBadge state={syncState} label={syncState === "pendente" ? "Catálogo a sincronizar" : syncState === "erro" ? "Erro no catálogo ERP" : undefined} /> : <StatusBadge tone="info">ERP · estado por consultar</StatusBadge>}<StatusBadge tone={value.active ? "success" : "neutral"}>{value.active ? "Ativo" : "Inativo"}</StatusBadge>{initial.id && <small>{value.code} · {erp ? "Wintouch" : "Local"}{details?.generated_at ? ` · Atualizado ${date(details.generated_at)}` : ""}</small>}</div></div>
        {classificationEditor && <section className="article-classification-editor" aria-label={`${classificationEditor.record ? "Editar" : "Nova"} ${classificationTypes[classificationEditor.key].singular}`}
          onKeyDown={e => { if (e.key === "Enter" && (e.target instanceof HTMLInputElement || e.target instanceof HTMLSelectElement)) { e.preventDefault(); e.stopPropagation(); void saveClassification(); } }}>
          <h3>{classificationEditor.record ? "Editar" : "Nova"} {classificationTypes[classificationEditor.key].singular}</h3>
          <p>{classificationEditor.record ? "A alteração fica guardada na tabela e atualiza os artigos que usam esta classificação." : "O novo registo fica disponível na tabela e selecionado nesta ficha."}</p>
          <div className="article-classification-editor-fields">
            {classificationEditor.key === "category" && <Field label="Família da subfamília"><select name="classification_family" value={classificationEditor.familyId} onChange={e => setClassificationEditor({ ...classificationEditor, familyId: e.target.value })}>
              <option value="">Escolha…</option>{(classifications.family || []).filter((r: Row) => r.active || r.id === classificationEditor.record?.family_id).map((r: Row) => <option key={r.id} value={r.id}>{r.name}{r.active ? "" : " (inativa)"}</option>)}
            </select></Field>}
            <Field label="Designação da classificação"><input name="classification_name" autoFocus maxLength={100} value={classificationEditor.name} onChange={e => setClassificationEditor({ ...classificationEditor, name: e.target.value })} /></Field>
          </div>
          {classificationError && <Notice tone="error" role="alert">{classificationError}</Notice>}
          <div className="article-classification-editor-actions"><ActionButton type="button" disabled={classificationBusy} onClick={() => finishClassificationEditor(classificationEditor.key)}>Cancelar edição da classificação</ActionButton>
            <ActionButton type="button" variant="primary" disabled={classificationBusy} onClick={() => void saveClassification()}>{classificationBusy ? "A guardar…" : "Guardar classificação"}</ActionButton></div>
        </section>}
        {classificationNotice && <div className="article-classification-notice" role="status">{classificationNotice}</div>}
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
            <RecordGroup title="Preços (IVA incluído)" icon={Coins} className="article-price-overview" action={<ActionButton type="button" variant="quiet" onClick={() => { setTab(1); if (erpEditable || (!erp && rights.change_price && !readOnly)) requestAnimationFrame(() => form.current?.querySelector<HTMLInputElement>(`input[name="${erp ? "erp_gross" : "sale_gross"}"]`)?.focus()); }}>{erpEditable || (!erp && rights.change_price && !readOnly) ? "Alterar preço" : "Ver preços"}</ActionButton>}>
              <dl className="article-price-list"><div className="is-pvp"><dt>{erp && current.erp_price_basis !== "Com IVA" ? "PVP com IVA não indicado" : "PVP"}</dt><dd>{money(erp ? erpChanged ? Number(erpGross) : current.erp_price_basis === "Com IVA" ? current.erp_price : null : prices.gross)}</dd></div>
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
          <ArticleWebshop articleId={initial.id} readOnly={readOnly} />
          <details><summary>Mais detalhes</summary><div className="form-grid">{text("reference", "Referência")}{numeric("precision", "Casas decimais (0 a 6)", false, 6, "1")}</div><p className="footnote">Código manual; não existe numeração automática configurada. O catálogo atual gere artigos de stock. A unidade e o controlo de lotes ficam protegidos depois de existirem movimentos.</p></details>
          <p className="footnote">Use + para criar e o lápis para editar famílias, subfamílias e marcas nesta ficha. A classificação acompanha os artigos publicados na próxima sincronização com o site.</p>
          {["unit"].map(key => <datalist key={key} id={list(key)}>{[...new Set<string>((catalog.facets?.[key] || catalog.articles.map((a: Row) => a[key]).filter(Boolean)))].map(v => <option key={v} value={v} />)}</datalist>)}
        </>}
        {index === 1 && <>
          <h3>Preço de venda e margem bruta</h3>
          <p>A margem utiliza o preço sem IVA e o custo médio ponderado dos movimentos. Os valores da venda são editáveis quando o seu perfil o permite; os custos e as margens são calculados.</p>
          {!erp && <div className="form-grid">{numeric("sale_net", "Venda sem IVA (€)", !rights.change_price, 1e9)}{gross("Venda com IVA (€)")}{numeric("vat_rate", "Taxa de IVA (%)", !rights.change_price, 100)}</div>}
          {erp && erpPrice && <section className="article-subsection article-erp-price" aria-labelledby={`${id}-erp-price`}>
            <h4 id={`${id}-erp-price`}>PVP na Wintouch (linha de preço 1)</h4>
            <div className="form-grid"><Field label="PVP com IVA (€)"><input name="erp_gross" type="number" inputMode="decimal" min="0.01" step="0.01" value={erpGross} readOnly={!erpEditable}
              aria-invalid={!!errors.erp_gross || undefined} onChange={e => { setErpGross(e.target.value); setErrors(old => { const next = { ...old }; delete next.erp_gross; return next; }); }} /></Field>
              {readonly("Preço sem IVA", erpChanged && erpNet != null ? `${money(erpNet)} (novo)` : money(current.erp_price_net))}</div>
            {errors.erp_gross && <small className="ui-field-error">{errors.erp_gross}</small>}
            {erpEditable ? <p className="footnote">Ao gravar a ficha, o PVP é enviado à Wintouch e confirmado por leitura; só depois a ficha é gravada.{(erpPrice.channels || []).some((ch: Row) => !ch.own_price) ? " A loja online recebe o novo preço na sincronização seguinte." : ""}{(erpPrice.channels || []).filter((ch: Row) => ch.own_price).map((ch: Row) => ` Na loja ${ch.connection} o artigo tem preço próprio do canal, que não muda.`).join("")}</p>
              : <Notice tone="info">{erpPrice.reason || "O PVP deste artigo só se altera na Wintouch."}</Notice>}
          </section>}
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
              ? <MarketPrices articleId={initial.id} ownPrice={erp && !erpChanged && current.erp_price_basis === "Com IVA" ? Number(current.erp_price) : prices.gross} vatRate={Number(erp ? current.erp_vat_rate : value.vat_rate) || 0} cost={rights.view_cost && !erp ? prices.cost : null} readOnly={readOnly} />
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
          <h3>Conteúdo para o site</h3>
          <WebContentAssistant articleId={initial.id} readOnly={readOnly} onUse={fields => {
            change("description", fields.description); change("web_summary", fields.web_summary); change("web_highlights", fields.web_highlights);
            change("seo_keyword", fields.seo_keyword || ""); change("seo_title", fields.seo_title); change("seo_description", fields.seo_description);
          }} />
          <Field label={`Resumo (${String(value.web_summary || "").length}/300)`}><textarea readOnly={readOnly} name="web_summary" rows={2} value={value.web_summary} maxLength={300} onChange={e => change("web_summary", e.target.value)} /></Field>
          <Field label="Pontos-chave (um por linha, até 8)"><textarea readOnly={readOnly} name="web_highlights" rows={4} value={(value.web_highlights || []).join("\n")} onChange={e => change("web_highlights", e.target.value.split("\n").slice(0, 8))} /></Field>
          <Field label="Palavra-chave principal (SEO)"><input readOnly={readOnly} name="seo_keyword" value={value.seo_keyword || ""} maxLength={60} placeholder="ex.: própolis spray sem álcool" onChange={e => change("seo_keyword", e.target.value)} /></Field>
          <Field label={`Título SEO (${String(value.seo_title || "").length}/60)`}><input readOnly={readOnly} name="seo_title" value={value.seo_title} maxLength={70} onChange={e => change("seo_title", e.target.value)} /></Field>
          <Field label={`Meta-descrição SEO (${String(value.seo_description || "").length}/155)`}><textarea readOnly={readOnly} name="seo_description" rows={2} value={value.seo_description} maxLength={170} onChange={e => change("seo_description", e.target.value)} /></Field>
          <p className="footnote">A loja online recebe a descrição, o resumo, os pontos-chave e o SEO pelo catálogo (a palavra-chave, o título e a meta-descrição vão para o Rank Math). Na descrição, uma linha a começar por «## » é um subtítulo no site. Reveja sempre as alegações de saúde antes de gravar.</p>
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
