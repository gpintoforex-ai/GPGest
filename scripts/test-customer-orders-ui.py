"""Customer order screens with synthetic browser-only API responses; no real writes/messages."""
import copy
import json
import re
from decimal import Decimal
from pathlib import Path
from urllib.parse import quote, urlsplit

from playwright.sync_api import expect, sync_playwright

ROOT = Path(__file__).resolve().parents[1]
fixture = ROOT / 'scripts/test-visual-template-ui.py'
ns = {'__file__': str(fixture)}
exec(fixture.read_text(encoding='utf-8').split('with sync_playwright() as playwright:')[0], ns)
TERMS = {'currency':'EUR','minimum_cents':25000,'minimum_basis':'net','discount':'0','tiers':[],
         'shipping_cents':0,'free_shipping_cents':None,'shipping_vat':'23','vat':'23','lead_days':7,
         'pending_days':7,'confirmation_days':3,'pickup_days':7,'email':'supplier@example.test','notes':'','prices':{}}
ARTICLE = {'id':'article','code':'CAF-01','name':'Café de teste','barcodes':['5600000000000'],
           'active':True,'origin':'Local','precision':0,'vat_rate':'23','sale_net':'12.5','last_cost':'37','supplier_id':'supplier'}


def run():
    with sync_playwright() as p:
        browser=p.chromium.launch()
        for width in [1440,390]:
            boot=copy.deepcopy(ns['boot']);boot['company']['id']='isolated-customer-orders'
            store=boot['stores'][0]['id']; orders=[]; simulations=[]; calls=[]; errors=[]; conferences=[]
            conference={'id':'review','revision':0,'status':'reviewed','store_id':store,'simulation_id':'simulation','document_id':'original','document_name':'fatura-original.pdf','supplier_id':'supplier','supplier_name':'Fornecedor UI','processing_status':'processado','file_url':'/api/archive/documents/original/file','cloud_status':'confirmed','cloud_error':'','cloud_id':'cloud-id','jobs':[], 'article_proposals':[], 'erp_operations':[], 'procurement_id':None,'proposal':None,'erp_message':'Wintouch: confirmação explícita e recuperação por consulta.',
                'review':{'supplier_nif':'500000000','document':'FT/1','document_type':'FT','document_date':'2026-10-21','due_date':'2026-10-22','currency':'EUR','total_cents':6150,'tax_cents':1150,'note':'Original conferido','lines':[{'article_id':'article','reference':'CAF-01','description':'Café de teste','quantity':'5','price':'10','discount':'0','vat':'23'}]},
                'comparison':{'rows':[{'article_id':'article','reference':'CAF-01','description':'Café de teste','quantity':'5','price':'10','discount':'0','vat':'23','ordered':'5','received':'5','billed':'5','candidates':[]}], 'missing':[],'discrepancies':[],'warnings':[],'fingerprint':'a'*64,'approved':False,'automatic_allowed':True,'totals':{'total_cents':6150,'tax_cents':1150}}}
            def intercept(route):
                path=urlsplit(route.request.url).path.removeprefix('/api')
                calls.append((path,route.request.method))
                if path=='/bootstrap':return route.fulfill(json=boot)
                if path=='/customer-orders':
                    if route.request.method=='POST':
                        body=route.request.post_data_json
                        assert body['customer_name']=='Cliente UI' and len(body['lines'])==2
                        assert body['lines'][0]['article_id']=='article' and not body['lines'][1]['article_id']
                        assert body['request_key'] and not body['contact_allowed']
                        row={**body,'id':'order','number':'PC-UI','created_at':body['requested_at'],'revision':0,'ready':False,'notified':False,'closed':False,'late':False}
                        row['lines']=[{**line,'id':f'line-{i}','provisional':not line['article_id'],'state':'waiting','state_label':'A aguardar encomenda',
                            'article_code':ARTICLE['code'] if line['article_id'] else '',
                            'quantities':{'requested':line['quantity'],'to_order':line['quantity'],**{k:'0' for k in ['reserved','simulated','ordered','sent','confirmed','received','delivered','cancelled']}}} for i,line in enumerate(body['lines'])]
                        orders.append(row);return route.fulfill(json=row)
                    return route.fulfill(json={'orders':orders,'simulations':simulations,'can_write':True,'can_buy':True,'integration_note':'Stock local; não envia ao ERP.'})
                if path=='/customer-orders/catalog':return route.fulfill(json={'customers':[], 'suppliers':[{'id':'supplier','name':'Fornecedor UI','email':'supplier@example.test','terms':TERMS,'revision':0}]})
                if path=='/customer-orders/history':return route.fulfill(json=[])
                if path=='/stock/catalog':return route.fulfill(json={'articles':[ARTICLE],'warehouses':[{'id':'wh','store_id':store,'name':'Principal'}]})
                if path=='/procurement':return route.fulfill(json={'documents':[]})
                if path=='/order-cycle/stock':return route.fulfill(json={'items':[{'article_id':'article','free':'10','incoming':'0','to_order':'5'}]})
                if path=='/order-cycle/recommendations':return route.fulfill(json={'items':[]})
                if path=='/archive/documents':return route.fulfill(json={'documents':[{'id':'original','original_name':'fatura-original.pdf','document_number':'FT/1'}]})
                if path=='/order-cycle/reviews':
                    if route.request.method=='POST':
                        body=route.request.post_data_json
                        assert body['document_id']=='original' and body['simulation_id']=='simulation'
                        conferences.append(copy.deepcopy(conference));return route.fulfill(json=conferences[-1])
                    return route.fulfill(json={'items':conferences})
                if path=='/order-cycle/reviews/review/approve':
                    body=route.request.post_data_json;assert body['confirmed'] and body['fingerprint']=='a'*64
                    conferences[0]['comparison']['approved']=True;conferences[0]['revision']+=1;conferences[0]['status']='approved';return route.fulfill(json=conferences[0])
                if path in ('/customer-orders/simulations/preview','/customer-orders/simulations'):
                    body=route.request.post_data_json
                    gross=sum(int(Decimal(line['quantity'])*Decimal(line['unit_cost'])*100) for line in body['lines'])
                    result={'gross_cents':gross,'discount_cents':0,'net_cents':gross,'vat_cents':0,'shipping_cents':0,'shipping_vat_cents':0,'total_cents':gross,'eligible_cents':gross,'missing_cents':max(0,25000-gross),'currency':'EUR','lines':[]}
                    if path.endswith('/preview'):return route.fulfill(json=result)
                    row={**body,'id':'simulation','revision':0,'status':'draft','supplier_name':'Fornecedor UI','totals':result,'created_at':'2026-10-21T12:00:00Z','procurement_id':None,'procurement':None}
                    simulations.append(row);return route.fulfill(json=row)
                return ns['intercept'](route)
            context=browser.new_context(viewport={'width':width,'height':1000},locale='pt-PT')
            if width==390:context.add_init_script("localStorage.setItem('iazela-theme','dark')")
            page=context.new_page();page.route('**/api/**',intercept);page.on('pageerror',lambda e:errors.append(str(e)))
            page.goto('http://127.0.0.1:5174/entrar?pagina='+quote('Encomendas de clientes'))
            expect(page.get_by_role('heading',name='Encomendas de clientes',exact=True)).to_be_visible()
            navigation=page.get_by_role('navigation',name='Áreas das encomendas de clientes',exact=True)
            for label in ['Encomendas de Clientes','Necessidades de Compra','Encomendas a Fornecedores','Simulador de Fornecedores','Receção e OCR','Conferência ERP','Levantamentos','Histórico','Resumo']:
                navigation.get_by_role('button',name=label,exact=True).click()
                assert not page.evaluate('document.documentElement.scrollWidth>innerWidth'),label
            page.screenshot(path=str(ROOT/f'.local/customer-orders-summary-{width}.png'),full_page=True)
            page.get_by_role('button',name='Novo pedido',exact=True).click()
            dialog=page.get_by_role('dialog',name='Novo pedido de cliente',exact=True)
            dialog.get_by_label('Nome do cliente',exact=True).fill('Cliente UI')
            first=dialog.get_by_role('group',name='Produto 1',exact=True)
            first.get_by_role('combobox',name='Pesquisar artigo por código, descrição ou EAN').fill('5600000000000')
            expect(first.get_by_label('Descrição',exact=True)).to_have_value('Café de teste')
            first.get_by_label('Quantidade pedida',exact=True).fill('5')
            dialog.get_by_role('button',name='Adicionar produto',exact=True).click()
            second=dialog.get_by_role('group',name='Produto 2',exact=True)
            second.get_by_label('Descrição',exact=True).fill('Produto provisório')
            second.get_by_label('Quantidade pedida',exact=True).fill('2')
            dialog.get_by_role('button',name='Guardar',exact=True).click()
            detail=page.get_by_role('dialog',name='Pedido PC-UI',exact=True)
            expect(detail).to_be_visible()
            expect(detail.get_by_text('Produto provisório',exact=True)).to_be_visible()
            detail.get_by_role('button',name='Fechar',exact=True).click()
            navigation.get_by_role('button',name='Simulador de Fornecedores',exact=True).click()
            page.get_by_role('button',name='Nova simulação',exact=True).click()
            sim=page.get_by_role('dialog',name='Simulador de compra',exact=True)
            sim.get_by_label('Fornecedor',exact=True).select_option('supplier')
            sim.get_by_role('checkbox',name=re.compile('Cliente UI.*Café de teste')).check()
            sim.get_by_role('button',name='Adicionar pedidos selecionados',exact=True).click()
            expect(sim.get_by_label('Quantidade de compra',exact=True)).to_have_value('5')
            sim.get_by_role('button',name='Calcular simulação',exact=True).click()
            expect(sim.get_by_role('status').filter(has_text='Faltam')).to_contain_text('65')
            sim.screenshot(path=str(ROOT/f'.local/customer-orders-simulator-{width}.png'))
            sim.get_by_role('button',name='Fechar',exact=True).click()
            expect(page.get_by_role('alertdialog',name='Descartar alterações?',exact=True)).to_be_visible()
            page.get_by_role('button',name='Continuar a editar',exact=True).click()
            sim.get_by_role('button',name='Guardar simulação',exact=True).click()
            expect(page.get_by_role('table',name='simulações de fornecedores',exact=True)).to_contain_text('Fornecedor UI')
            page.get_by_role('button',name='Abrir simulação',exact=True).click()
            expect(page.get_by_role('dialog',name='Detalhe da simulação')).to_contain_text('Cliente UI')
            page.get_by_role('dialog',name='Detalhe da simulação').get_by_role('button',name='Fechar',exact=True).click()
            # Continue from an already prepared supplier order (synthetic server state).
            simulations[0].update(procurement_id='procurement',procurement={'status':'confirmed','document':'ENC/UI'},status='prepared')
            page.get_by_role('button',name='Atualizar',exact=True).click()
            navigation.get_by_role('button',name='Receção e OCR',exact=True).click()
            page.get_by_text('Originais, OCR e cloud',exact=True).click()
            page.get_by_role('button',name='Associar ou importar original',exact=True).click()
            original=page.get_by_role('dialog',name='Associar original à encomenda',exact=True)
            original.get_by_label('Encomenda ao fornecedor',exact=True).select_option('simulation')
            original.get_by_label('Original no arquivo',exact=True).select_option('original')
            original.get_by_role('button',name='Associar e iniciar processamento',exact=True).click()
            page.get_by_role('button',name='Conferir documento',exact=True).click()
            conference_dialog=page.get_by_role('dialog',name='Conferência de encomenda',exact=True)
            expect(conference_dialog.get_by_role('table',name='encomendado recebido faturado',exact=True)).to_contain_text('Café de teste')
            conference_dialog.get_by_label('Motivo da decisão',exact=True).fill('Original e receção confirmados')
            conference_dialog.get_by_role('button',name='Aprovar conferência',exact=True).click()
            page.get_by_role('alertdialog',name='Aprovar conferência?',exact=True).get_by_role('button',name='Aprovar conferência',exact=True).click()
            expect(conference_dialog.get_by_role('button',name='Preparar proposta ERP',exact=True)).to_be_enabled()
            assert not page.evaluate('document.documentElement.scrollWidth>innerWidth')
            conference_dialog.screenshot(path=str(ROOT/f'.local/order-cycle-conference-{width}.png'))
            assert not errors,errors
            writes=[path for path,method in calls if method!='GET']
            assert set(writes)=={'/customer-orders','/customer-orders/simulations/preview','/customer-orders/simulations','/order-cycle/reviews','/order-cycle/reviews/review/approve'},writes
            print(f'PASS {width}px: nine areas, products, grouping, minimum simulation, original archive link and three-way approval; no stock, ERP or email writes',flush=True)
            context.close()
        browser.close()


if __name__=='__main__':run()
