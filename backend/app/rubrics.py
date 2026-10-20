"""Local category appearance and explicit Wintouch item import/export."""

import json
from typing import Literal
from uuid import uuid4

import httpx
from fastapi import Depends, HTTPException
from pydantic import Field
from sqlalchemy import func, select, update
from sqlalchemy.orm import Session

from . import db as database
from .db import db_session
from .models import Category, CategoryIntegrationLink, ERPDocumentRubric, Movement, PlannedAllocation, PlannedMovement, BusinessEntity
from sqlalchemy.exc import IntegrityError
from .platform import cipher, validate_url
from .platform_models import Company
from .schemas import Strict
from .wintouch_sales import Reader
from .wintouch_sync import SyncError, string

Icons = Literal[
    "tag",
    "receipt",
    "wallet",
    "droplet",
    "zap",
    "phone",
    "shopping-cart",
    "landmark",
    "shield",
    "users",
    "package",
    "truck",
    "briefcase",
    "coffee",
    "fuel",
]


class CategoryEdit(Strict):
    cash_difference: bool | None = None
    name: str = Field(min_length=2, max_length=100)
    icon: Icons = "tag"
    color: str = Field(default="#245fc5", pattern=r"^#[0-9a-fA-F]{6}$")
    # Percentagem estimada de IVA dedutível (vazio = 100%); só alterada quando enviada.
    vat_deduction_percent: int | None = Field(default=None, ge=0, le=100)


class ImportItems(Strict):
    ids: list[str] = Field(min_length=1, max_length=100)
    confirmed: Literal[True]


def set_cash_difference(db, category, enabled):
    if enabled is None:
        return
    # Serialize changes across the company; only one rubric can fulfil this role.
    db.scalar(select(Category.id).order_by(Category.id).limit(1).with_for_update())
    if enabled:
        db.execute(update(Category).where(Category.cash_difference.is_(True)).values(cash_difference=False))
        db.flush()
    category.cash_difference = enabled


def cash_difference_category(db):
    category = db.scalar(select(Category).where(Category.cash_difference.is_(True)))
    if category is None:
        category = db.scalar(select(Category).where(Category.name == "Diferenças de caixa"))
        if category is None:
            category = Category(name="Diferenças de caixa", icon="wallet")
            db.add(category)
        set_cash_difference(db, category, True)
        db.flush()
    return category


class RemoveCategory(Strict):
    confirmed: Literal[True]


class SendItem(Strict):
    code: str = Field(default="", max_length=200)
    template_id: str = ""
    automatic: bool = False
    confirmed: Literal[True]


def prefix_for(db):
    company_id = db.info.get("company", {}).get("id")
    with database.next_platform_session() as core:
        company = core.get(Company, company_id) if company_id else None
        return getattr(company, "rubric_code_prefix", "RUB") if company else "RUB"


def next_code(prefix, codes):
    occupied = {code.casefold() for code in codes}
    numbers = [int(code[len(prefix):]) for code in codes if code.casefold().startswith(prefix.casefold()) and code[len(prefix):].isdigit()]
    number = max(numbers, default=0) + 1
    while f"{prefix}{number:04d}".casefold() in occupied:
        number += 1
    return f"{prefix}{number:04d}"


def connection(db):
    company_id = db.info.get("company", {}).get("id")
    with database.next_platform_session() as core:
        company = core.get(Company, company_id) if company_id else None
        if company and getattr(company, "erp_provider", "wintouch") != "wintouch":
            raise HTTPException(409, "O ERP configurado não suporta esta ligação Wintouch.")
        if not company or not company.api_active or not company.api_enterprise:
            raise HTTPException(409, "Configure e ative a ligação Wintouch da empresa.")
        try:
            return (
                company.api_enterprise,
                validate_url(company.api_url),
                cipher().decrypt(company.api_key_ciphertext.encode()).decode(),
            )
        except HTTPException:
            raise
        except Exception:
            raise HTTPException(
                503, "Não foi possível abrir a configuração da integração."
            ) from None


