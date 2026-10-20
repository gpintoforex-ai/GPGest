"""Digitalização rápida por PIN no navegador (Chromium, câmara simulada, ecrã móvel).

Usa a aplicação local já iniciada (.\\Iniciar.ps1) em APP_MODE=demonstracao e a empresa de
demonstração. Configura temporariamente uma pasta de anexos fictícia e, no fim, remove os acessos
e digitalizações rápidas criados pelo teste e repõe a configuração. Sem conta Google ligada, o envio
termina com erro de autorização, que também é verificado.
"""
import json
import os
import re
import sys
import tempfile
from pathlib import Path

from PIL import Image, ImageDraw
from playwright.sync_api import expect, sync_playwright

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))
os.chdir(ROOT / "backend")
from app import db as database  # noqa: E402
from app.platform_models import Company  # noqa: E402
from sqlalchemy import select, text  # noqa: E402

if os.getenv("APP_MODE") != "demonstracao":
    raise SystemExit("Execute apenas com APP_MODE=demonstracao (.env).")
expect.set_options(timeout=20000)
BASE = "http://127.0.0.1:5174"
PIN = "482915"
NIF = "211779369"
access = (ROOT / ".local" / "ACESSO-LOCAL.txt").read_text(encoding="utf-8")
email = re.search(r"Email:\s*(\S+)", access)[1]
password = re.search(r"Palavra-passe:\s*(\S+)", access)[1]

with database.next_platform_session() as core:
    company = core.scalar(select(Company).where(Company.nif == NIF))
    assert company and company.mode == "demonstracao", "Empresa de demonstração não encontrada."
    schema = company.schema_name
work = Path(tempfile.mkdtemp(prefix="quick-scan-"))
photo = work / "documento.jpg"
image = Image.new("RGB", (1600, 1200), (70, 60, 55))
draw = ImageDraw.Draw(image)
draw.polygon([(420, 140), (1180, 190), (1240, 1080), (360, 1040)], fill=(245, 243, 238))
for i in range(14):
    draw.line([(500, 260 + i * 52), (1100 - (i % 3) * 120, 264 + i * 52)], fill=(30, 30, 30), width=6)
image.save(photo, quality=90)

with database.engine.begin() as conn:
    previous_folders = conn.execute(text(f'SELECT folders FROM "{schema}".invoice_workflow_settings WHERE id = \'default\'')).scalar()
    if previous_folders is None:
        raise SystemExit("Abra uma vez a Conferência documental na empresa de demonstração e repita.")
    existing = set(conn.execute(text(f'SELECT id FROM "{schema}".quick_scan_accesses')).scalars())
    conn.execute(text(f'UPDATE "{schema}".invoice_workflow_settings SET folders = :f WHERE id = \'default\''), {"f": json.dumps(["demoPastaTeste123"])})

