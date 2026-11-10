"""Rastreabilidade das operações: classificação de cada ligação, ficha lado a lado e leitura sem escrita."""

from datetime import date

import pytest
from app import operation_trace as module
from app.models import (
    ArchiveDocument,
    ArchiveLink,
    Audit,
    BusinessEntity,
    Category,
    DocumentERPLink,
    ERPDocument,
    PlannedMovement,
    Purchase,
    PurchaseAttachment,
    Store,
)
from sqlalchemy import func, select

NIF_A, NIF_B, NIF_C = "510774318", "503504564", "500100144"


@pytest.fixture
def fixed_today(monkeypatch):
    monkeypatch.setattr(module, "today", lambda: date(2026, 11, 10))


def erp(store, external, nif, number, day, total, base=None, tax=None, kind="FT"):
    return ERPDocument(store_id=store, enterprise_id="ent", external_id=external, supplier_nif=nif, supplier_name="F " + nif, document=number,
                       internal_document="VFC " + external, document_type=kind, document_date=day, amount_cents=total, base_cents=base, tax_cents=tax)


def scenario(factory):
    with factory.begin() as db:
        store = db.scalar(select(Store).where(Store.name == "Teste")).id
        rent = Category(name="Rendas")
        db.add(rent)
        db.flush()
        db.add(BusinessEntity(name="Senhorio", tax_number=NIF_A, is_supplier=True, category_id=rent.id))
        # 1) e-Fatura + ERP ligado + anexo: tudo confirmado, rubrica do fornecedor (provável), vencido por pagar.
        linked = Purchase(store_id=store, supplier_nif=NIF_A, supplier_name="Senhorio", document="FT A/1", document_type="FT", document_date="2026-10-01",
                          amount_cents=12300, tax_cents=2300, base_cents=10000, origin="efatura")
        # 2) Registo manual com total diferente do ERP: divergência.
        diverging = Purchase(store_id=store, supplier_nif=NIF_B, supplier_name="Energia", document="FT B/7", document_type="FT", document_date="2026-10-05",
                             amount_cents=5000, tax_cents=0, base_cents=5000, origin="manual")
        # 3) Sem ERP, duas propostas do arquivo: várias possibilidades.
        lonely = Purchase(store_id=store, supplier_nif=NIF_C, supplier_name="Papelaria", document="FT C/2", document_type="FT", document_date="2026-10-08",
                          amount_cents=2460, tax_cents=460, base_cents=2000, origin="ficheiro")
        db.add_all([linked, diverging, lonely])
        db.flush()
        linked_erp = erp(store, "e1", NIF_A, "FT A/1", "2026-10-01", 12300, 10000, 2300)
        db.add_all([linked_erp, erp(store, "e2", NIF_B, "FT B/7", "2026-10-05", 5100, 5100, 0), erp(store, "e3", NIF_A, "FT A/99", "2026-10-20", 7000, 7000, 0)])
        db.flush()
        db.add(DocumentERPLink(purchase_id=linked.id, erp_id=linked_erp.id, reason="Confirmado"))
        db.add(PurchaseAttachment(purchase_id=linked.id, name="fatura.pdf", mime="application/pdf", data=b"%PDF-1.4"))
        db.add(PlannedMovement(store_id=store, purchase_id=linked.id, category_id=rent.id, entity="Senhorio", document="FT A/1", due_date="2026-10-31",
                               amount_cents=12300, direction=-1))
        for index, total in enumerate((2460, 2500)):
            doc = ArchiveDocument(source_key=f"upload:{index}", origin="upload", storage="blob", storage_ref=str(index), original_name=f"c{index}.pdf",
                                  mime="application/pdf", entity_nif=NIF_C, total_cents=total, document_number="FT C/2")
            db.add(doc)
            db.flush()
            db.add(ArchiveLink(document_id=doc.id, target_kind="purchase", target_id=lonely.id, score=60, strength="provavel", status="proposta"))
        return {"store": store, "linked": linked.id, "diverging": diverging.id, "lonely": lonely.id, "rent": rent.id}


