"""Global search and existing detail views, entirely intercepted synthetic APIs."""
import base64
import copy
import os
import re
import runpy
import time
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

from playwright.sync_api import expect, sync_playwright

ROOT=Path(__file__).resolve().parents[1]
fixtures=runpy.run_path(str(ROOT/"scripts/test-order-center-board-ui.py"),run_name="fixture")
inv_path=ROOT/"scripts/test-inventory-ui.py"
inventory={"__file__":str(inv_path)}
exec(inv_path.read_text(encoding="utf-8").split("with sync_playwright() as p:")[0],inventory)
OUT=ROOT/".local/global-search"
OUT.mkdir(parents=True,exist_ok=True)
PNG=base64.b64decode("iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+jWZkAAAAASUVORK5CYII=")


def run():
    with sync_playwright() as p:
        browser=p.chromium.launch()
        for width in [int(v) for v in os.environ.get("GPGEST_TEST_WIDTHS","1536,1011,768,390").split(",")]:
            for theme in ["current","dark"]:
                boot,orders,supply,recommendations,receipt,simulation=fixtures["scenario"]()
                calls,writes,errors,held=[],[],[],[]
                fail_alerts=[width==1536 and theme=="current"]
                doc={"id":"doc-search","original_name":"Documento de teste.png","document_number":"FT TESTE/1","entity_name":"Entidade fictícia","origin":"upload","origin_label":"Teste","type_label":"Documento","doc_type":"outro","size":100,"mime":"image/png","received_at":"2026-11-01T10:00:00Z","store_id":boot["stores"][0]["id"],"review_status":"por_rever","processing_status":"processado","links":[],"events":[],"corrections":[],"jobs":[],"warnings":[],"extraction":{},"confidence":{}}
                def entity_info(kind):
                    return {"key":kind,"title":"Clientes" if kind=="customers" else "Fornecedores","singular":"cliente" if kind=="customers" else "fornecedor","feminine":False,"description":"","description_field":"name","code_field":None,"has_active":True,"fields":[{"name":"name","label":"Nome","type":"text","column":True,"required":True,"options":[]}],"extra_columns":[],"permissions":{k:True for k in ["view","create","edit","status","remove","import","export","erp_remove"]},"erp":{"integrated":False,"operations":{}}}
                def search_results(q):
                    items=[("articles","Artigos","p1","Suplemento de teste","P001","Artigos e validades",{"area.stock":"Artigos","pesquisa.artigos":"P001"},"article"),("customers","Clientes","local:customer","Cliente de teste","999000111","Clientes",{},"customers"),("suppliers","Fornecedores","local:supplier","Fornecedor de teste","999000222","Fornecedores",{},"suppliers"),("orders","Encomendas de clientes",orders[0]["id"],orders[0]["number"],orders[0]["customer_name"],"Encomendas de clientes",{"area.pedidos-clientes":"pedidos","filtro.pedidos":"todos"},"order"),("documents","Documentos do arquivo",doc["id"],doc["document_number"],doc["entity_name"],"Todos os documentos",{},"document")]
                    if q in ["primeira","segunda"]:
                        items=[("articles","Artigos","p1",q,"P001","Artigos e validades",{"area.stock":"Artigos"},"article")]
                    return {"read_only":True,"query":q,"groups":[{"id":kind,"label":label,"more":False,"items":[{"id":id,"label":name,"detail":detail,"target":{"page":page,"params":{"consulta.id":id,"consulta.tipo":record_kind,**params}}}]} for kind,label,id,name,detail,page,params,record_kind in items]}
                def intercept(route):
                    path=urlsplit(route.request.url).path.removeprefix("/api")
                    query=parse_qs(urlsplit(route.request.url).query)
                    calls.append((path,query))
                    if route.request.method!="GET":
                        writes.append(path);return route.fulfill(status=500,json={"detail":"Escrita inesperada no teste"})
                    if path=="/workspace/search":
                        text=query.get("q",[""])[0]
                        if text=="erro":return route.fulfill(status=503,json={"detail":"Pesquisa indisponível no teste"})
                        if text=="primeira":held.append(route);return
                        return route.fulfill(json=search_results(text))
                    if path=="/alerts":
                        if fail_alerts[0]:return route.fulfill(status=503,json={"detail":"Avisos indisponíveis no teste"})
                        return route.fulfill(json={"total":2,"groups":[{"id":"test-alert","label":"Pedidos de teste por rever","count":2,"tone":"warning","page":"Encomendas de clientes","params":{"area.pedidos-clientes":"pedidos"},"items":["Pedido de teste"]}]})
                    if path.startswith("/tables/customers") or path.startswith("/tables/suppliers"):
                        kind=path.split("/")[2]
                        details={"id":"local:customer" if kind=="customers" else "local:supplier","active":True,"origin":"local","values":{"name":"Cliente de teste" if kind=="customers" else "Fornecedor de teste"},"labels":{},"dependencies":[],"managed_fields":[],"sync_state":"apenas_local","sync_label":"Apenas local"}
                        if path.endswith("/details"):return route.fulfill(json=details)
                        if path.endswith("/records"):return route.fulfill(json={"rows":[details],"total":1,"total_all":1,"page":1,"pages":1,"size":25})
                        return route.fulfill(json=entity_info(kind))
                    if path=="/stock/catalog" and query.get("record_id"):
                        return route.fulfill(json={"articles":[row for row in inventory["catalog"]["articles"] if row["id"]==query["record_id"][0]],"page":1})
                    if path.startswith("/stock/") or path.startswith("/market-prices"):
                        return inventory["mock"](route)
                    responses={"/bootstrap":boot,"/customer-orders":{"orders":orders,"simulations":[simulation],"can_buy":True,"can_write":True},"/customer-orders/catalog":{"customers":[],"suppliers":[]},"/customer-orders/history":[],"/procurement":{"documents":[receipt]},"/order-cycle/stock":{"items":[]},"/order-cycle/analytics":fixtures["base"]["analytics"],"/order-cycle/supply-center":supply,"/order-cycle/recommendations":{"items":recommendations},"/archive/overview":{"can_manage":True,"can_upload":False,"warnings":[],"origins":[],"types":[]},"/archive/documents":{"documents":[doc],"total":1},"/archive/documents/doc-search":doc}
                    if path=="/archive/documents/doc-search/file":return route.fulfill(body=PNG,content_type="image/png")
                    if path in responses:return route.fulfill(json=responses[path])
                    return fixtures["ns"]["intercept"](route)
                context=browser.new_context(viewport={"width":width,"height":1000},locale="pt-PT")
                context.add_init_script(f"localStorage.setItem('iazela-theme','{theme}')")
                page=context.new_page();page.route("**/api/**",intercept)
                page.on("pageerror",lambda error:(errors.append(str(error)),print("UIERROR",error.stack or str(error),flush=True)))
                page.goto("http://127.0.0.1:5174/entrar?pagina=Dashboard")
                search=page.get_by_role("combobox",name="Pesquisa global",exact=True)
                expect(search).to_be_visible(timeout=30000)
                if fail_alerts[0]:
                    unknown=page.get_by_label("Avisos: indisponíveis",exact=True)
                    expect(unknown).to_be_visible(timeout=20000)
                    unknown.click()
                    expect(page.get_by_role("region",name="Avisos pendentes")).not_to_contain_text("Sem avisos pendentes")
                    fail_alerts[0]=False
                    page.get_by_role("button",name="Atualizar avisos",exact=True).click()
                    expect(page.get_by_label("Avisos: 2",exact=True)).to_be_visible()
                    page.get_by_label("Avisos: 2",exact=True).click()
                search.fill("suplemento")
                results=page.get_by_role("listbox",name="Resultados da pesquisa global",exact=True)
                expect(results.get_by_role("group")).to_have_count(5)
                input_box=search.bounding_box()
                for selector in [".header-tools", ".header-context"]:
                    other=page.locator(selector)
                    if other.is_visible():
                        rect=other.bounding_box()
                        assert not (input_box["x"]<rect["x"]+rect["width"] and rect["x"]<input_box["x"]+input_box["width"] and input_box["y"]<rect["y"]+rect["height"] and rect["y"]<input_box["y"]+input_box["height"]), (width,theme,selector,input_box,rect)
                box=page.locator(".workspace-search-panel").bounding_box()
                assert box["x"]>=0 and box["x"]+box["width"]<=width,box
                page.screenshot(path=str(OUT/f"search-{width}-{theme}.png"))
                results.get_by_role("group",name="Artigos",exact=True).get_by_role("option").click()
                dialog=page.get_by_role("dialog").last
                expect(dialog).to_be_visible(timeout=20000)
                expect(dialog.get_by_label("Designação",exact=True)).to_have_value("Suplemento de teste")
                expect(dialog.get_by_role("button",name="Guardar",exact=True)).to_have_count(0)
                page.keyboard.press("Control+k")
                expect(search).not_to_be_focused()
                expect(dialog).to_be_visible()
                dialog.get_by_role("button",name="Fechar",exact=True).first.click()
                expect(dialog).to_have_count(0)
                if width==1536 and theme=="current":
                    first_key=parse_qs(urlsplit(page.url).query)["consulta.chave"]
                    search.fill("suplemento")
                    results.get_by_role("group",name="Artigos",exact=True).get_by_role("option").click()
                    expect(page.get_by_role("dialog").last.get_by_label("Designação",exact=True)).to_have_value("Suplemento de teste")
                    assert parse_qs(urlsplit(page.url).query)["consulta.chave"]!=first_key
                    page.get_by_role("dialog").last.get_by_role("button",name="Fechar",exact=True).first.click()
                    page.reload()
                    expect(search).to_be_visible(timeout=30000)
                    expect(page.get_by_role("dialog").last.get_by_label("Designação",exact=True)).to_have_value("Suplemento de teste",timeout=20000)
                    page.get_by_role("dialog").last.get_by_role("button",name="Fechar",exact=True).first.click()
                for group_name,kind in [("Clientes","customers"),("Fornecedores","suppliers"),("Encomendas de clientes","order"),("Documentos do arquivo","document")]:
                    search.fill("suplemento")
                    expect(results.get_by_role("group",name=group_name,exact=True)).to_be_visible()
                    results.get_by_role("group",name=group_name,exact=True).get_by_role("option").click()
                    dialog_name={"customers":"Detalhes do cliente","suppliers":"Detalhes do fornecedor","order":"Pedido "+orders[0]["number"],"document":"Documento "+doc["original_name"]}[kind]
                    target_dialog=page.get_by_role("dialog",name=dialog_name,exact=True)
                    expect(target_dialog).to_be_visible(timeout=20000)
                    assert parse_qs(urlsplit(page.url).query)["consulta.tipo"]==[kind]
                    target_dialog.get_by_role("button",name="Fechar",exact=True).first.click()
                    expect(target_dialog).to_have_count(0)
                with page.expect_request(lambda request: urlsplit(request.url).path=="/api/workspace/search" and parse_qs(urlsplit(request.url).query).get("q")==["primeira"], timeout=20000):
                    search.fill("primeira")
                deadline=time.monotonic()+5
                while not held and time.monotonic()<deadline:
                    page.wait_for_timeout(50)
                assert held, "The first request must be pending before the second query"
                search.fill("segunda")
                expect(results.get_by_role("option",name="segunda",exact=False)).to_be_visible()
                for route in held:route.fulfill(json=search_results("primeira"))
                expect(results.get_by_role("option",name="primeira",exact=False)).to_have_count(0)
                search.fill("erro")
                expect(page.locator(".workspace-search-error")).to_contain_text("Pesquisa indisponível no teste")
                search.fill("Necessidades de compra")
                expect(results.get_by_role("group",name="Menus",exact=True)).to_be_visible()
                menu=results.get_by_role("group",name="Menus",exact=True).get_by_role("option",name="Necessidades de compra",exact=False)
                menu.click()
                expect(page.get_by_role("heading",name="Centro de Encomendas",exact=True)).to_be_visible(timeout=20000)
                expect(page.get_by_role("searchbox",name="Pesquisar no centro de encomendas",exact=True)).to_be_visible()
                search.focus();search.press("Escape")
                bell=page.get_by_label("Avisos: 2",exact=True);expect(bell).to_be_visible();bell.click()
                fail_alerts[0]=True
                page.get_by_role("button",name="Atualizar avisos",exact=True).click()
                expect(page.get_by_role("region",name="Avisos pendentes")).to_contain_text("última consulta")
                expect(page.get_by_role("region",name="Avisos pendentes")).not_to_contain_text("Sem avisos pendentes")
                assert not writes,writes
                assert not errors,errors
                assert not page.evaluate("document.documentElement.scrollWidth>innerWidth"),(width,theme)
                print(f"PASS {width}px {theme}: all search categories, existing detail views, stale responses discarded, failure states, menu navigation, local filter retained, alert failures preserve scoped prior data; no writes/errors/overflow",flush=True)
                context.close()
        browser.close()


if __name__=="__main__":run()
