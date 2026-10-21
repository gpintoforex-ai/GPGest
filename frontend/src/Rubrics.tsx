import { ActionButton, AppDialog, ManagedForm, Notice } from "./components/InteractionUI";
import { useEffect, useRef, useState } from "react";
import { api } from "./lib/api";
import { Field } from "./components/Form";
import RubricBadge, { rubricIcons } from "./components/RubricBadge";
import { suggestRubricColor } from "./components/rubricColors";
type Row = Record<string, any>;

export default function Rubrics({ manager, admin, reload, policy }: { manager: boolean; admin: boolean; reload: () => Promise<void>; policy?: Row }) {
  const [rows, setRows] = useState<Row[]>([]), [remote, setRemote] = useState<Row[]>([]);
  const [search, setSearch] = useState(""), [modal, setModal] = useState("");
  const [selected, setSelected] = useState<Row | null>(null), [ids, setIds] = useState<string[]>([]);
  const [name, setName] = useState(""), [icon, setIcon] = useState("tag"), [color, setColor] = useState("#245fc5"), [deduction, setDeduction] = useState("");
  const [code, setCode] = useState(""), [template, setTemplate] = useState(""), [confirmed, setConfirmed] = useState(false);
  const [busy, setBusy] = useState(false), [error, setError] = useState(""), [notice, setNotice] = useState("");
  const [proposals, setProposals] = useState<Row[]>([]);
  const [prefix, setPrefix] = useState("");
  const [erp, setErp] = useState<Row | null>(null);
  const dialog = useRef<HTMLDialogElement>(null);
  const [cashDifference, setCashDifference] = useState(false);
  const load = async () => setRows(await api("/categories"));
  useEffect(() => { load().catch(e => setError(e.message)); }, []);
  useEffect(() => { if (modal && !dialog.current?.open) dialog.current?.showModal(); }, [modal]);
  async function act(task: () => Promise<void>) { setBusy(true); setError(""); setNotice(""); try { await task(); } catch(e) { setError((e as Error).message); } finally { setBusy(false); } }
  function edit(row: Row | null) { setSelected(row); setDeduction(row?.vat_deduction_percent == null ? "" : String(row.vat_deduction_percent)); setCashDifference(!!row?.cash_difference); setName(row?.name || ""); setIcon(row?.icon || "tag"); setColor(row ? row.color || "#245fc5" : suggestRubricColor(rows.map(item => item.color))); setError(""); setModal("edit"); }
  async function integration(kind: string, row: Row | null = null) {
    setSelected(row); setIds([]); setConfirmed(false); setRemote([]); setCode(row?.integration?.external_code || ""); setTemplate(row?.integration?.external_id || ""); setModal(kind);
    setErp(null);
    await act(async () => { await load(); setErp(await api("/categories/erp/settings")); setRemote(await api("/categories/erp/items")); });
  }
  async function prepareSend() {
    const result = await api("/categories/erp/proposals");
    setProposals(result.rows); setPrefix(result.prefix); setConfirmed(false);
    setIds(result.rows.filter((row: Row) => !row.exists && !row.pending).map((row: Row) => row.id));
  }
  const importable = remote.filter(row => !rows.some(local => local.integration && (
    local.integration.external_id === row.id || local.integration.external_code === row.code
  )));
  return <>
    <div className="page-intro"><div><h1>Rubricas</h1><p>Organize as categorias com ícones e cores. As ligações ao ERP são opcionais.</p></div>{manager && <div className="actions">{(policy?.capabilities.read_rubrics ?? true) && <ActionButton disabled={busy} onClick={() => integration("import")}>Sincronizar ERP</ActionButton>}<ActionButton className="primary" onClick={() => edit(null)}>Adicionar rubrica</ActionButton></div>}</div>
    {error && !modal && <Notice className="error" role="alert">{error}</Notice>}{notice && <Notice onDismiss={() => setNotice("")} className="success" role="status">{notice}</Notice>}
    <section className="panel"><div className="filter-bar filter-bar-search"><Field label="Pesquisar rubrica"><input type="search" value={search} onChange={e => setSearch(e.target.value)} /></Field></div>
      {rows.filter(row => row.name.toLocaleLowerCase("pt-PT").includes(search.toLocaleLowerCase("pt-PT"))).map(row => <div className="setting-row rubric-row" key={row.id}><RubricBadge {...row} name={row.name} /><div className="actions">{row.cash_difference && <small>Diferenças de caixa</small>}{row.integration && <small>{row.integration.external_code} · {row.integration.status === "confirmado" ? "Associada ao ERP" : row.integration.status === "rejeitado" ? "Envio rejeitado" : "Envio por verificar"}</small>}{manager && <><ActionButton onClick={() => edit(row)}>Editar</ActionButton><ActionButton className="danger" disabled={busy} onClick={() => { setSelected(row); setError(""); setModal("remove"); }}>Remover</ActionButton></>}</div></div>)}
      {!rows.length && <p>Ainda não existem rubricas.</p>}
    </section>
    {modal && <AppDialog busy={busy} initialFocus={modal === "remove" ? "cancel" : "first"} className="doc-dialog" ref={dialog} aria-label="Configurar rubricas" onRequestClose={() => {  if (!busy) setModal(""); }}><div className="panel-title"><h2>{modal === "edit" ? selected ? "Editar rubrica" : "Nova rubrica" : modal === "remove" ? "Remover rubrica" : "Sincronizar ERP"}</h2></div>
      {error && <Notice className="error" role="alert">{error}</Notice>}
      {notice && <Notice onDismiss={() => setNotice("")} className="success" role="status">{notice}</Notice>}
      {["import", "send"].includes(modal) && <><p>{erp ? `ERP configurado: ${erp.name}` : busy ? "A consultar a configuração do ERP…" : "Sincronização indisponível. Consulte as configurações da empresa."}</p><div className="actions"><ActionButton disabled={busy || !erp} onClick={() => { setModal("import"); setIds([]); setConfirmed(false); }}>Importar do ERP</ActionButton>{admin && (policy?.capabilities.write_rubrics ?? true) && <ActionButton disabled={busy || !erp || !rows.length} onClick={() => { setModal("send"); setProposals([]); setIds([]); act(prepareSend); }}>Enviar para o ERP</ActionButton>}</div></>}
      {modal === "remove" ? <><p>Confirma a remoção da rubrica <b>{selected?.name}</b> da IAZELA?</p><p>Se existir no ERP, a rubrica mantém-se na origem.</p><div className="actions"><ActionButton disabled={busy} onClick={() => setModal("")}>Cancelar</ActionButton><ActionButton className="danger" disabled={busy} onClick={() => act(async () => { await api(`/categories/${selected!.id}/remove`, { confirmed: true }); await reload(); await load(); setModal(""); setNotice("Rubrica removida."); })}>Confirmar remoção</ActionButton></div></> : modal === "edit" ? <ManagedForm className="doc-form" onSubmit={e => { e.preventDefault(); act(async () => { await api(selected ? `/categories/${selected.id}` : "/categories", { name, icon, color, cash_difference: cashDifference, vat_deduction_percent: deduction === "" ? null : Number(deduction) }); await reload(); await load(); setModal(""); setNotice("Rubrica guardada."); }); }}>
        <Field label="Nome"><input value={name} onChange={e => setName(e.target.value)} required minLength={2} maxLength={100} /></Field>
        <Field label="Ícone"><select value={icon} onChange={e => setIcon(e.target.value)}>{Object.entries(rubricIcons).map(([key, value]) => <option key={key} value={key}>{value[0]}</option>)}</select></Field>
        <label className="doc-check"><input type="checkbox" checked={cashDifference} onChange={e => setCashDifference(e.target.checked)} />Usar nas diferenças de caixa</label><small>Apenas uma rubrica pode estar selecionada. Será usada para faltas e sobras no fecho da caixa.</small>
        <Field label="Cor"><input type="color" value={color} onChange={e => setColor(e.target.value)} /></Field>
        <Field label="IVA dedutível estimado nas compras (%)"><input type="number" min="0" max="100" step="1" placeholder="100" value={deduction} onChange={e => setDeduction(e.target.value)} /></Field><small>Só para o mapa de IVA indicativo (por exemplo, 50 em gasóleo de viaturas ligeiras, 0 em despesas não dedutíveis). Vazio corresponde a 100%. Confirme com o contabilista.</small>
        <RubricBadge name={name || "Pré-visualização da rubrica"} icon={icon} color={color} /><ActionButton className="primary" disabled={busy}>Guardar rubrica</ActionButton>
      </ManagedForm> : modal === "import" ? <>
        <p>Escolha as rubricas a importar. As rubricas já associadas à IAZELA não aparecem nesta lista.</p>
        {busy && <p role="status">A consultar o ERP…</p>}
        <div className="rubric-import-list">{importable.map(row => <label key={row.id} className="doc-check"><input type="checkbox" checked={ids.includes(row.id)} disabled={busy} onChange={e => setIds(e.target.checked ? [...ids, row.id] : ids.filter(id => id !== row.id))} />{row.code} · {row.name}</label>)}</div>
        {!busy && erp && !importable.length && <p>Não existem rubricas novas para importar.</p>}
        <ActionButton className="primary" disabled={busy || !ids.length || ids.length > 100} onClick={() => act(async () => { const result = await api("/categories/erp/import", { ids, confirmed: true }); await reload(); await load(); setModal(""); setNotice(`${result.imported} rubrica(s) importada(s).`); })}>Confirmar importação ({ids.length})</ActionButton>
      </> : <>
        <p>Prefixo configurado: <b>{prefix || "(sem prefixo)"}</b>. Os códigos são atribuídos sequencialmente no envio.</p>
        <p>As rubricas já existentes no ERP ficam excluídas da proposta.</p>
        <div className="rubric-import-list">{proposals.map(row => <div key={row.id}>
          {row.pending ? <div>{row.name} · Envio por verificar <ActionButton disabled={busy} onClick={() => act(async () => { await api(`/categories/${row.id}/erp/send`, { confirmed: true }); await prepareSend(); await load(); setNotice("Envio confirmado no ERP com sucesso."); })}>Verificar envio pendente</ActionButton></div> : <label className="doc-check"><input type="checkbox" checked={ids.includes(row.id)} disabled={busy || row.exists} onChange={e => setIds(e.target.checked ? [...ids, row.id] : ids.filter(id => id !== row.id))} />{row.name} · {row.exists ? "Já existe no ERP" : `Proposto: ${row.code}`}</label>}
        </div>)}</div>
        {!proposals.some(row => !row.exists && !row.pending) && <p>Não existem rubricas em falta para enviar.</p>}
        <p>Ao confirmar, as rubricas selecionadas serão criadas no ERP.</p>
        <ActionButton className="primary" disabled={busy || !erp || !ids.length} onClick={() => act(async () => {
          let sent = 0;
          try { for (const row of proposals.filter(row => ids.includes(row.id) && !row.exists && !row.pending)) { await api(`/categories/${row.id}/erp/send`, { confirmed: true }); sent++; } }
          finally { await load(); await prepareSend(); setNotice(sent ? `Envio confirmado no ERP: ${sent} rubrica(s) enviada(s) com sucesso.` : ""); }
        })}>Confirmar envio ao ERP ({ids.length})</ActionButton>
      </>}
    </AppDialog>}
  </>;
}
