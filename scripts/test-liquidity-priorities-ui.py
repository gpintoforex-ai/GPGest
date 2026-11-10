"""Previsão de liquidez: resumo por prioridade, horizonte de 365 dias e estimativas excluídas por duplicação.

Reutiliza os dados simulados de test-liquidity-ui.py (nenhum dado real). Requer o Vite em 127.0.0.1:5174.
"""
import json
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlsplit

from playwright.sync_api import expect, sync_playwright

ROOT = Path(__file__).resolve().parents[1]
source = (ROOT / "scripts/test-liquidity-ui.py").read_text(encoding="utf-8")
ns = {"__file__": str(ROOT / "scripts/test-liquidity-ui.py")}
exec(source.split("\nwith sync_playwright() as playwright:\n")[0], ns)
OUT = ROOT / ".local/liquidity-priorities"
OUT.mkdir(parents=True, exist_ok=True)
duplicate = {**ns["event"], "key": "budget:l1:2026-10-15", "source": "budget", "priority": 4, "priority_label": "Estimativas",
             "title": "Eletricidade · orçamento 2026", "amount_cents": 21000,
             "duplicate": "Provavelmente substituída por FT E/11 (Elétrica Lusa); associe-o no orçamento",
             "exclusion": "Provavelmente substituída por FT E/11 (Elétrica Lusa); associe-o no orçamento"}
estimate = {**ns["event"], "key": "obligation:o1", "source": "obligation", "priority": 4, "priority_label": "Estimativas", "title": "Seguro multirriscos · prazo 2026-10-20",
            "amount_cents": 50000, "due_date": "2026-10-20", "expected_date": "2026-10-20"}
PRIORITIES = {"levels": [{"priority": 1, "label": "Real", "incoming_cents": 0, "outgoing_cents": 0, "count": 0},
                         {"priority": 2, "label": "Documentos por liquidar", "incoming_cents": 0, "outgoing_cents": 2000, "count": 1},
                         {"priority": 3, "label": "Compromissos", "incoming_cents": 0, "outgoing_cents": 0, "count": 0},
                         {"priority": 4, "label": "Estimativas", "incoming_cents": 0, "outgoing_cents": 50000, "count": 1}],
              "excluded_duplicates": 1, "excluded_duplicates_cents": 21000}
requests = []


class Captured:
    """Obtém a resposta simulada original de test-liquidity-ui.py para lhe acrescentar as prioridades."""

    def __init__(self, request):
        self.request, self.value = request, None

    def fulfill(self, content_type=None, body=None, status=200):
        self.value = json.loads(body)


def intercept(route):
    path = urlsplit(route.request.url).path.removeprefix("/api")
    if path != "/liquidity/forecast":
        return ns["liquidity_intercept"](route)
    requests.append(route.request.post_data_json or {})
    captured = Captured(route.request)
    ns["liquidity_intercept"](captured)
    value = captured.value
    value.update({"events": [*value["events"], estimate, duplicate], "scheduled": [*value["scheduled"], estimate],
                  "pending": [*value["pending"], duplicate], "priorities": PRIORITIES})
    return route.fulfill(content_type="application/json", body=json.dumps(value))


with sync_playwright() as p:
    browser = p.chromium.launch()
    for theme, width in (("light", 1440), ("dark", 390)):
        requests.clear()
        page = browser.new_page(viewport={"width": width, "height": 1000}, locale="pt-PT")
        page.clock.install(time=datetime(2026, 10, 13, 12, tzinfo=timezone.utc))
        page.add_init_script(f"localStorage.setItem('iazela-theme','{theme}')")
        errors = []
        page.on("pageerror", lambda e: errors.append(str(e)))
        page.route("**/api/**", intercept)
        page.goto("http://127.0.0.1:5174/?pagina=Previs%C3%A3o%20de%20liquidez")
        expect(page.get_by_role("heading", name="Previsão de liquidez", exact=True, level=1)).to_be_visible()
        priorities = page.get_by_role("region", name="Origem dos valores por prioridade")
        expect(priorities).to_contain_text("2. Documentos por liquidar")
        expect(priorities).to_contain_text("4. Estimativas")
        expect(priorities).to_contain_text("1 estimativa(s)")
        with page.expect_request(lambda r: "/liquidity/forecast" in r.url and '"end": "2027-10-12"' in (r.post_data or "").replace('":"', '": "')):
            page.get_by_label("Horizonte").select_option("365")  # A previsão recarrega ao mudar o horizonte.
        audit = page.evaluate((ROOT / "scripts/contrast-audit.js").read_text(encoding="utf-8"))
        assert not audit.get("failures"), audit.get("failures")
        assert page.evaluate("document.documentElement.scrollWidth <= innerWidth"), f"Overflow at {width}"
        page.screenshot(path=str(OUT / f"resumo-{theme}-{width}.png"), full_page=True)
        page.get_by_role("tab", name="Pendências", exact=True).click()
        expect(page.get_by_text("Provavelmente substituída por FT E/11 (Elétrica Lusa); associe-o no orçamento", exact=True)).to_be_visible()
        expect(page.get_by_text("Orçamento (estimativa) · Estimativas", exact=False).first).to_be_visible()
        assert not errors, errors
        page.close()
    browser.close()
print("PASS: previsão de liquidez (resumo por prioridade, horizonte de 365 dias, estimativas excluídas por duplicação com motivo), temas e larguras.")