errors = []
try:
    with sync_playwright() as p:
        browser = p.chromium.launch(args=["--use-fake-device-for-media-stream", "--use-fake-ui-for-media-stream"])
        admin = browser.new_page(viewport={"width": 1300, "height": 900})
        admin.on("pageerror", lambda e: errors.append(str(e)))
        admin.goto(BASE + "/entrar")
        admin.get_by_label("NIF da empresa", exact=True).fill(NIF)
        admin.get_by_label("Email", exact=True).fill(email)
        admin.get_by_label("Palavra-passe", exact=True).fill(password)
        admin.get_by_label("Palavra-passe", exact=True).press("Enter")
        admin.get_by_role("button", name=re.compile("^Configurações")).first.click()
        admin.get_by_role("button", name=re.compile("^Parâmetros")).first.click()
        admin.get_by_role("button", name=re.compile("^Digitalização$")).first.click()
        admin.get_by_role("button", name="Novo acesso rápido").click()
        admin.get_by_label("Nome / dispositivo").fill("Telemóvel de teste")
        admin.get_by_label("PIN (6 a 12 algarismos)").fill(PIN)
        admin.get_by_label("Confirmar PIN").fill(PIN)
        admin.get_by_role("button", name="Criar e emparelhar").click()
        expect(admin.locator(".quick-pairing img")).to_be_visible()
        link = admin.get_by_label("Link de emparelhamento").input_value()
        assert "/entrar?digitalizar=p1." in link

        phone_context = browser.new_context(viewport={"width": 390, "height": 844}, permissions=["camera"], is_mobile=True, has_touch=True)
        phone = phone_context.new_page()
        phone.on("pageerror", lambda e: errors.append(str(e)))
        phone.on("dialog", lambda d: d.accept())
        phone.goto(link)
        expect(phone.get_by_role("heading", name="Introduza o PIN")).to_be_visible()
        assert "digitalizar=" not in phone.url, "O código de emparelhamento deve sair do endereço."
        phone.get_by_label("PIN (6 a 12 algarismos)").fill("999111")
        phone.get_by_role("button", name="Continuar").click()
        expect(phone.locator(".ui-notice-error")).to_contain_text("Restam 4")
        phone.get_by_label("PIN (6 a 12 algarismos)").fill(PIN)
        phone.get_by_role("button", name="Continuar").click()
        shutter = phone.get_by_role("button", name="Capturar página")
        expect(shutter).to_be_enabled()
        shutter.click()
        expect(phone.get_by_role("heading", name=re.compile("Rever o recorte"))).to_be_visible()
        handle = phone.get_by_role("slider", name="Canto superior esquerdo")
        box = handle.bounding_box()
        phone.mouse.move(box["x"] + 22, box["y"] + 22)
        phone.mouse.down()
        phone.mouse.move(box["x"] + 60, box["y"] + 70, steps=5)
        phone.mouse.up()
        assert not handle.get_attribute("aria-valuetext").startswith("3% horizontal")
        phone.get_by_role("button", name="Preto e branco").click()
        phone.get_by_role("button", name="Confirmar e fotografar outra").click()
        phone.locator("input[type=file][multiple]").set_input_files(str(photo))
        expect(phone.get_by_text("Margens detetadas automaticamente", exact=False)).to_be_visible()
        corners = [phone.get_by_role("slider").nth(i).get_attribute("aria-valuetext") for i in range(4)]
        assert corners[0].startswith("26% horizontal, 12%") and corners[2].startswith("77% horizontal, 90%"), corners
        phone.get_by_role("button", name="Confirmar e rever").click()
        expect(phone.locator(".qs-pages li")).to_have_count(2)
        phone.get_by_role("button", name="Descer página 1").click()
        phone.get_by_text("Despesa", exact=True).click()
        phone.get_by_label("Descrição (opcional)").fill("Teste de digitalização")
        phone.get_by_role("button", name="Gerar PDF").click()
        expect(phone.locator(".qs-summary")).to_contain_text("2 página(s)")
        assert re.fullmatch(r"\d{4}-\d{2}-\d{2}_despesa_[0-9a-f]{8}", phone.get_by_label("Nome do ficheiro").input_value())
        phone.get_by_role("button", name="Guardar na cloud").click()
        expect(phone.locator(".ui-notice-error")).to_contain_text("ligação à cloud")
        expect(phone.get_by_role("button", name="Tentar novamente")).to_be_visible()
        expect(phone.get_by_role("link", name="Descarregar")).to_be_visible()
        assert phone.evaluate("document.documentElement.scrollWidth <= innerWidth")
        phone.get_by_role("button", name="Terminar").first.click()
        expect(phone.get_by_role("button", name="Digitalizar documento")).to_be_visible()
        assert phone.url.endswith("/entrar")
        phone.get_by_role("button", name="Digitalizar documento").click()
        expect(phone.get_by_role("heading", name="Introduza o PIN")).to_be_visible()
        token = phone.evaluate("localStorage.getItem('iazela_digitalizacao_dispositivo')")

        iphone = "Mozilla/5.0 (iPhone; CPU iPhone OS 18_0 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/18.0 Mobile/15E148 Safari/604.1"
        denied_context = browser.new_context(viewport={"width": 390, "height": 844}, user_agent=iphone)
        denied_context.add_init_script("navigator.mediaDevices.getUserMedia = () => Promise.reject(new DOMException('recusada', 'NotAllowedError'));")
        denied = denied_context.new_page()
        denied.on("pageerror", lambda e: errors.append(str(e)))
        denied.goto(BASE + "/entrar")
        denied.evaluate("t => localStorage.setItem('iazela_digitalizacao_dispositivo', t)", token)
        denied.goto(BASE + "/digitalizar")
        denied.get_by_label("PIN (6 a 12 algarismos)").fill(PIN)
        denied.get_by_role("button", name="Continuar").click()
        expect(denied.locator(".ui-notice-warning")).to_contain_text("Definições do site")
        expect(denied.get_by_role("button", name="Fotografar")).to_be_visible()
        expect(denied.get_by_role("button", name="Tentar novamente")).to_be_visible()

        admin.get_by_role("button", name="Fechar").click()
        admin.locator("table").first.locator("tbody tr").last.get_by_role("button", name="Revogar").click()
        admin.get_by_role("alertdialog").get_by_role("button", name="Revogar").click()
        expect(admin.locator("table").first.locator("tbody tr").last).to_contain_text("Revogado")
        denied.locator("input[type=file][multiple]").set_input_files(str(photo))
        denied.get_by_role("button", name="Confirmar e rever").click()
        expect(denied.get_by_role("heading", name="Introduza o PIN")).to_be_visible()
        denied.get_by_label("PIN (6 a 12 algarismos)").fill(PIN)
        denied.get_by_role("button", name="Continuar").click()
        expect(denied.get_by_role("heading", name=re.compile("Emparelhar"))).to_be_visible()
        assert denied.evaluate("localStorage.getItem('iazela_digitalizacao_dispositivo')") is None
        browser.close()
    assert not errors, errors
    print("Digitalização rápida: interface verificada.")
finally:
    with database.engine.begin() as conn:
        created = [i for i in conn.execute(text(f'SELECT id FROM "{schema}".quick_scan_accesses')).scalars() if i not in existing]
        if created:
            conn.execute(text(f'DELETE FROM "{schema}".document_scans WHERE quick_access_id = ANY(:ids)'), {"ids": created})
            conn.execute(text(f'DELETE FROM "{schema}".quick_scan_sessions WHERE access_id = ANY(:ids)'), {"ids": created})
            conn.execute(text(f'DELETE FROM "{schema}".quick_scan_accesses WHERE id = ANY(:ids)'), {"ids": created})
        conn.execute(text(f'UPDATE "{schema}".invoice_workflow_settings SET folders = :f WHERE id = \'default\''), {"f": previous_folders})
