"""Entidades (clientes e fornecedores) locais e importadas do ERP, e consulta VIES por NIF."""

import json
import re

import httpx
from fastapi import APIRouter, Depends, HTTPException
from httpx import Client as HttpxClient
from pydantic import Field, model_validator
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from . import db as database
from .access import authorize, current, record
from .db import db_session
from .models import (
    BusinessEntity,
    CashSession,
    Category,
    CustomerOrder,
    CustomerOrderLine,
    CustomerPurchaseSimulation,
    Movement,
    SupplierOrderTerms,
    WintouchEntity,
)
from .operating_modes import policy as integration_policy
from .platform_models import Company
from .schemas import Strict

router = APIRouter()


class EntityEdit(Strict):
    category_id: str | None = None
    name: str = Field(min_length=2, max_length=300)
    tax_number: str = Field(default="", max_length=40)
    address: str = Field(default="", max_length=500)
    locality: str = Field(default="", max_length=120)
    postal_code: str = Field(default="", max_length=20)
    email: str = Field(default="", max_length=200)
    phone: str = Field(default="", max_length=60)
    is_customer: bool
    is_supplier: bool
    active: bool = True
    # Prazo de pagamento habitual em dias (informação local, também em entidades do ERP).
    payment_days: int | None = Field(default=None, ge=0, le=365)

    @model_validator(mode="after")
    def validate_entity(self):
        self.name = self.name.strip()
        self.tax_number = self.tax_number.strip()
        self.address = self.address.strip()
        self.locality = self.locality.strip()
        self.postal_code = self.postal_code.strip()
        self.email = self.email.strip().lower()
        if len(self.name) < 2 or not (self.is_customer or self.is_supplier):
            raise ValueError("Indique nome e tipo de entidade.")
        if self.email and ("@" not in self.email or any(c.isspace() for c in self.email)):
            raise ValueError("Email inválido.")
        return self


def entity_view(entity, source, key):
    return {
        "id": key,
        "source": source,
        "category_id": getattr(entity, "category_id", None),
        "payment_days": getattr(entity, "payment_days", None),
        **{
            name: getattr(entity, name, "")
            for name in (
                "name",
                "tax_number",
                "address",
                "locality",
                "postal_code",
                "email",
                "phone",
                "is_customer",
                "is_supplier",
                "active",
            )
        },
    }


def annotate_entity_movement_history(entities, db):
    if not entities:
        return
    entity_names = {item["name"].strip().casefold() for item in entities if item["name"].strip()}
    movement_names = set()
    if entity_names:
        movement_names = {
            name.strip().casefold()
            for name in db.scalars(
                select(Movement.entity).where(
                    func.lower(func.trim(Movement.entity)).in_(entity_names),
                    Movement.entity != "",
                )
            )
            if name
        }
    referenced_ids = set()
    order_entities = set(
        db.scalars(select(CustomerOrder.customer_id).where(CustomerOrder.customer_id.is_not(None)))
    )
    order_entities.update(
        db.scalars(
            select(CustomerOrderLine.supplier_id).where(CustomerOrderLine.supplier_id.is_not(None))
        )
    )
    order_entities.update(db.scalars(select(CustomerPurchaseSimulation.supplier_id)))
    order_entities.update(db.scalars(select(SupplierOrderTerms.supplier_id)))
    for entity in db.scalars(select(BusinessEntity).where(BusinessEntity.id.in_(order_entities))):
        referenced_ids.update([entity.id, "local:" + entity.id])
        if entity.wintouch_id:
            referenced_ids.add("wt:" + entity.wintouch_id)
    for raw_data in db.scalars(
        select(CashSession.count_data).where(CashSession.count_data.contains('"debit_supplier"'))
    ):
        counting = json.loads(raw_data or "{}")
        supplier = counting.get("debit_supplier")
        if not isinstance(supplier, dict):
            continue
        if supplier.get("id"):
            referenced_ids.add(supplier["id"])
        if supplier.get("name"):
            movement_names.add(supplier["name"].strip().casefold())
        allocations = supplier.get("allocations", [])
        if isinstance(allocations, list):
            referenced_ids.update(
                item["id"] for item in allocations if isinstance(item, dict) and item.get("id")
            )
    for entity in entities:
        entity["has_movements"] = (
            entity["id"] in referenced_ids or entity["name"].strip().casefold() in movement_names
        )


def entity_has_movement_history(db, entity, entity_key):
    result = entity_view(entity, "", entity_key)
    annotate_entity_movement_history([result], db)
    return result["has_movements"]


