"""Isolated direct-link and permission checks; no backend calls or real data changes."""

import copy
import re
import runpy
from pathlib import Path
from urllib.parse import urlsplit

from playwright.sync_api import expect, sync_playwright

ROOT = Path(__file__).resolve().parents[1]
ns = runpy.run_path(str(ROOT / "scripts/test-workspace-ui.py"))
boot = copy.deepcopy(ns["BOOT"])
boot["user"]["role"] = "consulta"
calls, errors = [], []


def intercept(route):
    path = urlsplit(route.request.url).path.removeprefix("/api")
    calls.append(path)
    if path == "/bootstrap":
        route.fulfill(json=boot)
    elif path == "/dashboard/overview":
        route.fulfill(
            json={
                "period": {"in_progress": False},
                "comparison": {"method": "Teste"},
                "generated_at": "2026-10-20T12:00:00Z",
                "attention": [],
                "permissions": {"finance": False, "costs": False},
            }
        )
    elif path == "/legal/public":
        route.fulfill(json={"published": False, "cookies": [], "optional_categories": []})
    elif path in ["/quick-scan/accesses", "/archive/settings", "/archive/overview"]:
        raise AssertionError("Protected area mounted for consulta: " + path)
    else:
        ns["ns"]["intercept"](route)


with sync_playwright() as p:
    browser = p.chromium.launch()
    page = browser.new_page()
    page.on("pageerror", lambda e: errors.append(e.stack))
    page.route("**/api/**", intercept)
    for destination in [
        "Digitaliza%C3%A7%C3%A3o",
        "Arquivo+%E2%80%94+Configura%C3%A7%C3%B5es",
        "__invalida__",
    ]:
        page.goto("http://127.0.0.1:5174/entrar?pagina=" + destination)
        expect(page).to_have_url(re.compile(r"pagina=Dashboard"))
        expect(
            page.get_by_role("heading", name="Visão geral do negócio", exact=True)
        ).to_be_visible()
    page.goto("http://127.0.0.1:5174/entrar?pagina=Tesouraria")
    expect(page).to_have_url(re.compile(r"pagina=Folha\+de\+caixa"))
    expect(page.get_by_role("navigation", name="Navegação de Tesouraria")).to_be_visible()
    assert not errors, errors
    print(
        "PASS: invalid URL fallback, module alias, role guard before mounting/caching protected areas"
    )
    browser.close()
