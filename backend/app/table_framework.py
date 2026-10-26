"""Comportamento comum das tabelas (Tabelas e Configurações).

Cada tabela declara uma `TableSpec`: campos, validações, permissões por operação, dependências
e, quando integrada, uma `ErpBinding` com as capacidades que o conector suporta. As rotas
`/api/tables/...` aplicam as mesmas regras a todas: listagem com pesquisa, filtros, ordenação e
paginação; criação, edição, ativação/desativação; remoção só sem dependências; sincronização com
pré-visualização obrigatória; auditoria de cada operação. Nenhuma ação afeta o ERP sem o pedido
explícito do utilizador, e um registo só fica «sincronizado» depois de o ERP o confirmar.
"""

import hashlib
import json
import logging
import re
import threading
import unicodedata
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from decimal import Decimal, InvalidOperation
from typing import Any, Literal

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import Field as PydanticField
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from . import db as database
from .access import authorize, current, record
from .db import db_session
from .models import ErpLink, Store, TableSyncSetting, WintouchSyncRun, now
from .schemas import Strict

logger = logging.getLogger(__name__)
router = APIRouter()
TABLES: dict[str, "TableSpec"] = {}

ROLES_ALL = ["administrador", "gestor", "operador"]
ROLES_MANAGE = ["administrador", "gestor"]
DEFAULT_PERMISSIONS = {
    "view": ROLES_ALL,
    "create": ROLES_MANAGE,
    "edit": ROLES_MANAGE,
    "status": ROLES_MANAGE,
    "remove": ROLES_MANAGE,
    "import": ROLES_MANAGE,
    "export": ["administrador"],
    "erp_remove": ["administrador"],
}
SYNC_STATES = {
    "apenas_local": "Apenas local",
    "sincronizado": "Sincronizado",
    "pendente": "Alterações pendentes",
    "conflito": "Conflito",
    "erro": "Erro",
}
ERP_OPERATIONS = {
    "read": "Consultar",
    "create": "Criar",
    "update": "Editar",
    "activate": "Ativar",
    "deactivate": "Desativar",
    "delete": "Eliminar",
}
ACTION_LABELS = {
    "criar_local": "Criar na IAZELA",
    "atualizar_local": "Atualizar na IAZELA",
    "ligar": "Ligar ao registo existente",
    "criar_erp": "Criar no ERP",
    "atualizar_erp": "Atualizar no ERP",
    "desativar_erp": "Desativar no ERP",
    "ativar_erp": "Ativar no ERP",
    "verificar": "Verificar escrita anterior",
    "ligar_atualizar_erp": "Ligar e atualizar no ERP",
    "conflito": "Resolver conflito",
    "ignorar": "Ignorar",
}
# Agrupamento do resumo pedido antes da execução.
SUMMARY = {
    "criar_local": "criar",
    "criar_erp": "criar",
    "atualizar_local": "atualizar",
    "atualizar_erp": "atualizar",
    "ativar_erp": "atualizar",
    "ligar": "atualizar",
    "verificar": "atualizar",
    "ligar_atualizar_erp": "atualizar",
    "desativar_erp": "desativar",
    "conflito": "conflito",
    "ignorar": "ignorar",
}


# -- Definição das tabelas ---------------------------------------------------------------


@dataclass
class TableField:
    name: str
    label: str
    # text, textarea, number, decimal, boolean, select, store, category, payment_term, email, nif, color, icon
    type: str = "text"
    required: bool = False
    max_length: int | None = None
    min_length: int | None = None
    minimum: int | None = None
    maximum: int | None = None
    options: list[tuple[str, str]] = field(default_factory=list)
    column: bool = True
    # Só editável na criação (ex.: estabelecimento de um armazém com histórico).
    create_only: bool = False
    # Obrigatório ao criar; os registos já existentes podem continuar sem o valor.
    create_required: bool = False
    # Disposição na janela: separador, secção e largura numa grelha de 6 colunas (vazio = predefinição).
    tab: str = ""
    group: str = ""
    span: int | None = None
    # Não aparece na janela (o valor é mantido e definido pelo servidor, ex.: tipo pela tabela).
    hidden: bool = False
    help: str = ""

    def describe(self):
        return {
            "name": self.name,
            "label": self.label,
            "type": self.type,
            "required": self.required,
            "max_length": self.max_length,
            "min_length": self.min_length,
            "minimum": self.minimum,
            "maximum": self.maximum,
            # Opções (valor, rótulo[, ativa]): as inativas só aparecem quando já estão escolhidas.
            "options": [
                {"value": o[0], "label": o[1], "active": o[2] if len(o) > 2 else True} for o in self.options
            ],
            "column": self.column,
            "create_only": self.create_only,
            "create_required": self.create_required,
            "tab": self.tab,
            "hidden": self.hidden,
            "group": self.group,
            "span": self.span,
            "help": self.help,
        }


@dataclass
class Context:
    db: Session
    user: Any
    spec: "TableSpec"
    _integration: dict | None = None

    @property
    def integration(self):
        if self._integration is None:
            from .operating_modes import policy

            self._integration = policy(self.db)
        return self._integration

    @property
    def company(self):
        return database.request_company(self.db)

    @property
    def company_id(self):
        return self.db.info.get("company", {}).get("id") or ""

    def audit(self, action, target, detail, store_id=None):
        record(
            self.db,
            self.user,
            action,
            str(target)[:100],
            {"tabela": self.spec.key, "empresa": self.company_id, **detail},
            store_id,
        )


class TableSpec:
    """Comportamento por omissão de uma tabela baseada num modelo SQLAlchemy."""

    key = ""
    title = ""
    singular = ""
    # Concordância das mensagens («a caixa», «o banco»).
    feminine = False
    description = ""
    model: Any = None
    fields: list[TableField] = []
    # Colunas só de leitura, preenchidas por `labels` (ex.: tipo, utilização).
    extra_columns: list[tuple[str, str]] = []
    code_field: str | None = "code"
    description_field = "name"
    has_active = True
    # Campos de escolha que a listagem oferece como filtro (ex.: natureza dos tipos de documento).
    filter_fields: tuple[str, ...] = ()
    allow_create = True
    allow_remove = True
    remove_reason = ""
    permissions: dict[str, list[str]] = {}
    erp: "ErpBinding | None" = None
    # Texto para o utilizador quando a tabela não tem integração ERP.
    local_only_reason = "Esta tabela é gerida apenas na IAZELA."
    # As operações locais delegam em funções que já registam a auditoria.
    self_audited = False

    def __init__(self):
        self.permissions = {**DEFAULT_PERMISSIONS, **self.permissions}

    def g(self, word):
        """Particípio concordante com o género da tabela: «removido» → «removida»."""
        return word[:-1] + "a" if self.feminine and word.endswith("o") else word

    # Leitura -----------------------------------------------------------------------
    def rows(self, ctx):
        return list(ctx.db.scalars(select(self.model)))

    def record_id(self, row):
        return row.id

    def get(self, ctx, record_id):
        row = ctx.db.get(self.model, record_id)
        if row is None:
            raise HTTPException(404, f"{self.singular.capitalize()} não {self.g('encontrado')}.")
        return row

    def values(self, ctx, row):
        values = {f.name: plain(getattr(row, f.name, None)) for f in self.fields}
        if self.has_active:
            values["active"] = self.is_active(row)
        return values

    def labels(self, ctx, rows):
        """Textos auxiliares por registo (ex.: nome do estabelecimento)."""
        return {}

    def store_of(self, row):
        return getattr(row, "store_id", None)

    def is_active(self, row):
        return bool(getattr(row, "active", True))

    # Permissões ------------------------------------------------------------------------
    def check(self, ctx, operation, row=None, data=None):
        authorize(ctx.user, ctx.db, roles=self.permissions[operation])
        store = (data or {}).get("store_id") or (self.store_of(row) if row is not None else None)
        if store and operation != "view":
            authorize(ctx.user, ctx.db, store)

    def allowed(self, ctx, operation):
        return ctx.user.role in self.permissions[operation]

    # Escrita ---------------------------------------------------------------------------
    def clean(self, ctx, data, row=None):
        cleaned = {}
        for f in self.fields:
            if f.create_only and row is not None:
                continue
            cleaned[f.name] = clean_value(ctx, f, data.get(f.name))
            if row is None and f.create_required and cleaned[f.name] in (None, ""):
                raise HTTPException(422, f"Preencha o campo «{f.label}» para criar o registo.")
        return cleaned

    def unique_checks(self, ctx, values, row=None):
        """Duplicados por chave da tabela (código), nunca apenas pela descrição."""
        if self.code_field and values.get(self.code_field):
            column = getattr(self.model, self.code_field)
            query = select(self.model).where(column == values[self.code_field])
            other = ctx.db.scalar(query)
            if other is not None and (row is None or self.record_id(other) != self.record_id(row)):
                raise HTTPException(409, f"Já existe um registo com o código {values[self.code_field]}.")

    def create(self, ctx, values):
        row = self.model(**values)
        ctx.db.add(row)
        ctx.db.flush()
        return row

    def update(self, ctx, row, values):
        for name, value in values.items():
            setattr(row, name, value)
        ctx.db.flush()

    def set_active(self, ctx, row, active):
        row.active = active
        ctx.db.flush()

    def blocked_reason(self, ctx, row, operation):
        """Regras próprias que impedem uma operação (ex.: meio obrigatório)."""
        return None

    def dependencies(self, ctx, row):
        return []

    def delete(self, ctx, row):
        ctx.db.delete(row)
        ctx.db.flush()

    # Descrição para a interface ----------------------------------------------------------
    def describe(self, ctx):
        erp = erp_status(ctx, self)
        return {
            "key": self.key,
            "title": self.title,
            "singular": self.singular,
            "feminine": self.feminine,
            "description": self.description,
            "fields": [f.describe() for f in self.fields],
            "extra_columns": [{"name": n, "label": label} for n, label in self.extra_columns],
            "code_field": self.code_field,
            "description_field": self.description_field,
            "has_active": self.has_active,
            "filters": list(self.filter_fields),
            "allow_create": self.allow_create,
            "allow_remove": self.allow_remove,
            "remove_reason": self.remove_reason,
            "permissions": {op: self.allowed(ctx, op) for op in self.permissions},
            "erp": erp,
            "local_only_reason": self.local_only_reason if self.erp is None else "",
        }


