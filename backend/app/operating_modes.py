"""Company policy and connector capabilities, independent from demonstration mode."""
import hashlib
import json
from datetime import date
from zoneinfo import ZoneInfo
from typing import Literal

import httpx
from fastapi import Depends, HTTPException, Request
from fastapi.dependencies.utils import get_dependant
from pydantic import Field
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from . import db as database
from .db import db_session
from .models import (AccountBalanceReference, Audit, BankAccount, BusinessEntity, Category,
                     CategoryIntegrationLink, ERPDocument, IntegrationBinding, LiquiditySettlement, Movement,
                     PlannedMovement, Register, Store, WintouchCashAccount, WintouchEntity,
                     WintouchProduct, WintouchSaleRecord, WintouchSyncRun, now)
from .platform_models import Company
from .schemas import Strict

RESOURCES = {"products": "Artigos", "entities": "Clientes e fornecedores", "cash_accounts": "Contas de caixa", "sales": "Documentos, valores em aberto e movimentos", "purchase_documents": "Documentos de compras", "rubrics": "Rubricas"}
CAPABILITIES = {"read_products": "Consultar artigos", "read_stocks": "Consultar quantidades em stock", "read_entities": "Consultar clientes e fornecedores", "read_accounts": "Consultar contas de caixa", "read_documents": "Consultar documentos", "read_open_items": "Consultar valores em aberto", "read_payments": "Consultar recebimentos e pagamentos", "read_rubrics": "Consultar rubricas", "write_rubrics": "Criar rubricas", "write_settlements": "Registar pagamentos e recebimentos", "write_expenses": "Criar documentos de despesas"}
CONNECTORS = {
    "wintouch": {"name": "Wintouch", "operational": True, "capabilities": [key for key in CAPABILITIES if key not in ("write_expenses", "read_stocks")], "incremental": False, "limitations": ["Os catálogos usam snapshots idempotentes; não existe cursor incremental validado.", "A criação de documentos de despesas e a consulta de quantidades em stock não estão implementadas neste conector."]},
    "eticadata": {"name": "Eticadata", "operational": False, "capabilities": [], "limitations": ["Falta um mecanismo de acesso documentado, autenticação, contratos de documentos e liquidações, versões e confirmação de escrita."]},
    "zonesoft": {"name": "ZoneSoft", "operational": False, "capabilities": [], "limitations": ["Falta um mecanismo de acesso documentado, autenticação, contratos de documentos e liquidações, versões e confirmação de escrita."]},
}


def company_policy(company):
    provider = getattr(company, "erp_provider", "none") if company else "none"
    mode = getattr(company, "operating_mode", None) or ("integrated" if company and getattr(company, "api_active", False) and provider != "none" else "local")
    declared = CONNECTORS.get(provider, {}).get("capabilities", [])
    enabled = mode == "integrated" and bool(company and getattr(company, "api_active", False))
    writes = bool(getattr(company, "connector_writes", 1)) if company else False
    return {"mode": mode, "provider": provider, "active": enabled, "writes": writes, "authority": json.loads(getattr(company, "data_authority", None) or "{}"), "capabilities": {key: enabled and key in declared and (writes or not key.startswith("write_")) for key in CAPABILITIES}, "resources": json.loads(getattr(company, "sync_resources", None) or json.dumps(list(RESOURCES)))}


def policy(db):
    company_id = db.info.get("company", {}).get("id")
    if not company_id:
        # Legacy isolated tests and standalone initialization have no platform selection.
        return {"mode": "integrated", "provider": "wintouch", "active": True, "writes": True, "capabilities": {key: key in CONNECTORS["wintouch"]["capabilities"] for key in CAPABILITIES}, "resources": list(RESOURCES)}
    with database.next_platform_session() as core:
        return company_policy(core.get(Company, company_id))


def require_capability(db, capability, resource=None):
    config = policy(db)
    if config["mode"] != "integrated":
        raise HTTPException(409, "A empresa está em modo local. Esta operação depende de uma ligação ERP configurada em Configurações > Integrações.")
    if not config["capabilities"].get(capability):
        raise HTTPException(409, "A ligação está desativada ou o conector não permite esta operação. Os dados já importados continuam disponíveis.")
    if resource and resource not in config["resources"]:
        raise HTTPException(409, "Este recurso está desativado nas opções de sincronização da empresa.")


