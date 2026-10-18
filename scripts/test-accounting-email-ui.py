"""Accounting email UX using intercepted APIs only: no SMTP connections or real data."""

from copy import deepcopy
from pathlib import Path
from urllib.parse import urlsplit

from playwright.sync_api import expect, sync_playwright

boot = {
    "mode": "demonstracao", "company": {"id": "email-ui", "name": "Empresa fictícia"},
    "integration": {"mode": "local", "provider": "none", "active": False, "writes": False},
    "user": {"id": "ui", "name": "Utilizador fictício", "role": "administrador"},
    "stores": [{"id": "store-a", "name": "Loja A"}], "registers": [], "accounts": [],
    "categories": [], "users": [], "payment_methods": {},
}
config = {
    "revision": 1, "enabled": False, "host": "smtp.example.test", "port": 587,
    "security": "starttls", "username": "test", "has_password": True, "manual_only": True,
    "sender_name": "Empresa fictícia", "sender_email": "zela@example.test", "max_message_bytes": 20000000,
    "accountant_name": "Gabinete fictício", "accountant_email": "gabinete@example.test",
    "additional_to": [], "cc": [], "subject_template": "Documentação — {empresa} — {periodo}",
    "message_template": "Documentação do período {periodo}.", "signature": "Empresa fictícia",
    "periodicity": "mensal", "custom_period_note": "",
}
rows, writes, errors = [], [], []
fail_preview = True


def mock_api(route):
    global config, fail_preview
    path, method = urlsplit(route.request.url).path, route.request.method
    if method == "POST":
        body = route.request.post_data_json
        writes.append((path, deepcopy(body)))
        if path == "/api/settings/email":
            assert not body["password"]
            config = {**body, "has_password": True, "manual_only": True, "revision": body["revision"] + 1}
            config.pop("password", None)
            config.pop("clear_password", None)
            result = config
        elif path == "/api/accounting/email/prepare":
            row = {
                "id": "draft-ui", "status": "prepared", "revision": 1, "created_by": "ui",
                "created_at": "2026-10-17T10:00:00Z", "start": body["start"], "end": body["end"],
                "store_ids": body["store_ids"], "error": "", "uncertain": False, "parts": [],
                "draft": {"to": [config["accountant_email"]], "cc": [], "subject": "Documentação fictícia",
                          "message": "Exmos. Senhores, segue a documentação disponível.", "selected_keys": ["documentos/original.pdf"], "format": "zip", "allow_split": False},
                "manifest": {"company": boot["company"], "pending": [{"document": "FT/2", "reason": "Anexo em falta"}],
                             "files": [{"key": "documentos/original.pdf", "document": "FT/1", "supplier": "Fornecedor fictício", "nif": "500000001", "original_name": "original.pdf", "size_bytes": 1234, "sha256": "a" * 64}]},
            }
            rows.append(row)
            result = row
        elif path.endswith("/preview"):
            if fail_preview:
                fail_preview = False
                route.fulfill(status=422, json={"detail": "Reveja o endereço do destinatário."})
                return
            row = rows[0]
            row["draft"] = {k: v for k, v in body.items() if k != "revision"}
            row["revision"] += 1
            row["manifest"]["plan"] = {"messages": 1, "total_bytes": 4567, "limit_bytes": 20000000, "requires_split_confirmation": False}
            result = row
        elif path.endswith("/send"):
            assert body["confirmed"] is True and body["revision"] == rows[0]["revision"]
            rows[0]["status"] = "accepted"
            rows[0]["parts"] = [{"id": "part-ui", "number": 1, "status": "accepted", "size_bytes": 4567, "message_id": "<ficticio@example.test>"}]
            result = rows[0]
        elif path.endswith("/receipt"):
            rows[0].update(status="received", received_by="ui", received_at="2026-10-17T11:00:00Z", receipt_note=body["note"])
            result = rows[0]
        else:
            raise AssertionError(f"Unexpected write: {path}")
    elif path == "/api/bootstrap":
        result = boot
    elif path == "/api/dashboard":
        result = {}
    elif path == "/api/settings/email":
        result = config
    elif path == "/api/emails":
        result = rows
    elif path.startswith("/api/emails/"):
        result = rows[0]
    elif path in {"/api/accounting/email/periods", "/api/accounting/delivery-periods"}:
        result = {"periodicity": "mensal", "vat_period": "", "note": "", "periods": []}
    else:
        result = []
    route.fulfill(json=result)


