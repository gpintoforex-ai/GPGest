"""Deteção das margens em direto na digitalização rápida, com câmara simulada (vídeo sintético).

O Chromium recebe um vídeo Y4M como câmara: uma fatura rodada sobre uma mesa de madeira e, num
segundo navegador, só a mesa. Verifica o contorno em direto, a captura automática com os cantos
corretos, a captura manual com a automática desligada e a ausência de captura sem documento.

Usa a aplicação local já iniciada (.\\Iniciar.ps1) em APP_MODE=demonstracao e a empresa de
demonstração; no fim remove os acessos criados e repõe a configuração das pastas.
"""
import json
import math
import os
import re
import sys
import tempfile
from pathlib import Path

import httpx
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
WIDTH, HEIGHT = 640, 480
access_text = (ROOT / ".local" / "ACESSO-LOCAL.txt").read_text(encoding="utf-8")
email = re.search(r"Email:\s*(\S+)", access_text)[1]
password = re.search(r"Palavra-passe:\s*(\S+)", access_text)[1]


def rotated(cx, cy, w, h, degrees):
    a = math.radians(degrees)
    return [(cx + x * math.cos(a) - y * math.sin(a), cy + x * math.sin(a) + y * math.cos(a))
            for x, y in ((-w / 2, -h / 2), (w / 2, -h / 2), (w / 2, h / 2), (-w / 2, h / 2))]


