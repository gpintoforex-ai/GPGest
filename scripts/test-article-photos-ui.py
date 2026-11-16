"""Fotografias da ficha do artigo: carregar a fotografia do produto (ficheiro) e a do rótulo, ver as miniaturas,
retirar o rótulo; o botão da câmara fica sobre a fotografia. API simulada (reaproveita o teste de artigos); temas
claro e escuro, ecrã largo e telemóvel, contraste. Requer o Vite em 127.0.0.1:5174.
"""
import io
import re
from pathlib import Path
from urllib.parse import urlsplit

from PIL import Image
from playwright.sync_api import expect, sync_playwright

ROOT = Path(__file__).resolve().parents[1]
fixture = ROOT / "scripts/test-inventory-ui.py"
ns = {"__file__": str(fixture)}
exec(fixture.read_text(encoding="utf-8").split("with sync_playwright() as p:")[0], ns)
OUT = ROOT / ".local/article-photos"
OUT.mkdir(parents=True, exist_ok=True)


def jpeg(color, size=(600, 600)):
    out = io.BytesIO()
    Image.new("RGB", size, color).save(out, "JPEG")
    return out.getvalue()


FILES = {"photo": jpeg((180, 40, 120)), "label": jpeg((235, 235, 220), (500, 800))}
state = {}
calls = []


def meta(article, role):
    return {"id": f"img-{role}", "role": role, "url": f"/api/stock/articles/{article}/images/{role}/{'a' * 32}.jpg",
            "width": 600, "height": 600, "size": len(FILES[role]), "created_at": "2026-11-15T10:00:00+00:00"}


def intercept(route):
    path, method = urlsplit(route.request.url).path, route.request.method
    match = re.match(r"^/api/stock/articles/([^/]+)/images/(photo|label)/from-url$", path)
    if match and method == "POST":
        calls.append(("FROM-URL", route.request.post_data_json["url"]))
        state[match.group(2)] = meta(match.group(1), match.group(2))
        return route.fulfill(json={"photo": state.get("photo"), "label": state.get("label")})
    match = re.match(r"^/api/stock/articles/([^/]+)/images/label/publication$", path)
    if match and method == "PUT":
        body = route.request.post_data_json
        calls.append(("PUBLISH", body))
        state["label"] = {**state["label"], "public": body["public"], "gallery": body["public"] and body["gallery"]}
        return route.fulfill(json={"photo": state.get("photo"), "label": state.get("label")})
    match = re.match(r"^/api/stock/articles/([^/]+)/images(?:/(photo|label))?(?:/([^/]+))?$", path)
    if match:
        article, role, name = match.groups()
        calls.append((method, path))
        if name:
            return route.fulfill(body=FILES[role], content_type="image/jpeg")
        if method == "PUT":
            assert b"filename=" in route.request.post_data_buffer
            state[role] = meta(article, role)
        elif method == "DELETE":
            state.pop(role, None)
        return route.fulfill(json={"photo": state.get("photo"), "label": state.get("label")})
    return ns["mock"](route)


def audit(page, card):
    """Contraste dos textos do cartão das fotografias (o resto da ficha tem o seu próprio teste)."""
    page.mouse.move(1, 1)
    page.wait_for_timeout(400)
    result = page.evaluate((ROOT / "scripts/contrast-audit.js").read_text(encoding="utf-8"))
    text = card.inner_text()
    failures = [f for f in result.get("failures") or [] if f.get("text") and f["text"] in text]
    assert not failures, failures


def pick(page, dialog, summary, path):
    summary.click()
    popover = dialog.locator("details[open] .article-photo-popover")
    with page.expect_file_chooser() as chooser:
        popover.get_by_role("button", name="Escolher ficheiro").click()
    chooser.value.set_files(str(path))
    page.wait_for_timeout(300)
    summary.click()  # fecha