with sync_playwright() as playwright:
    expect.set_options(timeout=15000)
    browser = playwright.chromium.launch()
    page = browser.new_page(viewport={"width": 1440, "height": 1000}, locale="pt-PT")
    page.on("pageerror", lambda error: errors.append(str(error)))
    page.route("**/api/**", mock_api)
    page.goto("http://127.0.0.1:5174")
    page.get_by_role("button", name="Configurações", exact=True).click()
    page.get_by_role("button", name="Email", exact=True).click()
    output = Path(".local/email-ui")
    output.mkdir(parents=True, exist_ok=True)
    expect(page.get_by_label("Servidor SMTP", exact=True)).to_have_value("smtp.example.test")
    page.screenshot(path=str(output / "settings.png"), full_page=True)
    page.get_by_role("textbox", name="Nome do contabilista ou gabinete", exact=True).fill("Gabinete atualizado")
    page.get_by_role("button", name="Guardar configuração de email", exact=True).click()
    expect(page.get_by_text("Configuração de email guardada nesta empresa.", exact=False)).to_be_visible()
    expect(page.get_by_label("Palavra-passe SMTP", exact=True)).to_have_value("")
    page.get_by_role("button", name="Contabilidade", exact=True).click()
    page.get_by_role("button", name="Entrega à contabilidade", exact=True).click()
    page.get_by_role("button", name="Enviar à contabilidade", exact=True).click()
    dialog = page.get_by_role("dialog", name="Enviar à contabilidade", exact=True)
    expect(dialog.get_by_role("button", name="Fechar", exact=True).first).to_be_visible()
    dialog.get_by_label("Data início", exact=True).fill("2026-01-01")
    dialog.get_by_label("Data fim", exact=True).fill("2026-01-31")
    dialog.get_by_role("button", name="Preparar dossiê e email", exact=True).click()
    expect(dialog.get_by_text("Anexo em falta", exact=False)).to_be_visible()
    dialog.evaluate("element => element.scrollTop = 0")
    page.screenshot(path=str(output / "prepared.png"), full_page=True)
    subject = dialog.get_by_label("Assunto", exact=True)
    subject.fill("Documentação revista e preservada")
    dialog.get_by_role("button", name="Fechar", exact=True).first.click()
    discard = page.get_by_role("alertdialog", name="Descartar alterações?", exact=True)
    expect(discard).to_be_visible()
    expect(discard.get_by_role("button", name="Continuar a editar", exact=True)).to_be_focused()
    discard.get_by_role("button", name="Continuar a editar", exact=True).click()
    expect(subject).to_have_value("Documentação revista e preservada")
    dialog.get_by_role("button", name="Atualizar pré-visualização", exact=True).click()
    expect(dialog.get_by_text("Reveja o endereço do destinatário.", exact=True)).to_be_visible()
    expect(subject).to_have_value("Documentação revista e preservada")
    send = dialog.get_by_role("button", name="Enviar à contabilidade", exact=True)
    expect(send).to_be_disabled()
    dialog.get_by_role("button", name="Atualizar pré-visualização", exact=True).click()
    approval = dialog.get_by_role("checkbox", name="Revi destinatários", exact=False)
    expect(approval).to_be_enabled()
    approval.check()
    expect(send).to_be_enabled()
    subject.fill("Versão final")
    expect(send).to_be_disabled()
    dialog.get_by_role("button", name="Atualizar pré-visualização", exact=True).click()
    approval.check()
    send.click()
    expect(dialog.get_by_text("Aceite pelo servidor SMTP. A receção pelo contabilista ainda não está confirmada.", exact=True)).to_be_visible()
    assert len([p for p, _ in writes if p.endswith("/send")]) == 1
    dialog.get_by_label("Confirmação de receção: como e por quem", exact=True).fill("Gabinete confirmou por telefone")
    dialog.get_by_role("button", name="Registar receção confirmada", exact=True).click()
    expect(dialog.get_by_text("Receção confirmada", exact=True).first).to_be_visible()
    dialog.evaluate("element => element.scrollTop = 0")
    page.screenshot(path=str(output / "desktop.png"), full_page=True)
    page.set_viewport_size({"width": 390, "height": 844})
    expect(dialog).to_be_visible()
    box = dialog.bounding_box()
    assert box and box["x"] >= 0 and box["width"] <= 390
    page.screenshot(path=str(output / "mobile.png"), full_page=True)
    page.keyboard.press("Escape")
    expect(dialog).not_to_be_visible()
    assert not errors, errors
    browser.close()
print("SMTP settings, dossier preview, error preservation, explicit send, receipt, keyboard and mobile: passed. No real emails.")
