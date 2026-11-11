"""Task menu regression: isolated fixture APIs, no orders or messages are created."""
from pathlib import Path
import re
from urllib.parse import quote,urlsplit
from playwright.sync_api import sync_playwright,expect
ROOT=Path(__file__).resolve().parents[1]
fixture=ROOT/'scripts/test-order-center-board-ui.py'
ns={'__file__':str(fixture)}
exec(fixture.read_text(encoding='utf-8').split('def run():')[0],ns)
OUT=ROOT/'.local/navigation-reorganization';OUT.mkdir(exist_ok=True)
with sync_playwright() as p:
    expect.set_options(timeout=20000)
    browser=p.chromium.launch()
    for width in [1440,390]:
        boot,orders,supply,recommendations,receipt,simulation=ns['scenario']()
        store=boot['stores'][0]['id']; errors=[];writes=[]
        page=browser.new_page(viewport={'width':width,'height':1000})
        page.on('pageerror',lambda e:errors.append(str(e)))
        def intercept(route):
            path=urlsplit(route.request.url).path.removeprefix('/api')
            if route.request.method!='GET':
                writes.append(path);return route.fulfill(status=400,json={'detail':'No writes expected in this test'})
            responses={
                '/bootstrap':boot,
                '/customer-orders':{'orders':orders,'simulations':[simulation],'can_write':True,'can_buy':True},
                '/customer-orders/catalog':{'customers':[],'suppliers':[{'id':s['supplier_id'],'name':s['name'],'terms':s['terms']} for s in supply['suppliers']]},
                '/stock/catalog':{'articles':[{'id':a['id'],'code':a['code'],'name':a['description']} for a in supply['articles']],'warehouses':[]},
                '/customer-orders/history':[], '/order-cycle/recommendations':{'items':[]},
                '/order-cycle/supply-center':supply,'/procurement':{'documents':[],'can_write':True},
                '/archive/overview':{'can_manage':False,'types':{},'origin_labels':{}},
                '/archive/documents':{'documents':[],'total':0},
                '/archive/pending':{'items':[],'groups':[]},
                '/archive/links':[],
            }
            if path in responses:return route.fulfill(json=responses[path])
            return ns['ns']['intercept'](route)
        page.route('**/api/**',intercept)
        page.goto('http://127.0.0.1:5174/?pagina='+quote('Encomendas de clientes')+'&area.pedidos-clientes=pedidos')
        def menu(group,item):
            if width==390:page.get_by_role('button',name='Abrir menu',exact=True).click()
            nav=page.get_by_role('navigation',name='Navegação principal',exact=True)
            opener=nav.get_by_role('button',name=group,exact=True)
            if opener.get_attribute('aria-expanded')!='true':opener.click()
            nav.get_by_role('button',name=item,exact=True).click()
        menu('Encomendas','Pedidos de clientes')
        expect(page.get_by_role('table',name='pedidos de clientes',exact=True)).to_be_visible()
        expect(page.locator('.supply-center')).to_have_count(0)
        menu('Encomendas','Visão geral')
        expect(page.locator('.supply-center')).to_be_visible()
        expect(page.locator('.supply-center > details.ocb-advanced')).not_to_have_attribute('open','')
        menu('Encomendas','Planeamento de compras')
        planning=page.get_by_role('navigation',name='Planeamento de compras',exact=True)
        expect(planning).to_be_visible()
        expect(page.locator('.supply-center > details.ocb-advanced')).to_have_attribute('open','')
        expect(page.get_by_role('heading',name='Necessidades de compra',exact=True)).to_be_visible()
        expect(page.locator('.supply-quick-filters').get_by_role('button',name='Por comprar',exact=True)).to_have_attribute('aria-pressed','true')
        page.screenshot(path=str(OUT/f'needs-{width}.png'))
        planning.get_by_role('button',name='Simulações',exact=True).click()
        expect(page.get_by_role('button',name='Nova simulação',exact=True)).to_be_visible()
        expect(page.locator('.supply-center')).to_have_count(0)
        page.go_back()
        expect(planning.get_by_role('button',name='Necessidades',exact=True)).to_have_attribute('aria-current','page')
        menu('Encomendas','Levantamentos e contactos')
        expect(page.get_by_role('group',name='Filtrar pedidos',exact=True).get_by_role('button',name='Disponíveis para levantar (8)',exact=True)).to_have_attribute('aria-pressed','true')
        menu('Compras','Receções de mercadoria')
        expect(page.locator('.header-page-context')).to_contain_text('Compras / Receções de mercadoria')
        menu('Entidades','Fornecedores')
        page.get_by_role('button',name='Condições comerciais',exact=True).click()
        expect(page.get_by_role('button',name='Gerir fornecedores',exact=True)).to_be_visible()
        expect(page.locator('.header-page-context')).to_contain_text('Entidades / Condições comerciais dos fornecedores')
        menu('Contabilidade','Arquivo documental')
        archive=page.get_by_role('navigation',name='Arquivo documental',exact=True)
        expect(archive).to_be_visible()
        archive.get_by_role('button',name='Caixa de entrada',exact=True).click()
        expect(archive.get_by_role('button',name='Caixa de entrada',exact=True)).to_have_attribute('aria-current','page')
        expect(page.locator('.header-page-context')).to_contain_text('Contabilidade / Caixa de entrada')
        page.go_back()
        expect(archive.get_by_role('button',name='Todos os documentos',exact=True)).to_have_attribute('aria-current','page')
        search=page.get_by_role('combobox',name='Pesquisa global',exact=True)
        search.fill('Caixa de entrada')
        page.get_by_role('option',name=re.compile('Caixa de entrada')).first.click()
        expect(archive.get_by_role('button',name='Caixa de entrada',exact=True)).to_have_attribute('aria-current','page')
        assert page.evaluate('document.documentElement.scrollWidth<=innerWidth')
        assert not writes,writes
        assert not errors,errors
        page.screenshot(path=str(OUT/f'archive-{width}.png'))
        page.goto('http://127.0.0.1:5174/?pagina='+quote('Encomendas de clientes')+'&area.pedidos-clientes=planeamento&area.planeamento-compras=necessidades&filtro.abastecimento=buy')
        expect(page.get_by_label('Loja do planeamento',exact=True)).to_have_value(store)
        expect(page.get_by_label('Filtrar loja',exact=True)).to_have_count(0)
        expect(page.get_by_role('button',name='Atualizar',exact=True)).to_have_count(1)
        page.close()
        print(f'PASS {width}: distinct tasks, planning tabs, back navigation, supplier conditions, archive views; no writes.',flush=True)
    browser.close()
