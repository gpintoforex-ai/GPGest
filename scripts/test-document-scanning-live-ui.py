"""Câmara com deteção das margens em direto na digitalização da área autenticada.

Navegador Chromium com um vídeo sintético como câmara (fatura sobre madeira) e API simulada: nenhum
dado real é lido ou escrito. Verifica o modo contínuo (captura automática sem repetir a mesma página,
captura manual), «Repetir fotografia» com substituição e os cantos enviados ao gerar o PDF.
"""
import json
import math
import tempfile
from io import BytesIO
from pathlib import Path
from urllib.parse import urlsplit

from PIL import Image, ImageDraw
from playwright.sync_api import expect, sync_playwright

expect.set_options(timeout=20000)
# Base de testes visuais (sessão, empresa e API simulada), num espaço de nomes próprio.
template = Path(__file__).with_name("test-visual-template-ui.py")
fixture = {"__file__": str(template)}
exec(template.read_text(encoding="utf-8").split("\nwith sync_playwright() as playwright:")[0], fixture)
boot, documents, pdf_buffer, intercept = fixture["boot"], fixture["documents"], fixture["pdf_buffer"], fixture["intercept"]
WIDTH, HEIGHT = 640, 480


def rotated(cx, cy, w, h, degrees):
    a = math.radians(degrees)
    return [(cx + x * math.cos(a) - y * math.sin(a), cy + x * math.sin(a) + y * math.cos(a))
            for x, y in ((-w / 2, -h / 2), (w / 2, -h / 2), (w / 2, h / 2), (-w / 2, h / 2))]


