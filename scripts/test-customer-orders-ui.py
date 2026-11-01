"""Customer order screens with synthetic browser-only API responses; no real writes/messages."""
import copy
import json
import re
from decimal import Decimal
from pathlib import Path
from urllib.parse import quote, urlsplit

from playwright.sync_api import expect, sync_playwright

ROOT = Path(__file__).resolve().parents[1]
import sys  # noqa: E402

sys.path.insert(0, str(ROOT / 'backend'))
import app.table_specs  # noqa: E402,F401
from app.cash_local import BASE_METHODS  # noqa: E402
from app.table_framework import TABLES  # noqa: E402

SUPPLIER_FIELDS = [f.describe() for f in TABLES['suppliers'].fields]
for _field in SUPPLIER_FIELDS:
    if _field['type'] == 'payment_method':
        _field['options'] = [{'value': k, 'label': v, 'active': True} for k, v in BASE_METHODS.items()]
SUPPLIER_TABLE = {'key':'suppliers','title':'Fornecedores','singular':'fornecedor','feminine':False,'description':'',
    'fields':SUPPLIER_FIELDS,'extra_columns':[],'code_field':None,'description_field':'name','has_active':True,
    'allow_create':True,'allow_remove':True,'remove_reason':'','local_only_reason':'',
    'permissions':{k:True for k in ('view','create','edit','status','remove','import','export','erp_remove')},
    'erp':{'integrated':False,'available':False,'reason':'Modo local.','operations':{}}}
