"""Etiquetas de preço: definições da empresa (folha, dimensões, conteúdo e logótipo)."""

LOGO = "data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNk+M9QDwADhgGAWjR9awAAAABJRU5ErkJggg=="


def test_label_settings_default_to_a4_sheet_and_save_with_revision_and_audit(setup):
    from app.models import Audit
    from sqlalchemy import select
    client, _, factory = setup
    defaults = client.get("/api/stock/label-settings").json()
    assert (defaults["columns"], defaults["rows"], defaults["label_width"], defaults["label_height"]) == (5, 13, "3.81", "2.20")
    assert defaults["margin_top"] == "1.00" and defaults["gap_horizontal"] == "0.40" and defaults["revision"] == 0
    changed = {**defaults, "footer": "Tel: 236 023 218", "logo": LOGO, "show_code": True}
    saved = client.post("/api/stock/label-settings", json=changed)
    assert saved.status_code == 200, saved.text
    assert saved.json()["revision"] == 1 and saved.json()["footer"] == "Tel: 236 023 218" and saved.json()["logo"] == LOGO
    # A revisão impede gravar por cima de uma alteração feita entretanto.
    assert client.post("/api/stock/label-settings", json=changed).status_code == 409
    with factory() as db:
        event = db.scalar(select(Audit).where(Audit.action == "etiquetas_configuradas"))
        assert event and "base64" not in event.detail  # A imagem não vai para a auditoria.


def test_label_settings_refuse_labels_that_do_not_fit_and_invalid_logos(setup):
    client = setup[0]
    defaults = client.get("/api/stock/label-settings").json()
    too_wide = client.post("/api/stock/label-settings", json={**defaults, "columns": 6})
    assert too_wide.status_code == 422 and "não cabem" in too_wide.json()["detail"]
    assert client.post("/api/stock/label-settings", json={**defaults, "logo": "data:text/html;base64,PGI+"}).status_code == 422
    assert client.post("/api/stock/label-settings", json={**defaults, "logo": "https://exemplo.pt/logo.png"}).status_code == 422


def test_only_managers_configure_labels_but_everyone_can_read_them(setup):
    client = setup[0]
    client.post("/api/auth/logout")
    assert client.post("/api/auth/login", json={"email": "operator@test.pt", "password": "PasswordTest123!"}).status_code == 200
    defaults = client.get("/api/stock/label-settings")
    assert defaults.status_code == 200
    assert client.post("/api/stock/label-settings", json={**defaults.json(), "footer": "x"}).status_code == 403