def video(path, quad):
    """Vídeo Y4M (4:2:0) estático: mesa de madeira com uma fatura com texto."""
    picture = Image.new("RGB", (WIDTH, HEIGHT), (120, 80, 44))
    draw = ImageDraw.Draw(picture)
    for y in range(0, HEIGHT, 7):
        draw.line([(0, y), (WIDTH, y + 3)], fill=(108, 70, 38), width=2)
    draw.polygon(quad, fill=(244, 242, 236))
    cx, cy = sum(p[0] for p in quad) / 4, sum(p[1] for p in quad) / 4
    for i in range(-6, 7):
        draw.line([(cx - 95, cy + i * 22), (cx + 60 - (i % 3) * 25, cy + i * 22 + 4)], fill=(35, 35, 40), width=4)
    y_plane, cb, cr = picture.convert("YCbCr").split()
    cb, cr = cb.resize((WIDTH // 2, HEIGHT // 2), Image.BOX), cr.resize((WIDTH // 2, HEIGHT // 2), Image.BOX)
    path.write_bytes(f"YUV4MPEG2 W{WIDTH} H{HEIGHT} F30:1 Ip A1:1 C420jpeg\n".encode() + (b"FRAME\n" + y_plane.tobytes() + cb.tobytes() + cr.tobytes()) * 30)


def corner_error(found, truth):
    normal = [(x / WIDTH, y / HEIGHT) for x, y in truth]
    return min(max(math.dist(found[i], normal[(i + shift) % 4]) for i in range(4)) for shift in range(4))


work = Path(tempfile.mkdtemp(prefix="scanner-live-"))
truth = rotated(320, 240, 250, 330, 9)
video(work / "documento.y4m", truth)
preview = BytesIO()
Image.new("RGB", (600, 800), "white").save(preview, "JPEG")
calls = {"preview": 0, "prepare": 0}
prepared = {}


def scan_intercept(route):
    path = urlsplit(route.request.url).path.removeprefix("/api")
    if path == "/scans/setup":
        value = {"company": boot["company"], "account": "demo@example.test", "connected": True, "folder_configured": True,
                 "settings": {"max_pages": 20, "max_bytes": 5 * 1024 * 1024, "organisation": "company_period_type"},
                 "stores": boot["stores"], "categories": [], "documents": documents, "recent": []}
    elif path == "/entities":
        value = []
    elif path == "/scans/image-preview":
        calls["preview"] += 1
        route.fulfill(content_type="image/jpeg", body=preview.getvalue())
        return
    elif path == "/scans/prepare":
        calls["prepare"] += 1
        body = route.request.post_data_buffer
        payload = body.split(b'name="payload"', 1)[1].split(b"\r\n\r\n", 1)[1].split(b"\r\n--", 1)[0]
        prepared.update(json.loads(payload))
        value = {"id": "demo-scan", "status": "pdf_generated", "name": "DEMO.pdf", "pages": 2, "size": len(pdf_buffer.getvalue()),
                 "view_url": "/api/scans/demo-scan/pdf", "validation_status": "por_validar", "warnings": [], "cloud_id": None,
                 "purchase_id": "0", "movement_id": None, "metadata": {"store_id": "demo", "purchase_id": "0", "amount_cents": 12300}}
    else:
        return intercept(route)
    route.fulfill(content_type="application/json", body=json.dumps(value))


with sync_playwright() as playwright:
    browser = playwright.chromium.launch(args=["--use-fake-device-for-media-stream", "--use-fake-ui-for-media-stream",
                                               f"--use-file-for-fake-video-capture={work / 'documento.y4m'}"])
    context = browser.new_context(viewport={"width": 390, "height": 844}, is_mobile=True, has_touch=True, locale="pt-PT", permissions=["camera"])
    page = context.new_page()
    errors = []
    page.on("pageerror", lambda error: errors.append(str(error)))
    page.route("**/api/**", scan_intercept)
    page.goto("http://127.0.0.1:5174")
    page.get_by_role("button", name="Abrir menu").click()
    page.get_by_role("button", name="Contabilidade", exact=True).click()
    page.get_by_role("button", name="Conferência documental", exact=True).click()
    page.get_by_role("main").get_by_role("button", name="Digitalizar documento", exact=True).click()
    dialog = page.get_by_role("dialog", name="Digitalizar documento", exact=True)
    dialog.get_by_role("button", name="Câmara com deteção de margens").click()
    camera = dialog.get_by_role("region", name="Câmara com deteção de margens")
    expect(camera.locator(".qs-live-quad")).to_be_visible()
    # Modo contínuo: a 1.ª página é fotografada sozinha e a mesma página não volta a ser fotografada.
    expect(dialog.locator(".scanner-pages article")).to_have_count(1, timeout=15000)
    expect(camera.locator(".qs-live-hint")).to_contain_text("Página fotografada")
    page.wait_for_timeout(2500)
    expect(dialog.locator(".scanner-pages article")).to_have_count(1)
    page.screenshot(path=str(work / "camara.png"))
    report = page.evaluate((Path(__file__).with_name("contrast-audit.js")).read_text(encoding="utf-8"))
    assert not report.get("failures"), report.get("failures")  # textos sobre o vídeo legíveis também dentro da janela
    camera.get_by_role("button", name="Capturar página").click()
    expect(dialog.locator(".scanner-pages article")).to_have_count(2)
    expect(dialog.get_by_text("Margens detetadas · reveja o recorte")).to_have_count(2)
    camera.get_by_role("button", name="Concluir câmara").click()
    expect(camera).to_have_count(0)
    # «Repetir fotografia» abre a câmara para substituir só essa página e fecha-a depois.
    dialog.locator(".scanner-pages article").first.get_by_role("button", name="Repetir fotografia").click()
    expect(dialog.get_by_text("A repetir uma página")).to_be_visible()
    expect(camera).to_have_count(0, timeout=15000)
    expect(dialog.locator(".scanner-pages article")).to_have_count(2)
    assert calls["preview"] >= 3, calls  # miniaturas recortadas das três fotografias
    dialog.get_by_label("Associar a documento existente").select_option("0")
    dialog.get_by_role("button", name="Gerar e pré-visualizar PDF", exact=True).click()
    expect(dialog.get_by_role("link", name="Descarregar PDF", exact=True)).to_be_visible()
    corners = [edit["corners"] for edit in prepared["edits"]]
    assert len(corners) == 2 and all(corners), prepared["edits"]
    worst = max(corner_error(found, truth) for found in corners)
    assert worst <= 0.03, (worst, corners)
    page.screenshot(path=str(work / "paginas.png"), full_page=True)
    assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
    assert not errors, errors
    browser.close()
print(f"PASS: câmara em direto na área autenticada, modo contínuo sem duplicados, manual, repetir página e cantos no PDF (erro máx. {worst:.1%}). Imagens em {work}")
