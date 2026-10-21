"""Arquivo digital no navegador: menus, atualização, carregamento com QR, detalhe, pesquisa, páginas e assistente.

Usa a aplicação local já iniciada (.\Iniciar.ps1) em APP_MODE=demonstracao e a empresa de demonstração.
No fim remove os documentos de teste carregados (nome começado por TESTE-ARQUIVO).
"""
import io
import os
import re
import sys
import tempfile
from pathlib import Path

from playwright.sync_api import expect, sync_playwright
from reportlab.graphics import renderPDF
from reportlab.graphics.barcode.qr import QrCodeWidget
from reportlab.graphics.shapes import Drawing
from reportlab.pdfgen import canvas

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))
os.chdir(ROOT / "backend")
from app import db as database  # noqa: E402
from sqlalchemy import text as sql  # noqa: E402

if os.getenv("APP_MODE") != "demonstracao":
    raise SystemExit("Execute apenas com APP_MODE=demonstracao (.env).")
out = Path(tempfile.mkdtemp(prefix="arquivo-ui-"))
expect.set_options(timeout=20000)
text = (ROOT / ".local" / "ACESSO-LOCAL.txt").read_text(encoding="utf-8")
email = re.search(r"Email:\s*(\S+)", text)[1]
password = re.search(r"Palavra-passe:\s*(\S+)", text)[1]
BASE = "http://127.0.0.1:5174"


def qr_pdf(payload, lines):
    output = io.BytesIO()
    pdf = canvas.Canvas(output)
    for index, line in enumerate(lines):
        pdf.drawString(40, 800 - index * 18, line)
    qr = QrCodeWidget(payload)
    x, y, right, top = qr.getBounds()
    drawing = Drawing(130, 130, transform=[130 / (right - x), 0, 0, 130 / (top - y), 0, 0])
    drawing.add(qr)
    renderPDF.draw(drawing, pdf, 350, 100)
    pdf.save()
    return output.getvalue()


invoice = out / "TESTE-ARQUIVO-fatura-edp.pdf"
invoice.write_bytes(qr_pdf("A:503504564*B:211779369*C:PT*D:FT*E:N*F:20260915*G:FT TESTE/77*H:T-77*I1:PT*I7:81.30*I8:18.70*N:18.70*O:100.00*Q:ABCD*R:9999",
                           ["EDP Comercial", "Fatura FT TESTE/77", "Total: 100,00 EUR"]))