SUPPLIER_RECORD = {'id':'local:supplier','values':{'tax_number':'500000000','name':'Fornecedor UI','email':'','phone':'','address':'',
    'locality':'','postal_code':'','category_id':None,'payment_term_id':None,'payment_method_id':None,'is_customer':False,
    'is_supplier':True,'active':True},'labels':{},'code':None,'description':'Fornecedor UI','active':True,'origin':'local',
    'erp_id':'','erp_code':'','sync_state':'apenas_local','sync_label':'Apenas local','last_sync_at':None,'last_error':'',
    'dependencies':[],'managed_fields':[],'remove_blocked':{'app':None,'both':None}}
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
                if path=='/customer-orders/order' and route.request.method=='PUT':
                    body=route.request.post_data_json
                    assert 'store_id' not in body and body['revision']==orders[0]['revision']
                    assert [line.get('id') for line in body['lines']]==['line-0','line-1']
                    assert body['notes']=='Levantar à tarde' and body['phone']=='+351910000000'
                    orders[0].update({k:v for k,v in body.items() if k not in ('lines','request_key','reason')},revision=orders[0]['revision']+1)
                    return route.fulfill(json=orders[0])
                if path=='/tables/suppliers':return route.fulfill(json=SUPPLIER_TABLE)
                if path=='/tables/suppliers/records/local%3Asupplier/details':return route.fulfill(json=SUPPLIER_RECORD)
                if path=='/categories':return route.fulfill(json=[])
                if path=='/payment-terms':return route.fulfill(json=[])
                if path=='/customer-orders/catalog':return route.fulfill(json={'customers':[], 'suppliers':[{'id':'supplier','name':'Fornecedor UI','email':'supplier@example.test','terms':TERMS,'revision':0}]})
                if path=='/customer-orders/history':return route.fulfill(json=[])
                if path=='/stock/catalog':return route.fulfill(json={'articles':[ARTICLE],'warehouses':[{'id':'wh','store_id':store,'name':'Principal'}]})
                if path=='/procurement':return route.fulfill(json={'documents':[]})
                if path=='/order-cycle/stock':return route.fulfill(json={'items':[{'article_id':'article','free':'10','incoming':'0','to_order':'5'}]})
                if path=='/order-cycle/supply-center':return route.fulfill(json={'store_id':store,'stock_known':True,'read_only':True,'note':'Dados fictícios.',
                    'metrics':{k:0 for k in ('pending_orders','short_articles','needs_to_buy','sent_orders','partial_orders','ready_orders','urgent_orders')},
                    'articles':[],'suppliers':[],'supplier_catalog':[]})
                if path=='/order-cycle/recommendations':
                    assert 'period_days=' in route.request.url
                    return route.fulfill(json={'items':[{'article_id':'tea','code':'CHA-01','description':'Chá de teste','free':'1','minimum':'4',
                        'below_minimum':True,'period_sales':'12','period_days':90,'incoming':'0','quantity':'6','unit_cost':'2','vat':'23'}]})
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
            # O Centro de Encomendas (separador inicial) é um ecrã próprio, com teste próprio: começa nos pedidos.
            page.goto('http://127.0.0.1:5174/entrar?pagina='+quote('Encomendas de clientes')+'&area.pedidos-clientes=pedidos')
            # O Centro de Encomendas (separador inicial) não mostra o cabeçalho da página: espera-se pelo menu.
            navigation=page.get_by_role('navigation',name='Áreas das encomendas de clientes',exact=True)
            expect(navigation).to_be_visible()
            # Menu sem duplicados: o Centro substitui Resumo e Necessidades; Levantamentos é um filtro dos pedidos.
            labels=['Centro de Encomendas','Pedidos de clientes','Encomendas a fornecedores','Receção e conferência','Condições dos fornecedores','Histórico','Indicadores']
            expect(navigation.get_by_role('button')).to_have_count(len(labels))
            for label in labels[1:6]+labels[1:2]:
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
            expect(detail.get_by_role('heading',name='Produto provisório',exact=True)).to_be_visible()
            expect(detail.get_by_text('Produto sem fornecedor',exact=True)).to_be_visible()
            # Editar o pedido na mesma janela do registo.
            detail.get_by_role('button',name='Editar pedido',exact=True).click()
            edit=page.get_by_role('dialog',name='Editar pedido de cliente',exact=True)
            expect(edit.get_by_label('Nome do cliente',exact=True)).to_have_value('Cliente UI')
            expect(edit.get_by_role('group',name='Produto 2',exact=True).get_by_label('Descrição',exact=True)).to_have_value('Produto provisório')
            expect(edit.get_by_label('Estabelecimento',exact=True)).to_be_disabled()
            edit.get_by_label('Telefone (com indicativo)',exact=True).fill('+351910000000')
            edit.get_by_label('Observações do pedido',exact=True).fill('Levantar à tarde')
            edit.screenshot(path=str(ROOT/f'.local/customer-orders-edit-{width}.png'))
            edit.get_by_role('button',name='Guardar alterações',exact=True).click()
            expect(edit).to_be_hidden()
            expect(detail).to_contain_text('Levantar à tarde')
            expect(detail).to_contain_text('+351910000000')
            detail.get_by_role('button',name='Fechar',exact=True).click()
            navigation.get_by_role('button',name='Pedidos de clientes',exact=True).click()
            expect(page.get_by_role('table',name='pedidos de clientes',exact=True)).to_contain_text('A aguardar produtos')
            assert 'status-warning' in page.get_by_text('A aguardar produtos',exact=True).get_attribute('class')
            filters=page.get_by_role('group',name='Filtrar pedidos',exact=True)
            filters.get_by_role('button',name=re.compile(r'^(Prontos a levantar|Disponíveis para levantar) \(0\)$')).click()
            expect(page.get_by_role('table',name='pedidos de clientes',exact=True)).not_to_contain_text('PC-UI')
            filters.get_by_role('button',name='Em aberto (1)',exact=True).click()
            expect(page.get_by_role('table',name='pedidos de clientes',exact=True)).to_contain_text('PC-UI')
            # A ficha do fornecedor mostra um separador de cada vez; rubrica, prazo e meio nas condições comerciais.
            navigation.get_by_role('button',name='Condições dos fornecedores',exact=True).click()
            page.get_by_role('button',name='Editar fornecedor Fornecedor UI',exact=True).click()
            record=page.get_by_role('dialog',name='Editar fornecedor',exact=True)
            expect(record.get_by_text('Identificação e contactos',exact=True)).to_be_visible()
            expect(record.get_by_text('Prazo de pagamento',exact=True)).to_be_hidden()
            record.get_by_role('tab',name='Condições comerciais',exact=True).click()
            expect(record.get_by_text('Prazo de pagamento',exact=True)).to_be_visible()
            expect(record.get_by_text('Encomenda mínima aceite (€)',exact=True)).to_be_visible()
            expect(record.get_by_text('Portes grátis a partir de (€)',exact=True)).to_be_visible()
            expect(record.get_by_text('Avisar quantos meses antes da validade',exact=True)).to_be_hidden()
            record.get_by_role('tab',name='Validades e devoluções',exact=True).click()
            expect(record.get_by_text('Avisar quantos meses antes da validade',exact=True)).to_be_visible()
            expect(record.get_by_label('Aceita devolução de produtos a acabar a validade')).to_be_visible()
            expect(record.get_by_text('Encomenda mínima aceite (€)',exact=True)).to_be_hidden()
            record.screenshot(path=str(ROOT/f'.local/customer-orders-supplier-expiry-{width}.png'))
            expect(record.get_by_text('Identificação e contactos',exact=True)).to_be_hidden()
            record.screenshot(path=str(ROOT/f'.local/customer-orders-supplier-terms-{width}.png'))
            record.get_by_role('button',name='Cancelar',exact=True).click()
            expect(record).to_be_hidden()
            navigation.get_by_role('button',name='Encomendas a fornecedores',exact=True).click()
            # Encomendas necessárias para os pedidos: sem fornecedor primeiro; valor face ao mínimo sem portes.
            needed=page.get_by_role('table',name='encomendas necessárias por fornecedor',exact=True)
            expect(needed.get_by_role('row')).to_have_count(3)
            expect(needed).to_contain_text('Sem fornecedor')
            supplier_row=needed.get_by_role('row').filter(has_text='Fornecedor UI')
            expect(supplier_row).to_contain_text('Faltam 65')
            expect(supplier_row).to_contain_text('PC-UI')
            expect(needed.get_by_role('row').filter(has_text='Sem fornecedor')).to_have_class(re.compile('co-need-row-danger'))
            expect(supplier_row).to_have_class(re.compile('co-need-row-warning'))
            expect(supplier_row).to_contain_text('Sem data de entrega')
            page.get_by_role('region',name=re.compile('^Encomendas necessárias')).screenshot(path=str(ROOT/f'.local/customer-orders-needed-{width}.png'))
            supplier_row.get_by_role('button',name='Simular encomenda',exact=True).click()
            sim=page.get_by_role('dialog',name='Simulador de compra',exact=True)
            expect(sim.get_by_label('Quantidade de compra',exact=True)).to_have_value('5')
            expect(sim.get_by_label('Condições do fornecedor')).to_contain_text('Encomenda mínima aceite (sem portes): 250,00')
            conditions=sim.get_by_text('Consultar condições da ficha do fornecedor',exact=True)
            if conditions.count():conditions.click()
            expect(sim.get_by_label('Condições do fornecedor').get_by_role('button',name='Editar na ficha',exact=True)).to_be_visible()
            expect(sim.get_by_text('Ajustar condições só nesta simulação',exact=True)).to_be_visible()
            # Reposição automática: abaixo do mínimo e com vendas no período do fornecedor.
            low=sim.get_by_role('table',name='Artigos abaixo do mínimo com vendas',exact=True)
            expect(low).to_contain_text('Chá de teste')
            expect(sim.get_by_label('Período de vendas',exact=True)).to_have_value('90')
            with page.expect_response(lambda response: '/order-cycle/recommendations' in response.url and 'days=30' in response.url):
                sim.get_by_label('Período de vendas',exact=True).select_option('30')
            expect(sim.get_by_role('button',name='Adicionar à encomenda (1)',exact=True)).to_be_enabled()
            sim.get_by_role('button',name='Adicionar à encomenda (1)',exact=True).click()
            expect(sim.get_by_role('group',name='Produto de compra 2',exact=True).get_by_label('Descrição',exact=True)).to_have_value('Chá de teste')
            sim.screenshot(path=str(ROOT/f'.local/customer-orders-replenishment-{width}.png'))
            sim.get_by_role('button',name='Fechar',exact=True).click()
            page.get_by_role('alertdialog',name='Descartar alterações?',exact=True).get_by_role('button',name='Descartar alterações',exact=True).click()
            expect(sim).to_be_hidden()
            page.get_by_role('button',name='Nova simulação',exact=True).click()
            sim=page.get_by_role('dialog',name='Simulador de compra',exact=True)
            sim.get_by_role('combobox',name='Fornecedor',exact=True).fill('forn')
            sim.get_by_role('option',name='Fornecedor UI').click()
            sim.locator('details > summary').filter(has_text='Pedidos pendentes deste fornecedor').click()
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
            sims_table=page.get_by_role('table',name='simulações de fornecedores',exact=True)
            sims_table.get_by_role('button',name='Abrir',exact=True).click()
            expect(page.get_by_role('dialog',name='Detalhe da simulação')).to_contain_text('Cliente UI')
            page.get_by_role('dialog',name='Detalhe da simulação').get_by_role('button',name='Fechar',exact=True).click()
            # Continue from an already prepared supplier order (synthetic server state).
            simulations[0].update(procurement_id='procurement',procurement={'status':'confirmed','document':'ENC/UI','needs_resend':True,
                'lines':[{'received':'0','invoiced':'0'}],'email_history':[{'id':'e1','status':'accepted','created_at':'2026-10-21T12:00:00Z'}]},status='prepared')
            page.get_by_role('button',name='Atualizar',exact=True).click()
            # Encomenda emitida: edita-se na janela da emissão; alterada depois do envio = reenviar.
            expect(sims_table).to_contain_text('Alterada: reenviar ao fornecedor')
            sims_table.get_by_role('button',name='Editar ENC/UI de Fornecedor UI',exact=True).click()
            issued=page.get_by_role('dialog',name='Simulador de compra',exact=True)
            expect(issued.get_by_role('heading',name='Editar encomenda ENC/UI',exact=True)).to_be_visible()
            expect(issued).to_contain_text('fica marcada para reenviar')
            expect(issued.get_by_role('button',name='Guardar alterações à encomenda',exact=True)).to_be_visible()
            issued.get_by_role('button',name='Fechar',exact=True).click()
            expect(issued).to_be_hidden()
            simulations[0]['procurement']['lines']=[{'received':'2','invoiced':'0'}]
            page.get_by_role('button',name='Atualizar',exact=True).click()
            expect(sims_table.get_by_role('button',name='Editar ENC/UI de Fornecedor UI',exact=True)).to_be_disabled()
            simulations[0]['procurement']['lines']=[{'received':'0','invoiced':'0'}]
            navigation.get_by_role('button',name='Receção e conferência',exact=True).click()
            expect(page.get_by_role('heading',name='Conferência com o ERP',exact=True)).to_be_visible()
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
            assert set(writes)=={'/customer-orders','/customer-orders/order','/customer-orders/simulations/preview','/customer-orders/simulations','/order-cycle/reviews','/order-cycle/reviews/review/approve'},writes
            print(f'PASS {width}px: six areas without duplicates, products, grouping, minimum simulation, original archive link and three-way approval; no stock, ERP or email writes',flush=True)
            context.close()
        browser.close()


if __name__=='__main__':run()
