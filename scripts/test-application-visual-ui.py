"""Global appearance smoke checks using isolated API fixtures only."""
from pathlib import Path
from urllib.parse import quote
from playwright.sync_api import expect, sync_playwright
ROOT=Path(__file__).resolve().parents[1]
fixture=ROOT/'scripts/test-visual-template-ui.py'
ns={'__file__':str(fixture)}
exec(fixture.read_text(encoding='utf-8').split('with sync_playwright() as playwright:')[0],ns)
OUT=ROOT/'.local/application-visual';OUT.mkdir(exist_ok=True)
audit=(ROOT/'scripts/contrast-audit.js').read_text(encoding='utf-8')
with sync_playwright() as p:
    browser=p.chromium.launch()
    for theme in ['current','dark']:
        for width in [1440,390]:
            page=browser.new_page(viewport={'width':width,'height':900})
            page.set_default_timeout(15000)
            errors=[];writes=[]
            page.on('pageerror',lambda e:errors.append(str(e)))
            page.add_init_script(f"localStorage.setItem('iazela-theme','{theme}')")
            def intercept(route):
                if route.request.method!='GET':writes.append(route.request.url)
                return ns['intercept'](route)
            page.route('**/api/**',intercept)
            for index,destination in enumerate(['Dashboard','Clientes','Conferência documental','Múltiplas contas']):
                page.goto('http://127.0.0.1:5174/?pagina='+quote(destination))
                expect(page.locator('.workspace')).to_be_visible()
                expect(page.locator('.header-page-context')).to_contain_text('Visão geral' if destination=='Dashboard' else destination)
                expect(page.locator('#main-workspace').get_by_role('heading').first).to_be_visible()
                if width==390:page.get_by_role('button',name='Abrir menu',exact=True).click()
                selected=page.locator('.sidebar nav button.active').first
                expect(selected).to_be_visible()
                colors=selected.evaluate('(e)=>({background:getComputedStyle(e).backgroundColor,image:getComputedStyle(e).backgroundImage,ink:getComputedStyle(e).color})')
                assert colors['image']=='none',colors
                assert colors['background']==('rgb(155, 233, 222)' if theme=='current' else 'rgb(112, 227, 211)'),colors
                if width==390:
                    page.keyboard.press('Escape')
                    expect(page.locator('.sidebar')).not_to_have_class('sidebar visible')
                assert page.evaluate('document.documentElement.scrollWidth <= innerWidth'),(theme,width,destination)
                result=page.evaluate(audit)
                assert not result.get('failures'),(theme,width,destination,result.get('failures'))
                page.screenshot(path=str(OUT/f'{theme}-{width}-{index}.png'))
            assert not errors,errors
            assert not writes,writes
            page.close()
            print(f'PASS {theme} {width}: navigation, pages, contrast, responsive; no writes.',flush=True)
    browser.close()
