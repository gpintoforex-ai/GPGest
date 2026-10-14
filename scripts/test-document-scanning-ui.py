"""Mobile browser simulation; fictitious documents and cloud responses only."""
from pathlib import Path
from io import BytesIO
from urllib.parse import urlsplit
from PIL import Image
from playwright.sync_api import expect, sync_playwright
expect.set_options(timeout=20000)
exec(Path(__file__).with_name("test-visual-template-ui.py").read_text(encoding="utf-8").split("\nwith sync_playwright() as playwright:")[0])

image = BytesIO()
Image.new("RGB", (1000, 1400), "white").save(image, "JPEG")
job = {"id": "demo-scan", "status": "pdf_generated", "name": "DEMONSTRACAO.pdf", "pages": 2, "size": len(pdf_buffer.getvalue()), "view_url": "/api/scans/demo-scan/pdf", "validation_status": "por_validar", "warnings": [], "cloud_id": None}
calls = {"prepare": 0, "upload": 0, "preview": 0}
cloud_configured = True

def scan_intercept(route):
    path = urlsplit(route.request.url).path.removeprefix("/api")
    value = None
    if path == "/scans/setup":
        value = {"company": boot["company"], "account": "demo@example.test", "connected": cloud_configured, "folder_configured": cloud_configured, "settings": {"max_pages": 20, "max_bytes": 5*1024*1024, "organisation": "company_period_type"}, "stores": boot["stores"], "categories": [], "documents": documents, "recent": []}
    elif path == "/entities": value = []
    elif path == "/scans/read-qr": value = {"status":"found","documents":[{"nif":"500000000","document_type":"FT","document_date":"2026-10-01","document":"FT DEMO/1","amount_cents":12300,"page":1}]}
    elif path == "/scans/image-preview":
        calls["preview"] += 1
        route.fulfill(content_type="image/jpeg", body=image.getvalue()); return
    elif path == "/scans/prepare":
        calls["prepare"] += 1
        body = route.request.post_data_buffer
        assert b'"purchase_id":"0"' in body
        if calls["prepare"] == 1: assert b'"rotation":90' in body
        else: assert b'"edits":[]' in body and b'existing.pdf' in body
        job["status"] = "pdf_generated"
        job["purchase_id"] = "0"
        job["movement_id"] = None
        job["metadata"] = {"store_id": "demo", "purchase_id": "0", "amount_cents":12300}
        value = job.copy()
    elif path.endswith("/pdf") and path.startswith("/scans/"):
        route.fulfill(content_type="application/pdf", body=pdf_buffer.getvalue()); return
    elif path == "/scans/demo-scan/upload":
        calls["upload"] += 1
        job["cloud_id"] = "fictitious-cloud-id"
        if calls["upload"] == 1:
            job["status"] = "upload_error"
            route.fulfill(status=502, content_type="application/json", body=json.dumps({"detail": "Associação fictícia interrompida; repetir conserva o ficheiro."})); return
        job["status"] = "cloud_saved"; value = job.copy()
    elif path == "/scans/demo-scan": value = job.copy()
    elif path == "/scans/demo-scan/validate":
        job["validation_status"] = "validado"; value = job.copy()
    else: return intercept(route)
    route.fulfill(content_type="application/json", body=json.dumps(value))

with sync_playwright() as playwright:
    browser = playwright.chromium.launch()
    page = browser.new_page(viewport={"width": 1440, "height": 1000}, is_mobile=True, has_touch=True, locale="pt-PT")
    page.route("**/api/**", scan_intercept)
    page.on("dialog", lambda dialog: dialog.accept())
    page.goto("http://127.0.0.1:5174")
    page.get_by_role("button", name="Contabilidade", exact=True).click()
    page.get_by_role("button", name="Conferência documental", exact=True).click()
    page.set_viewport_size({"width":390,"height":844})
    page.get_by_role("main").get_by_role("button", name="Digitalizar documento", exact=True).click()
    dialog = page.get_by_role("dialog", name="Digitalizar documento", exact=True)
    expect(dialog.get_by_label("Fotografar página")).to_have_attribute("capture", "environment")
    dialog.get_by_label("Selecionar fotografias da galeria").set_input_files([
        {"name": "pagina1.jpg", "mimeType": "image/jpeg", "buffer": image.getvalue()},
        {"name": "pagina2.jpg", "mimeType": "image/jpeg", "buffer": image.getvalue()},
    ])
    expect(dialog.locator(".scanner-pages article")).to_have_count(2)
    dialog.get_by_role("button", name="Rodar", exact=True).first.click()
    expect(dialog.get_by_role("button", name="Rodar", exact=True).first).to_be_enabled()
    dialog.get_by_role("button", name="Descer página 1", exact=True).click()
    dialog.get_by_label("Associar a documento existente").select_option("0")
    expect(dialog.get_by_label("Número do documento", exact=True)).to_have_value("FT DEMO/1")
    dialog.get_by_role("button", name="Gerar e pré-visualizar PDF", exact=True).click()
    expect(dialog.get_by_role("link", name="Descarregar PDF", exact=True)).to_be_visible()
    save = dialog.get_by_role("button", name="Guardar na cloud", exact=True)
    expect(save).to_be_disabled()
    dialog.get_by_role("checkbox", name="Revi o PDF", exact=False).check()
    save.click()
    page.wait_for_function("document.querySelector('.scanner-dialog').innerText.includes('Erro no envio')")
    retry = dialog.get_by_role("button", name="Repetir envio / recuperar associação", exact=True)
    expect(retry).to_be_visible()
    expect(dialog.get_by_text("Guardado na cloud", exact=True)).to_have_count(0)
    retry.click()
    expect(dialog.get_by_text("Guardado na cloud", exact=True)).to_be_visible()
    expect(dialog.get_by_text("Anexo por validar", exact=True)).to_be_visible()
    dialog.get_by_role("button", name="Validar anexo após revisão", exact=True).click()
    expect(dialog.get_by_text("Anexo validado", exact=True)).to_be_visible()
    assert calls == {"prepare": 1, "upload": 2, "preview": 1}, calls
    dialog.get_by_role("button", name="Fechar digitalização", exact=True).click()
    cloud_configured = False
    page.get_by_role("main").get_by_role("button", name="Digitalizar documento", exact=True).click()
    dialog.get_by_label("Carregar PDF existente", exact=True).set_input_files({"name":"existing.pdf","mimeType":"application/pdf","buffer":pdf_buffer.getvalue()})
    expect(dialog.get_by_label("Número do documento", exact=True)).to_have_value("FT DEMO/1")
    expect(dialog.get_by_label("Data do documento", exact=True)).to_have_value("2026-10-01")
    expect(dialog.get_by_label("Fornecedor / entidade", exact=True)).to_have_value(documents[0]["supplier_name"])
    dialog.get_by_label("Associar a documento existente").select_option("0")
    dialog.get_by_role("button", name="Gerar e pré-visualizar PDF", exact=True).click()
    expect(dialog.get_by_role("link", name="Descarregar PDF", exact=True)).to_be_visible()
    dialog.get_by_role("checkbox", name="Revi o PDF", exact=False).check()
    expect(dialog.get_by_role("button", name="Guardar na cloud", exact=True)).to_be_disabled()
    expect(dialog.get_by_role("button", name="Configurar conta e pasta cloud", exact=True)).to_be_visible()
    assert calls["prepare"] == 2 and calls["upload"] == 2
    browser.close()
print("PASS: mobile emulado, galeria multipágina, rotação, ordem, associação, PDF, recuperação e validação separada; sem câmara ou cloud reais")