def test_operations_classify_each_link_and_list_attention_first(setup, fixed_today):
    client, _, factory = setup
    ids = scenario(factory)
    with factory() as db:
        audits = db.scalar(select(func.count()).select_from(Audit))
    data = client.get("/api/operations?start=2026-10-01&end=2026-10-31")
    assert data.status_code == 200, data.text
    body = data.json()
    rows = {r["id"]: r for r in body["rows"]}
    linked = rows[ids["linked"]]["dimensions"]
    assert linked["efatura"]["state"] == "confirmada" and linked["erp"]["state"] == "confirmada" and linked["pdf"]["state"] == "confirmada"
    assert linked["rubrica"]["state"] == "provavel" and "Rendas" in linked["rubrica"]["label"]
    assert linked["pagamento"]["state"] == "em_falta" and linked["pagamento"]["overdue"] and "vencido desde 2026-10-31" in linked["pagamento"]["label"]
    assert linked["banco"]["state"] == "nao_aplicavel"
    diverging = rows[ids["diverging"]]["dimensions"]
    assert diverging["erp"]["state"] == "divergencia" and "Total" in diverging["erp"]["label"]
    assert diverging["efatura"]["state"] == "em_falta" and diverging["rubrica"]["state"] == "em_falta"
    lonely = rows[ids["lonely"]]["dimensions"]
    assert lonely["pdf"]["state"] == "varias" and lonely["erp"]["state"] == "em_falta" and lonely["efatura"]["state"] == "confirmada"
    # Documento só no ERP (sem compra): aparece uma vez, sem duplicar o que já está ligado.
    erp_rows = [r for r in body["rows"] if r["kind"] == "erp"]
    assert [r["document"] for r in erp_rows] == ["FT A/99"] and erp_rows[0]["dimensions"]["efatura"]["state"] == "em_falta"
    assert erp_rows[0]["dimensions"]["rubrica"]["state"] == "provavel"
    # O documento só do ERP com o mesmo NIF/número de uma compra (e2 ↔ FT B/7) não aparece em duplicado.
    assert body["total"] == 4 and body["attention"] == 4
    assert all(r["attention"] for r in body["rows"][:body["attention"]])
    assert client.get("/api/operations?start=2026-10-01&end=2026-10-31&attention_only=true").json()["rows"]
    assert body["summary"]["erp"]["divergencia"] == 1
    # Só leitura: nenhuma auditoria nem ligação nova.
    with factory() as db:
        assert db.scalar(select(func.count()).select_from(Audit)) == audits
        assert db.scalar(select(func.count()).select_from(DocumentERPLink)) == 1


def test_operation_detail_side_by_side_and_permissions(setup, fixed_today):
    client, _, factory = setup
    ids = scenario(factory)
    detail = client.get(f"/api/operations/purchase/{ids['diverging']}")
    assert detail.status_code == 200, detail.text
    body = detail.json()
    fields = {r["key"]: r for r in body["comparison"]}
    assert fields["amount_cents"]["values"] == {"documento": 5000, "erp": 5100, "pdf": None} and fields["amount_cents"]["different"]
    assert not fields["document"]["different"] and not fields["supplier_nif"]["different"]
    lonely = client.get(f"/api/operations/purchase/{ids['lonely']}").json()
    assert [f["source"] for f in lonely["files"]] == ["arquivo", "arquivo"] and lonely["files"][0]["view_url"].startswith("/api/archive/documents/")
    assert {r["key"]: r for r in lonely["comparison"]}["amount_cents"]["values"]["pdf"] in (2460, 2500)
    linked = client.get(f"/api/operations/purchase/{ids['linked']}").json()
    assert linked["files"][0]["view_url"].startswith("/api/documents/attachments/")
    assert linked["rubrics"] == [{"category_id": ids["rent"], "category_name": "Rendas", "base_cents": 10000, "tax_cents": 2300}]
    assert linked["planned"][0]["due_date"] == "2026-10-31" and not linked["planned"][0]["settled"]
    with factory() as db:
        only = db.scalar(select(ERPDocument).where(ERPDocument.external_id == "e3")).id
    erp_detail = client.get(f"/api/operations/erp/{only}").json()
    assert erp_detail["kind"] == "erp" and erp_detail["rubric_source"] == "fornecedor"
    assert client.get("/api/operations/purchase/inexistente").status_code == 404
    client.post("/api/auth/logout")
    assert client.post("/api/auth/login", json={"email": "operator@test.pt", "password": "PasswordTest123!"}).status_code == 200
    assert client.get("/api/operations").status_code == 403
    assert client.get(f"/api/operations/purchase/{ids['linked']}").status_code == 403