def vies_address_parts(value):
    address = value.strip() if isinstance(value, str) else ""
    if address in {"", "---", "----"}:
        return "", "", ""
    address = address.replace("\r\n", "\n").replace("\r", "\n")
    lines = [line.strip() for line in address.split("\n") if line.strip()]
    for index, line in enumerate(lines):
        match = re.search(r"(?<!\d)(\d{4}-\d{3})(?!\d)", line)
        if not match:
            continue
        street_lines = lines[:index]
        street = line[: match.start()].strip(" ,;")
        if street:
            street_lines.append(street)
        locality_lines = [line[match.end() :].strip(" ,;"), *lines[index + 1 :]]
        locality = " ".join(part for part in locality_lines if part)
        return "\n".join(street_lines), locality, match.group(1)
    return "\n".join(lines), "", ""


@router.get("/api/entities/lookup")
def lookup_entity_by_nif(tax_number: str, user=Depends(current), db: Session = Depends(db_session)):
    authorize(user, db, roles=["administrador", "gestor"])
    normalized = re.sub(r"[\s.-]", "", tax_number.strip().upper())
    if normalized.startswith("PT"):
        normalized = normalized[2:]
    if not re.fullmatch(r"\d{9}", normalized):
        raise HTTPException(422, "Indique um NIF português com 9 dígitos.")
    try:
        with HttpxClient(timeout=10, follow_redirects=False) as client:
            response = client.post(
                "https://ec.europa.eu/taxation_customs/vies/rest-api/check-vat-number",
                json={"countryCode": "PT", "vatNumber": normalized},
                headers={"Accept": "application/json"},
            )
            response.raise_for_status()
            result = response.json()
    except (httpx.HTTPError, ValueError) as exc:
        raise HTTPException(502, "Não foi possível consultar o VIES. Tente novamente.") from exc
    if not isinstance(result, dict) or not isinstance(result.get("valid"), bool):
        raise HTTPException(502, "O VIES devolveu uma resposta inválida.")
    name = result.get("name", "")
    raw_address = result.get("address", "")
    if name is None:
        name = ""
    if raw_address is None:
        raw_address = ""
    if not isinstance(name, str) or not isinstance(raw_address, str):
        raise HTTPException(502, "O VIES devolveu dados de entidade inválidos.")
    address, locality, postal_code = vies_address_parts(raw_address)
    return {
        "is_valid": result["valid"],
        "name": name.strip() if name.strip() not in {"---", "----"} else "",
        "address": address,
        "locality": locality,
        "postal_code": postal_code,
    }


@router.get("/api/entities")
def list_entities(user=Depends(current), db: Session = Depends(db_session)):
    local = list(db.scalars(select(BusinessEntity).order_by(BusinessEntity.name)))
    overrides = {e.wintouch_id: e for e in local if e.wintouch_id}
    config = integration_policy(db)
    external_primary = (
        config["mode"] == "integrated"
        and config.get("authority", {}).get("entities", "erp") == "erp"
        and bool(db.info.get("company", {}).get("id"))
    )
    result = [
        entity_view(e, "GPGest", "local:" + e.id)
        for e in local
        if not e.wintouch_id and not e.removed
    ]
    with database.next_platform_session() as core:
        company_id = db.info.get("company", {}).get("id")
        company = core.get(Company, company_id) if company_id else None
        enterprise = company.api_enterprise if company else None
    if not enterprise:
        annotate_entity_movement_history(result, db)
        return sorted(result, key=lambda e: e["name"].casefold())
    for imported in db.scalars(
        select(WintouchEntity).where(WintouchEntity.enterprise_id == enterprise)
    ):
        entity = imported if external_primary else overrides.get(imported.id, imported)
        if isinstance(entity, BusinessEntity) and entity.removed:
            continue
        result.append(
            entity_view(
                entity,
                "Wintouch (edição local)" if imported.id in overrides else "Wintouch",
                "wt:" + imported.id,
            )
        )
        result[-1]["editable"] = not external_primary
        if external_primary and imported.id in overrides:
            result[-1]["category_id"] = overrides[imported.id].category_id
            result[-1]["payment_days"] = overrides[imported.id].payment_days
    annotate_entity_movement_history(result, db)
    return sorted(result, key=lambda e: e["name"].casefold())