def register(spec):
    TABLES[spec.key] = spec
    return spec


def plain(value):
    if isinstance(value, Decimal):
        return f"{value:.2f}"
    if isinstance(value, datetime):
        return value.isoformat()
    return value


def normalized(value):
    text = unicodedata.normalize("NFD", str(value or "")).encode("ascii", "ignore").decode()
    return re.sub(r"\s+", " ", text).strip().casefold()


def clean_value(ctx, f, value):
    if f.type == "boolean":
        if value is None:
            return False
        if not isinstance(value, bool):
            raise HTTPException(422, f"{f.label}: valor inválido.")
        return value
    if value is None or (isinstance(value, str) and not value.strip()):
        if f.required:
            raise HTTPException(422, f"Preencha o campo «{f.label}».")
        return None if f.type in ("number", "decimal", "select", "store", "category", "payment_term", "payment_method") else ""
    if f.type == "number":
        if isinstance(value, bool):
            raise HTTPException(422, f"{f.label}: indique um número inteiro.")
        try:
            number = int(str(value).strip())
        except ValueError:
            raise HTTPException(422, f"{f.label}: indique um número inteiro.") from None
        if (f.minimum is not None and number < f.minimum) or (f.maximum is not None and number > f.maximum):
            raise HTTPException(422, f"{f.label}: indique um valor entre {f.minimum} e {f.maximum}.")
        return number
    if f.type == "decimal":
        try:
            number = Decimal(str(value).strip().replace(",", "."))
        except InvalidOperation:
            raise HTTPException(422, f"{f.label}: indique um número.") from None
        if number != number.quantize(Decimal("0.01")) or (
            (f.minimum is not None and number < f.minimum) or (f.maximum is not None and number > f.maximum)
        ):
            raise HTTPException(422, f"{f.label}: indique um valor entre {f.minimum} e {f.maximum}, com até 2 casas.")
        return f"{number:.2f}"
    if not isinstance(value, str):
        raise HTTPException(422, f"{f.label}: valor inválido.")
    value = value.strip()
    if f.max_length and len(value) > f.max_length:
        raise HTTPException(422, f"{f.label}: máximo de {f.max_length} caracteres.")
    if f.min_length and value and len(value) < f.min_length:
        raise HTTPException(422, f"{f.label}: indique pelo menos {f.min_length} caracteres.")
    if f.type == "select" and value not in {v for v, _ in f.options}:
        raise HTTPException(422, f"{f.label}: escolha uma opção válida.")
    if f.type == "store":
        if not ctx.db.get(Store, value):
            raise HTTPException(422, "Estabelecimento não encontrado.")
        authorize(ctx.user, ctx.db, value)
    if f.type == "email" and value and ("@" not in value or any(c.isspace() for c in value)):
        raise HTTPException(422, "Email inválido.")
    return value


# -- Ligação ao ERP ------------------------------------------------------------------------


@dataclass
class LinkInfo:
    local_id: str
    external_id: str
    external_code: str = ""
    origin: str = "erp"
    state: str = "sincronizado"
    base: dict = field(default_factory=dict)
    synced_at: datetime | None = None
    error: str = ""
    version: str | None = None
    operation: dict | None = None


class ErpLinkStore:
    """Ligações guardadas em erp_links, por tabela, ERP e empresa na API."""

    def __init__(self, binding):
        self.binding = binding

    def _query(self, ctx):
        return select(ErpLink).where(
            ErpLink.table_key == self.binding.spec.key,
            ErpLink.provider == self.binding.provider,
            ErpLink.enterprise_id == enterprise_of(ctx),
        )

    @staticmethod
    def info(row):
        return LinkInfo(
            local_id=row.local_id,
            external_id=row.external_id,
            external_code=row.external_code,
            origin=row.origin,
            state=row.state,
            base=json.loads(row.base_values or "{}"),
            synced_at=row.synced_at,
            error=row.error,
            version=row.remote_version,
            operation=json.loads(row.operation) if row.operation else None,
        )

    def all(self, ctx):
        return {row.local_id: self.info(row) for row in ctx.db.scalars(self._query(ctx))}

    def row(self, ctx, local_id):
        return ctx.db.scalar(self._query(ctx).where(ErpLink.local_id == local_id))

    def by_external(self, ctx, external_id):
        return ctx.db.scalar(self._query(ctx).where(ErpLink.external_id == external_id))

    def save(self, ctx, info):
        row = self.row(ctx, info.local_id)
        if row is None:
            row = ErpLink(
                table_key=self.binding.spec.key,
                local_id=info.local_id,
                provider=self.binding.provider,
                enterprise_id=enterprise_of(ctx),
            )
            ctx.db.add(row)
        row.external_id = info.external_id
        row.external_code = info.external_code or ""
        row.origin = info.origin
        row.state = info.state
        row.base_values = canonical_json(info.base)
        row.remote_version = info.version
        row.synced_at = info.synced_at
        row.error = info.error or ""
        row.error_at = now() if info.error else None
        row.operation = json.dumps(info.operation, ensure_ascii=False) if info.operation else ""
        ctx.db.flush()

    def delete(self, ctx, local_id):
        row = self.row(ctx, local_id)
        if row is not None:
            ctx.db.delete(row)
            ctx.db.flush()


def enterprise_of(ctx):
    company = ctx.company
    return (company.api_enterprise or "") if company else ""


def canonical_json(values):
    return json.dumps(values, ensure_ascii=False, sort_keys=True, default=str)


class ErpRejected(Exception):
    """O ERP recusou a operação: nada foi criado/alterado do lado do ERP."""


class ErpUncertain(Exception):
    """Resultado desconhecido (falha de comunicação ou resposta inesperada)."""


