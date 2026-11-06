import { useEffect, useState } from "react";
import { api } from "./lib/api";
type Item = { id: string; name: string; store_id?: string };
type Connection = Item & { warehouse_id: string; active: boolean; expires_at: string };
export default function WebshopSettings() {
  const [options,setOptions] = useState<{company:{id?:string;name?:string};stores:Item[];warehouses:Item[]}>({company:{},stores:[],warehouses:[]});
  const [rows,setRows] = useState<Connection[]>([]), [store,setStore] = useState(""), [warehouse,setWarehouse] = useState(""), [name,setName] = useState("Raizpura"), [secret,setSecret] = useState<{id:string;token:string}|null>(null), [error,setError] = useState(""), [busy,setBusy] = useState(false);
  const load = async () => { const [o,c] = await Promise.all([api("/webshop/options"),api("/webshop/connections")]); setOptions(o);setRows(c); };
  useEffect(()=>{load().catch(e=>setError(e.message));},[]);
  async function action(path:string, body:unknown) {setBusy(true);setError("");setSecret(null);try { const result=await api(path,body);if(result.token)setSecret(result);await load(); }catch(e){setError((e as Error).message);}finally{setBusy(false);} }
  return <section className="panel"><h3>Raizpura · Loja online</h3><p>Empresa selecionada: <strong>{options.company.name||"empresa atual"}</strong>. Escolha a loja e o armazém que abastecem o site.</p>{error&&<p role="alert" className="error">{error}</p>}
    <form onSubmit={e=>{e.preventDefault();action("/webshop/connections",{name,store_id:store,warehouse_id:warehouse});}}>
    <label>Nome da ligação<input required maxLength={100} value={name} onChange={e=>setName(e.target.value)}/></label>
    <label>Loja<select required value={store} onChange={e=>{setStore(e.target.value);setWarehouse("");}}><option value="">Selecionar loja</option>{options.stores.map(s=><option key={s.id} value={s.id}>{s.name}</option>)}</select></label>
    <label>Armazém<select required value={warehouse} onChange={e=>setWarehouse(e.target.value)}><option value="">Selecionar armazém</option>{options.warehouses.filter(w=>w.store_id===store).map(w=><option key={w.id} value={w.id}>{w.name}</option>)}</select></label><button disabled={busy}>Criar ligação</button></form>
    {secret&&<div role="status"><h4>Configurar no WordPress → WooCommerce → GPGest</h4><p>A chave é apresentada uma única vez. Guarde-a na configuração da Raizpura.</p><label>Empresa<input readOnly value={options.company.id||""}/></label><label>Identificador da ligação<input readOnly value={secret.id}/></label><label>Chave da integração<textarea readOnly value={secret.token} autoComplete="off"/></label><button onClick={()=>setSecret(null)}>Ocultar chave</button></div>}
    {rows.map(r=><div key={r.id} style={{borderTop:"1px solid var(--border)",paddingTop:12,marginTop:16}}><strong>{r.name}</strong><p>{options.stores.find(s=>s.id===r.store_id)?.name} · {options.warehouses.find(w=>w.id===r.warehouse_id)?.name} · {r.active?"Ativa":"Revogada"}</p><small>Validade: {new Date(r.expires_at).toLocaleDateString("pt-PT")}</small>{r.active&&<div className="actions"><button disabled={busy} onClick={()=>action(`/webshop/connections/${r.id}/rotate`,{})}>Renovar chave</button><button disabled={busy} onClick={()=>action(`/webshop/connections/${r.id}/revoke`,{})}>Desativar ligação</button></div>}</div>)}
    <p>Renovar a chave invalida imediatamente a anterior. Atualize também a chave no WordPress.</p>
    <p>O catálogo e o stock seguem do GPGest para o site. As encomendas online entram no Centro de Encomendas com reserva; a entrega no GPGest regista a saída física. Esta ligação não emite faturas nem movimenta dinheiro.</p>
  </section>;
}