def remote_items(client, url, enterprise, key):
    reader = Reader(client, url, enterprise, key)
    try:
        rows = list(reader.collection("items"))
        for row in rows:
            string(row.get("ID"), 200, True)
            string(row.get("Code"), 200, True)
            string(row.get("Name"), 100, True)
        return rows
    except SyncError:
        raise HTTPException(
            502, "Não foi possível validar as rubricas devolvidas pela Wintouch."
        ) from None


def verify_item(client, url, enterprise, key, headers, link):
    payload = json.loads(link.payload)
    response = client.get(url.rstrip("/") + "/items/" + link.external_id, headers=headers)
    remote = response.json() if response.status_code == 200 else {}
    if not isinstance(remote, dict) or not remote.get("ID"):
        matches = [row for row in remote_items(client, url, enterprise, key)
                   if row["Code"] == payload.get("Code")]
        if len(matches) != 1:
            return False
        remote = matches[0]
    if not all(remote.get(k) == v for k, v in payload.items() if k != "ID"):
        return False
    # The ERP may assign its own ID on creation; persist the verified identity.
    link.external_id = string(remote.get("ID"), 200, True)
    payload["ID"] = link.external_id
    link.payload = json.dumps(payload)
    return True


def register_routes(app, current, authorize, record, serial):
    adapters = {}

    def selected_adapter(db):
        company_id = db.info.get("company", {}).get("id")
        with database.next_platform_session() as core:
            company = core.get(Company, company_id) if company_id else None
            provider = getattr(company, "erp_provider", "wintouch") if company else "none"
            active = bool(company and company.api_active)
        if not active or provider == "none":
            raise HTTPException(409, "Configure e ative um ERP nas configurações da empresa para sincronizar rubricas.")
        if provider not in adapters:
            raise HTTPException(409, "O ERP configurado ainda não tem um adaptador de sincronização de rubricas.")
        return provider, adapters[provider]

    @app.get("/api/categories/erp/settings")
    def erp_settings(user=Depends(current), db: Session = Depends(db_session)):
        authorize(user, db, roles=["administrador", "gestor"])
        provider, adapter = selected_adapter(db)
        return {"provider": provider, "name": adapter["name"], "reference_required": adapter["reference_required"]}

    @app.get("/api/categories/erp/items")
    def erp_options(user=Depends(current), db: Session = Depends(db_session)):
        _, adapter = selected_adapter(db)
        return adapter["options"](user, db)

    @app.post("/api/categories/erp/import")
    def erp_import(data: ImportItems, user=Depends(current), db: Session = Depends(db_session)):
        _, adapter = selected_adapter(db)
        return adapter["import"](data, user, db)

    @app.post("/api/categories/{category_id}/erp/send")
    def erp_send(category_id: str, data: SendItem, user=Depends(current), db: Session = Depends(db_session)):
        _, adapter = selected_adapter(db)
        return adapter["send"](category_id, data.model_copy(update={"automatic": True}), user, db)

    @app.get("/api/categories/erp/proposals")
    def proposals(user=Depends(current), db: Session = Depends(db_session)):
        provider, adapter = selected_adapter(db)
        incoming = adapter["options"](user, db)
        prefix = prefix_for(db)
        enterprise, _, _ = connection(db)
        links = {link.category_id: link for link in db.scalars(select(CategoryIntegrationLink).where(CategoryIntegrationLink.provider == provider, CategoryIntegrationLink.enterprise_id == enterprise))}
        codes = [row["code"] for row in incoming] + [link.external_code for link in links.values()]
        result = []
        for category in db.scalars(select(Category).order_by(Category.name)):
            link = links.get(category.id)
            pending = bool(link and link.status in {"a_enviar", "por_verificar"})
            exists = any(row["name"].strip().casefold() == category.name.strip().casefold() or (link and row["id"] == link.external_id) for row in incoming)
            code = link.external_code if link else next_code(prefix, codes)
            if not exists and not pending:
                codes.append(code)
            result.append({"id": category.id, "name": category.name, "code": code, "exists": exists, "pending": pending})
        return {"prefix": prefix, "rows": result}
    @app.get("/api/categories")
    def listing(user=Depends(current), db: Session = Depends(db_session)):
        company_id = db.info.get("company", {}).get("id")
        with database.next_platform_session() as core:
            co = core.get(Company, company_id) if company_id else None
            enterprise = co.api_enterprise if co else ""
            provider = getattr(co, "erp_provider", "wintouch") if co else "none"
        links = {
            r.category_id: r
            for r in db.scalars(
                select(CategoryIntegrationLink).where(
                    CategoryIntegrationLink.provider == provider,
                    CategoryIntegrationLink.enterprise_id == enterprise,
                )
            )
        }
        return [
            {**serial(c), "integration": serial(links[c.id]) if c.id in links else None}
            for c in db.scalars(select(Category).order_by(Category.name))
        ]

    @app.post("/api/categories/{category_id}/remove")
    def remove(category_id: str, data: RemoveCategory, user=Depends(current), db: Session = Depends(db_session)):
        authorize(user, db, roles=["administrador", "gestor"])
        category = db.scalar(select(Category).where(Category.id == category_id).with_for_update())
        if not category:
            raise HTTPException(404, "Rubrica inexistente.")
        for model in (Movement, PlannedMovement, PlannedAllocation, ERPDocumentRubric, BusinessEntity):
            if db.scalar(select(model.id).where(model.category_id == category_id).limit(1)):
                raise HTTPException(409, "Esta rubrica está a ser utilizada. Altere as associações antes de a remover.")
        links = list(db.scalars(select(CategoryIntegrationLink).where(CategoryIntegrationLink.category_id == category_id).with_for_update()))
        if any(link.status in {"a_enviar", "por_verificar"} for link in links):
            raise HTTPException(409, "Verifique o envio pendente ao ERP antes de remover esta rubrica.")
        record(db, user, "rubrica_removida", category.id, {"name": category.name, "scope": "local"})
        for link in links:
            db.delete(link)
        db.flush()
        db.delete(category)
        try:
            db.commit()
        except IntegrityError:
            db.rollback()
            raise HTTPException(409, "Esta rubrica está a ser utilizada e não pode ser removida.") from None
        return {"removed": True}

    @app.post("/api/categories/{category_id}")
    def edit(
        category_id: str,
        data: CategoryEdit,
        user=Depends(current),
        db: Session = Depends(db_session),
    ):
        authorize(user, db, roles=["administrador", "gestor"])
        row = db.get(Category, category_id)
        if not row:
            raise HTTPException(404, "Rubrica inexistente.")
        name = data.name.strip()
        if len(name) < 2:
            raise HTTPException(422, "Indique o nome da rubrica.")
        if db.scalar(
            select(Category.id).where(
                func.lower(Category.name) == name.lower(), Category.id != row.id
            )
        ):
            raise HTTPException(409, "Já existe uma rubrica com esse nome.")
        row.name, row.icon, row.color = name, data.icon, data.color
        if "vat_deduction_percent" in data.model_fields_set:
            row.vat_deduction_percent = data.vat_deduction_percent
        set_cash_difference(db, row, data.cash_difference)
        record(db, user, "rubrica_alterada", row.id, data.model_dump())
        db.commit()
        return serial(row)

    @app.get("/api/categories/wintouch/items")
    def options(user=Depends(current), db: Session = Depends(db_session)):
        authorize(user, db, roles=["administrador", "gestor"])
        enterprise, url, key = connection(db)
        try:
            with httpx.Client(timeout=20, follow_redirects=False) as client:
                return [
                    {
                        "id": r["ID"],
                        "code": r["Code"],
                        "name": r["Name"],
                        "nature": r.get("CashAccountDocumentsNature"),
                        "type": r.get("Type"),
                        "can_reference": r.get("Type") == "M" and r.get("CashAccountDocumentsNature") in (1, 2),
                    }
                    for r in remote_items(client, url, enterprise, key)
                ]
        except httpx.HTTPError:
            raise HTTPException(502, "Falha de comunicação com a Wintouch.") from None

    @app.post("/api/categories/wintouch/import")
    def import_items(data: ImportItems, user=Depends(current), db: Session = Depends(db_session)):
        authorize(user, db, roles=["administrador", "gestor"])
        enterprise, url, key = connection(db)
        try:
            with httpx.Client(timeout=20, follow_redirects=False) as client:
                incoming = {r["ID"]: r for r in remote_items(client, url, enterprise, key)}
        except httpx.HTTPError:
            raise HTTPException(502, "Falha de comunicação com a Wintouch.") from None
        if not set(data.ids) <= incoming.keys():
            raise HTTPException(409, "Uma rubrica selecionada já não existe na Wintouch.")
        count = 0
        for external_id in sorted(set(data.ids)):
            remote = incoming[external_id]
            link = db.scalar(
                select(CategoryIntegrationLink).where(
                    CategoryIntegrationLink.provider == "wintouch",
                    CategoryIntegrationLink.enterprise_id == enterprise,
                    CategoryIntegrationLink.external_id == external_id,
                )
            )
            if link:
                if link.status in {"a_enviar", "por_verificar"}:
                    raise HTTPException(409, "Verifique primeiro o envio pendente desta rubrica.")
                continue
            category = next(
                (
                    c
                    for c in db.scalars(select(Category).with_for_update())
                    if c.name.strip().casefold() == remote["Name"].strip().casefold()
                ),
                None,
            )
            if not category:
                category = Category(name=remote["Name"].strip())
                db.add(category)
                db.flush()
            if db.scalar(
                select(CategoryIntegrationLink.id).where(
                    CategoryIntegrationLink.provider == "wintouch",
                    CategoryIntegrationLink.enterprise_id == enterprise,
                    CategoryIntegrationLink.category_id == category.id,
                )
            ):
                raise HTTPException(
                    409,
                    "Existem rubricas externas com nomes iguais. Resolva a associação antes de importar.",
                )
            db.add(
                CategoryIntegrationLink(
                    category_id=category.id,
                    provider="wintouch",
                    enterprise_id=enterprise,
                    external_id=external_id,
                    external_code=remote["Code"],
                    status="confirmado",
                    payload="{}",
                )
            )
            record(
                db,
                user,
                "rubrica_importada",
                category.id,
                {"provider": "wintouch", "external_id": external_id},
            )
            count += 1
        db.commit()
        return {"imported": count}

    @app.post("/api/categories/{category_id}/send-wintouch")
    def send(
        category_id: str, data: SendItem, user=Depends(current), db: Session = Depends(db_session)
    ):
        authorize(user, db, roles=["administrador"])
        enterprise, url, key = connection(db)
        # One company-local lock serializes allocation, including durable pending codes.
        db.scalar(select(Category).order_by(Category.id).limit(1).with_for_update())
        category = db.scalar(select(Category).where(Category.id == category_id).with_for_update())
        if not category:
            raise HTTPException(404, "Rubrica inexistente.")
        link = db.scalar(
            select(CategoryIntegrationLink)
            .where(
                CategoryIntegrationLink.provider == "wintouch",
                CategoryIntegrationLink.enterprise_id == enterprise,
                CategoryIntegrationLink.category_id == category_id,
            )
            .with_for_update()
        )
        headers = {
            "Authorization": "ApiKey " + key,
            "x-current-enterprise": enterprise,
            "Accept": "application/json",
        }
        with httpx.Client(timeout=20, follow_redirects=False) as client:
            if link and link.status in {"a_enviar", "por_verificar"}:
                try:
                    if verify_item(client, url, enterprise, key, headers, link):
                        link.status = "confirmado"
                        record(
                            db,
                            user,
                            "rubrica_envio_verificado",
                            category.id,
                            {"external_id": link.external_id},
                        )
                        db.commit()
                        return {"status": "confirmado", "external_id": link.external_id}
                except (httpx.HTTPError, ValueError):
                    pass
                raise HTTPException(
                    409,
                    "Resultado do envio ainda por verificar. Não foi repetida a escrita na Wintouch.",
                )
            try:
                items = remote_items(client, url, enterprise, key)
            except httpx.HTTPError:
                raise HTTPException(
                    502, "Não foi possível consultar a Wintouch antes do envio."
                ) from None
            if data.automatic and not link and any(r["Name"].strip().casefold() == category.name.strip().casefold() for r in items):
                raise HTTPException(409, "Esta rubrica já existe no ERP. Não foi criado um duplicado.")
            code = data.code.strip()
            if data.automatic:
                codes = [r["Code"] for r in items] + list(db.scalars(select(CategoryIntegrationLink.external_code).where(CategoryIntegrationLink.provider == "wintouch", CategoryIntegrationLink.enterprise_id == enterprise)))
                code = link.external_code if link else next_code(prefix_for(db), codes)
            if not code or any(
                r["Code"].casefold() == code.casefold()
                and (not link or r["ID"] != link.external_id)
                for r in items
            ):
                raise HTTPException(
                    409, "Código vazio ou já existente na Wintouch. Importe a rubrica existente."
                )
            template = next((r for r in items if r["ID"] == data.template_id), None)
            if data.automatic:
                # Default cash rubric definition documented by the Wintouch Item schema.
                template = next((r for r in items if link and r["ID"] == link.external_id), {"Type": "M", "CashAccountDocumentsNature": 2})
            if (
                not template
                or template.get("Type") != "M"
                or template.get("CashAccountDocumentsNature") not in (1, 2)
            ):
                raise HTTPException(
                    422,
                    "Escolha uma rubrica de caixa existente com natureza validada como configuração de referência.",
                )
            payload = {
                "ID": link.external_id if link else str(uuid4()),
                "Code": code,
                "Name": category.name,
                "Type": template["Type"],
                "CashAccountDocumentsNature": template["CashAccountDocumentsNature"],
                "VATID": template.get("VATID"),
                "VATExemptionMotiveID": template.get("VATExemptionMotiveID"),
            }
            updating = bool(link and any(r["ID"] == link.external_id for r in items))
            if link and link.status == "confirmado" and not updating:
                raise HTTPException(
                    409, "A rubrica associada deixou de existir na Wintouch. Reveja a associação."
                )
            if not link:
                link = CategoryIntegrationLink(
                    category_id=category.id,
                    provider="wintouch",
                    enterprise_id=enterprise,
                    external_id=payload["ID"],
                    external_code=code,
                    status="a_enviar",
                )
                db.add(link)
            link.status, link.payload, link.external_code = "a_enviar", json.dumps(payload), code
            record(
                db,
                user,
                "rubrica_envio_iniciado",
                category.id,
                {"external_id": link.external_id, "code": code},
            )
            db.commit()  # Durable identity before any remote write; ambiguous writes are never repeated.
            try:
                target = url.rstrip("/") + "/items" + ("/" + link.external_id if updating else "")
                response = client.request(
                    "PUT" if updating else "POST", target, headers=headers, json=payload
                )
                if response.status_code in (400, 401, 403, 409, 422):
                    link.status = "rejeitado"
                    db.commit()
                    raise HTTPException(
                        502, f"A Wintouch rejeitou o envio (HTTP {response.status_code})."
                    )
                if not 200 <= response.status_code < 300:
                    raise ValueError("unknown response")
                if not verify_item(client, url, enterprise, key, headers, link):
                    raise ValueError("unconfirmed")
            except (httpx.HTTPError, ValueError):
                link.status = "por_verificar"
                record(
                    db,
                    user,
                    "rubrica_envio_por_verificar",
                    category.id,
                    {"external_id": link.external_id},
                )
                db.commit()
                raise HTTPException(
                    502,
                    "Não foi possível confirmar o envio. Use Verificar envio; uma repetição não cria outra rubrica.",
                ) from None
            link.status = "confirmado"
            record(
                db, user, "rubrica_envio_confirmado", category.id, {"external_id": link.external_id}
            )
            db.commit()
            return {"status": "confirmado", "external_id": link.external_id}

    adapters["wintouch"] = {"name": "Wintouch", "reference_required": False,
        "options": options, "import": import_items, "send": send}