class Configuration(Strict):
    mode: Literal["local", "integrated"]
    provider: Literal["none", "wintouch"] = "none"
    api_active: bool = False
    allow_writes: bool = False
    api_url: str = Field(default="https://api.wintouchcloud.com/api/v1", max_length=300)
    api_enterprise: str = Field(default="", max_length=100)
    api_key: str = Field(default="", max_length=4096)
    resources: list[str] = Field(default_factory=lambda: list(RESOURCES), max_length=6)
    authority: dict[str, Literal["local", "erp"]] = Field(default_factory=dict)
    interval_minutes: int = Field(default=0, ge=0, le=1440)
    periodic_demo_confirmed: bool = False
    reason: str = Field(min_length=3, max_length=2000)


class Apply(Configuration):
    preview_token: str
    confirmed: Literal[True]
    pending_reviewed: bool = False


class LinkInput(Strict):
    internal_kind: Literal["entity", "register", "purchase"]
    internal_id: str
    external_id: str
    reason: str = Field(min_length=3, max_length=2000)


class CopyPending(Strict):
    category_id: str
    reason: str = Field(min_length=3, max_length=2000)
    confirmed: Literal[True]


class BalanceInput(Strict):
    target: str
    amount_cents: int = Field(strict=True, ge=-(10**13), le=10**13)
    fund_cents: int = Field(default=0, strict=True, ge=0, le=10**13)
    reference_date: date
    reason: str = Field(min_length=3, max_length=2000)


def pending_operations(db):
    pending = [{"kind": "rubric", "id": row.id, "state": "Pendente de envio" if row.status == "a_enviar" else "Resultado por verificar"} for row in db.scalars(select(CategoryIntegrationLink).where(CategoryIntegrationLink.status.notin_(["confirmado", "rejeitado"])))]
    finished = {r.target_id for r in db.scalars(select(Audit).where(Audit.action.in_(["liquidacao_wintouch_concluida", "liquidacao_wintouch_recusada"])))}
    for row in db.scalars(select(Audit).where(Audit.action == "liquidacao_wintouch_iniciada")):
        identifier = json.loads(row.detail).get("settlement_id")
        if identifier not in finished:
            pending.append({"kind": "settlement", "id": identifier, "record_id": row.target_id, "state": "Resultado por verificar"})
    return pending


