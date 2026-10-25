"""Página comum das tabelas: listagem, estados, remoção explícita e sincronização. Só fixtures."""

import json
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

from playwright.sync_api import expect, sync_playwright

fixtures = Path(__file__).with_name("test-visual-template-ui.py")
shared = {"__file__": str(fixtures)}
exec(fixtures.read_text(encoding="utf-8").split("with sync_playwright() as playwright:")[0], shared)
base_intercept = shared["intercept"]
calls = []

OPS = {
    "read": {"label": "Consultar", "supported": True, "reason": ""},
    "create": {"label": "Criar", "supported": True, "reason": ""},
    "update": {"label": "Editar", "supported": True, "reason": ""},
    "activate": {"label": "Ativar", "supported": False, "reason": "Os prazos da Wintouch não têm estado ativo/inativo."},
    "deactivate": {"label": "Desativar", "supported": False, "reason": "Os prazos da Wintouch não têm estado ativo/inativo."},
    "delete": {"label": "Eliminar", "supported": True, "reason": ""},
}
FIELDS = [
    {"name": "code", "label": "Código", "type": "text", "required": True, "max_length": 40, "minimum": None, "maximum": None, "options": [], "column": True, "create_only": False, "help": ""},
    {"name": "name", "label": "Descrição", "type": "text", "required": True, "max_length": 200, "minimum": None, "maximum": None, "options": [], "column": True, "create_only": False, "help": ""},
    {"name": "days", "label": "Dias", "type": "number", "required": False, "max_length": None, "minimum": 0, "maximum": 3650, "options": [], "column": True, "create_only": False, "help": ""},
]
PERMISSIONS = {"view": True, "create": True, "edit": True, "status": True, "remove": True, "import": True, "export": True, "erp_remove": True}
TERMS = {
    "key": "payment_terms", "title": "Prazos de pagamento", "singular": "prazo de pagamento", "description": "Prazos usados nas fichas.",
    "fields": FIELDS, "extra_columns": [{"name": "entities", "label": "Entidades"}], "code_field": "code", "description_field": "name",
    "has_active": True, "allow_create": True, "allow_remove": True, "remove_reason": "", "permissions": PERMISSIONS,
    "erp": {"integrated": True, "available": True, "reason": "", "provider": "wintouch", "operations": OPS, "managed_fields": [], "conflict_policy": "perguntar", "import_options": []},
    "local_only_reason": "",
}
BANKS = {**TERMS, "key": "banks", "title": "Bancos", "singular": "banco", "code_field": None, "extra_columns": [],
         "fields": [FIELDS[1]], "erp": {"integrated": True, "available": False, "reason": "A Wintouch desta empresa não tem bancos registados.", "operations": {}},
         "local_only_reason": "A Wintouch desta empresa não tem bancos registados."}


def row(identifier, code, name, active=True, state="sincronizado", label="Sincronizado", error="", erp_id="t1"):
    return {"id": identifier, "values": {"code": code, "name": name, "days": 30, "active": active}, "labels": {"entities": "2"},
            "code": code, "description": name, "active": active, "origin": "erp" if erp_id else "local", "erp_id": erp_id,
            "erp_code": code if erp_id else "", "sync_state": state, "sync_label": label,
            "last_sync_at": "2026-10-24T10:00:00Z" if erp_id else None, "last_error": error}


ROWS = [
    row("a", "30DIAS", "30 Dias"),
    row("b", "45DIAS", "45 Dias", state="pendente", label="Alterações pendentes"),
    row("c", "FIMMES", "Fim do mês", state="erro", label="Erro", error="O ERP recusou a operação (HTTP 400)."),
    row("d", "LOCAL", "Prazo só local", erp_id="", state="apenas_local", label="Apenas local"),
    row("e", "ANTIGO", "Prazo antigo", active=False),
]
RESULTS = [
    {"key": "d", "label": "LOCAL — Prazo só local", "action": "criar_erp", "action_label": "Criar no ERP", "ok": True, "message": "Criado no ERP e confirmado.", "retry": False},
    {"key": "b", "label": "45DIAS — 45 Dias", "action": "atualizar_erp", "action_label": "Atualizar no ERP", "ok": False, "message": "O ERP recusou a operação (HTTP 400).", "retry": True},
]
POLLS = []