errors = []
try:
    with sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page(viewport={"width": 1300, "height": 900})
        page.on("pageerror", lambda e: errors.append(str(e)))
        page.on("response", lambda r: print("api", r.status, r.url.split("/api")[1][:80]) if "/api/archive" in r.url and r.status >= 400 else None)
        page.on("dialog", lambda d: d.accept("Teste de anulação"))
        page.goto(BASE + "/entrar")
        page.get_by_label("NIF da empresa", exact=True).fill("211779369")
        page.get_by_label("Email", exact=True).fill(email)
        page.get_by_label("Palavra-passe", exact=True).fill(password)
        page.get_by_label("Palavra-passe", exact=True).press("Enter")
        page.get_by_role("button", name="Arquivo digital").click()
        print("submenu:", page.locator("#archive-submenu button").all_inner_texts())
        print("contabilidade:", (page.get_by_role("button", name="Contabilidade").click(), page.locator("#accounting-submenu button").all_inner_texts())[1])
        page.locator("#archive-submenu").get_by_role("button", name="Visão geral").click()
        expect(page.get_by_role("heading", name="Arquivo digital")).to_be_visible()
        page.get_by_role("button", name="Atualizar arquivo").click()
        expect(page.locator(".ui-notice-success, .ui-notice-error").first).to_be_visible(timeout=120000)
        print("sync:", page.locator(".ui-notice-success, .ui-notice-error").first.inner_text()[:200])
        print("metrics:", [t.replace("\n", " ")[:60] for t in page.locator(".arc-metrics .metric-card").all_inner_texts()])
        page.screenshot(path=out / "arc-overview.png", full_page=True)

        page.locator("#archive-submenu").get_by_role("button", name="Caixa de entrada").click()
        page.locator(".arc-drop input[type=file]").set_input_files(str(invoice))
        expect(page.locator(".arc-results")).to_be_visible(timeout=60000)
        print("upload:", page.locator(".arc-results").inner_text())
        expect(page.get_by_role("button", name="TESTE-ARQUIVO-fatura-edp.pdf")).to_be_visible(timeout=60000)
        page.get_by_role("button", name="TESTE-ARQUIVO-fatura-edp.pdf").first.click()
        dialog = page.locator("dialog.arc-dialog")
        expect(dialog.get_by_role("heading", name=re.compile("TESTE-ARQUIVO"))).to_be_visible()
        expect(dialog.locator("iframe")).to_be_visible()
        print("fields:", dialog.get_by_label("NIF da entidade").input_value(), dialog.get_by_label("Total").input_value(), dialog.get_by_label("Tipo de documento").input_value())
        print("confidence:", dialog.locator(".arc-confidence").first.inner_text())
        page.screenshot(path=out / "arc-detail.png", full_page=False)
        dialog.get_by_label("Descrição").fill("Eletricidade (teste)")
        dialog.get_by_role("button", name="Guardar e marcar revisto").click()
        expect(dialog.locator(".ui-notice-success")).to_be_visible()
        dialog.locator("summary", has_text="Associar manualmente").click()
        dialog.get_by_role("button", name="Procurar").click()
        page.wait_for_timeout(1500)
        print("manual targets:", dialog.locator(".arc-targets li").count())
        dialog.get_by_role("button", name="Fechar").first.click()
        page.wait_for_timeout(800)

        page.locator("#archive-submenu").get_by_role("button", name="Todos os documentos").click()
        page.get_by_label("Pesquisar").fill("TESTE/77")
        page.get_by_role("button", name="Filtrar").click()
        page.wait_for_timeout(1500)
        print("search rows:", page.locator(".arc-table tbody tr").count())
        page.screenshot(path=out / "arc-all.png", full_page=True)

        for name in ("Associações", "Pendências", "Dossiês contabilísticos", "Arquivo — Auditoria", "Arquivo — Configurações"):
            page.locator("#archive-submenu").get_by_role("button", name=name, exact=True).click()
            page.wait_for_timeout(2000)
            print(name, "->", page.locator(".arc h1").first.inner_text(), "| erros:", page.locator(".arc .ui-notice-error").count())
            page.screenshot(path=out / f"arc-{name[-6:]}.png", full_page=True)

        page.locator("#archive-submenu").get_by_role("button", name="Visão geral").click()
        page.get_by_role("button", name="Encontra as faturas da EDP de setembro.").click()
        expect(page.locator(".arc-answer")).to_be_visible()
        print("assistant:", page.locator(".arc-answer-text").inner_text()[:200])
        page.get_by_role("button", name="Relaciona os anexos disponíveis com as compras do ERP.").click()
        expect(page.locator(".arc-answer")).to_be_visible()
        print("assistant write:", page.locator(".arc-answer-text").inner_text()[:200], "| plan button:", page.get_by_role("button", name="Confirmar e executar").count())

        mobile = browser.new_page(viewport={"width": 390, "height": 844}, is_mobile=True)
        mobile.context.add_cookies(page.context.cookies())
        mobile.goto(BASE + "/")
        mobile.wait_for_timeout(4000)
        mobile.get_by_role("button", name="Abrir menu").click()
        mobile.get_by_role("button", name="Arquivo digital").click()
        mobile.locator("#archive-submenu").get_by_role("button", name="Caixa de entrada").click()
        mobile.wait_for_timeout(2500)
        print("mobile h-scroll ok:", mobile.evaluate("document.documentElement.scrollWidth <= innerWidth"))
        mobile.screenshot(path=out / "arc-mobile.png", full_page=True)
        browser.close()
    assert not errors, errors
    print("Arquivo digital: interface verificada.")
finally:
    with database.engine.begin() as conn:
        ids = list(conn.execute(sql("SELECT id FROM public.archive_documents WHERE origin = 'upload' AND original_name LIKE 'TESTE-ARQUIVO%'")).scalars())
        for table, column in (("archive_events", "document_id"), ("archive_jobs", "document_id"), ("archive_links", "document_id"), ("archive_blobs", "document_id"), ("archive_documents", "id")):
            conn.execute(sql(f"DELETE FROM public.{table} WHERE {column} = ANY(:ids)"), {"ids": ids})