with sync_playwright() as p:
    browser = p.chromium.launch()
    for theme, viewport in (("light", {"width": 1440, "height": 1000}), ("dark", {"width": 390, "height": 844})):
        state.clear()
        calls.clear()
        for role, data in FILES.items():
            (OUT / f"{role}.jpg").write_bytes(data)
        page = browser.new_page(viewport=viewport, locale="pt-PT")
        errors = []
        page.on("pageerror", lambda e: errors.append(str(e)))
        page.add_init_script(f"localStorage.setItem('iazela-theme','{theme}')")
        page.route("**/api/**", intercept)
        page.goto("http://127.0.0.1:5174/?pagina=Artigos%20e%20validades&area.stock=Artigos")
        page.get_by_label("Mais ações para Suplemento de teste", exact=True).click()
        page.get_by_role("menuitem", name="Editar Suplemento de teste", exact=True).click()
        dialog = page.get_by_role("dialog", name="Editar artigo", exact=True)
        card = dialog.locator(".article-photo-card")
        expect(card).to_contain_text("Rótulo")
        expect(card).to_contain_text("Por tirar")
        # O botão da câmara fica sobre a fotografia (canto inferior direito da imagem).
        image_box = card.locator(".article-image").bounding_box()
        camera = card.locator(".article-photo-main > details > summary")
        camera_box = camera.bounding_box()
        assert image_box["y"] < camera_box["y"] < image_box["y"] + image_box["height"], (image_box, camera_box)
        # Endereço HTTPS: o painel aberto fica por cima do botão do rótulo; «Guardar esta imagem na IAZELA».
        camera.click()
        popover = dialog.locator("details[open] .article-photo-popover")
        title_box = popover.locator("strong").first.bounding_box()
        hit = page.evaluate("([x, y]) => document.elementFromPoint(x, y)?.closest('.article-photo-popover') !== null",
                            [title_box["x"] + 4, title_box["y"] + title_box["height"] / 2])
        assert hit, "o título do painel da fotografia não pode ficar tapado"
        popover.get_by_text("Ou usar um endereço HTTPS").click()
        save_url = popover.get_by_role("button", name="Guardar esta imagem na IAZELA")
        popover.locator("input[name='photo']").fill("https://www.exemplo.pt/imgs/produto.jpg")
        save_url.click()
        expect(popover).to_contain_text("Imagem guardada na IAZELA.")
        assert ("FROM-URL", "https://www.exemplo.pt/imgs/produto.jpg") in calls, calls
        camera.click()  # fecha
        pick(page, dialog, camera, OUT / "photo.jpg")
        expect(card.locator(".article-image img")).to_have_attribute("src", re.compile(r"/images/photo/"))
        pick(page, dialog, card.locator(".article-label-editor > summary"), OUT / "label.jpg")
        expect(card.locator(".article-label-photo img")).to_be_visible()
        expect(card).to_contain_text("Para a IA")
        assert [c[0] for c in calls if c[0] in ("PUT", "DELETE", "FROM-URL")] == ["FROM-URL", "PUT", "PUT"], calls
        # Rótulo: endereço HTTPS guardado na IAZELA e publicação no site (legível e em português), com galeria.
        card.locator(".article-label-editor > summary").click()
        label_popover = dialog.locator("details[open] .article-photo-popover")
        expect(label_popover.get_by_text("A IA lê-a para propor um descritivo mais completo", exact=False)).to_be_visible()
        clipped = label_popover.locator("small").first.evaluate("e => e.scrollWidth > e.clientWidth + 1")
        assert not clipped, "texto do painel do rótulo cortado"
        url_details = label_popover.locator("details.article-photo-url")
        if url_details.get_attribute("open") is None:
            url_details.locator("summary").click()
        label_popover.get_by_label("URL da fotografia (HTTPS)").fill("https://www.exemplo.pt/rotulo.jpg")
        label_popover.get_by_role("button", name="Guardar esta imagem na IAZELA").click()
        expect(label_popover).to_contain_text("Rótulo guardado na IAZELA.")
        gallery = label_popover.get_by_label("Também como última imagem da galeria")
        expect(gallery).to_be_disabled()
        publish_box = label_popover.get_by_label("Publicar o rótulo no site (legível e em português)")
        publish_box.click()  # só fica marcada depois de a IAZELA confirmar
        expect(publish_box).to_be_checked()
        expect(card).to_contain_text("No site")
        gallery.click()
        expect(gallery).to_be_checked()
        label_popover.screenshot(path=str(OUT / f"rotulo-{theme}.png"))
        assert [c[1] for c in calls if c[0] == "PUBLISH"] == [{"public": True, "gallery": False}, {"public": True, "gallery": True}], calls
        card.locator(".article-label-editor > summary").click()  # fecha
        # Carregar imagens não deixa o formulário com alterações por guardar.
        audit(page, card)
        card.screenshot(path=str(OUT / f"cartao-{theme}.png"))
        dialog.locator(".article-identity").screenshot(path=str(OUT / f"identidade-{theme}.png"))
        card.locator(".article-label-editor > summary").click()
        dialog.locator("details[open] .article-photo-popover").get_by_role("button", name="Retirar fotografia do rótulo").click()
        page.get_by_role("alertdialog").get_by_role("button", name="Retirar rótulo").click()
        expect(card).to_contain_text("Por tirar")
        page.keyboard.press("Escape")
        page.keyboard.press("Escape")
        expect(page.get_by_role("alertdialog")).to_have_count(0)
        assert not errors, errors
        page.close()
    browser.close()
print("PASS: fotografias da ficha (produto e rótulo; carregar, ver, retirar; botão sobre a fotografia), temas, telemóvel e contraste.")