def run_state(done):
    return {"id": "r1", "status": "em_curso", "total": 3, "done": done, "current": "45DIAS — 45 Dias", "ok": done, "failed": 0,
            "error": "", "started_at": "2026-10-25T21:00:00Z", "finished_at": None, "results": [], "retry": []}


FINAL = {**run_state(3), "status": "concluida", "current": "", "ok": 1, "failed": 1, "results": RESULTS, "retry": ["b"],
         "finished_at": "2026-10-25T21:00:05Z"}
PLAN = {
    "direction": "sync", "ids": [], "options": {}, "token": "t", "conflict_policy": "perguntar",
    "summary": {"criar": 1, "atualizar": 1, "desativar": 0, "remover": 0, "ignorar": 1, "conflito": 1},
    "items": [
        {"key": "d", "action": "criar_erp", "action_label": "Criar no ERP", "summary": "criar", "label": "LOCAL — Prazo só local", "reason": "Só existe na IAZELA.", "local_id": "d", "external_id": None, "changes": [], "optional": True},
        {"key": "b", "action": "atualizar_erp", "action_label": "Atualizar no ERP", "summary": "atualizar", "label": "45DIAS — 45 Dias", "reason": "", "local_id": "b", "external_id": "t2", "changes": [{"field": "name", "label": "Descrição", "local": "45 Dias", "erp": "45 dias"}]},
        {"key": "erp:t9", "action": "conflito", "action_label": "Resolver conflito", "summary": "conflito", "label": "90DIAS — 90 Dias", "reason": "Já existe na IAZELA com o mesmo código e dados diferentes.", "local_id": "x", "external_id": "t9", "changes": [{"field": "name", "label": "Descrição", "local": "Noventa", "erp": "90 Dias"}]},
        {"key": "a", "action": "ignorar", "action_label": "Ignorar", "summary": "ignorar", "label": "30DIAS — 30 Dias", "reason": "Sem alterações.", "local_id": "a", "external_id": "t1", "changes": []},
    ],
}


def tables_intercept(route):
    path = urlsplit(route.request.url).path.removeprefix("/api")
    method = route.request.method
    if not path.startswith("/tables"):
        return base_intercept(route)
    calls.append((method, path, route.request.post_data_json if method == "POST" else None))
    if path == "/tables":
        body = [TERMS, BANKS]
    elif path == "/tables/payment_terms":
        body = TERMS
    elif path == "/tables/banks":
        body = BANKS
    elif path.startswith("/tables/") and path.endswith("/records"):
        params = parse_qs(urlsplit(route.request.url).query)
        status = params.get("status", ["active"])[0]
        source = ROWS if "payment_terms" in path else [row("k1", None, "Banco Demo", erp_id="", state="apenas_local", label="Apenas local")]
        rows = [r for r in source if status == "all" or r["active"] == (status == "active")]
        body = {"rows": rows, "total": len(rows), "total_all": len(source), "page": 1, "pages": 1, "size": 25}
    elif path.endswith("/details"):
        identifier = path.split("/")[4]
        base = next((r for r in ROWS if r["id"] == identifier), ROWS[0])
        body = {**base, "dependencies": [{"label": "clientes/fornecedores que o usam", "count": 2}] if identifier == "a" else [], "managed_fields": []}
    elif path.endswith("/sync/plan"):
        body = PLAN
    elif path.endswith("/sync/running"):
        body = None
    elif path.endswith("/sync/execute"):
        body = run_state(0)
    elif "/sync/runs/" in path:
        # Em segundo plano: primeiro um registo feito (com o seguinte em curso), depois o fim.
        POLLS.append(path)
        body = run_state(1) if len(POLLS) <= 3 else FINAL
    elif path.endswith("/remove"):
        body = {"removed": False, "erp": "«LOCAL — Prazo só local» eliminado(a) no ERP."}
    else:
        body = {"id": "new"}
    route.fulfill(content_type="application/json", body=json.dumps(body))