class ErpBinding:
    """Integração de uma tabela com um recurso do ERP. As subclasses mapeiam os campos."""

    provider = "wintouch"
    resource = ""
    synced_fields: list[str] = []
    # Campo usado para ligar registos ainda sem ligação (nunca a descrição).
    match_field: str | None = "code"
    # Chave em Integrações → recursos/autoridade; None quando a tabela não depende disso.
    authority_resource: str | None = None
    # Campos geridos exclusivamente pelo ERP quando a autoridade é do ERP.
    erp_managed: tuple[str, ...] = ()
    capabilities: dict[str, bool] = {}
    reasons: dict[str, str] = {}
    # Capacidades da política da empresa (Integrações) exigidas por operação.
    capability_keys: dict[str, str] = {}
    import_options: list[TableField] = []
    # Parâmetros do GET de um registo antes de o reescrever (ex.: moradas incluídas).
    fetch_params: dict | None = None

    def __init__(self, spec):
        self.spec = spec
        self.links = ErpLinkStore(self)

    # Mapeamento ------------------------------------------------------------------------
    def remote_rows(self, ctx, client):
        return client.list(self.resource)

    def remote_id(self, raw):
        return str(raw.get("ID") or "")

    def remote_code(self, raw):
        return str(raw.get("Code") or "")

    def remote_version(self, raw):
        return raw.get("Version")

    def canonical(self, ctx, raw):
        raise NotImplementedError

    def local_canonical(self, ctx, row):
        values = self.spec.values(ctx, row)
        return {name: values.get(name) for name in self.synced_fields}

    def create_values(self, ctx, canonical, options):
        """Valores para criar o registo local a partir do ERP."""
        return dict(canonical)

    def apply_remote(self, ctx, row, canonical):
        self.spec.update(ctx, row, {k: v for k, v in canonical.items() if k in self.synced_fields})

    def create_payload(self, ctx, values, external_id):
        raise NotImplementedError

    def update_payload(self, ctx, raw, values):
        raise NotImplementedError

    def active_payload(self, raw, active):
        raise NotImplementedError

    # Capacidades ------------------------------------------------------------------------
    def managed_fields(self, ctx):
        if not self.erp_managed or not self.authority_resource:
            return set()
        authority = (ctx.integration.get("authority") or {}).get(self.authority_resource, "erp")
        return set(self.erp_managed) if authority == "erp" else set()

    def resource_enabled(self, ctx):
        return not self.authority_resource or self.authority_resource in ctx.integration.get("resources", [])

    def cannot_export(self, ctx, values):
        """Motivo para um registo local não poder ser criado no ERP (None quando pode)."""
        if self.match_field and not values.get(self.match_field):
            return "Indique o código antes de enviar para o ERP."
        return None


def erp_status(ctx, spec):
    """O que a integração permite nesta tabela, com o motivo de cada indisponibilidade."""
    binding = spec.erp
    config = ctx.integration
    integrated = config.get("mode") == "integrated" and config.get("provider") not in (None, "none")
    if binding is None:
        return {"integrated": integrated, "available": False, "reason": spec.local_only_reason, "operations": {}}
    if not integrated:
        return {"integrated": False, "available": False, "reason": "A empresa está em modo local.", "operations": {}}
    reason = ""
    if config.get("provider") != binding.provider:
        reason = "O conector do ERP configurado não suporta esta tabela."
    elif not config.get("active"):
        reason = "A ligação ao ERP está desativada em Configurações → Integrações."
    elif not binding.resource_enabled(ctx):
        reason = "Este recurso está desativado nas opções de sincronização (Integrações)."
    operations = {}
    for op, label in ERP_OPERATIONS.items():
        supported = bool(binding.capabilities.get(op))
        why = "" if supported else binding.reasons.get(op, f"O conector não permite «{label}» nesta tabela.")
        if supported and reason:
            supported, why = False, reason
        if supported and op != "read" and not config.get("writes"):
            supported, why = False, "A escrita no ERP não está autorizada em Configurações → Integrações."
        policy_key = binding.capability_keys.get(op)
        if supported and policy_key and not (config.get("capabilities") or {}).get(policy_key):
            supported, why = False, "A capacidade não está autorizada para este conector em Configurações → Integrações."
        operations[op] = {"label": label, "supported": supported, "reason": why}
    setting = ctx.db.get(TableSyncSetting, spec.key)
    return {
        "integrated": True,
        "available": not reason,
        "reason": reason,
        "provider": binding.provider,
        "operations": operations,
        "managed_fields": sorted(binding.managed_fields(ctx)),
        "conflict_policy": setting.conflict_policy if setting else "perguntar",
        "import_options": [f.describe() for f in binding.import_options],
    }


def operation_allowed(ctx, spec, op):
    info = erp_status(ctx, spec)["operations"].get(op)
    return bool(info and info["supported"])


# -- Listagem ------------------------------------------------------------------------------


def sync_state(ctx, binding, row, link):
    if link is None:
        return "apenas_local"
    if link.state in ("conflito", "erro"):
        return link.state
    if link.state == "a_enviar":
        return "erro"
    if binding.local_canonical(ctx, row) != {k: link.base.get(k) for k in binding.synced_fields}:
        return "pendente"
    return "sincronizado"


def link_view(ctx, binding, row, link):
    state = sync_state(ctx, binding, row, link) if binding else "apenas_local"
    error = (link.error if link else "") or (
        "Escrita no ERP por confirmar. A próxima sincronização verifica o resultado sem repetir a criação."
        if link and link.state == "a_enviar"
        else ""
    )
    return {
        "origin": link.origin if link else "local",
        "erp_id": link.external_id if link else "",
        "erp_code": link.external_code if link else "",
        "sync_state": state,
        "sync_label": SYNC_STATES[state],
        "last_sync_at": link.synced_at.isoformat() if link and link.synced_at else None,
        "last_error": error,
    }


def build_rows(ctx, spec):
    rows = spec.rows(ctx)
    labels = spec.labels(ctx, rows)
    links = spec.erp.links.all(ctx) if spec.erp and enterprise_of(ctx) else {}
    result = []
    for row in rows:
        identifier = spec.record_id(row)
        values = spec.values(ctx, row)
        item = {
            "id": identifier,
            "values": values,
            "labels": labels.get(identifier, {}),
            "code": values.get(spec.code_field) if spec.code_field else "",
            "description": values.get(spec.description_field) or "",
            "active": spec.is_active(row),
            **link_view(ctx, spec.erp, row, links.get(identifier)),
        }
        result.append(item)
    return result


def table(key):
    spec = TABLES.get(key)
    if spec is None:
        raise HTTPException(404, "Tabela não encontrada.")
    return spec


def context(key, user, db):
    return Context(db=db, user=user, spec=table(key))


@router.get("/api/tables")
def list_tables(user=Depends(current), db: Session = Depends(db_session)):
    result = []
    for spec in TABLES.values():
        ctx = Context(db=db, user=user, spec=spec)
        if spec.allowed(ctx, "view"):
            result.append(spec.describe(ctx))
    return result


@router.get("/api/tables/{key}")
def describe_table(key: str, user=Depends(current), db: Session = Depends(db_session)):
    ctx = context(key, user, db)
    ctx.spec.check(ctx, "view")
    return ctx.spec.describe(ctx)


