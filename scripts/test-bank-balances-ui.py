"""Bank balances and missing-record preview with intercepted APIs; no real writes."""
import json
from pathlib import Path
from urllib.parse import urlsplit
from playwright.sync_api import sync_playwright,expect

root=Path(__file__).resolve().parents[1]
fixture=root/'scripts/test-visual-template-ui.py';ns={'__file__':str(fixture)}
exec(fixture.read_text(encoding='utf-8').split('with sync_playwright() as playwright:')[0],ns)
ns['boot']['accounts']=[{'id':'bank-a','store_id':'demo','name':'Conta teste','bank':'Banco Demo','iban_last4':'1234','currency':'EUR'}]
app_row={'id':'app','source':'movement','store_id':'demo','account_id':'bank-a','account':'Conta teste','currency':'EUR','date':'2026-01-31',
    'description':'Pagamento fornecedor','reference':'FT TESTE/1','entity':'Fornecedor','payment':'transferencia','amount_cents':-20000,'associated_cents':0,'available_cents':20000,'remaining_cents':20000,'status':'pendente','origin':'Aplicação','created_at':'2026-01-31T12:00:00Z'}
bank_row={**app_row,'source':'bank','id':'bank','description':'Débito banco'}
summary={'account_id':'bank-a','store_id':'demo','name':'Conta teste','bank':'Banco Demo','currency':'EUR','as_of':'2026-01-31',
    'book_balance_cents':130000,'book_origin':'Saldo pelos registos da aplicação','book_date':'2026-01-31','opening_cents':100000,'opening_date':'2026-01-01',
    'opening_note':'Saldo inicial antes dos movimentos desse dia.','book_incoming_cents':50000,'book_outgoing_cents':20000,'bank_balance_cents':125000,'bank_date':'2026-01-31',
    'bank_age_days':0,'bank_origin':'Último saldo do extrato importado','comparable':True,'difference_cents':5000,'comparison_reason':'Mesma conta, moeda e data',
    'pending_application':{'count':1,'incoming_cents':0,'outgoing_cents':20000,'absolute_cents':20000},'pending_bank':{'count':1,'incoming_cents':0,'outgoing_cents':20000,'absolute_cents':20000},
    'reconciled':{'incoming_cents':50000,'outgoing_cents':0,'net_cents':50000,'basis':'Valores confirmados no extrato. Não é saldo disponível.'},'application_rows':[app_row],'bank_rows':[bank_row]}
posts=[]
def intercept(route):
    path=urlsplit(route.request.url).path.removeprefix('/api')
    if path=='/bank-balances':result={'accounts':[summary],'mode':{'mode':'local'},'warnings':[]}
    elif path=='/reconciliation':result={'banks':[bank_row],'internals':[app_row],'suggestions':[],'card_suggestions':[],'groups':[]}
    elif path=='/purchases':result=[]
    elif path=='/bank-transactions/bank/resolution-preview':
        posts.append(path);result={**route.request.post_data_json,'preview_token':'a'*64,'amount_cents':-20000,'currency':'EUR','date':'2026-01-31','candidates':[app_row],'effect':'Pré-visualização apenas; nenhum registo criado.'}
    else:return ns['intercept'](route)
    route.fulfill(content_type='application/json',body=json.dumps(result))

with sync_playwright() as p:
    browser=p.chromium.launch()
    for theme,width in [('current',1366),('dark',1366),('dark',390)]:
        page=browser.new_page(viewport={'width':width,'height':900})
        errors=[];page.on('pageerror',lambda e:errors.append(str(e)))
        page.add_init_script(f"localStorage.setItem('iazela-theme','{theme}')")
        page.route('**/api/**',intercept);page.goto('http://127.0.0.1:5174/')
        page.get_by_role('button',name='Bancos',exact=True,include_hidden=True).first.evaluate('(e)=>e.click()')
        page.get_by_role('button',name='Múltiplas contas',exact=True,include_hidden=True).evaluate('(e)=>e.click()')
        expect(page.get_by_role('heading',name='Saldo contabilístico',exact=True)).to_be_visible()
        difference=page.locator('.bank-balance-card').filter(has=page.get_by_role('heading',name='Diferença entre saldos',exact=True))
        expect(difference).to_contain_text('50,00')
        expect(page.get_by_role('heading',name='Saldo bancário',exact=True)).to_be_visible()
        assert not page.evaluate('document.documentElement.scrollWidth>innerWidth')
        report=page.evaluate((root/'scripts/contrast-audit.js').read_text(encoding='utf-8'));assert not report.get('failures'),report.get('failures')
        output=root/'.local/bank-balances-ui';output.mkdir(exist_ok=True)
        page.screenshot(path=str(output/f'summary-{theme}-{width}.png'),full_page=True)
        page.get_by_role('button',name='Reconciliar esta conta',exact=True).click()
        expect(page.get_by_label('Conta bancária',exact=True)).to_have_value('bank-a')
        page.get_by_role('button',name='Resolver movimento em falta',exact=True).click()
        dialog=page.get_by_role('dialog',name='Resolver movimento bancário',exact=True)
        expect(dialog).to_be_visible()
        dialog.get_by_role('button',name='Pré-visualizar e procurar duplicações',exact=True).click()
        expect(dialog.get_by_role('button',name='Aprovar registo e associação',exact=True)).to_be_disabled()
        dialog.get_by_role('button',name='Selecionar registo existente para associação',exact=True).click()
        expect(dialog).not_to_be_visible()
        expect(page.get_by_role('button',name='Confirmar reconciliação',exact=True)).to_be_enabled()
        expect(page.get_by_text('Diferença selecionada:',exact=False)).to_contain_text('0,00')
        page.wait_for_timeout(250)  # Audit the settled enabled button, after its colour transition.
        report=page.evaluate((root/'scripts/contrast-audit.js').read_text(encoding='utf-8'));assert not report.get('failures'),report.get('failures')
        assert not errors,errors
        page.close()
    browser.close()
assert len(posts)==3,posts
print('Bank cards, dated balances, difference, account filter, duplicate preview, existing selection, dark/light/mobile contrast: OK')
