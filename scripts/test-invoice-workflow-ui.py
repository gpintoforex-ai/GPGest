"""Fictitious browser responses only. No cloud or ERP writes."""
from pathlib import Path
from urllib.parse import urlsplit
from playwright.sync_api import expect,sync_playwright
exec(Path(__file__).with_name("test-visual-template-ui.py").read_text(encoding="utf-8").split("\nwith sync_playwright() as playwright:")[0])
workflow_rows=[{**item,"complete":index==0,"attachment_state":"missing" if index==1 else "validated","drive_state":"checked","erp_state":"not_found" if index==2 else "linked","attachments":[] if index==1 else [{"key":"drive:demo-"+str(index),"name":"demo.pdf","url":"/api/demo.pdf"}],"erp_candidates":[],"drive_candidates":[],"messages":[],"supplier":{"state":"not_found"},"proposal":{},"proposal_reviewed_at":None} for index,item in enumerate(documents[:3])]
verified=False
def workflow_intercept(route):
    global verified
    path=urlsplit(route.request.url).path.removeprefix("/api")
    if path=="/invoice-workflow":
        value={"rows":workflow_rows,"summary":{"completed":1,"pending":2,"total":3},"creation_available":False}
    elif path=="/invoice-workflow/verify":
        verified=True;value={"checked":3,"erp":"checked","drive":"checked"}
    elif path.endswith("/proposal") and path.startswith("/invoice-workflow/"):
        if route.request.method=="POST":
            body=route.request.post_data_json
            assert body["confirmed"] and body["address"]=="" and body["lines"]==[]
            value={"message":"Proposta revista; não foi criado um lançamento no ERP.","creation_available":False}
        else:
            row=workflow_rows[2]
            value={"fields":{"supplier_name":row["supplier_name"],"supplier_nif":row["supplier_nif"],"document":row["document"],"document_type":row["document_type"],"document_date":row["document_date"],"amount_cents":row["amount_cents"],"currency":"EUR","lines":[],"address":"","email":"","notes":""},"supplier_action":"propose_creation","creation_available":False,"missing_fields":["Contrato de criação não implementado"],"message":"Guardar a proposta não cria fornecedores ou documentos no ERP."}
    elif path.endswith("/history") and path.startswith("/invoice-workflow/"): value=[]
    else: return intercept(route)
    route.fulfill(content_type="application/json",body=json.dumps(value))

with sync_playwright() as playwright:
    browser=playwright.chromium.launch()
    page=browser.new_page(viewport={"width":1440,"height":1000},locale="pt-PT")
    page.route("**/api/**",workflow_intercept)
    page.goto("http://127.0.0.1:5174")
    page.get_by_role("button",name="Contabilidade",exact=True).click()
    page.get_by_role("button",name="Conferência documental",exact=True).click()
    page.get_by_role("button",name="Clara · Verificar documentos",exact=True).click()
    page.get_by_role("button",name="Continuar com documentos existentes",exact=True).click()
    flow=page.get_by_role("region",name="Verificação de faturas e-Fatura",exact=True)
    expect(flow.get_by_text("1 concluídas",exact=True)).to_be_visible()
    expect(flow.locator("tbody tr")).to_have_count(3)
    flow.get_by_role("button",name="Anexos em falta",exact=True).click()
    expect(flow.locator("tbody tr")).to_have_count(1)
    expect(flow.get_by_text("Lançado",exact=True)).to_be_visible()
    expect(flow.get_by_text("Em falta",exact=True)).to_be_visible()
    flow.get_by_role("button",name="Por lançar no ERP",exact=True).click()
    flow.get_by_role("button",name="Rever proposta de lançamento",exact=True).click()
    flow.get_by_role("button",name="Rever proposta para o ERP",exact=True).click()
    expect(flow.get_by_role("heading",name="Revisão conjunta · fornecedor e fatura",exact=True)).to_be_visible()
    expect(flow.get_by_text("Fornecedor não encontrado:",exact=False)).to_be_visible()
    expect(flow.get_by_label("Morada, se conhecida",exact=True)).to_have_value("")
    expect(flow.get_by_role("button",name="Guardar proposta revista",exact=True)).to_be_disabled()
    flow.get_by_role("checkbox",name="Revi fornecedor e fatura.",exact=False).check()
    flow.get_by_role("button",name="Guardar proposta revista",exact=True).click()
    expect(flow.get_by_role("link",name="Exportar proposta revista",exact=True)).to_be_visible()
    flow.get_by_role("button",name="Fechar revisão",exact=True).click()
    flow.get_by_role("button",name="Verificar faturas",exact=True).click()
    expect(flow.get_by_text("3 faturas verificadas.",exact=False)).to_be_visible()
    assert verified
    page.set_viewport_size({"width":390,"height":844})
    expect(flow.get_by_role("button",name="Todas",exact=True)).to_be_visible()
    browser.close()
print("PASS: resumo, estados independentes, filtros, proposta conjunta sem dados inventados, confirmação e layout móvel emulado")