@router.get("/api/tables/{key}/records")
def list_records(
    key: str,
    q: str = "",
    status: Literal["active", "inactive", "all"] = "active",
    origin: Literal["all", "local", "erp"] = "all",
    sync: str = "all",
    store: str = "",
    sort: str = "description",
    direction: Literal["asc", "desc"] = "asc",
    page: int = 1,
    size: int = 25,
    request: Request = None,
    user=Depends(current),
    db: Session = Depends(db_session),
):
    ctx = context(key, user, db)
    spec = ctx.spec
    spec.check(ctx, "view")
    rows = build_rows(ctx, spec)
    total_all = len(rows)
    for name in spec.filter_fields:
        wanted = request.query_params.get("f_" + name) if request is not None else None
        if wanted:
            rows = [r for r in rows if str(r["values"].get(name) or "") == wanted]
    if spec.has_active and status != "all":
        rows = [r for r in rows if r["active"] == (status == "active")]
    if origin != "all":
        rows = [r for r in rows if r["origin"] == origin]
    if sync != "all":
        rows = [r for r in rows if r["sync_state"] == sync]
    if store:
        rows = [r for r in rows if r["values"].get("store_id") in (None, store)]
    if q.strip():
        needle = normalized(q)
        rows = [
            r
            for r in rows
            if needle
            in normalized(
                " ".join(
                    str(v)
                    for v in [r["code"], r["description"], r["erp_code"], *r["values"].values(), *r["labels"].values()]
                    if v is not None
                )
            )
        ]

    def sort_key(r):
        if sort in ("code", "description", "erp_code", "sync_state", "last_sync_at", "origin"):
            value = r.get(sort)
        elif sort == "active":
            value = r["active"]
        else:
            value = r["labels"].get(sort, r["values"].get(sort))
        if isinstance(value, bool | int | float):
            return (0, value, "")
        return (1, 0, normalized(value))

    rows.sort(key=sort_key, reverse=direction == "desc")
    size = min(max(size, 1), 200)
    pages = max(1, -(-len(rows) // size))
    page = min(max(page, 1), pages)
    return {
        "rows": rows[(page - 1) * size : page * size],
        "total": len(rows),
        "total_all": total_all,
        "page": page,
        "pages": pages,
        "size": size,
    }


@router.get("/api/tables/{key}/records/{record_id:path}/details")
def record_details(key: str, record_id: str, user=Depends(current), db: Session = Depends(db_session)):
    ctx = context(key, user, db)
    spec = ctx.spec
    row = spec.get(ctx, record_id)
    spec.check(ctx, "view", row)
    link = spec.erp.links.all(ctx).get(record_id) if spec.erp and enterprise_of(ctx) else None
    dependencies = [{"label": label, "count": count} for label, count in spec.dependencies(ctx, row) if count]
    blocked = {
        "app": spec.blocked_reason(ctx, row, "remove"),
        "both": spec.blocked_reason(ctx, row, "remove_both"),
    }
    return {
        "remove_blocked": blocked,
        "id": record_id,
        "values": spec.values(ctx, row),
        "labels": spec.labels(ctx, [row]).get(record_id, {}),
        "active": spec.is_active(row),
        "dependencies": dependencies,
        **link_view(ctx, spec.erp, row, link),
        "managed_fields": sorted(spec.erp.managed_fields(ctx)) if spec.erp and link else [],
    }


class RecordInput(Strict):
    values: dict[str, Any] = PydanticField(default_factory=dict)


class StatusInput(Strict):
    active: bool


class RemoveInput(Strict):
    scope: Literal["app", "erp", "both"]
    # Quando o ERP não permite eliminar, a desativação no ERP é a alternativa.
    erp_action: Literal["delete", "deactivate"] = "delete"
    confirmed: Literal[True]
    confirm_demo: bool = False


def locked_changes(ctx, spec, row, values):
    """Campos geridos pelo ERP não podem ser alterados num registo ligado."""
    if not spec.erp or not enterprise_of(ctx):
        return
    managed = spec.erp.managed_fields(ctx)
    if not managed or spec.erp.links.all(ctx).get(spec.record_id(row)) is None:
        return
    current_values = spec.values(ctx, row)
    changed = [
        f.label for f in spec.fields if f.name in managed and f.name in values and values[f.name] != current_values.get(f.name)
    ]
    if changed:
        raise HTTPException(
            409, "Campos geridos pelo ERP não podem ser alterados na IAZELA: " + ", ".join(changed) + "."
        )


@router.post("/api/tables/{key}/records")
def create_record(key: str, data: RecordInput, user=Depends(current), db: Session = Depends(db_session)):
    ctx = context(key, user, db)
    spec = ctx.spec
    if not spec.allow_create:
        raise HTTPException(409, spec.remove_reason or "Esta tabela não permite criar registos.")
    spec.check(ctx, "create", data=data.values)
    values = spec.clean(ctx, data.values)
    spec.unique_checks(ctx, values)
    try:
        row = spec.create(ctx, values)
    except IntegrityError:
        db.rollback()
        raise HTTPException(409, "Já existe um registo com estes dados.") from None
    identifier = spec.record_id(row)
    if not spec.self_audited:
        ctx.audit("tabela_registo_criado", identifier, {"valores": values, "resultado": "concluido"}, spec.store_of(row))
    db.commit()
    return {"id": identifier, "erp": push_new_record(ctx, spec, identifier)}


def push_new_record(ctx, spec, identifier):
    """Em modo integrado, um registo novo é gravado logo no ERP (mesmas regras da sincronização).

    Devolve None quando não há integração; caso contrário {"ok", "message"}. Se o envio não for
    possível ou falhar, o registo fica na IAZELA (erro visível) e segue na próxima sincronização.
    """
    if spec.erp is None:
        return None
    status = erp_status(ctx, spec)
    if not status["integrated"] or not status["available"]:
        return None
    local_only = "Ficou só na IAZELA: "
    create = status["operations"].get("create") or {}
    if not create.get("supported"):
        return {"ok": False, "message": local_only + (create.get("reason") or "o conector não cria registos nesta tabela.")}
    if not spec.allowed(ctx, "export"):
        return {"ok": False, "message": local_only + "a gravação no ERP exige um administrador (use Sincronizar)."}
    if getattr(ctx.company, "mode", "") == "demonstracao":
        return {"ok": False, "message": local_only + "na empresa de demonstração, envie para o ERP com Sincronizar."}
    try:
        from types import SimpleNamespace

        from .wintouch_catalog import erp_client

        with erp_client(ctx, write=True) as client:
            items = plan_items(ctx, spec, client, "export", [identifier], {}, policy_for(ctx, spec))
            entry = next((i for i in items if i["local_id"] == identifier), None)
            if entry is None or entry["action"] != "criar_erp":
                reason = entry["reason"] if entry else "não foi possível preparar o envio."
                return {"ok": False, "message": local_only + reason}
            outcome = run_item(ctx, spec, client, entry, SimpleNamespace(direction="export", resolutions={}, options={}))
    except HTTPException as exc:
        return {"ok": False, "message": local_only + str(exc.detail)}
    if outcome["ok"]:
        return {"ok": True, "message": outcome["message"]}
    return {"ok": False, "message": local_only + outcome["message"] + " Pode repetir com Sincronizar."}


@router.post("/api/tables/{key}/records/{record_id:path}/status")
def set_status(key: str, record_id: str, data: StatusInput, user=Depends(current), db: Session = Depends(db_session)):
    ctx = context(key, user, db)
    spec = ctx.spec
    if not spec.has_active:
        raise HTTPException(409, "Esta tabela não tem estado ativo/inativo.")
    row = spec.get(ctx, record_id)
    spec.check(ctx, "status", row)
    reason = spec.blocked_reason(ctx, row, "activate" if data.active else "deactivate")
    if reason:
        raise HTTPException(409, reason)
    before = spec.is_active(row)
    if before != data.active:
        spec.set_active(ctx, row, data.active)
        if not spec.self_audited:
            ctx.audit(
                "tabela_registo_ativado" if data.active else "tabela_registo_desativado",
                record_id,
                {"antes": before, "depois": data.active, "resultado": "concluido"},
                spec.store_of(row),
            )
        db.commit()
    return {"id": record_id, "active": data.active}


@router.post("/api/tables/{key}/records/{record_id:path}/remove")
def remove_record(key: str, record_id: str, data: RemoveInput, user=Depends(current), db: Session = Depends(db_session)):
    ctx = context(key, user, db)
    spec = ctx.spec
    row = spec.get(ctx, record_id)
    label = record_label(ctx, spec, row)
    if data.scope in ("app", "both"):
        if not spec.allow_remove:
            raise HTTPException(409, spec.remove_reason or "Esta tabela não permite remover registos.")
        spec.check(ctx, "remove", row)
        reason = spec.blocked_reason(ctx, row, "remove" if data.scope == "app" else "remove_both")
        if reason:
            raise HTTPException(409, reason)
        blocking = [f"{count} {name}" for name, count in spec.dependencies(ctx, row) if count]
        if blocking:
            raise HTTPException(
                409,
                f"Não é possível remover «{label}»: tem {', '.join(blocking)}. "
                + (f"Pode {'desativá-la' if spec.feminine else 'desativá-lo'} para impedir novas utilizações." if spec.has_active else ""),
            )
    link = spec.erp.links.all(ctx).get(record_id) if spec.erp and enterprise_of(ctx) else None
    erp_result = None
    if data.scope in ("erp", "both"):
        spec.check(ctx, "erp_remove", row)
        if link is None:
            raise HTTPException(409, f"«{label}» não está {spec.g('ligado')} a nenhum registo do ERP.")
        from .wintouch_catalog import erp_client

        mode = data.erp_action
        if not operation_allowed(ctx, spec, "delete" if mode == "delete" else "deactivate"):
            raise HTTPException(
                409, erp_status(ctx, spec)["operations"]["delete" if mode == "delete" else "deactivate"]["reason"]
            )
        with erp_client(ctx, write=True, confirm_demo=data.confirm_demo) as client:
            erp_result, handled = remove_in_erp(ctx, spec, row, link, client, mode, keep_local=data.scope == "erp")
        if handled:
            # A ligação ao ERP já tratou o lado da IAZELA (ex.: entidades).
            return {"removed": data.scope == "both", "erp": erp_result}
    if data.scope in ("app", "both"):
        store_id = spec.store_of(row)
        if link is not None:
            spec.erp.links.delete(ctx, record_id)
        spec.delete(ctx, row)
        if not spec.self_audited:
            ctx.audit(
                "tabela_registo_removido",
                record_id,
                {"registo": label, "destino": "aplicacao" if data.scope == "app" else "ambos", "resultado": "concluido"},
                store_id,
            )
        try:
            db.commit()
        except IntegrityError:
            db.rollback()
            raise HTTPException(409, f"«{label}» está a ser {spec.g('utilizado')} e não pode ser {spec.g('removido')}.") from None
    return {"removed": data.scope in ("app", "both"), "erp": erp_result}


@router.post("/api/tables/{key}/records/{record_id:path}")
def update_record(key: str, record_id: str, data: RecordInput, user=Depends(current), db: Session = Depends(db_session)):
    ctx = context(key, user, db)
    spec = ctx.spec
    row = spec.get(ctx, record_id)
    spec.check(ctx, "edit", row, data.values)
    values = spec.clean(ctx, data.values, row)
    locked_changes(ctx, spec, row, values)
    spec.unique_checks(ctx, values, row)
    before = spec.values(ctx, row)
    try:
        spec.update(ctx, row, values)
    except IntegrityError:
        db.rollback()
        raise HTTPException(409, "Já existe um registo com estes dados.") from None
    if not spec.self_audited:
        ctx.audit(
            "tabela_registo_alterado",
            record_id,
            {"antes": before, "depois": spec.values(ctx, row), "resultado": "concluido"},
            spec.store_of(row),
        )
    db.commit()
    return {"id": record_id}


def record_label(ctx, spec, row):
    values = spec.values(ctx, row)
    code = values.get(spec.code_field) if spec.code_field else ""
    name = values.get(spec.description_field) or ""
    return f"{code} — {name}" if code else str(name)


# -- Sincronização -------------------------------------------------------------------------


class SyncInput(Strict):
    direction: Literal["import", "export", "sync"]
    ids: list[str] = PydanticField(default_factory=list, max_length=500)
    options: dict[str, Any] = PydanticField(default_factory=dict)
    confirm_demo: bool = False


class ExecuteInput(SyncInput):
    token: str
    items: list[str] = PydanticField(min_length=1, max_length=2000)
    # Conflitos: "local" (prevalece a IAZELA), "erp" (prevalece o ERP) ou "ignorar".
    resolutions: dict[str, Literal["local", "erp", "ignorar"]] = PydanticField(default_factory=dict)


class SettingsInput(Strict):
    conflict_policy: Literal["perguntar", "erp", "local"]


def item(key, action, label, reason="", local_id=None, external_id=None, changes=None, raw=None, remote=None):
    return {
        "key": key,
        "action": action,
        "action_label": ACTION_LABELS[action],
        "summary": SUMMARY[action],
        "label": label,
        "reason": reason,
        "local_id": local_id,
        "external_id": external_id,
        "changes": changes or [],
        # Proposta opcional (ex.: criar no ERP um registo antigo só da IAZELA): desmarcada por omissão.
        "optional": False,
        "_raw": raw,
        "_remote": remote,
    }


def differences(spec, binding, left, right):
    labels = {f.name: f.label for f in spec.fields}
    return [
        {"field": name, "label": labels.get(name, name), "local": left.get(name), "erp": right.get(name)}
        for name in binding.synced_fields
        if left.get(name) != right.get(name)
    ]


def plan_items(ctx, spec, client, direction, ids, options, policy):
    """Compara os dois lados. Não altera nada."""
    binding = spec.erp
    remote = {}
    for raw in binding.remote_rows(ctx, client):
        external_id = binding.remote_id(raw)
        if external_id:
            remote[external_id] = raw
    links = binding.links.all(ctx)
    rows = {spec.record_id(row): row for row in spec.rows(ctx)}
    selected = set(ids)
    managed = binding.managed_fields(ctx)
    items = []
    linked_external = set()
    for local_id, link in links.items():
        linked_external.add(link.external_id)
        row = rows.get(local_id)
        if row is None or (selected and local_id not in selected):
            continue
        label = record_label(ctx, spec, row)
        local_values = binding.local_canonical(ctx, row)
        raw = remote.get(link.external_id)
        if link.state == "a_enviar" or (link.operation and link.operation.get("state") == "uncertain"):
            items.append(item(local_id, "verificar", label, "Confirma no ERP a escrita anterior sem a repetir.", local_id, link.external_id, raw=raw))
            continue
        if raw is None:
            if link.synced_at is None and link.origin == "local" and direction in ("export", "sync"):
                # Envio anterior recusado: volta a propor a criação com o mesmo identificador.
                items.append(item(local_id, "criar_erp", label, "Envio anterior não concluído.", local_id, link.external_id))
            else:
                items.append(item(local_id, "ignorar", label, "Não encontrado no ERP. O registo da IAZELA não é eliminado.", local_id, link.external_id))
            continue
        remote_values = binding.canonical(ctx, raw)
        base = {k: link.base.get(k) for k in binding.synced_fields}
        local_changed = local_values != base
        remote_changed = remote_values != base
        # Campos geridos pelo ERP: prevalece sempre o ERP.
        if managed and any(local_values.get(k) != remote_values.get(k) for k in managed):
            local_values = {**local_values, **{k: remote_values.get(k) for k in managed}}
        changes = differences(spec, binding, local_values, remote_values)
        if not changes:
            reason = "Sem alterações." if not (local_changed or remote_changed) else "Iguais nos dois lados."
            items.append(item(local_id, "ignorar", label, reason, local_id, link.external_id, raw=raw, remote=remote_values))
            continue
        if remote_changed and not local_changed:
            if direction == "export":
                items.append(item(local_id, "ignorar", label, "O ERP tem alterações por importar.", local_id, link.external_id, changes))
            else:
                items.append(item(local_id, "atualizar_local", label, "", local_id, link.external_id, changes, raw, remote_values))
            continue
        if local_changed and not remote_changed:
            if direction == "import":
                items.append(item(local_id, "ignorar", label, "Alterações locais pendentes: não são substituídas pela importação.", local_id, link.external_id, changes))
            else:
                items.append(export_item(ctx, spec, local_id, label, link, changes, raw, remote_values))
            continue
        # Alterado nos dois lados: conflito, salvo regra configurada.
        if policy == "erp" and direction != "export":
            items.append(item(local_id, "atualizar_local", label, "Conflito resolvido pela regra: prevalece o ERP.", local_id, link.external_id, changes, raw, remote_values))
        elif policy == "local" and direction != "import":
            items.append(export_item(ctx, spec, local_id, label, link, changes, raw, remote_values, "Conflito resolvido pela regra: prevalece a IAZELA."))
        else:
            items.append(item(local_id, "conflito", label, "Alterado na IAZELA e no ERP desde a última sincronização.", local_id, link.external_id, changes, raw, remote_values))
    if direction in ("import", "sync") and not selected:
        unlinked = {local_id: row for local_id, row in rows.items() if local_id not in links}
        by_key = {}
        by_name = {}
        for local_id, row in unlinked.items():
            values = spec.values(ctx, row)
            if binding.match_field and values.get(binding.match_field):
                by_key.setdefault(normalized(values[binding.match_field]), []).append(local_id)
            by_name.setdefault(normalized(values.get(spec.description_field)), []).append(local_id)
        for external_id, raw in remote.items():
            if external_id in linked_external:
                continue
            remote_values = binding.canonical(ctx, raw)
            label = f"{binding.remote_code(raw)} — {remote_values.get(spec.description_field) or ''}".strip(" —")
            key = "erp:" + external_id
            matches = by_key.get(normalized(remote_values.get(binding.match_field))) if binding.match_field else None
            if matches and len(matches) == 1:
                local_id = matches[0]
                changes = differences(spec, binding, binding.local_canonical(ctx, unlinked[local_id]), remote_values)
                if not changes:
                    items.append(item(key, "ligar", label, "Mesmo código na IAZELA e no ERP.", local_id, external_id, raw=raw, remote=remote_values))
                elif policy == "erp":
                    items.append(item(key, "atualizar_local", label, "Mesmo código; prevalece o ERP.", local_id, external_id, changes, raw, remote_values))
                else:
                    conflict = item(key, "conflito", label, "Já existe na IAZELA com o mesmo código e dados diferentes.", local_id, external_id, changes, raw, remote_values)
                    conflict["_match"] = "code"
                    items.append(conflict)
                continue
            if matches:
                items.append(item(key, "ignorar", label, "Vários registos da IAZELA com o mesmo código: resolva o duplicado.", None, external_id))
                continue
            namesakes = by_name.get(normalized(remote_values.get(spec.description_field)))
            if namesakes:
                # Mesma descrição, sem chave comum: não se liga automaticamente.
                items.append(
                    item(key, "conflito", label, "Existe na IAZELA um registo com a mesma descrição e sem código comum. Escolha «Usar o ERP» para o ligar e atualizar, ou ignore.", namesakes[0], external_id, differences(spec, binding, binding.local_canonical(ctx, unlinked[namesakes[0]]), remote_values), raw, remote_values)
                )
                continue
            items.append(item(key, "criar_local", label, "", None, external_id, raw=raw, remote=remote_values))
    if direction in ("export", "sync"):
        for local_id, row in rows.items():
            if local_id in links or (selected and local_id not in selected):
                continue
            label = record_label(ctx, spec, row)
            values = binding.local_canonical(ctx, row)
            if binding.match_field and values.get(binding.match_field):
                twin = next(
                    (r for r in remote.values() if r and normalized(binding.canonical(ctx, r).get(binding.match_field)) == normalized(values[binding.match_field])),
                    None,
                )
                if twin is not None:
                    if direction == "export":
                        key_label = next((f.label for f in spec.fields if f.name == binding.match_field), "código")
                        key_label = key_label if key_label.isupper() else key_label.lower()
                        items.append(item(local_id, "ignorar", label, f"Já existe no ERP com o mesmo {key_label}: importe para os ligar.", local_id, binding.remote_id(twin)))
                    continue
            items.append(export_create_item(ctx, spec, local_id, label, values))
    return items


def export_item(ctx, spec, local_id, label, link, changes, raw, remote_values, reason=""):
    only_active = [c["field"] for c in changes] == ["active"]
    if only_active:
        local_active = next(c["local"] for c in changes)
        op = "activate" if local_active else "deactivate"
        action = "ativar_erp" if local_active else "desativar_erp"
    else:
        op, action = "update", "atualizar_erp"
    if not operation_allowed(ctx, spec, op):
        return item(local_id, "ignorar", label, erp_status(ctx, spec)["operations"][op]["reason"], local_id, link.external_id, changes)
    return item(local_id, action, label, reason, local_id, link.external_id, changes, raw, remote_values)


def export_create_item(ctx, spec, local_id, label, values=None):
    if not operation_allowed(ctx, spec, "create"):
        return item(local_id, "ignorar", label, erp_status(ctx, spec)["operations"]["create"]["reason"], local_id)
    reason = spec.erp.cannot_export(ctx, values or {})
    if reason:
        return item(local_id, "ignorar", label, reason, local_id)
    entry = item(local_id, "criar_erp", label, "Só existe na IAZELA.", local_id)
    entry["optional"] = True
    return entry


def public(items):
    return [{k: v for k, v in i.items() if not k.startswith("_")} for i in items]


def plan_token(spec, direction, items):
    core = [(i["key"], i["action"], i["external_id"], i["changes"]) for i in items]
    return hashlib.sha256(canonical_json([spec.key, direction, core]).encode()).hexdigest()


def policy_for(ctx, spec):
    setting = ctx.db.get(TableSyncSetting, spec.key)
    return setting.conflict_policy if setting else "perguntar"


def require_sync(ctx, spec, direction):
    if spec.erp is None:
        raise HTTPException(409, spec.local_only_reason)
    status = erp_status(ctx, spec)
    if not status["available"]:
        raise HTTPException(409, status["reason"])
    if not status["operations"]["read"]["supported"]:
        raise HTTPException(409, status["operations"]["read"]["reason"])
    spec.check(ctx, "export" if direction == "export" else "import")


@router.post("/api/tables/{key}/sync/plan")
def sync_plan(key: str, data: SyncInput, user=Depends(current), db: Session = Depends(db_session)):
    ctx = context(key, user, db)
    spec = ctx.spec
    require_sync(ctx, spec, data.direction)
    from .wintouch_catalog import erp_client

    with erp_client(ctx, write=False, confirm_demo=data.confirm_demo) as client:
        items = plan_items(ctx, spec, client, data.direction, data.ids, data.options, policy_for(ctx, spec))
    summary = {name: 0 for name in ("criar", "atualizar", "desativar", "remover", "ignorar", "conflito")}
    for entry in items:
        summary[entry["summary"]] += 1
    return {
        "direction": data.direction,
        # A execução tem de repetir a mesma seleção e as mesmas opções.
        "ids": data.ids,
        "options": data.options,
        "token": plan_token(spec, data.direction, items),
        "summary": summary,
        "items": public(items),
        "conflict_policy": policy_for(ctx, spec),
    }


# Uma sincronização sem sinal de vida há mais do que isto ficou interrompida (ex.: servidor reiniciado).
STALE_RUN = timedelta(minutes=10)


def release_stale_runs(db, enterprise, resource):
    limit = now() - STALE_RUN
    for stale in db.scalars(
        select(WintouchSyncRun).where(
            WintouchSyncRun.enterprise_id == enterprise,
            WintouchSyncRun.resource == resource,
            WintouchSyncRun.status == "em_curso",
        )
    ):
        if (stale.heartbeat_at or stale.started_at) < limit:
            stale.status, stale.finished_at = "falhou", now()
            stale.error_message = "Sincronização interrompida antes de terminar. Os registos já tratados ficaram gravados."
    db.commit()


@router.post("/api/tables/{key}/sync/execute")
def sync_execute(key: str, data: ExecuteInput, user=Depends(current), db: Session = Depends(db_session)):
    """Arranca a sincronização em segundo plano e devolve logo o identificador para seguir o progresso."""
    ctx = context(key, user, db)
    spec = ctx.spec
    require_sync(ctx, spec, data.direction)
    enterprise = enterprise_of(ctx)
    resource = "tabela:" + spec.key
    release_stale_runs(db, enterprise, resource)
    run = WintouchSyncRun(
        enterprise_id=enterprise,
        resource=resource,
        status="em_curso",
        total_count=len(set(data.items)),
        current_label="A comparar com o ERP",
        heartbeat_at=now(),
    )
    db.add(run)
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        raise HTTPException(409, "Já existe uma sincronização desta tabela em curso.") from None
    # Sessão própria (mesmo esquema da empresa): a do pedido fecha quando a resposta sai.
    worker = Session(bind=db.get_bind(), expire_on_commit=False)
    worker.info.update(db.info)
    threading.Thread(
        target=run_sync, args=(worker, user.id, spec.key, run.id, data), name=f"sync-{spec.key}", daemon=True
    ).start()
    return run_status(run)


def run_sync(db, user_id, key, run_id, data):
    from .models import User
    from .wintouch_catalog import erp_client

    with db:
        run = db.get(WintouchSyncRun, run_id)
        results = []
        try:
            ctx = context(key, db.get(User, user_id), db)
            spec = ctx.spec
            with erp_client(ctx, write=False, confirm_demo=data.confirm_demo) as client:
                items = plan_items(ctx, spec, client, data.direction, data.ids, data.options, policy_for(ctx, spec))
                if plan_token(spec, data.direction, items) != data.token:
                    raise HTTPException(
                        409, "Os dados mudaram desde a pré-visualização. Reveja as alterações e confirme de novo."
                    )
                wanted = set(data.items)
                chosen = [i for i in items if i["key"] in wanted and i["action"] != "ignorar"]
                for entry in items:
                    if entry["action"] == "conflito" and entry["key"] not in wanted:
                        mark_conflict(ctx, spec, entry)
                run = db.get(WintouchSyncRun, run_id)
                run.total_count = len(chosen)
                run.current_label = chosen[0]["label"] if chosen else ""
                run.heartbeat_at = now()
                db.commit()
                for index, entry in enumerate(chosen):
                    results.append(run_item(ctx, spec, client, entry, data))
                    # Progresso visível a cada registo (o registo em si já foi gravado por run_item).
                    run = db.get(WintouchSyncRun, run_id)
                    run.done_count = index + 1
                    run.current_label = chosen[index + 1]["label"][:300] if index + 1 < len(chosen) else ""
                    run.results = canonical_json(results)
                    run.heartbeat_at = now()
                    db.commit()
            run = db.get(WintouchSyncRun, run_id)
            run.status = "concluida"
        except HTTPException as exc:
            db.rollback()
            run = db.get(WintouchSyncRun, run_id)
            run.status, run.error_message = "falhou", str(exc.detail)
        except Exception:
            logger.exception("Falha inesperada na sincronização da tabela %s", key)
            db.rollback()
            run = db.get(WintouchSyncRun, run_id)
            run.status, run.error_message = "falhou", "Falha inesperada durante a sincronização."
        run.finished_at = now()
        run.current_label = ""
        run.results = canonical_json(results)
        run.read_count = len(results)
        run.inserted_count = sum(1 for r in results if r["ok"] and r["action"] in ("criar_local", "criar_erp"))
        run.updated_count = sum(1 for r in results if r["ok"] and r["action"] not in ("criar_local", "criar_erp"))
        failed = sum(1 for r in results if not r["ok"])
        if failed and not run.error_message:
            run.error_message = f"{failed} registo(s) com erro."
        db.commit()


def run_status(run):
    results = json.loads(run.results or "[]")
    failed = [r for r in results if not r["ok"]]
    finished = run.status != "em_curso"
    return {
        "id": run.id,
        "status": run.status,
        "total": run.total_count,
        "done": run.done_count,
        "current": run.current_label,
        "ok": len(results) - len(failed),
        "failed": len(failed),
        "error": run.error_message if run.status == "falhou" else "",
        "started_at": run.started_at,
        "finished_at": run.finished_at,
        # A lista completa só no fim (o progresso é consultado a cada segundo).
        "results": results if finished else [],
        "retry": [r["key"] for r in failed if r.get("retry")] if finished else [],
    }


@router.get("/api/tables/{key}/sync/runs/{run_id}")
def sync_run(key: str, run_id: str, user=Depends(current), db: Session = Depends(db_session)):
    ctx = context(key, user, db)
    ctx.spec.check(ctx, "view")
    run = db.get(WintouchSyncRun, run_id)
    if not run or run.resource != "tabela:" + ctx.spec.key:
        raise HTTPException(404, "Sincronização não encontrada.")
    return run_status(run)


@router.get("/api/tables/{key}/sync/running")
def sync_running(key: str, user=Depends(current), db: Session = Depends(db_session)):
    """Sincronização desta tabela ainda em curso (para retomar o acompanhamento ao reabrir)."""
    ctx = context(key, user, db)
    ctx.spec.check(ctx, "view")
    run = db.scalar(
        select(WintouchSyncRun)
        .where(WintouchSyncRun.resource == "tabela:" + ctx.spec.key, WintouchSyncRun.status == "em_curso")
        .order_by(WintouchSyncRun.started_at.desc())
    )
    return run_status(run) if run and (run.heartbeat_at or run.started_at) >= now() - STALE_RUN else None


@router.post("/api/tables/{key}/settings")
def table_settings(key: str, data: SettingsInput, user=Depends(current), db: Session = Depends(db_session)):
    ctx = context(key, user, db)
    spec = ctx.spec
    authorize(user, db, roles=["administrador"])
    if spec.erp is None:
        raise HTTPException(409, spec.local_only_reason)
    row = db.get(TableSyncSetting, spec.key) or TableSyncSetting(table_key=spec.key)
    before = row.conflict_policy
    row.conflict_policy = data.conflict_policy
    db.add(row)
    ctx.audit("tabela_regra_conflitos", spec.key, {"antes": before, "depois": data.conflict_policy, "resultado": "concluido"})
    db.commit()
    return {"conflict_policy": data.conflict_policy}


# -- Execução por registo ------------------------------------------------------------------


ERP_WRITES = {
    "criar_erp": "create",
    "atualizar_erp": "update",
    "ligar_atualizar_erp": "update",
    "ativar_erp": "activate",
    "desativar_erp": "deactivate",
    "verificar": "create",
}


def run_item(ctx, spec, client, entry, data):
    action = entry["action"]
    if action in ERP_WRITES:
        refused = write_refused(ctx, spec, ERP_WRITES[action])
        if refused:
            return result(entry, False, refused)
    if action == "conflito":
        choice = data.resolutions.get(entry["key"])
        if choice is None or choice == "ignorar":
            mark_conflict(ctx, spec, entry)
            return result(entry, True, "Conflito mantido por resolver: nada foi alterado.", action="ignorar")
        if choice == "erp":
            action = "atualizar_local" if entry["local_id"] else "criar_local"
        else:
            if not operation_allowed(ctx, spec, "update"):
                return result(entry, False, erp_status(ctx, spec)["operations"]["update"]["reason"])
            refused = write_refused(ctx, spec, "update")
            if refused:
                return result(entry, False, refused)
            # Registo ainda sem ligação: liga primeiro e envia depois os valores da IAZELA.
            action = "ligar_atualizar_erp" if entry["key"].startswith("erp:") else "atualizar_erp"
    try:
        message = binding_execute(ctx, spec, client, entry, action, data.options)
        ctx.audit(
            "tabela_sincronizacao",
            entry["local_id"] or entry["external_id"],
            {"acao": action, "direcao": data.direction, "registo": entry["label"], "resultado": "concluido"},
        )
        ctx.db.commit()
        return result(entry, True, message, action=action)
    except (ErpRejected, ErpUncertain, HTTPException, IntegrityError, Exception) as exc:
        ctx.db.rollback()
        if isinstance(exc, HTTPException):
            detail = exc.detail
        elif isinstance(exc, ErpRejected | ErpUncertain):
            detail = str(exc)
        elif isinstance(exc, IntegrityError):
            detail = "Já existe um registo com estes dados na IAZELA."
        else:
            logger.exception("Falha inesperada a sincronizar %s / %s", spec.key, entry["key"])
            detail = "Falha inesperada neste registo; os restantes continuaram."
        if entry["local_id"] and spec.erp and enterprise_of(ctx):
            mark_error(ctx, spec, entry, detail, uncertain=isinstance(exc, ErpUncertain))
        ctx.audit(
            "tabela_sincronizacao",
            entry["local_id"] or entry["external_id"],
            {"acao": action, "direcao": data.direction, "registo": entry["label"], "resultado": "erro", "erro": detail},
        )
        ctx.db.commit()
        return result(entry, False, detail, action=action, retry=True)


def write_refused(ctx, spec, operation):
    """Motivo para não gravar no ERP (perfil ou capacidade), ou None quando é permitido."""
    if not spec.allowed(ctx, "export"):
        return "O seu perfil não permite gravar no ERP: só recebe alterações. Peça a um administrador para sincronizar."
    if not operation_allowed(ctx, spec, operation):
        return erp_status(ctx, spec)["operations"][operation]["reason"]
    return None


def result(entry, ok, message, action=None, retry=False):
    return {
        "key": entry["key"],
        "label": entry["label"],
        "action": action or entry["action"],
        "action_label": ACTION_LABELS.get(action or entry["action"], ""),
        "ok": ok,
        "message": message,
        "retry": retry,
    }


def mark_error(ctx, spec, entry, detail, uncertain=False):
    store = spec.erp.links
    row = store.row(ctx, entry["local_id"]) if isinstance(store, ErpLinkStore) else None
    if row is None:
        return
    row.error = str(detail)[:2000]
    row.error_at = now()
    if uncertain and row.state == "a_enviar":
        # Resultado desconhecido: a próxima sincronização verifica antes de repetir.
        operation = json.loads(row.operation) if row.operation else {}
        operation["state"] = "uncertain"
        row.operation = json.dumps(operation, ensure_ascii=False)
    else:
        # Recusado pelo ERP: nada foi escrito do lado do ERP.
        row.state, row.operation = "erro", ""
    ctx.db.flush()


def mark_conflict(ctx, spec, entry):
    """Conflito por resolver fica visível na listagem; nunca é resolvido em silêncio."""
    store = spec.erp.links
    if not entry["local_id"] or type(store) is not ErpLinkStore:
        return
    if entry["key"].startswith("erp:"):
        if entry.get("_match") != "code":
            return  # Só a descrição coincide: sem ligação automática.
        raw = entry["_raw"] or {}
        store.save(
            ctx,
            LinkInfo(
                local_id=entry["local_id"],
                external_id=entry["external_id"],
                external_code=spec.erp.remote_code(raw),
                origin="erp",
                state="conflito",
                base={},
                error="Existe no ERP com o mesmo código e dados diferentes: conflito por resolver.",
                version=spec.erp.remote_version(raw),
            ),
        )
        ctx.db.commit()
        return
    row = store.row(ctx, entry["local_id"])
    if row is not None:
        row.state, row.error, row.error_at = "conflito", "Alterado na IAZELA e no ERP: conflito por resolver.", now()
        ctx.db.commit()


def binding_execute(ctx, spec, client, entry, action, options):
    """Executa uma ação; a binding pode substituir a execução (ex.: rubricas, entidades)."""
    custom = getattr(spec.erp, "execute", None)
    if custom is not None:
        return custom(ctx, client, entry, action, options)
    return generic_execute(ctx, spec, client, entry, action, options)


def generic_execute(ctx, spec, client, entry, action, options):
    binding = spec.erp
    store = binding.links
    if action == "criar_local":
        values = binding.create_values(ctx, entry["_remote"], options)
        spec.unique_checks(ctx, values)
        row = spec.create(ctx, values)
        local_id = spec.record_id(row)
        store.save(ctx, linked(entry, local_id, binding, ctx, row, origin="erp"))
        return "Criado na IAZELA a partir do ERP."
    if action in ("ligar", "atualizar_local"):
        row = spec.get(ctx, entry["local_id"])
        if action == "atualizar_local":
            binding.apply_remote(ctx, row, entry["_remote"])
        previous = store.all(ctx).get(entry["local_id"])
        store.save(ctx, linked(entry, entry["local_id"], binding, ctx, row, origin=previous.origin if previous else "erp"))
        return "Atualizado na IAZELA." if action == "atualizar_local" else "Ligado ao registo do ERP."
    if action == "criar_erp":
        row = spec.get(ctx, entry["local_id"])
        values = binding.local_canonical(ctx, row)
        previous = store.all(ctx).get(entry["local_id"])
        external_id = previous.external_id if previous else client.new_id()
        payload = binding.create_payload(ctx, values, external_id)
        # Identidade e intenção gravadas antes do POST: uma repetição nunca cria um duplicado.
        store.save(
            ctx,
            LinkInfo(
                local_id=entry["local_id"],
                external_id=external_id,
                external_code=binding.remote_code(payload),
                origin="local",
                state="a_enviar",
                operation={"kind": "create", "state": "sending"},
            ),
        )
        ctx.db.commit()
        client.create(binding.resource, payload)
        return confirm_write(ctx, spec, client, entry["local_id"], external_id, values, "Criado no ERP e confirmado.")
    if action == "ligar_atualizar_erp":
        row = spec.get(ctx, entry["local_id"])
        raw = entry["_raw"] or {}
        store.save(
            ctx,
            LinkInfo(
                local_id=entry["local_id"],
                external_id=entry["external_id"],
                external_code=binding.remote_code(raw),
                origin="erp",
                state="sincronizado",
                base=binding.canonical(ctx, raw),
                synced_at=now(),
                version=binding.remote_version(raw),
            ),
        )
        action = "atualizar_erp"
    if action in ("atualizar_erp", "ativar_erp", "desativar_erp"):
        row = spec.get(ctx, entry["local_id"])
        link = store.all(ctx)[entry["local_id"]]
        fresh = client.get(binding.resource, link.external_id, binding.fetch_params)
        if fresh is None:
            raise ErpRejected("O registo já não existe no ERP. Reveja a ligação antes de enviar.")
        if link.version and binding.remote_version(fresh) and binding.remote_version(fresh) != binding.remote_version(entry["_raw"] or {}):
            raise ErpRejected("O registo mudou no ERP depois da pré-visualização. Volte a pré-visualizar.")
        values = binding.local_canonical(ctx, row)
        managed = binding.managed_fields(ctx)
        payload = binding.update_payload(ctx, fresh, {k: v for k, v in values.items() if k not in managed})
        row_link = store.row(ctx, entry["local_id"])
        row_link.state, row_link.operation = "a_enviar", json.dumps({"kind": "update", "state": "sending"})
        ctx.db.commit()
        client.update(binding.resource, link.external_id, payload)
        expected = {**values, **{k: binding.canonical(ctx, fresh).get(k) for k in managed}}
        return confirm_write(ctx, spec, client, entry["local_id"], link.external_id, expected, "Atualizado no ERP e confirmado.")
    if action == "verificar":
        row = spec.get(ctx, entry["local_id"])
        link = store.all(ctx)[entry["local_id"]]
        values = binding.local_canonical(ctx, row)
        return confirm_write(ctx, spec, client, entry["local_id"], link.external_id, values, "Escrita anterior confirmada no ERP.", retry_create=True)
    raise HTTPException(422, "Ação de sincronização desconhecida.")


def linked(entry, local_id, binding, ctx, row, origin):
    raw = entry["_raw"] or {}
    return LinkInfo(
        local_id=local_id,
        external_id=entry["external_id"],
        external_code=binding.remote_code(raw),
        origin=origin,
        state="sincronizado",
        base=binding.local_canonical(ctx, row),
        synced_at=now(),
        version=binding.remote_version(raw),
    )


def confirm_write(ctx, spec, client, local_id, external_id, expected, message, retry_create=False):
    """Só marca «sincronizado» depois de o ERP devolver os valores enviados."""
    binding = spec.erp
    remote = client.get(binding.resource, external_id, binding.fetch_params)
    if remote is None:
        if retry_create:
            row = binding.links.row(ctx, local_id)
            row.state, row.operation = "erro", ""
            row.error = "A escrita anterior não chegou ao ERP. Pode voltar a enviar (será usado o mesmo identificador)."
            row.error_at = now()
            ctx.db.flush()
            raise ErpRejected(row.error)
        raise ErpUncertain("O ERP não devolveu o registo enviado. Verifique antes de repetir.")
    received = binding.canonical(ctx, remote)
    mismatched = [k for k in binding.synced_fields if received.get(k) != expected.get(k)]
    if mismatched:
        raise ErpUncertain("O ERP não confirmou todos os campos enviados: " + ", ".join(mismatched) + ".")
    binding.links.save(
        ctx,
        LinkInfo(
            local_id=local_id,
            external_id=external_id,
            external_code=binding.remote_code(remote),
            origin=binding.links.all(ctx).get(local_id).origin if binding.links.all(ctx).get(local_id) else "local",
            state="sincronizado",
            base=received,
            synced_at=now(),
            version=binding.remote_version(remote),
        ),
    )
    return message


def remove_in_erp(ctx, spec, row, link, client, mode, keep_local=True):
    """Elimina/desativa no ERP. Devolve (mensagem, lado local já tratado pela ligação)."""
    binding = spec.erp
    label = record_label(ctx, spec, row)
    handled = False
    try:
        if mode == "delete":
            check = getattr(binding, "before_erp_delete", None)
            if check is not None:
                check(ctx, client, link)
            client.delete(binding.resource, link.external_id)
            if client.get(binding.resource, link.external_id) is not None:
                raise ErpUncertain("O ERP ainda devolve o registo depois da eliminação.")
            after = getattr(binding, "after_erp_delete", None)
            if after is not None:
                handled = after(ctx, row, keep_local)
            else:
                binding.links.delete(ctx, spec.record_id(row))
            message = f"«{label}» {spec.g('eliminado')} no ERP."
        else:
            fresh = client.get(binding.resource, link.external_id, binding.fetch_params)
            if fresh is None:
                raise ErpRejected("O registo já não existe no ERP.")
            client.update(binding.resource, link.external_id, binding.active_payload(fresh, False))
            confirmed = client.get(binding.resource, link.external_id, binding.fetch_params)
            if confirmed is None or binding.canonical(ctx, confirmed).get("active") is not False:
                raise ErpUncertain("O ERP não confirmou a desativação.")
            message = f"«{label}» {spec.g('desativado')} no ERP."
    except (ErpRejected, ErpUncertain) as exc:
        ctx.audit(
            "tabela_remocao_erp",
            spec.record_id(row),
            {"registo": label, "acao": mode, "resultado": "erro", "erro": str(exc)},
            spec.store_of(row),
        )
        ctx.db.commit()
        hint = " Se o ERP não permitir eliminar, use «Desativar no ERP»." if mode == "delete" and operation_allowed(ctx, spec, "deactivate") else ""
        raise HTTPException(502, f"{exc}{hint}") from None
    ctx.audit(
        "tabela_remocao_erp",
        spec.record_id(row),
        {"registo": label, "acao": mode, "destino": "erp" if keep_local else "ambos", "resultado": "concluido"},
        spec.store_of(row),
    )
    ctx.db.commit()
    return message, handled
