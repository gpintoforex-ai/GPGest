import { useEffect, useId, useRef, useState, type FormEvent } from "react";
import { Package } from "lucide-react";
import { Field } from "./components/Form";
import { ActionButton, ApiError, ManagedForm, Notice, markDialogSaved, reportFormError, useDialogWorking } from "./components/InteractionUI";
import { api } from "./lib/api";
import { articlePayload, articlePrices, validateArticle, type ArticleRow as Row } from "./articleValues";

const sections = ["Geral", "Preços e margens", "Stock e validades", "Compras e fornecedores", "Descrição e anexos", "ERP e histórico"];
const fieldSection: Record<string, number> = { minimum: 2, reorder: 2, location: 2, lots: 2, expiry: 2, supplier_id: 3, supplier_reference: 3, description: 4, internal_notes: 4, additional_photos: 4, attachments: 4 };
const money = (v: any) => v === null || v === undefined ? "Sem informação" : new Intl.NumberFormat("pt-PT", { style: "currency", currency: "EUR" }).format(Number(v));
const quantity = (v: any) => v === null || v === undefined ? "Sem informação" : new Intl.NumberFormat("pt-PT", { maximumFractionDigits: 6 }).format(Number(v));
const date = (v: any) => v ? new Intl.DateTimeFormat("pt-PT", { dateStyle: "short", timeZone: "Europe/Lisbon" }).format(new Date(v)) : "Sem informação";

