"""Purchasing UI with mocked APIs; never creates real orders or sends email."""
import json
from pathlib import Path
from urllib.parse import urlsplit
from playwright.sync_api import sync_playwright, expect

root=Path(__file__).resolve().parents[1]
fixture=root/'scripts/test-visual-template-ui.py'
ns={'__file__':str(fixture)}
exec(fixture.read_text(encoding='utf-8').split('with sync_playwright() as playwright:')[0],ns)
inv={'__file__':str(root/'scripts/test-inventory-ui.py')}
exec(Path(inv['__file__']).read_text(encoding='utf-8').split('with sync_playwright() as p:')[0],inv)
catalog=inv['catalog'];catalog['warehouses'][0]['store_id']='demo'
ns['boot']['categories']=[{'id':'cat','name':'Compras'}]
supplier={'id':'supplier','name':'Fornecedor de teste','tax_number':'500000000','is_supplier':True,'active':True,'removed':False,'email':'fornecedor@example.com'}
documents=[]
def intercept(route):
    path=urlsplit(route.request.url).path.removeprefix('/api')
    if path=='/procurement' and route.request.method=='POST':
        data=route.request.post_data_json
        result={**data,'id':'order-test','supplier_name':supplier['name'],'supplier_nif':supplier['tax_number'],'status':'draft','revision':0,
            'confirmed_at':None,'created_at':'2026-10-18T12:00:00Z','total_cents':24600,'email_history':[], 'attachment_status':'missing','check_status':'pending','erp_status':'not_linked','payment_status':'unpaid',
            'lines':[{**line,'received':'0','invoiced':'0','returned':'0','credited':'0','pending':line['quantity']} for line in data['lines']]}
        documents.append(result)
    elif path=='/procurement':result={'documents':documents,'can_write':True,'mode':{'mode':'local'},'integration_note':'Circuito local; sem escrita no ERP.'}
    elif path=='/stock/catalog':result=catalog
    elif path=='/entities':result=[supplier]
    elif path=='/purchases':result=[]
    elif path=='/procurement-ledger':result={'rows':[],'more':False}
    else:return ns['intercept'](route)
    route.fulfill(content_type='application/json',body=json.dumps(result))

with sync_playwright() as p:
    browser=p.chromium.launch()
    for theme,width in [('current',1366),('dark',1366),('dark',390)]:
        documents.clear()
        page=browser.new_page(viewport={'width':width,'height':900})
        errors=[];page.on('pageerror',lambda e:errors.append(str(e)))
        page.add_init_script(f"localStorage.setItem('iazela-theme','{theme}')")
        page.route('**/api/**',intercept)
        page.goto('http://127.0.0.1:5174/')
        page.get_by_role('button',name='Compras e Stock',exact=True,include_hidden=True).evaluate('(e)=>e.click()')
        page.get_by_role('button',name='Encomendas a fornecedores',exact=True,include_hidden=True).evaluate('(e)=>e.click()')
        page.get_by_role('button',name='Nova encomenda',exact=True).click()
        dialog=page.get_by_role('dialog',name='Encomenda — rascunho')
        expect(dialog).to_be_visible()
        assert dialog.evaluate('(e)=>e.matches(":modal")')
        dialog.get_by_label('Fornecedor',exact=True).select_option('supplier')
        dialog.get_by_label('Artigo',exact=True).select_option('p1')
        dialog.get_by_label('Quantidade',exact=True).fill('20')
        expect(dialog.get_by_label('Descrição',exact=True)).to_have_value(inv['article']['name'])
        output=root/'.local/procurement-ui';output.mkdir(exist_ok=True)
        page.screenshot(path=str(output/f'order-{theme}-{width}.png'))
        audit=page.evaluate((root/'scripts/contrast-audit.js').read_text(encoding='utf-8'))
        assert not audit.get('failures'),audit.get('failures')
        assert not audit.get('overflow'),audit
        dialog.get_by_role('button',name='Guardar rascunho',exact=True).click()
        detail=page.get_by_role('dialog',name='Documento de compras')
        expect(detail).to_be_visible()
        expect(detail.get_by_role('button',name='Confirmar encomenda',exact=True)).to_be_visible()
        assert len(documents)==1 and documents[0]['lines'][0]['quantity']=='20'
        assert not errors,errors
        page.close()
    browser.close()
print('Purchasing menu, order form, article selection, draft, modal/mobile and contrast: OK')
