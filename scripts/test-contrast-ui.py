"""Computed-style contrast audit using demo responses; no database/ERP writes."""
import json, os
from pathlib import Path
from urllib.parse import urlsplit
from playwright.sync_api import sync_playwright, expect

root=Path(__file__).resolve().parents[1]
output=root/".local/contrast-audit"
output.mkdir(parents=True,exist_ok=True)
fixtures=root/"scripts/test-visual-template-ui.py"
ns={"__file__":str(fixtures)}
exec(fixtures.read_text(encoding="utf-8").split("with sync_playwright() as playwright:")[0],ns)
inventory_fixture=root/"scripts/test-inventory-ui.py"
inv={"__file__":str(inventory_fixture)}
exec(inventory_fixture.read_text(encoding="utf-8").split("with sync_playwright() as p:")[0],inv)
data={"company":{"name":"Empresa de demonstração", "store_name":"Loja de demonstração","store_id":"demo","currency":"EUR","timezone":"Europe/Lisbon","nif":"500000000"}, "mode":{"mode":"local","resources":[]},"registers":[],"accounts":[{"key":"bank","name":"Conta de teste","bank":"Banco Demo"}],"terminals":[],"skipped":[],"payments":[{"key":key,"name":name,"selected":True,"bank_receivable":key!='numerario',"account":None,"cash":key=='numerario',"receipts":True,"payments":True,"erp_id":None,"mbway_via":"account"} for key,name in [('numerario','Numerário'),('cartao','Cartão / TPA'),('mbway','MB WAY')]]}
def intercept(route):
    path=urlsplit(route.request.url).path.removeprefix('/api')
    if path=='/setup/status': value={"completed":False,"started":True,"progress":50,"pending":[],"unclosed_count":0}
    elif path=='/setup': value={"data":data,"step":"payments","revision":1,"stores":ns['boot']['stores'],"erp_methods":[],"catalog_labels":{},"resource_labels":{}}
    elif path.startswith('/stock/'):
        return inv['mock'](route)
    elif path=='/settings/fiscal': value={"vat_period":"mensal"}
    elif path=='/entities': value=[]
    else: return ns['intercept'](route)
    route.fulfill(content_type='application/json',body=json.dumps(value))
audit=(root/'scripts/contrast-audit.js').read_text(encoding='utf-8')
sizes=[(375,812),(430,932),(768,1024),(1366,768),(1920,1080)]
if os.environ.get('CONTRAST_QUICK')=='1': sizes=[(1366,768)]
baseline=os.environ.get('CONTRAST_BASELINE')=='1'
results=[]
with sync_playwright() as p:
    browser=p.chromium.launch()
    for theme in (['dark'] if baseline else ['current','dark']):
        for width,height in ([(1366,768)] if baseline else sizes):
            page=browser.new_page(viewport={'width':width,'height':height})
            errors=[]
            page.on('pageerror',lambda error:errors.append(str(error)))
            page.add_init_script(f"localStorage.setItem('iazela-theme','{theme}')")
            page.route('**/api/**',intercept)
            page.goto('http://127.0.0.1:5174/')
            page.get_by_role('button',name='Continuar configuração',exact=True).click()
            expect(page.get_by_role('heading',name='Meios de pagamento',exact=True)).to_be_visible()
            report=page.evaluate(audit)
            results.append({'screen':'setup-payments','theme':theme,'width':width,**report})
            page.screenshot(path=str(output/f"{'before' if baseline else 'after'}-setup-{theme}-{width}.png"))
            if not baseline:
                # Explicit interaction states; mandatory cash checkbox remains disabled.
                field=page.get_by_label('Nome do meio de pagamento 1',exact=True)
                field.focus()
                results.append({'screen':'setup-focus','theme':theme,'width':width,**page.evaluate(audit)})
                page.get_by_role('button',name='Adicionar meio personalizado',exact=True).hover()
                results.append({'screen':'setup-hover','theme':theme,'width':width,**page.evaluate(audit)})
                page.keyboard.press('Escape')
                for group,item in [(None,None),('Tesouraria','Movimentos reais'),('Bancos','Múltiplas contas'),('Contabilidade','Conferência documental'),('Tabelas','Clientes'),('Tabelas','Artigos e validades'),('Configurações','Regime de IVA'),('Receitas e despesas',None),('Relatórios',None)]:
                    page.goto('http://127.0.0.1:5174/')
                    expect(page.get_by_role('button',name='Contabilidade',exact=True)).to_be_attached()
                    if group:
                        page.get_by_role('button',name=group,exact=True,include_hidden=True).first.evaluate('(el)=>el.click()')
                        if item=='Regime de IVA': page.get_by_role('button',name='Parâmetros',exact=True,include_hidden=True).evaluate('(el)=>el.click()')
                        if item: page.get_by_role('button',name=item,exact=True,include_hidden=True).evaluate('(el)=>el.click()')
                        if item=='Artigos e validades': expect(page.locator('.stock-module')).to_be_visible()
                        page.wait_for_timeout(400)
                    label=item or group or 'Dashboard'
                    results.append({'screen':label,'theme':theme,'width':width,**page.evaluate(audit)})
                    page.screenshot(path=str(output/f"after-{theme}-{width}-{label.replace(' ','_')}.png"))
            assert not errors, errors
            page.close()
            print(f'Auditado: {theme}, {width}px',flush=True)
    browser.close()
filename='before.json' if baseline else 'after.json'
(output/filename).write_text(json.dumps(results,ensure_ascii=False,indent=2),encoding='utf-8')
summary={}
nontext={}
for r in results:
    for f in r['failures']:
        key=(r['screen'],r['theme'],f['text'],str(f.get('cls')))
        summary[key]=f['ratio']
    for f in r.get('controls',[]):
        if not f['pass']: nontext[(r['screen'],r['theme'],f['kind'],f['label'])]=f['ratio']
print(json.dumps({'screens':len(results),'text_checks':sum(r['checked'] for r in results),'failures':len(summary),'nontext_failures':len(nontext),'overflow_screens':sum(bool(r.get('overflow')) for r in results),'examples':[{'screen':k[0],'theme':k[1],'text':k[2],'class':k[3],'ratio':v} for k,v in list(summary.items())[:45]],'nontext_examples':[{'screen':k[0],'theme':k[1],'kind':k[2],'label':k[3],'ratio':v} for k,v in list(nontext.items())[:20]]},ensure_ascii=True,indent=2))
if not baseline and (summary or nontext or any(r.get('overflow') for r in results)): raise SystemExit(1)