export default function ArticleForm({ initial, catalog, onSave, onCancel, onNavigate, onSync, canSync }: {
  initial: Row; catalog: Row; onSave: (value: Row) => Promise<void>; onCancel: () => void;
  onNavigate: (tab: string, id: string) => void; onSync: () => void; canSync: boolean;
}) {
  const [value, setValue] = useState<Row>(() => articlePayload(initial));
  const [imageText, setImageText] = useState((initial.additional_photos || []).join("\n"));
  const [attachmentText, setAttachmentText] = useState((initial.attachments || []).map((a: Row) => a.name ? `${a.name} | ${a.url}` : a.url).join("\n"));
  const [tab, setTab] = useState(0), [errors, setErrors] = useState<Record<string, string>>({});
  const [error, setError] = useState(""), [busy, setBusy] = useState(false);
  const [details, setDetails] = useState<Row | null>(null), [detailError, setDetailError] = useState("");
  const [attempt, setAttempt] = useState(0);
  const form = useRef<HTMLFormElement>(null), saving = useRef(false);
  const id = useId();
  useDialogWorking(busy);
  const erp = initial.origin === "ERP", rights = catalog.permissions || {};
  const prices = articlePrices(erp ? null : value.sale_net, value.vat_rate, rights.view_cost ? initial.average_cost : null);
  const change = (key: string, next: any) => {
    setValue(old => ({ ...old, [key]: next }));
    setErrors(old => { const nextErrors = { ...old }; delete nextErrors[key]; return nextErrors; });
  };
  useEffect(() => {
    if (!initial.id) return;
    let active = true;
    setDetailError("");
    api(`/stock/articles/${initial.id}/details`).then(result => { if (active) setDetails(result); })
      .catch(e => { if (active) setDetailError(e.message); });
    return () => { active = false; };
  }, [initial.id, attempt]);
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
        field?.focus();
      });
    }
  };
  async function submit(event: FormEvent) {
    event.preventDefault();
    if (saving.current) return;
    const invalid = validateArticle(value, catalog.articles, initial.id);
    if (Object.keys(invalid).length) {
      revealErrors(invalid);
      reportFormError(form.current, new ApiError("Reveja os campos assinalados.", invalid));
      setError("Reveja os campos assinalados.");
      return;
    }
    saving.current = true;
    setBusy(true); setError(""); setErrors({});
    try {
      await onSave({ ...value, code: value.code.trim(), name: value.name.trim(), precision: Number(value.precision), barcodes: value.barcodes.map((v: string) => v.trim()).filter(Boolean) });
      markDialogSaved(form.current);
    } catch (e) {
      if (e instanceof ApiError) {
        const normalized = Object.fromEntries(Object.entries(e.fields).map(([key, message]) => [key.split(".")[0], message]));
        reportFormError(form.current, new ApiError(e.message, normalized)); revealErrors(normalized);
      }
      setError((e as Error).message);
    } finally { saving.current = false; setBusy(false); }
  }
  const text = (key: string, label: string, required = false, readOnly = false, list?: string) => <Field label={label}>
    <input name={key} value={value[key] ?? ""} required={required} readOnly={readOnly} list={list}
      aria-invalid={!!errors[key] || undefined} onChange={e => change(key, e.target.value)} />
  </Field>;
  const numeric = (key: string, label: string, readOnly = false, max = 1e12, step = "any") => <Field label={label}>
    <input name={key} type="number" inputMode="decimal" min="0" max={max} step={step} value={value[key] ?? ""}
      readOnly={readOnly} aria-invalid={!!errors[key] || undefined} onChange={e => change(key, e.target.value)} />
  </Field>;
  const gross = (label = "Preço com IVA (€)") => <Field label={label}>
    <input name="sale_gross" type="number" inputMode="decimal" min="0" step="any" readOnly={!rights.change_price}
      value={prices.gross === null ? "" : Number(prices.gross.toFixed(6))}
      onChange={e => change("sale_net", e.target.value === "" ? "" : String(Number((Number(e.target.value) / (1 + Number(value.vat_rate) / 100)).toFixed(6))))} />
  </Field>;
  const readonly = (label: string, v: any) => <div className="article-fact"><span>{label}</span><strong>{v}</strong></div>;
  const navigate = (section: string) => onNavigate(section, initial.id);
  const suppliers: Row[] = catalog.suppliers || [];
  const supplier = suppliers.find(s => s.id === value.supplier_id);
  const list = (key: string) => `${id}-${key}`;
  return <ManagedForm ref={form} onSubmit={submit} noValidate className="article-form" aria-busy={busy || undefined}
    onKeyDown={e => { if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === "s") { e.preventDefault(); form.current?.requestSubmit(); } }}>
    <fieldset className="ui-form-fields" disabled={busy}>
      <div className="article-header">
        <div className="article-image">{value.photo ? <img src={value.photo} alt="Imagem do artigo" referrerPolicy="no-referrer" onError={e => { e.currentTarget.style.visibility = "hidden"; }} onLoad={e => { e.currentTarget.style.visibility = "visible"; }} /> : <Package size={32} aria-hidden="true" />}</div>
        <div><span className="article-eyebrow">{value.code || "Novo artigo"}</span><h3>{value.name || "Designação do artigo"}</h3><span className="article-state">{value.active ? "Ativo" : "Inativo"} · {erp ? "Wintouch" : "Local"}</span></div>
      </div>
      <div className="article-summary" aria-label="Resumo do artigo">
        {readonly(erp ? "Preço no ERP · IVA não identificado" : "Preço de venda com IVA", money(erp ? initial.erp_price : prices.gross))}
        {rights.view_cost && <>{readonly("Custo médio por unidade", money(prices.cost))}{readonly("Margem bruta / un. sem IVA", prices.margin === null ? "Sem informação" : `${money(prices.margin)} · ${prices.marginPct === null ? "Não calculável" : `${quantity(prices.marginPct)} %`}`)}</>}
        {readonly("Stock disponível", initial.id ? quantity(initial.available_quantity) : "Sem movimentos")}
      </div>
      {prices.margin !== null && prices.margin < 0 && <Notice tone="warning">O preço sem IVA está abaixo do custo médio.</Notice>}
      {erp && <Notice tone="info">Artigo da Wintouch: código, nome, estado e preço vêm do ERP e só se alteram na origem. Os restantes dados e o stock são geridos aqui.</Notice>}
      <div className="article-tabs" role="tablist" aria-label="Ficha do artigo" onKeyDown={e => {
        if (!["ArrowLeft", "ArrowRight", "Home", "End"].includes(e.key)) return;
        e.preventDefault();
        const next = e.key === "Home" ? 0 : e.key === "End" ? sections.length - 1 : (tab + (e.key === "ArrowRight" ? 1 : -1) + sections.length) % sections.length;
        setTab(next); document.getElementById(`${id}-tab-${next}`)?.focus();
      }}>
        {sections.map((label, index) => <ActionButton key={label} id={`${id}-tab-${index}`} type="button" role="tab" aria-selected={tab === index} aria-controls={`${id}-panel-${index}`} tabIndex={tab === index ? 0 : -1} onClick={() => setTab(index)}>
          {label}{Object.keys(errors).some(key => (fieldSection[key] ?? 0) === index) && <span aria-label="Precisa de correção"> !</span>}
        </ActionButton>)}
      </div>
      {sections.map((label, index) => <section key={label} role="tabpanel" id={`${id}-panel-${index}`} aria-labelledby={`${id}-tab-${index}`} hidden={tab !== index} className="article-panel">
        {index === 0 && <>
          <p className="footnote">* Campos obrigatórios. Pode guardar o artigo com os dados deste separador.</p>
          <div className="form-grid article-main-fields">
            {text("code", "Código interno", true, erp)}{text("name", "Designação", true, erp)}
            {readonly("Tipo de artigo", "Produto de stock")}
            <Field label="Códigos de barras (separados por |)"><input name="barcodes" value={value.barcodes.join("|")} onChange={e => change("barcodes", e.target.value.split("|"))} /></Field>
            {text("family", "Família", false, false, list("family"))}{text("category", "Subfamília / categoria", false, false, list("category"))}
            {text("brand", "Marca", false, false, list("brand"))}{text("unit", "Unidade", true, false, list("unit"))}
            {numeric("vat_rate", "IVA (%)", !rights.change_price, 100)}
            {erp ? readonly("Preço no ERP (€)", money(initial.erp_price)) : <>{numeric("sale_net", "Preço sem IVA (€)", !rights.change_price, 1e9)}{gross()}</>}
            <Field label="Estado"><select name="active" value={String(value.active)} disabled={erp} onChange={e => change("active", e.target.value === "true")}><option value="true">Ativo</option><option value="false">Inativo</option></select></Field>
            {text("photo", "URL da fotografia (HTTPS)")}
          </div>
          <details><summary>Mais detalhes</summary><div className="form-grid">{text("reference", "Referência")}{numeric("precision", "Casas decimais (0 a 6)", false, 6, "1")}</div><p className="footnote">Código manual; não existe numeração automática configurada. O catálogo atual gere artigos de stock. A unidade e o controlo de lotes ficam protegidos depois de existirem movimentos.</p></details>
          {["family", "category", "brand", "unit"].map(key => <datalist key={key} id={list(key)}>{[...new Set<string>(catalog.articles.map((a: Row) => a[key]).filter(Boolean))].map(v => <option key={v} value={v} />)}</datalist>)}
        </>}
        {index === 1 && <>
          <h3>Preço de venda e margem bruta</h3>
          <p>A margem utiliza o preço sem IVA e o custo médio ponderado dos movimentos. Os valores da venda são editáveis quando o seu perfil o permite; os custos e as margens são calculados.</p>
          {!erp && <div className="form-grid">{numeric("sale_net", "Venda sem IVA (€)", !rights.change_price, 1e9)}{gross("Venda com IVA (€)")}{numeric("vat_rate", "Taxa de IVA (%)", !rights.change_price, 100)}</div>}
          <div className="article-facts">{readonly("Venda sem IVA", money(prices.net))}{readonly("Venda com IVA", money(prices.gross))}{readonly("IVA", erp ? "Não identificado para o preço do ERP" : `${quantity(value.vat_rate)} %`)}
            {rights.view_cost && <>{readonly("Custo médio ponderado", money(initial.average_cost))}{readonly("Último custo de compra", money(initial.last_cost))}{readonly("Margem bruta por unidade", money(prices.margin))}{readonly("Margem sobre a venda sem IVA", prices.marginPct === null ? "Não calculável" : `${quantity(prices.marginPct)} %`)}</>}
          </div>
          <ActionButton type="button" onClick={() => { setTab(0); requestAnimationFrame(() => form.current?.querySelector<HTMLElement>('[name="sale_net"]')?.focus()); }}>Consultar / editar preço e IVA</ActionButton>
          {!rights.view_cost && <p>O seu perfil não permite consultar custos e margens.</p>}
          {erp && <p>O conector não identifica a base de IVA do preço do ERP; a margem de venda não pode ser calculada.</p>}
        </>}
        {index === 2 && <>
          <h3>Quantidades e controlo</h3>
          <p>O stock atual é a soma dos movimentos nos armazéns a que tem acesso. O reservado corresponde aos pedidos de clientes; o disponível considera armazéns ativos e exclui essas reservas e os lotes expirados, sem compensar faltas entre armazéns.</p>
          <div className="article-facts">{readonly("Stock atual", initial.id ? quantity(initial.quantity) : "Sem movimentos")}{readonly("Stock reservado", quantity(initial.reserved_quantity))}{readonly("Disponível para saída", quantity(initial.available_quantity))}</div>
          <div className="form-grid">{numeric("minimum", "Stock mínimo")}{numeric("reorder", "Nível de reposição")}{text("location", "Localização")}</div>
          <label className="stock-check" title="Portes, mão de obra, assinaturas…: conta nas compras e vendas, mas não movimenta stock."><input name="service" type="checkbox" checked={!!value.service} onChange={e => setValue({ ...value, service: e.target.checked, ...(e.target.checked ? { lots: false, expiry: false } : {}) })} />Serviço (não movimenta stock)</label>
          {[["lots", "Controlar lotes"], ["expiry", "Controlar validades"]].map(([key, title]) => <label key={key} className="stock-check"><input name={key} type="checkbox" checked={value[key]} disabled={!!value.service} onChange={e => change(key, e.target.checked)} />{title}</label>)}
          <p className="footnote">Alertas de validade: {(catalog.settings?.alerts || []).join(", ")} dias antes da validade. O nível de reposição é a quantidade de referência para repor stock.</p>
          {details?.warehouses?.length > 0 && <div className="article-warehouse-list"><h4>Stock por armazém autorizado</h4>{details?.warehouses.map((w: Row) => <div key={w.id}><span>{w.name}</span><strong>{quantity(w.quantity)} {value.unit}</strong></div>)}</div>}
          {details?.expiry_alerts?.length > 0 && <Notice tone="warning">Lotes com validade próxima ou ultrapassada: {details?.expiry_alerts.map((lot: Row) => `${lot.number} · ${date(lot.expiry)}`).join("; ")}. Consulte os lotes antes de registar saídas.</Notice>}
          {initial.id && <div className="stock-actions"><ActionButton type="button" onClick={() => navigate("Movimentos")}>Consultar movimentos</ActionButton><ActionButton type="button" onClick={() => navigate("Lotes e validades")}>Consultar lotes e validades</ActionButton></div>}
          <p className="footnote">As correções usam «Registar movimento» ou um inventário, com motivo e histórico. As quantidades não se alteram nesta ficha.</p>
        </>}
        {index === 3 && <>
          <h3>Dados de compra do artigo</h3>
          <div className="form-grid"><Field label="Fornecedor principal"><select name="supplier_id" value={value.supplier_id || ""} onChange={e => change("supplier_id", e.target.value || null)}><option value="">Sem fornecedor definido</option>{suppliers.map(s => <option key={s.id} value={s.id}>{s.name}</option>)}</select></Field>{text("supplier_reference", "Referência do fornecedor")}</div>
          {supplier && <p>Fornecedor associado: <strong>{supplier.name}</strong>. As condições gerais do fornecedor são geridas na respetiva ficha, em Entidades.</p>}
          <div className="article-facts">{rights.view_cost && readonly("Último custo de compra registado", money(initial.last_cost))}{readonly("Última compra nos armazéns autorizados", date(details?.last_purchase?.date))}{readonly("Documento de compra", details?.last_purchase?.document || "Sem informação")}</div>
          <p className="footnote">A referência e o fornecedor principal pertencem a este artigo. O histórico mantém os fornecedores e os custos de cada compra.</p>
          {initial.id && <ActionButton type="button" onClick={() => navigate("Movimentos")}>Consultar histórico de compras e movimentos</ActionButton>}
        </>}
        {index === 4 && <>
          <h3>Descrição do artigo</h3><Field label="Descrição"><textarea name="description" value={value.description} maxLength={4000} onChange={e => change("description", e.target.value)} /></Field>
          <Field label="Observações internas"><textarea name="internal_notes" value={value.internal_notes} maxLength={4000} onChange={e => change("internal_notes", e.target.value)} /></Field>
          <p className="footnote">As observações internas destinam-se à equipa e são guardadas separadamente da descrição do artigo.</p>
          <Field label="Imagens adicionais — endereço HTTPS por linha"><textarea name="additional_photos" value={imageText} onChange={e => { setImageText(e.target.value); change("additional_photos", e.target.value.split("\n").map(v => v.trim()).filter(Boolean)); }} /></Field>
          <Field label="Anexos — nome | endereço HTTPS, um por linha"><textarea name="attachments" value={attachmentText} onChange={e => {
            setAttachmentText(e.target.value);
            change("attachments", e.target.value.split("\n").filter(v => v.trim()).map(line => {
              const separator = line.indexOf("|");
              return separator < 0 ? { name: "", url: line.trim() } : { name: line.slice(0, separator).trim(), url: line.slice(separator + 1).trim() };
            }));
          }} /></Field>
          <p className="footnote">Ligue imagens e fichas técnicas já alojadas por HTTPS. Até 10 imagens e 20 anexos; os ficheiros mantêm-se no alojamento de origem.</p>
          {!!value.additional_photos.length && <div className="article-gallery">{value.additional_photos.filter((url: string) => /^https:\/\//.test(url)).map((url: string, i: number) => <img key={`${i}-${url}`} src={url} alt={`Imagem adicional ${i + 1}`} referrerPolicy="no-referrer" loading="lazy" />)}</div>}
          {!!value.attachments.length && <ul>{value.attachments.filter((a: Row) => /^https:\/\//.test(a.url)).map((a: Row, i: number) => <li key={i}><a href={a.url} target="_blank" rel="noopener noreferrer">{a.name || "Consultar anexo"}</a></li>)}</ul>}
          <ActionButton type="button" onClick={() => setTab(0)}>Editar imagem principal em Geral</ActionButton>
        </>}
        {index === 5 && <>
          <h3>{erp ? "Ligação ao ERP e histórico" : "Origem local e histórico"}</h3>
          <div className="article-facts">{readonly("Criado em", date(details?.created?.created_at))}{readonly("Criado por", details?.created?.user_name || "Sem informação")}{readonly("Última alteração em", date(details?.updated?.created_at))}{readonly("Alterado por", details?.updated?.user_name || "Sem informação")}</div>
          <div className="article-facts">{readonly("Origem", erp ? "Wintouch" : "Local")}{readonly("Empresa", details?.company_name || "Sem informação")}{readonly("Código no ERP", erp ? initial.code : "Não se aplica")}{erp && <>{readonly("Identificador no ERP", details?.erp?.external_id || "Sem informação")}{readonly("Empresa no ERP", details?.erp?.enterprise_id || "Sem informação")}{readonly("Última sincronização do catálogo", date(initial.synced_at))}{readonly("Estado de sincronização", initial.synced_at ? "Catálogo recebido" : "Sem informação")}</>}{readonly("Revisão da ficha", initial.revision ?? 0)}</div>
          {erp && <p>Os dados locais são guardados nesta aplicação. O conector atual importa o catálogo e não permite criar ou alterar artigos na Wintouch.</p>}
          {erp && details?.sync_state === "falhou" && <Notice tone="warning">A última sincronização do catálogo falhou. Consulte Integrações antes de atualizar os artigos.</Notice>}
          {erp && ["pendente", "em_curso"].includes(details?.sync_state) && <p role="status">Existe uma sincronização do catálogo pendente ou em curso.</p>}
          {canSync && <ActionButton type="button" onClick={onSync}>Importar / atualizar catálogo do ERP</ActionButton>}
          {!erp && <p>Não precisa de configurar um ERP para criar e gerir este artigo.</p>}
          {details?.history?.length > 0 ? <ol className="article-history">{details?.history.map((entry: Row) => <li key={entry.id}><strong>{entry.label}</strong><span>{date(entry.created_at)} · {entry.user_name || "Sem informação"}</span></li>)}</ol> : <p>Sem histórico de alterações disponível.</p>}
        </>}
        {initial.id && [2, 3, 5].includes(index) && <>{detailError ? <Notice tone="error">{detailError}<ActionButton type="button" onClick={() => setAttempt(v => v + 1)}>Voltar a tentar</ActionButton></Notice> : !details && <p role="status">A carregar informação do artigo…</p>}</>}
      </section>)}
    </fieldset>
    {error && <Notice tone="error" role="alert">{error}</Notice>}
    <div className="ui-dialog-actions article-form-actions"><span className="footnote">Ctrl / ⌘ + S para guardar</span><ActionButton type="button" disabled={busy} onClick={onCancel}>Cancelar</ActionButton><ActionButton type="submit" className="primary" disabled={busy}>{busy ? "A guardar…" : "Guardar"}</ActionButton></div>
  </ManagedForm>;
}