def video(path, quad, glare_every=0):
    """Vídeo Y4M (4:2:0) estático: mesa de madeira e, opcionalmente, uma fatura com texto. Com
    `glare_every`, um em cada N fotogramas fica branco (reflexo ou exposição a ajustar)."""
    image = Image.new("RGB", (WIDTH, HEIGHT), (120, 80, 44))
    draw = ImageDraw.Draw(image)
    for y in range(0, HEIGHT, 7):  # veios da madeira
        draw.line([(0, y), (WIDTH, y + 3)], fill=(108, 70, 38), width=2)
    if quad:
        draw.polygon(quad, fill=(244, 242, 236))
        cx, cy = sum(p[0] for p in quad) / 4, sum(p[1] for p in quad) / 4
        for i in range(-6, 7):  # linhas de texto, com margens
            y = cy + i * 22
            draw.line([(cx - 95, y), (cx + 60 - (i % 3) * 25, y + 4)], fill=(35, 35, 40), width=4)
    y_plane, cb, cr = image.convert("YCbCr").split()
    cb, cr = cb.resize((WIDTH // 2, HEIGHT // 2), Image.BOX), cr.resize((WIDTH // 2, HEIGHT // 2), Image.BOX)
    frame = b"FRAME\n" + y_plane.tobytes() + cb.tobytes() + cr.tobytes()
    glare = b"FRAME\n" + bytes([250]) * (WIDTH * HEIGHT) + bytes([128]) * (WIDTH * HEIGHT // 2)
    frames = [glare if glare_every and i % glare_every == 0 else frame for i in range(30)]
    path.write_bytes(f"YUV4MPEG2 W{WIDTH} H{HEIGHT} F30:1 Ip A1:1 C420jpeg\n".encode() + b"".join(frames))


def corners_of(page):
    values = []
    for i in range(4):
        found = re.match(r"(\d+)% horizontal, (\d+)% vertical", page.get_by_role("slider").nth(i).get_attribute("aria-valuetext"))
        values.append((int(found[1]) / 100, int(found[2]) / 100))
    return values


def assert_corners(found, truth, tolerance=0.03):
    normal = [(x / WIDTH, y / HEIGHT) for x, y in truth]
    error = min(max(math.dist(found[i], normal[(i + shift) % 4]) for i in range(4)) for shift in range(4))
    assert error <= tolerance, f"cantos {found} longe de {normal} (erro {error:.3f})"
    return error


with database.next_platform_session() as core:
    company = core.scalar(select(Company).where(Company.nif == NIF))
    assert company and company.mode == "demonstracao", "Empresa de demonstração não encontrada."
    schema = company.schema_name
work = Path(tempfile.mkdtemp(prefix="quick-scan-live-"))
truth = rotated(320, 240, 250, 330, 9)
video(work / "documento.y4m", truth)
video(work / "mesa.y4m", None)
video(work / "reflexos.y4m", truth, glare_every=5)

with database.engine.begin() as conn:
    previous_folders = conn.execute(text(f'SELECT folders FROM "{schema}".invoice_workflow_settings WHERE id = \'default\'')).scalar()
    if previous_folders is None:
        raise SystemExit("Abra uma vez a Conferência documental na empresa de demonstração e repita.")
    existing = set(conn.execute(text(f'SELECT id FROM "{schema}".quick_scan_accesses')).scalars())
    conn.execute(text(f'UPDATE "{schema}".invoice_workflow_settings SET folders = :f WHERE id = \'default\''), {"f": json.dumps(["demoPastaTeste123"])})


def pairing_code(client, store, name):
    r = client.post("/api/quick-scan/accesses", json={"name": name, "store_id": store, "folder_id": "demoPastaTeste123", "pin": PIN})
    assert r.status_code == 200, r.text
    return r.json()["pairing"]["code"]


def open_camera(browser, code):
    context = browser.new_context(viewport={"width": 390, "height": 844}, permissions=["camera"], is_mobile=True, has_touch=True)
    page = context.new_page()
    errors = []
    page.on("pageerror", lambda e: errors.append(str(e)))
    page.on("dialog", lambda d: d.accept())
    page.goto(f"{BASE}/entrar?digitalizar={code}")
    page.get_by_label("PIN (6 a 12 algarismos)").fill(PIN)
    page.get_by_role("button", name="Continuar").click()
    expect(page.get_by_role("button", name="Capturar página")).to_be_enabled()
    return page, errors


try:
    with httpx.Client(base_url=BASE, timeout=30) as client:
        assert client.post("/api/company/select", json={"nif": NIF}).status_code == 200
        assert client.post("/api/auth/login", json={"email": email, "password": password}).status_code == 200
        store = client.get("/api/bootstrap").json()["stores"][0]["id"]
        first, second = pairing_code(client, store, "Teste em direto"), pairing_code(client, store, "Teste sem documento")
        third = pairing_code(client, store, "Teste com reflexos")

    with sync_playwright() as p:
        fake = ["--use-fake-device-for-media-stream", "--use-fake-ui-for-media-stream"]
        browser = p.chromium.launch(args=[*fake, f"--use-file-for-fake-video-capture={work / 'documento.y4m'}"])
        page, errors = open_camera(browser, first)
        # Contorno em direto e captura automática (ligada por omissão) com os cantos vistos.
        expect(page.locator(".qs-live-quad")).to_be_visible()
        # A indicação acompanha a deteção (nunca «A procurar…» com o contorno visível).
        expect(page.locator(".qs-live-hint")).not_to_contain_text("A procurar", timeout=3000)
        page.screenshot(path=str(work / "direto.png"))
        report = page.evaluate((ROOT / "scripts" / "contrast-audit.js").read_text(encoding="utf-8"))
        assert not report.get("failures"), report.get("failures")
        # Como num digitalizador: a página aparece logo recortada e melhorada.
        expect(page.get_by_role("heading", name="Página 1 pronta")).to_be_visible(timeout=15000)
        result = page.get_by_role("img", name="Página 1 recortada")
        expect(result).to_be_visible()
        page.wait_for_function("img => img.complete && img.naturalWidth > 0", arg=result.element_handle())
        size = result.evaluate("img => [img.naturalWidth, img.naturalHeight]")
        assert size[1] > size[0], f"O recorte deve ter a forma da folha (vertical), não a da imagem: {size}"
        page.screenshot(path=str(work / "resultado.png"))
        page.get_by_role("button", name="Ajustar cantos").click()
        expect(page.get_by_text("Margens detetadas automaticamente", exact=False)).to_be_visible()
        automatic = assert_corners(corners_of(page), truth)
        # Repetir com a captura automática desligada: nada é fotografado sozinho; o botão usa a deteção.
        page.get_by_role("button", name="Repetir fotografia").click()
        page.get_by_role("button", name=re.compile("Captura automática ligada")).click()
        expect(page.get_by_role("button", name=re.compile("Captura automática desligada"))).to_have_attribute("aria-pressed", "false")
        expect(page.locator(".qs-live-hint")).to_contain_text("Toque no botão para fotografar")
        page.wait_for_timeout(2500)
        expect(page.get_by_role("heading", name=re.compile("Rever o recorte|pronta"))).to_have_count(0)
        page.get_by_role("button", name="Capturar página").click()
        expect(page.get_by_role("heading", name="Página 1 pronta")).to_be_visible()
        page.get_by_role("button", name="Ajustar cantos").click()
        manual = assert_corners(corners_of(page), truth)
        page.screenshot(path=str(work / "recorte.png"))
        assert page.evaluate("localStorage.getItem('iazela_digitalizacao_auto')") == "0"
        page.get_by_role("button", name="Terminar").first.click()
        # Com páginas por guardar, a saída pede confirmação (diálogo partilhado da aplicação).
        page.get_by_role("alertdialog").get_by_role("button", name="Terminar sem guardar").click()
        expect(page.get_by_role("button", name="Digitalizar documento")).to_be_visible()
        assert not errors, errors
        browser.close()

        # Imagens falhadas pontuais (1 em cada 5 fotogramas branco) não impedem a captura automática.
        browser = p.chromium.launch(args=[*fake, f"--use-file-for-fake-video-capture={work / 'reflexos.y4m'}"])
        page, errors = open_camera(browser, third)
        expect(page.get_by_role("heading", name="Página 1 pronta")).to_be_visible(timeout=12000)
        page.get_by_role("button", name="Ajustar cantos").click()
        flicker = assert_corners(corners_of(page), truth)
        assert not errors, errors
        browser.close()

        browser = p.chromium.launch(args=[*fake, f"--use-file-for-fake-video-capture={work / 'mesa.y4m'}"])
        page, errors = open_camera(browser, second)
        expect(page.locator(".qs-live-hint")).to_contain_text("A procurar o documento")
        page.wait_for_timeout(3000)
        expect(page.locator(".qs-live-quad")).to_have_count(0)
        expect(page.get_by_role("heading", name=re.compile("Rever o recorte|pronta"))).to_have_count(0)
        assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
        assert not errors, errors
        browser.close()
    print(f"Deteção em direto: contorno, captura automática (erro {automatic:.1%}), manual (erro {manual:.1%}), com reflexos intermitentes (erro {flicker:.1%}), sem documento sem captura. Imagens em {work}")
finally:
    with database.engine.begin() as conn:
        created = [i for i in conn.execute(text(f'SELECT id FROM "{schema}".quick_scan_accesses')).scalars() if i not in existing]
        if created:
            conn.execute(text(f'DELETE FROM "{schema}".document_scans WHERE quick_access_id = ANY(:ids)'), {"ids": created})
            conn.execute(text(f'DELETE FROM "{schema}".quick_scan_sessions WHERE access_id = ANY(:ids)'), {"ids": created})
            conn.execute(text(f'DELETE FROM "{schema}".quick_scan_accesses WHERE id = ANY(:ids)'), {"ids": created})
        conn.execute(text(f'UPDATE "{schema}".invoice_workflow_settings SET folders = :f WHERE id = \'default\''), {"f": previous_folders})