with sync_playwright() as playwright:
    from urllib.parse import parse_qs

    browser = playwright.chromium.launch()
    page = browser.new_page(viewport={"width": 1440, "height": 1000})
    errors = []
    page.on("pageerror", lambda error: errors.append(str(error)))
    page.route("**/api/**", tables_intercept)
    page.goto("http://127.0.0.1:5174/?pagina=Prazos%20de%20pagamento")
    table = page.get_by_role("table", name="Prazos de pagamento")
    expect(table).to_be_visible()
    # Estados com texto (não só cor) e colunas comuns.
    for text in ("Sincronizado", "Alterações pendentes", "Erro", "Apenas local", "Código no ERP", "Última sincronização", "Origem"):
        expect(table.get_by_text(text, exact=True).first).to_be_visible()
    expect(table.get_by_text("Prazo antigo", exact=True)).to_have_count(0)
    page.get_by_label("Estado", exact=True).select_option("inactive")
    expect(table.get_by_text("Prazo antigo", exact=True)).to_be_visible()
    # Sem coluna Estado: o inativo é indicado por texto junto ao nome.
    expect(table.get_by_role("row", name="ANTIGO").get_by_text("Inativo", exact=True)).to_be_visible()
    expect(table.get_by_role("columnheader", name="Estado")).to_have_count(0)
    page.get_by_label("Estado", exact=True).select_option("active")
    # Capacidades do conector com motivo visível.
    page.get_by_text("Operações suportadas pelo conector", exact=True).click()
    expect(page.get_by_text("indisponível — Os prazos da Wintouch não têm estado ativo/inativo.").first).to_be_visible()

    # Último erro acessível.
    table.get_by_role("button", name="Ver erro").click()
    dialog = page.get_by_role("dialog")
    expect(dialog.get_by_text("O ERP recusou a operação (HTTP 400).")).to_be_visible()
    page.keyboard.press("Escape")

    # Remoção: dependências bloqueiam a aplicação e propõem desativar; o ERP é uma escolha explícita.
    table.get_by_role("button", name="Remover 30DIAS — 30 Dias", exact=True).click()
    dialog = page.get_by_role("dialog", name="Remover prazo de pagamento")
    expect(dialog.get_by_text("Não pode ser removido da aplicação:")).to_be_visible()
    expect(dialog.get_by_role("radio", name="Remover da aplicação")).to_be_disabled()
    expect(dialog.get_by_role("radio", name="Remover de ambos")).to_be_disabled()
    expect(dialog.get_by_role("radio", name="Remover do ERP")).to_be_enabled()
    expect(dialog.get_by_role("button", name="Desativar", exact=True)).to_be_visible()
    expect(dialog.get_by_role("button", name="Remover", exact=True)).to_be_disabled()
    dialog.get_by_role("radio", name="Remover do ERP").check()
    dialog.get_by_role("button", name="Remover do ERP", exact=True).click()
    confirm = page.get_by_role("alertdialog").or_(page.get_by_role("dialog", name="Remover do ERP?"))
    expect(confirm.get_by_text("Destino:")).to_be_visible()
    expect(confirm.get_by_text("ERP", exact=True)).to_be_visible()
    confirm.get_by_role("button", name="Remover do ERP", exact=True).click()
    expect(page.get_by_text("eliminado(a) no ERP")).to_be_visible()
    assert any(c[0] == "POST" and c[1].endswith("/remove") and c[2]["scope"] == "erp" for c in calls)

    # Sincronizar: pré-visualização com resumo, conflito por decidir e resultado por registo.
    page.get_by_role("button", name="Sincronizar", exact=True).click()
    sync = page.get_by_role("dialog", name="Sincronizar — Prazos de pagamento")
    expect(sync.get_by_text("Recebe as alterações do ERP e envia as da IAZELA", exact=False)).to_be_visible()
    expect(sync.get_by_role("list", name="Resumo")).to_contain_text("1 A criar")
    expect(sync.get_by_label("Resolução de 90DIAS — 90 Dias")).to_have_value("ignorar")
    sync.get_by_label("Resolução de 90DIAS — 90 Dias").select_option("erp")
    assert not any(c[1].endswith("/execute") for c in calls)
    # Os que só existem na IAZELA começam desmarcados; «Marcar todos» inclui-os.
    expect(sync.get_by_label("Incluir LOCAL — Prazo só local")).not_to_be_checked()
    expect(sync.get_by_text("2 de 3 marcados")).to_be_visible()
    sync.get_by_label("Marcar todos", exact=True).check()
    expect(sync.get_by_text("3 de 3 marcados")).to_be_visible()
    sync.get_by_label("Marcar todos", exact=True).uncheck()
    expect(sync.get_by_role("button", name="Confirmar sincronização (0)")).to_be_disabled()
    sync.get_by_label("Marcar todos", exact=True).check()
    page.screenshot(path=str(Path(__file__).resolve().parents[1] / ".local" / "tables-sync-select-all.png"))
    sync.get_by_label("Incluir LOCAL — Prazo só local").uncheck()
    assert sync.get_by_label("Marcar todos", exact=True).evaluate("el => el.indeterminate")
    sync.get_by_label("Marcar todos", exact=True).check()
    sync.get_by_role("button", name="Confirmar sincronização (3)").click()
    # Barra de progresso enquanto corre no servidor; pode fechar-se a janela sem interromper.
    progress = sync.get_by_role("progressbar", name="Registos sincronizados")
    expect(progress).to_have_attribute("aria-valuenow", "1")
    expect(sync.get_by_text("A sincronizar: 1 de 3")).to_be_visible()
    expect(sync.get_by_text("45DIAS — 45 Dias", exact=True)).to_be_visible()
    expect(sync.get_by_role("button", name="Fechar (continua em segundo plano)")).to_be_enabled()
    page.screenshot(path=str(Path(__file__).resolve().parents[1] / ".local" / "tables-sync-progress.png"))
    expect(sync.get_by_text("1 operação(ões) concluída(s), 1 com erro.")).to_be_visible()
    expect(sync.get_by_role("button", name="Repetir os que falharam (1)")).to_be_visible()
    execute = next(c for c in calls if c[1].endswith("/execute"))
    assert execute[2]["token"] == "t" and execute[2]["resolutions"]["erp:t9"] == "erp"
    page.keyboard.press("Escape")

    # Tabela sem integração no conector: o motivo é explicado e as ações ficam indisponíveis.
    page.goto("http://127.0.0.1:5174/?pagina=Bancos")
    expect(page.get_by_role("table", name="Bancos")).to_be_visible()
    expect(page.get_by_role("button", name="Sincronizar", exact=True)).to_be_disabled()
    expect(page.get_by_role("button", name="Importar do ERP", exact=True)).to_have_count(0)
    expect(page.get_by_text("A Wintouch desta empresa não tem bancos registados.").first).to_be_visible()

    page.set_viewport_size({"width": 390, "height": 844})
    page.goto("http://127.0.0.1:5174/?pagina=Prazos%20de%20pagamento")
    expect(page.get_by_role("table", name="Prazos de pagamento")).to_be_visible()
    assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth")
    assert not errors, errors
    browser.close()
print("Tables UI passed: states as text, filters, connector reasons, explicit removal target, sync preview and results, mobile. Fixtures only.")
