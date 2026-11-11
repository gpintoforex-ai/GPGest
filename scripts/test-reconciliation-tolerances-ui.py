"""Reconciliação: regras de correspondência com tolerância de valor, limites de confiança e exceções por fornecedor.

Reutiliza os dados simulados de test-reconciliation-center-ui.py. Só o PUT das regras é aceite e verificado; nenhuma
associação financeira é feita. Requer o Vite em 127.0.0.1:5174.
"""
import json
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import quote, urlsplit

from playwright.sync_api import expect, sync_playwright

ROOT = Path(__file__).resolve().parents[1]
source = (ROOT / "scripts/test-reconciliation-center-ui.py").read_text(encoding="utf-8")
ns = {"__file__": str(ROOT / "scripts/test-reconciliation-center-ui.py")}
exec(source.split("with sync_playwright() as p:")[0], ns)
account, data, state = ns["account"], ns["data"], ns["state"]
OUT = ROOT / ".local/reconciliation-tolerances"
OUT.mkdir(parents=True, exist_ok=True)
RULES = {"date_tolerance_days": 10, "grouped_proposals": True, "amount_tolerance_cents": 0, "amount_tolerance_percent": "0",
         "min_score": 30, "safe_score": 80, "safe_gap": 20, "suppliers": []}

with sync_playwright() as p:
    expect.set_options(timeout=20000)
    browser = p.chromium.launch()
    for theme, width in (("light", 1440), ("dark", 390)):
        errors, writes = [], []
        page = browser.new_page(viewport={"width": width, "height": 1000}, locale="pt-PT")
        page.clock.install(time=datetime(2026, 10, 12, 12, tzinfo=timezone.utc))
        page.add_init_script(f"localStorage.setItem('iazela-theme','{theme}')")
        page.on("pageerror", lambda e: errors.append(str(e)))

        def intercept(route):
            path = urlsplit(route.request.url).path.removeprefix("/api")
            if route.request.method == "PUT" and path == f'/reconciliation/accounts/{account["id"]}/rules':
                writes.append(json.loads(route.request.post_data))
                return route.fulfill(json=writes[-1])
            if route.request.method != "GET":
                writes.append(("inesperada", path))
                return route.fulfill(status=400, json={"detail": "Escrita não autorizada no teste"})
            responses = {"/bootstrap": ns["boot"], "/reconciliation": {**data, "rules": {account["id"]: RULES}},
                         f'/reconciliation/accounts/{account["id"]}/period': state, f'/reconciliation/accounts/{account["id"]}/rules': RULES,
                         f'/accounts/{account["id"]}/transactions': [], "/bank-balances": {"accounts": []}}
            if path in responses:
                return route.fulfill(json=responses[path])
            return ns["ns"]["intercept"](route) if "ns" in ns else ns["intercept"](route)

        page.route("**/api/**", intercept)
        page.goto("http://127.0.0.1:5174/?pagina=" + quote("Reconciliação automática"))
        expect(page.get_by_role("heading", name="Reconciliação bancária", exact=True)).to_be_visible()
        page.get_by_label("Conta da reconciliação", exact=True).select_option(account["id"])
        page.get_by_role("button", name="Regras, relatórios e fecho", exact=True).click()
        page.get_by_role("button", name="Regras de correspondência").click()
        dialog = page.get_by_role("dialog", name="Regras de correspondência")
        dialog.get_by_label("Tolerância de valor (€)").fill("0,50")
        dialog.get_by_label("Tolerância de valor (%)").fill("1,5")
        dialog.get_by_label("Confiança mínima para propor (30–95)").fill("70")
        dialog.get_by_label("Confiança para aceitar em lote (50–100)").fill("60")
        expect(dialog).to_contain_text("não pode ser inferior à confiança mínima")
        expect(dialog.get_by_role("button", name="Guardar regras")).to_be_disabled()
        dialog.get_by_label("Confiança para aceitar em lote (50–100)").fill("90")
        dialog.get_by_role("button", name="Acrescentar exceção").click()
        dialog.get_by_label("Fornecedor (nome ou referência)").fill("EDP Comercial")
        dialog.get_by_label("Datas (dias)", exact=True).fill("40")
        dialog.get_by_label("Nota", exact=True).fill("Débito direto")
        audit = page.evaluate((ROOT / "scripts/contrast-audit.js").read_text(encoding="utf-8"))
        assert not audit.get("failures"), audit.get("failures")
        assert page.evaluate("document.documentElement.scrollWidth <= innerWidth"), f"Overflow at {width}"
        page.screenshot(path=str(OUT / f"regras-{theme}-{width}.png"), full_page=True)
        dialog.get_by_role("button", name="Guardar regras").click()
        expect(dialog).to_be_hidden()
        assert writes == [{"date_tolerance_days": 10, "grouped_proposals": True, "amount_tolerance_cents": 50, "amount_tolerance_percent": "1.5",
                           "min_score": 70, "safe_score": 90, "safe_gap": 20,
                           "suppliers": [{"match": "EDP Comercial", "note": "Débito direto", "date_tolerance_days": 40, "amount_tolerance_cents": None}]}], writes
        assert not errors, errors
        page.close()
    browser.close()
print("PASS: regras de correspondência (tolerância de valor em € e %, limites de confiança validados, exceção por fornecedor), temas e larguras.")