@router.post("/api/entities")
def create_entity(data: EntityEdit, user=Depends(current), db: Session = Depends(db_session)):
    authorize(user, db, roles=["administrador", "gestor"])
    if data.category_id and not db.get(Category, data.category_id):
        raise HTTPException(422, "Rubrica não encontrada.")
    entity = BusinessEntity(**data.model_dump())
    db.add(entity)
    db.flush()
    record(db, user, "entidade_criada", entity.id, data.model_dump())
    db.commit()
    return entity_view(entity, "GPGest", "local:" + entity.id)


@router.post("/api/entities/{entity_key}")
def edit_entity(
    entity_key: str, data: EntityEdit, user=Depends(current), db: Session = Depends(db_session)
):
    authorize(user, db, roles=["administrador", "gestor"])
    if data.category_id and not db.get(Category, data.category_id):
        raise HTTPException(422, "Rubrica não encontrada.")
    source, separator, key = entity_key.partition(":")
    if not separator or source not in {"local", "wt"}:
        raise HTTPException(404, "Entidade não encontrada.")
    if source == "local":
        entity = db.get(BusinessEntity, key)
    else:
        imported = db.get(WintouchEntity, key)
        with database.next_platform_session() as core:
            company = core.get(Company, db.info["company"]["id"])
            enterprise = company.api_enterprise
        if not imported or imported.enterprise_id != enterprise:
            raise HTTPException(404, "Entidade não encontrada.")
        config = integration_policy(db)
        if (
            config["mode"] == "integrated"
            and config.get("authority", {}).get("entities", "erp") == "erp"
        ):
            if any(
                (getattr(data, field) or "") != (getattr(imported, field) or "")
                for field in (
                    "name",
                    "tax_number",
                    "email",
                    "phone",
                    "is_customer",
                    "is_supplier",
                    "active",
                )
            ):
                raise HTTPException(
                    409,
                    "Os dados principais desta entidade são geridos pelo ERP. Pode manter as informações locais associadas.",
                )
        entity = db.scalar(select(BusinessEntity).where(BusinessEntity.wintouch_id == key))
        if not entity:
            entity = BusinessEntity(wintouch_id=key, **data.model_dump())
            db.add(entity)
    if not entity or entity.removed:
        raise HTTPException(404, "Entidade não encontrada.")
    before = entity_view(entity, source, entity_key) if entity.id else {}
    for name, value in data.model_dump().items():
        setattr(entity, name, value)
    db.flush()
    record(db, user, "entidade_alterada", entity.id, {"antes": before, "depois": data.model_dump()})
    db.commit()
    return entity_view(
        entity, "GPGest" if source == "local" else "Wintouch (edição local)", entity_key
    )


@router.post("/api/entities/{entity_key}/remove")
def remove_entity(entity_key: str, user=Depends(current), db: Session = Depends(db_session)):
    authorize(user, db, roles=["administrador", "gestor"])
    source, _, key = entity_key.partition(":")
    if source == "local":
        entity = db.get(BusinessEntity, key)
    elif source == "wt":
        imported = db.get(WintouchEntity, key)
        with database.next_platform_session() as core:
            company = core.get(Company, db.info["company"]["id"])
            enterprise = company.api_enterprise
        if not imported or imported.enterprise_id != enterprise:
            raise HTTPException(404, "Entidade não encontrada.")
        config = integration_policy(db)
        if (
            config["mode"] == "integrated"
            and config.get("authority", {}).get("entities", "erp") == "erp"
        ):
            raise HTTPException(
                409,
                "A entidade pertence ao ERP. Altere o estado na origem ou escolha explicitamente a gest\u00e3o local.",
            )
        entity = db.scalar(select(BusinessEntity).where(BusinessEntity.wintouch_id == key))
        if not entity:
            entity = BusinessEntity(
                wintouch_id=key,
                name=imported.name,
                tax_number=imported.tax_number or "",
                email=imported.email or "",
                phone=imported.phone or "",
                is_customer=bool(imported.is_customer),
                is_supplier=bool(imported.is_supplier),
            )
            db.add(entity)
    else:
        raise HTTPException(404, "Entidade não encontrada.")
    if not entity or entity.removed:
        raise HTTPException(404, "Entidade não encontrada.")
    if entity_has_movement_history(db, entity, entity_key):
        entity.active = False
        db.flush()
        record(
            db,
            user,
            "entidade_inativada",
            entity.id,
            {"nome": entity.name, "origem": source, "motivo": "movimentos associados"},
        )
        db.commit()
        return {"ok": True, "action": "deactivated"}
    entity.removed = True
    entity.active = False
    db.flush()
    record(db, user, "entidade_removida", entity.id, {"nome": entity.name, "origem": source})
    db.commit()
    return {"ok": True, "action": "removed"}
