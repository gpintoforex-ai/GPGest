"""Isolated entity sheet checks: no real API writes, ERP calls or messages."""
import copy
import sys
from datetime import date
from pathlib import Path
from urllib.parse import unquote, urlsplit
from playwright.sync_api import expect, sync_playwright
ROOT = Path(__file__).resolve().parents[1]
ns = {'__file__': str(ROOT / 'scripts/test-supplier-dialog-ui.py')}
exec((ROOT / 'scripts/test-supplier-dialog-ui.py').read_text(encoding='utf-8').split('with sync_playwright() as p:')[0], ns)
kind = sys.argv[1] if len(sys.argv) > 1 else 'customers'
from app.table_framework import TABLES
info = copy.deepcopy(ns['INFO'])
info.update(key=kind, title='Clientes' if kind == 'customers' else 'Fornecedores', singular='cliente' if kind == 'customers' else 'fornecedor', fields=[f.describe() for f in TABLES[kind].fields])
row = copy.deepcopy(ns['ROW']); row['values'].update(name='Entidade de teste', is_customer=kind=='customers', is_supplier=kind=='suppliers')
row['description']='Entidade de teste'
expected = ['Geral','Contactos','Condições Comerciais','Vendas','Encomendas','Conta Corrente','Documentos','Histórico'] if kind=='customers' else ['Geral','Contactos','Condições de Compra','Artigos','Encomendas','Compras','Conta Corrente','Histórico']
with sync_playwright() as p:
    browser=p.chromium.launch()
    for width,height in [(1440,900),(1024,768),(390,844)]:
        calls=[]; errors=[]; saved=[]; managed=[]
        def intercept(route):
            path=unquote(urlsplit(route.request.url).path.removeprefix('/api'))
            calls.append((path, route.request.method))
            if path==f'/tables/{kind}': return route.fulfill(json=info)
            if path==f'/tables/{kind}/records': return route.fulfill(json={'rows':[row], 'total':1,'total_all':1,'page':1,'pages':1,'size':25})
            if path.endswith('/details'): return route.fulfill(json={**row,'related_entity_id':'1','dependencies':[], 'managed_fields':managed, 'remove_blocked':{'app':None,'both':None}})
            if path==f'/tables/{kind}/records/local:1' and route.request.method=='POST':
                saved.append(route.request.post_data_json)
                return route.fulfill(json={'ok':True})
            if path=='/customer-orders': return route.fulfill(json={'orders':[{'id':'order','customer_id':'1','number':'PC-TESTE','requested_at':'2026-11-02','closed':False}], 'simulations':[]})
            if path=='/customer-orders/history': return route.fulfill(json=[{'id':'event','order_id':'order','at':'2026-11-02','action':'create','user':'Teste'}])
            if path=='/procurement': return route.fulfill(json={'documents':[
                {'id':'order','supplier_id':'1','kind':'order','status':'confirmed','document':'EF-TESTE','document_date':'2026-11-02'},
                {'id':'recent','supplier_id':'1','kind':'invoice','status':'confirmed','document':'COMPRA-RECENTE','document_date':date.today().isoformat()},
                {'id':'old','supplier_id':'1','kind':'invoice','status':'confirmed','document':'COMPRA-ANTIGA','document_date':'2020-01-01'},
                {'id':'draft','supplier_id':'1','kind':'invoice','status':'draft','document':'RASCUNHO','document_date':date.today().isoformat()}]})
            if path=='/stock/catalog': return route.fulfill(json={'articles':[{'id':'a','code':'A1','name':'Artigo teste','supplier_id':'1'}]})
            if path=='/archive/documents': return route.fulfill(json={'total':2,'documents':[
                {'id':'doc1','entity_nif':'PT506406768','document_number':'DOC-CERTO','issue_date':'2026-11-02'},
                {'id':'doc2','entity_nif':'999999999','document_number':'DOC-OUTRA-ENTIDADE','issue_date':'2026-11-02'}]})
            return ns['intercept'](route)
        page=browser.new_page(viewport={'width':width,'height':height})
        page.on('pageerror',lambda e:errors.append(str(e)))
        page.route('**/api/**',intercept)
        page.goto('http://127.0.0.1:5174/?pagina='+info['title'])
        page.get_by_role('button',name='Editar Entidade de teste').click()
        dialog=page.get_by_role('dialog',name='Editar '+info['singular'],exact=True)
        expect(dialog.get_by_label('Nome',exact=True)).to_have_value('Entidade de teste')
        assert dialog.get_by_role('tab').all_text_contents()==expected
        if kind=='suppliers':
            expect(dialog.locator('.record-indicator').filter(has_text='Compras recentes').locator('strong')).to_have_text('1')
        for tab in expected:
            dialog.get_by_role('tab',name=tab,exact=True).click()
            expect(dialog.get_by_role('tabpanel')).to_have_count(1)
            if tab=='Documentos':
                expect(dialog.get_by_role('link',name='DOC-CERTO',exact=True)).to_be_visible()
                expect(dialog.get_by_role('link',name='DOC-OUTRA-ENTIDADE',exact=True)).to_have_count(0)
        dialog.get_by_role('tab',name='Encomendas',exact=True).click()
        expect(dialog.get_by_text('PC-TESTE' if kind=='customers' else 'EF-TESTE',exact=True)).to_be_visible()
        dialog.get_by_role('tab',name='Contactos',exact=True).click()
        dialog.get_by_label('Telefone',exact=True).fill('910000001')
        dialog.get_by_label('Email',exact=True).fill('invalido')
        dialog.get_by_role('tab',name='Geral',exact=True).click()
        dialog.get_by_role('button',name='Guardar alterações',exact=True).click()
        expect(dialog.get_by_role('tab',name='Contactos',exact=True)).to_have_attribute('aria-selected','true')
        assert not saved, 'A validação não pode ser omitida por o campo estar noutro separador.'
        dialog.get_by_label('Email',exact=True).fill('')
        dialog.get_by_role('tab',name='Geral',exact=True).click()
        assert dialog.evaluate('(d) => d.scrollWidth <= d.clientWidth + 1')
        assert dialog.locator('.business-record-layout').evaluate('(d) => d.scrollWidth <= d.clientWidth + 1')
        out=ROOT/'.local/entity-record-ui'; out.mkdir(exist_ok=True)
        dialog.screenshot(path=str(out/f'{kind}-{width}.png'))
        dialog.get_by_role('button',name='Guardar alterações',exact=True).click()
        expect(dialog).not_to_be_visible()
        assert saved and saved[0]['values']['phone']=='910000001'
        assert saved[0]['values']['is_customer']==(kind=='customers')
        assert saved[0]['values']['is_supplier']==(kind=='suppliers')
        if width==1440:
            managed.extend(['name','tax_number'])
            info['erp']['integrated']=True
            row['origin']='erp'
            page.reload()
            page.get_by_role('button',name='Editar Entidade de teste').click()
            dialog=page.get_by_role('dialog',name='Editar '+info['singular'],exact=True)
            expect(dialog.get_by_label('Nome (ERP)',exact=True)).to_be_disabled()
            expect(dialog.get_by_label('NIF (ERP)',exact=True)).to_be_disabled()
            dialog.get_by_role('button',name='Fechar',exact=True).click()
            info['permissions']['edit']=False
            page.reload()
            page.get_by_role('button',name='Consultar Entidade de teste').click()
            readonly=page.get_by_role('dialog',name='Detalhes do '+info['singular'],exact=True)
            expect(readonly.get_by_role('button',name='Editar',exact=True)).to_have_count(0)
            expect(readonly.get_by_role('button',name='Guardar alterações',exact=True)).to_have_count(0)
            expect(readonly.get_by_text('Entidade de teste',exact=True)).to_have_count(2)
            info['permissions']['edit']=True
            info['erp']['integrated']=False
            row['origin']='local'
        assert not errors,errors
        assert not [path for path,method in calls if method!='GET' and path!=f'/tables/{kind}/records/local:1']
        page.close()
    browser.close()
print(kind+': tabs, scoped relations, preserved field payload and hidden flags; desktop, tablet, mobile passed.')