def register_routes(app, current, authorize, scope, audit, serial):
    def get_company(db, core, lock=False):
        identifier = db.info.get("company", {}).get("id")
        query = select(Company).where(Company.id == identifier)
        row = core.scalar(query.with_for_update() if lock else query)
        if not row or not row.active:
            raise HTTPException(404, "Empresa ativa indisponível.")
        return row

    def preview(data, company, db):
        if any(resource not in RESOURCES for resource in data.resources) or any(key not in RESOURCES for key in data.authority):
            raise HTTPException(422, "Escolha recursos e origens suportados.")
        if data.mode == "integrated":
            if not CONNECTORS.get(data.provider, {}).get("operational"):
                raise HTTPException(422, "Escolha um conector operacional.")
            from .platform import validate_url
            validate_url(data.api_url)
            if data.api_active and (not data.api_enterprise.strip() or not data.api_key and not company.api_key_ciphertext):
                raise HTTPException(422, "Configure a empresa no ERP e a chave antes de ativar a ligação.")
        if data.interval_minutes and (data.interval_minutes < 15 or data.mode != "integrated" or not data.api_active):
            raise HTTPException(422, "A sincronização periódica exige ligação integrada ativa e um intervalo mínimo de 15 minutos.")
        if data.interval_minutes and company.mode == "demonstracao" and not data.periodic_demo_confirmed:
            raise HTTPException(422, "Confirme a importação periódica de dados reais nesta empresa de demonstração.")
        pending = pending_operations(db)
        running = list(db.scalars(select(WintouchSyncRun).where(WintouchSyncRun.status == "em_curso")))
        counts = {"entities": db.scalar(select(func.count()).select_from(WintouchEntity)), "products": db.scalar(select(func.count()).select_from(WintouchProduct)), "financial_records": db.scalar(select(func.count()).select_from(WintouchSaleRecord))}
        duplicates = []
        for native in db.scalars(select(BusinessEntity)):
            tax = getattr(native, "nif", None) or getattr(native, "tax_number", None)
            if tax:
                candidates = list(db.scalars(select(WintouchEntity).where(WintouchEntity.tax_number == tax)))
                if candidates:
                    duplicates.append({"id": native.id, "name": native.name, "candidates": [{"id": e.id, "name": e.name} for e in candidates], "ambiguous": len(candidates) != 1})
        digest_data = {"company": company.id, "revision": company.integration_revision, "configuration": data.model_dump(mode="json"), "pending": pending, "running": [r.id for r in running], "counts": counts}
        token = hashlib.sha256(json.dumps(digest_data, sort_keys=True).encode()).hexdigest()
        review_allowed = data.mode == "local" or not data.allow_writes and not data.interval_minutes and data.provider == company.erp_provider and data.api_enterprise.strip() == company.api_enterprise
        return {"token": token, "from": company.operating_mode, "to": data.mode, "review_allowed": review_allowed, "pending": pending, "running": [r.id for r in running], "counts": counts, "duplicates": duplicates, "effects": ["Os dados importados, anexos, observações e histórico serão preservados.", "Nenhum registo local será enviado automaticamente para o ERP.", "Os campos financeiros importados mantêm-se apenas de leitura. Os registos locais continuam editáveis.", "As sincronizações periódicas serão interrompidas no modo local." if data.mode == "local" else "A sincronização respeitará os recursos selecionados; contas e entidades são associadas explicitamente."]}

    @app.get("/api/company/integration")
    def configuration(user=Depends(current), db: Session = Depends(db_session)):
        authorize(user, db, roles=["administrador", "gestor"])
        with database.next_platform_session() as core:
            company = get_company(db, core)
            config = company_policy(company)
            settings = {"mode": company.operating_mode, "provider": company.erp_provider, "api_active": bool(company.api_active), "allow_writes": bool(company.connector_writes), "api_url": company.api_url, "api_enterprise": company.api_enterprise, "has_key": bool(company.api_key_ciphertext), "resources": config["resources"], "authority": json.loads(company.data_authority or "{}"), "interval_minutes": company.sync_interval_minutes}
        history = [{**serial(row), "rejected_count": row.read_count if row.status == "falhou" else 0} for row in db.scalars(select(WintouchSyncRun).order_by(WintouchSyncRun.started_at.desc()).limit(50))]
        mappings = [{"id": row.id, "name": row.name, "register_id": row.register_id, "store_id": row.store_id} for row in db.scalars(select(WintouchCashAccount))]
        bindings = []
        allowed_stores = set(scope(user, db))
        for row in db.scalars(select(IntegrationBinding)):
            native_model = {"planned": PlannedMovement, "register": Register}.get(row.internal_kind)
            if native_model:
                native = db.get(native_model, row.internal_id)
                if not native or native.store_id not in allowed_stores:
                    continue
            bindings.append(serial(row))
        records = []
        for kind, model in [("entity", WintouchEntity), ("product", WintouchProduct), ("account", WintouchCashAccount), ("financial", WintouchSaleRecord), ("purchase", ERPDocument)]:
            for row in db.scalars(select(model).limit(200)):
                if kind in ("financial", "purchase") and row.store_id not in scope(user, db):
                    continue
                detail = json.loads(row.data) if kind == "financial" else {}
                records.append({"company_id": db.info.get("company", {}).get("id"), "source": "ERP", "connector": "wintouch", "kind": kind, "external_id": row.external_id, "internal_id": row.id, "synced_at": row.synced_at, "version": getattr(row, "source_version", None), "sync_status": "confirmed", "name": detail.get("document") or getattr(row, "name", None) or getattr(row, "document", row.id), "record_kind": getattr(row, "kind", None), "amount_cents": detail.get("amount_cents", getattr(row, "amount_cents", None)), "due_date": detail.get("due_date"), "active": getattr(row, "active", True)})
        return {"settings": settings, "policy": config, "connectors": CONNECTORS, "capability_labels": CAPABILITIES, "resource_labels": RESOURCES, "history": history, "pending": pending_operations(db), "mappings": mappings, "bindings": bindings, "records": records, "entities": [{"id": row.id, "name": row.name} for row in db.scalars(select(BusinessEntity).where(BusinessEntity.removed.is_(False)))], "balances": [serial(row) for row in db.scalars(select(AccountBalanceReference).where(AccountBalanceReference.store_id.in_(scope(user, db))))]}

    @app.post("/api/company/integration/preview")
    def mode_preview(data: Configuration, user=Depends(current), db: Session = Depends(db_session)):
        authorize(user, db, roles=["administrador"])
        with database.next_platform_session() as core:
            return preview(data, get_company(db, core), db)

    @app.post("/api/company/integration/apply")
    def mode_apply(data: Apply, user=Depends(current), db: Session = Depends(db_session)):
        authorize(user, db, roles=["administrador"])
        from .platform import cipher, validate_url
        clean = Configuration(**data.model_dump(exclude={"confirmed", "preview_token", "pending_reviewed"}))
        with database.next_platform_session() as core:
            company = get_company(db, core, True)
            result = preview(clean, company, db)
            if result["token"] != data.preview_token:
                raise HTTPException(409, "A configuração ou as operações pendentes mudaram. Atualize a pré-visualização.")
            if result["running"] or result["pending"] and not (result["review_allowed"] and data.pending_reviewed):
                raise HTTPException(409, "Aguarde as sincronizações e reveja os envios pendentes. Resultados incertos só permitem passagem local ou ligação de leitura à mesma origem, após revisão explícita.")
            before = company.operating_mode
            company.operating_mode = data.mode
            # Preserve connector parameters when switching to local; credentials stay encrypted.
            if data.mode == "integrated":
                company.erp_provider = data.provider
                company.api_url, company.api_enterprise = validate_url(data.api_url), data.api_enterprise.strip()
                if data.api_key:
                    company.api_key_ciphertext = cipher().encrypt(data.api_key.encode()).decode()
            company.api_active = int(data.api_active) if data.mode == "integrated" else 0
            company.connector_writes = int(data.allow_writes) if data.mode == "integrated" else 0
            company.sync_resources, company.data_authority = json.dumps(data.resources), json.dumps(data.authority)
            company.sync_interval_minutes = data.interval_minutes if data.mode == "integrated" else 0
            company.integration_revision += 1
            audit(db, user, "modo_funcionamento_alterado", company.id, {"from": before, "to": data.mode, "provider": company.erp_provider, "allow_writes": bool(company.connector_writes), "resources": data.resources, "authority": data.authority, "reason": data.reason, "pending_reviewed": result["pending"] if data.pending_reviewed else []})
            db.commit()
            core.commit()
        return {"ok": True, "mode": data.mode}

    @app.post("/api/company/integration/test")
    def connection_test(user=Depends(current), db: Session = Depends(db_session)):
        authorize(user, db, roles=["administrador"])
        require_capability(db, "read_products")
        with database.next_platform_session() as core:
            company = get_company(db, core)
            from .platform import cipher, validate_url
            try:
                key = cipher().decrypt(company.api_key_ciphertext.encode()).decode()
                with httpx.Client(timeout=20, follow_redirects=False) as client:
                    response = client.get(validate_url(company.api_url) + "/products", params={"limit": 1, "skip": 0}, headers={"Authorization": "ApiKey " + key, "x-current-enterprise": company.api_enterprise, "Accept": "application/json"})
                    success = response.status_code == 200
                    if success:
                        response.json()
                result = {"success": success, "message": "Ligação de leitura confirmada." if success else f"A ligação respondeu com HTTP {response.status_code}."}
            except Exception:
                result = {"success": False, "message": "Não foi possível confirmar a ligação. Os últimos dados guardados mantêm-se acessíveis."}
        audit(db, user, "teste_ligacao_erp", db.info["company"]["id"], result)
        db.commit()
        return result

    @app.post("/api/company/integration/bind")
    def associate(data: LinkInput, user=Depends(current), db: Session = Depends(db_session)):
        authorize(user, db, roles=["administrador"])
        models = {"entity": (BusinessEntity, WintouchEntity), "register": (Register, WintouchCashAccount)}
        if data.internal_kind not in models:
            raise HTTPException(422, "Associe documentos pela conferência documental, que valida a correspondência financeira.")
        native_model, remote_model = models[data.internal_kind]
        native, remote = db.get(native_model, data.internal_id), db.get(remote_model, data.external_id)
        if not native or not remote:
            raise HTTPException(404, "Registo local ou externo indisponível.")
        company_id = db.info.get("company", {}).get("id", "")
        with database.next_platform_session() as core:
            company = get_company(db, core)
            if remote.enterprise_id != company.api_enterprise:
                raise HTTPException(404, "Registo externo indisponível nesta ligação.")
        previous = db.scalar(select(IntegrationBinding).where(IntegrationBinding.company_id == company_id, IntegrationBinding.connector == "wintouch", IntegrationBinding.external_kind == data.internal_kind, IntegrationBinding.external_id == remote.external_id))
        if previous and previous.internal_id != native.id:
            raise HTTPException(409, "O registo externo já está associado a outro registo local.")
        reverse = db.scalar(select(IntegrationBinding).where(IntegrationBinding.company_id == company_id, IntegrationBinding.connector == "wintouch", IntegrationBinding.internal_kind == data.internal_kind, IntegrationBinding.internal_id == native.id))
        if reverse and reverse.external_id != remote.external_id:
            raise HTTPException(409, "O registo local já está associado a outro registo externo.")
        if data.internal_kind == "register":
            authorize(user, db, native.store_id)
            remote.register_id, remote.store_id = native.id, native.store_id
        else:
            if native.wintouch_id and native.wintouch_id != remote.id or db.scalar(select(BusinessEntity.id).where(BusinessEntity.wintouch_id == remote.id, BusinessEntity.id != native.id)):
                raise HTTPException(409, "Existe uma associação anterior desta entidade. Reveja-a antes de associar.")
            native.wintouch_id = remote.id
        if not previous:
            previous = IntegrationBinding(company_id=company_id, connector="wintouch", external_kind=data.internal_kind, external_id=remote.external_id, internal_kind=data.internal_kind, internal_id=native.id, synced_at=remote.synced_at, external_version=remote.source_version)
            db.add(previous)
        audit(db, user, "associacao_erp_local", native.id, data.model_dump())
        db.commit()
        return {"ok": True}

    @app.post("/api/company/integration/pending/{record_id}/verify")
    def verify_payment(record_id: str, user=Depends(current), db: Session = Depends(db_session)):
        authorize(user, db, roles=["administrador", "gestor"])
        require_capability(db, "read_payments", "sales")
        row = db.get(WintouchSaleRecord, record_id)
        if not row:
            raise HTTPException(404, "Documento indisponível.")
        authorize(user, db, row.store_id)
        intent = db.scalar(select(Audit).where(Audit.action == "liquidacao_wintouch_iniciada", Audit.target_id == record_id).order_by(Audit.created_at.desc()).limit(1))
        if not intent:
            raise HTTPException(404, "Não existe envio por verificar.")
        details = json.loads(intent.detail)
        identifier = details["settlement_id"]
        sent = db.scalar(select(Audit).where(Audit.action == "liquidacao_wintouch_enviada", Audit.target_id == identifier).order_by(Audit.created_at.desc()).limit(1))
        external_id = json.loads(sent.detail)["wintouch_settlement_id"] if sent else identifier
        from .platform import cipher, validate_url
        from .wintouch_sales import Reader, amount
        with database.next_platform_session() as core:
            company = get_company(db, core)
            if row.enterprise_id != company.api_enterprise:
                raise HTTPException(409, "Este documento pertence a outra ligação. Reveja a configuração.")
            try:
                with httpx.Client(timeout=20, follow_redirects=False) as client:
                    reader = Reader(client, validate_url(company.api_url), row.enterprise_id, cipher().decrypt(company.api_key_ciphertext.encode()).decode())
                    confirmed = reader.get("settlement_documents/" + external_id)
                    if confirmed.get("ID") != external_id or amount(confirmed.get("Amount")) != details["amount_cents"]:
                        raise ValueError("Unconfirmed")
                    pending_id = row.external_id.removeprefix("pending:")
                    pending = reader.get("payablesreceivables/" + pending_id, {"embed": "CreditAccountState"})
                    remaining = abs(amount(pending["StateValue"]))
            except Exception:
                raise HTTPException(409, "O envio ainda não foi confirmado. Não foi repetido; confira o identificador na origem.") from None
        if not db.scalar(select(Audit.id).where(Audit.action == "liquidacao_wintouch_concluida", Audit.target_id == identifier)):
            audit(db, user, "liquidacao_wintouch_concluida", identifier, {"record_id": record_id, "wintouch_settlement_id": external_id, "verification": True}, row.store_id)
        values = json.loads(row.data)
        values["amount_cents"] = remaining
        row.data, row.active, row.synced_at = json.dumps(values), bool(remaining), now()
        db.commit()
        return {"ok": True, "message": "Liquidação confirmada no ERP. O envio não foi repetido."}

    @app.post("/api/company/integration/pending/{record_id}/local-copy")
    def local_copy(record_id: str, data: CopyPending, user=Depends(current), db: Session = Depends(db_session)):
        authorize(user, db, roles=["administrador"])
        if policy(db)["mode"] != "local":
            raise HTTPException(409, "A apropriação local de um pendente exige modo local explícito.")
        row = db.get(WintouchSaleRecord, record_id)
        if not row or not row.active or row.kind != "previsto":
            raise HTTPException(404, "Pendente importado indisponível.")
        authorize(user, db, row.store_id)
        if db.scalar(select(LiquiditySettlement.id).where(LiquiditySettlement.source_key == "wintouch:" + record_id)):
            raise HTTPException(409, "Confira as liquidações locais deste documento antes de o converter.")
        existing = db.scalar(select(IntegrationBinding).where(IntegrationBinding.external_kind == "pending", IntegrationBinding.external_id == row.external_id, IntegrationBinding.connector == "wintouch"))
        if existing:
            return {"id": existing.internal_id, "existing": True}
        if not db.get(Category, data.category_id):
            raise HTTPException(404, "Rubrica indisponível.")
        values = json.loads(row.data)
        if not values.get("due_date") or values["amount_cents"] <= 0:
            raise HTTPException(422, "O pendente precisa de valor e vencimento válidos.")
        native = PlannedMovement(store_id=row.store_id, category_id=data.category_id, entity=values.get("entity") or "Entidade não indicada", document=values["document"], due_date=values["due_date"], amount_cents=values["amount_cents"], direction=values["direction"])
        db.add(native); db.flush()
        db.add(IntegrationBinding(company_id=db.info.get("company", {}).get("id", ""), connector="wintouch", external_kind="pending", external_id=row.external_id, internal_kind="planned", internal_id=native.id, synced_at=row.synced_at, external_version=row.source_version, status="local_owned", local_data=json.dumps({"source_id": row.id, "financial_snapshot": values})))
        audit(db, user, "pendente_assumido_localmente", native.id, {"external_id": row.external_id, "reason": data.reason}, row.store_id)
        db.commit()
        return {"id": native.id, "existing": False}

    @app.post("/api/company/integration/balances")
    def save_balance(data: BalanceInput, user=Depends(current), db: Session = Depends(db_session)):
        authorize(user, db, roles=["administrador"])
        kind, _, identifier = data.target.partition(":")
        obj = db.get(BankAccount if kind == "bank" else Register, identifier) if kind in ("bank", "cash") else None
        if not obj:
            raise HTTPException(404, "Conta ou caixa indisponível.")
        authorize(user, db, obj.store_id)
        if data.reference_date > now().astimezone(ZoneInfo("Europe/Lisbon")).date() or data.fund_cents > max(0, data.amount_cents) or kind == "bank" and data.fund_cents or kind == "cash" and data.amount_cents < 0:
            raise HTTPException(422, "Data ou fundo de maneio inválido.")
        from .deposits import unlocked
        unlocked(db, [data.reference_date.isoformat()])
        row = db.get(AccountBalanceReference, data.target) or AccountBalanceReference(target=data.target, store_id=obj.store_id, user_id=user.id)
        db.add(row)
        row.amount_cents, row.fund_cents = data.amount_cents, data.fund_cents
        row.currency, row.reference_date, row.updated_at, row.user_id = obj.currency, data.reference_date.isoformat(), now(), user.id
        audit(db, user, "saldo_inicial_definido", data.target, data.model_dump(mode="json"), obj.store_id)
        db.commit()
        return serial(row)

    # Guard legacy connector routes too: capabilities cannot be bypassed by calling URLs directly.
    for route in app.routes:
        path = getattr(route, "path", "")
        capability, resource = None, None
        if path in ("/api/integrations/wintouch/sales",) and "POST" in getattr(route, "methods", set()) or path.endswith("/import-erp"):
            capability, resource = "read_documents", "sales"
        elif path.endswith("/settle") and "wintouch" in path or path.endswith("/settlement-options"):
            capability = "write_settlements"
        elif path.endswith("/erp-sync"):
            capability, resource = "read_documents", "purchase_documents"
        elif path in ("/api/integrations/wintouch/sync",):
            capability = "read_products"
        elif path.startswith("/api/categories/") and ("/erp/" in path or "/wintouch/" in path or path.endswith("/send-wintouch")):
            capability, resource = ("write_rubrics" if path.endswith(("/send", "/send-wintouch")) else "read_rubrics"), "rubrics"
        if capability:
            def build_guard(capability, resource):
                async def guard(request: Request, db: Session = Depends(db_session), user=Depends(current)):
                    chosen_resource = resource
                    chosen_capability = capability
                    if request.url.path.endswith("/sync"):
                        body = await request.json()
                        chosen_resource = body.get("resource")
                        chosen_capability = {"entities": "read_entities", "cash_accounts": "read_accounts", "products": "read_products"}.get(chosen_resource, capability)
                    require_capability(db, chosen_capability, chosen_resource)
                return guard
            route.dependant.dependencies.append(get_dependant(path=path, call=build_guard(capability, resource)))
