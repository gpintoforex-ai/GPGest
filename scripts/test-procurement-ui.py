"""Purchasing UI with mocked APIs; never creates real orders or sends email."""
import json
from pathlib import Path
from urllib.parse import quote, urlsplit
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
    elif path=='/procurement/expiry-returns':result={'items':[{'article_id':'p1','code':inv['article']['code'],'description':inv['article']['name'],'unit':'un',
        'lot_id':'lot1','lot_number':'L-VAL','expiry':'2026-11-20','days_left':21,'warn_from':'2026-08-20','quantity':'3','warehouse_id':catalog['warehouses'][0]['id'],
        'warehouse_name':catalog['warehouses'][0]['name'],'store_id':'demo','supplier_id':'supplier','supplier_name':supplier['name'],'months':3}]}
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
        # Páginas abertas pelo endereço: o menu está a ser reorganizado e não faz parte deste teste.
        open_page=lambda name:page.goto('http://127.0.0.1:5174/?pagina='+quote(name))
        open_page('Encomendas a fornecedores')
        page.get_by_role('button',name='Nova encomenda',exact=True).click()
        dialog=page.get_by_role('dialog',name='Encomenda — rascunho')
        expect(dialog).to_be_visible()
        assert dialog.evaluate('(e)=>e.matches(":modal")')
        dialog.get_by_label('Fornecedor',exact=True).select_option('supplier')
        dialog.get_by_role('tab',name='Artigos',exact=True).click()
        # Pesquisa do artigo pelo nome (parte do nome, sem acentos): escolhe-se na lista de resultados.
        dialog.get_by_label('Artigo',exact=True).fill(inv['article']['name'].split()[0][:4])
        dialog.get_by_role('option').filter(has_text=inv['article']['name']).first.click()
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
        detail.get_by_role('tab',name='Operações',exact=True).click()
        expect(detail.get_by_role('button',name='Confirmar encomenda',exact=True)).to_be_visible()
        assert len(documents)==1 and documents[0]['lines'][0]['quantity']=='20'
        detail.get_by_role('button',name='Fechar',exact=True).click()
        # Receção: encomendas por receber no topo, com os clientes à espera.
        documents[0].update(status='confirmed',confirmed_at='2026-10-18T12:00:00Z',document='ENC/1',expected_date='2026-10-01',
            customer_orders=[{'order_id':'o1','number':'PC-1','customer':'Cliente Urgente','description':inv['article']['name'],'waiting':'5','urgent':True,'due_date':None}])
        open_page('Receções de mercadoria')
        pending=page.get_by_role('table',name='Encomendas por receber',exact=True)
        expect(pending).to_contain_text('ENC/1')
        expect(pending).to_contain_text('PC-1 · Cliente Urgente · 5')
        expect(pending.get_by_text('Em atraso',exact=True)).to_be_visible()
        page.screenshot(path=str(output/f'to-receive-{theme}-{width}.png'),full_page=True)
        audit=page.evaluate((root/'scripts/contrast-audit.js').read_text(encoding='utf-8'))
        assert not audit.get('failures'),audit.get('failures')
        assert not audit.get('overflow'),audit
        pending.get_by_role('button',name='Receber',exact=True).click()
        receipt=page.get_by_role('dialog',name='Receção — rascunho')
        expect(receipt.get_by_label('Quantidade recebida',exact=True)).to_have_value('20')
        expect(receipt.get_by_label('Anexar PDF (guarda no Arquivo digital)',exact=True)).to_be_visible()
        receipt.get_by_role('button',name='Fechar',exact=True).click()
        # Devoluções: lotes a acabar a validade de fornecedores que aceitam devolução.
        open_page('Devoluções a fornecedores')
        expiring=page.get_by_role('table',name='Produtos a devolver por validade',exact=True)
        expect(expiring).to_contain_text('L-VAL')
        expect(expiring).to_contain_text('Faltam 21 dia(s)')
        page.screenshot(path=str(output/f'expiry-returns-{theme}-{width}.png'),full_page=True)
        expiring.get_by_role('button',name='Preparar devolução',exact=True).click()
        back=page.get_by_role('dialog',name='Devolução — rascunho')
        expect(back.get_by_label('Quantidade',exact=True)).to_have_value('3')
        expect(back.get_by_label('Divergências / motivo',exact=True)).to_have_value('Validade 20/11/2026: devolução aceite pelo fornecedor')
        expect(back.get_by_label('Tipo de motivo',exact=True)).to_have_value('validade')
        back.get_by_role('button',name='Fechar',exact=True).click()
        dirty=page.get_by_role('alertdialog',name='Descartar alterações?',exact=True)
        if dirty.count():dirty.get_by_role('button',name='Descartar alterações',exact=True).click()
        assert not errors,errors
        page.close()
    browser.close()
print('Purchasing menu, order form, article selection, draft, modal/mobile and contrast: OK')
