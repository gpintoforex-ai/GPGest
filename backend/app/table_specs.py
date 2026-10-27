"""Tabelas existentes declaradas no framework comum (ver table_framework.py)."""

import json
import re
from collections import Counter
from decimal import Decimal
from types import SimpleNamespace

import httpx
from fastapi import HTTPException
from sqlalchemy import func, select

from . import entities as entity_routes
from .banks import bank_accounts, rename_bank
from .inventory import permissions as stock_permissions
from .inventory import require as stock_require
from .inventory import settings as stock_settings
from .inventory import warehouse_dependencies
from .models import (
    BankInstitution,
    BusinessEntity,
    Category,
    CategoryIntegrationLink,
    ERPDocumentRubric,
    ErpLink,
    Movement,
    PaymentTerm,
    PlannedAllocation,
    PlannedMovement,
    PurchaseDocumentType,
    Register,
    StockWarehouse,
    Store,
    WintouchEntity,
    WintouchPaymentMethod,
    now,
)
from .payment_terms import TERM_TYPES, payment_days_for
from .purchase_document_types import (
    CONDITIONS,
    NATURES,
    derived_flags,
    document_type_usage,
    sign_of,
)
from .purchase_document_types import ensure_defaults as ensure_document_types
from .register_management import register_dependencies
from .rubrics import ERP_ADAPTERS, SendItem, set_cash_difference
from .table_framework import (
    ErpBinding,
    ErpLinkStore,
    ErpRejected,
    ErpUncertain,
    LinkInfo,
    TableField,
    TableSpec,
    enterprise_of,
    register,
)
from .wintouch_catalog import DocumentTypesBinding, PaymentTermsBinding, WarehousesBinding
from .wintouch_sync import main_address

# -- Prazos de pagamento -------------------------------------------------------------------


class PaymentTermsSpec(TableSpec):
    key = "payment_terms"
    title = "Prazos de pagamento"
    singular = "prazo de pagamento"
    description = "Prazos usados nas fichas de clientes e fornecedores e no cálculo de vencimentos."
    model = PaymentTerm
    fields = [
        TableField("code", "Código", required=True, max_length=40, help="Ex.: 30DIAS"),
        TableField("name", "Descrição", required=True, min_length=2, max_length=200),
        TableField(
            "term_type",
            "Tipo de pagamento",
            "select",
            required=True,
            options=[(k, v) for k, v in TERM_TYPES.items() if k != "outro"],
        ),
        TableField("days", "Dias", "number", minimum=0, maximum=3650, help="Obrigatório em «Após N dias»."),
        TableField("discount_percent", "Desconto (%)", "decimal", minimum=0, maximum=100),
    ]
    extra_columns = [("entities", "Entidades")]

    def __init__(self):
        super().__init__()
        self.erp = PaymentTermsBinding(self)

    def values(self, ctx, row):
        return {
            "code": row.code,
            "name": row.name,
            "term_type": row.term_type,
            "days": row.days,
            "discount_percent": f"{Decimal(row.discount_basis_points or 0) / 100:.2f}",
            "active": row.active,
        }

    def labels(self, ctx, rows):
        usage = dict(
            ctx.db.execute(
                select(BusinessEntity.payment_term_id, func.count())
                .where(BusinessEntity.payment_term_id.is_not(None), BusinessEntity.removed.is_(False))
                .group_by(BusinessEntity.payment_term_id)
            ).all()
        )
        return {
            row.id: {"term_type": TERM_TYPES.get(row.term_type, row.term_type), "entities": str(usage.get(row.id, 0))}
            for row in rows
        }

    def clean(self, ctx, data, row=None):
        if row is not None and row.term_type == "outro" and data.get("term_type") == "outro":
            # Tipo Wintouch não mapeado: mantém-se, sem o poder escolher em prazos novos.
            data = {**data, "term_type": "apos"}
            values = super().clean(ctx, data, row)
            values["term_type"], values["days"] = "outro", row.days
        else:
            values = super().clean(ctx, data, row)
        values["code"] = values["code"].upper()
        if any(c.isspace() for c in values["code"]):
            raise HTTPException(422, "O código não pode ter espaços.")
        if values["term_type"] == "apos" and values["days"] is None:
            raise HTTPException(422, "Indique o número de dias.")
        if values["term_type"] in ("fim_mes", "fim_mes_seguinte"):
            values["days"] = None
        values["discount_percent"] = values["discount_percent"] or "0.00"
        return values

    def unique_checks(self, ctx, values, row=None):
        other = ctx.db.scalar(select(PaymentTerm).where(func.upper(PaymentTerm.code) == values["code"].upper()))
        if other is not None and (row is None or other.id != row.id):
            raise HTTPException(409, f"Já existe um prazo com o código {values['code']}.")

    @staticmethod
    def _model_values(values):
        result = {k: v for k, v in values.items() if k != "discount_percent"}
        if "discount_percent" in values:
            result["discount_basis_points"] = int(Decimal(values["discount_percent"] or "0") * 100)
        return result

    def create(self, ctx, values):
        row = PaymentTerm(**self._model_values(values))
        ctx.db.add(row)
        ctx.db.flush()
        return row

    def update(self, ctx, row, values):
        for name, value in self._model_values(values).items():
            setattr(row, name, value)
        # Os dias equivalentes guardados nas entidades acompanham o prazo.
        for entity in ctx.db.scalars(select(BusinessEntity).where(BusinessEntity.payment_term_id == row.id)):
            entity.payment_days = payment_days_for(row)
        ctx.db.flush()

    def dependencies(self, ctx, row):
        count = ctx.db.scalar(
            select(func.count())
            .select_from(BusinessEntity)
            .where(BusinessEntity.payment_term_id == row.id, BusinessEntity.removed.is_(False))
        )
        return [("clientes/fornecedores que o usam", count)]


# -- Armazéns --------------------------------------------------------------------------------


class WarehousesSpec(TableSpec):
    key = "warehouses"
    title = "Armazéns"
    singular = "armazém"
    description = "Armazéns por estabelecimento, partilhados com a gestão de artigos e stock."
    model = StockWarehouse
    fields = [
        TableField("code", "Código", max_length=40, help="Necessário para enviar para o ERP."),
        TableField("name", "Descrição", required=True, min_length=2, max_length=100),
        TableField("store_id", "Estabelecimento", "store", required=True, create_only=True),
    ]

    def __init__(self):
        super().__init__()
        self.erp = WarehousesBinding(self)

    def check(self, ctx, operation, row=None, data=None):
        super().check(ctx, operation, row, data)
        if operation not in ("view", "export", "erp_remove", "import"):
            stock_require(ctx.user, stock_settings(ctx.db), "adjust_stock")

    def allowed(self, ctx, operation):
        if not super().allowed(ctx, operation):
            return False
        if operation in ("view", "export", "erp_remove", "import"):
            return True
        return bool(stock_permissions(ctx.user, stock_settings(ctx.db)).get("adjust_stock"))

    def values(self, ctx, row):
        return {"code": row.code or "", "name": row.name, "store_id": row.store_id, "active": row.active}

    def labels(self, ctx, rows):
        stores = dict(ctx.db.execute(select(Store.id, Store.name)).all())
        return {row.id: {"store_id": stores.get(row.store_id, "")} for row in rows}

    def clean(self, ctx, data, row=None):
        values = super().clean(ctx, data, row)
        values["code"] = values.get("code") or None
        return values

    def unique_checks(self, ctx, values, row=None):
        store_id = values.get("store_id") or (row.store_id if row else None)
        clash = ctx.db.scalar(
            select(StockWarehouse.id).where(
                StockWarehouse.store_id == store_id,
                StockWarehouse.name == values["name"],
                *([StockWarehouse.id != row.id] if row else []),
            )
        )
        if clash:
            raise HTTPException(409, "Já existe um armazém com esse nome neste estabelecimento.")
        if values.get("code"):
            other = ctx.db.scalar(select(StockWarehouse).where(func.upper(StockWarehouse.code) == values["code"].upper()))
            if other is not None and (row is None or other.id != row.id):
                raise HTTPException(409, f"Já existe um armazém com o código {values['code']}.")

    def create(self, ctx, values):
        row = StockWarehouse(**values)
        ctx.db.add(row)
        ctx.db.flush()
        return row

    def dependencies(self, ctx, row):
        return warehouse_dependencies(ctx.db, row)


# -- Bancos ------------------------------------------------------------------------------------


class BanksSpec(TableSpec):
    key = "banks"
    title = "Bancos"
    singular = "banco"
    description = "Instituições financeiras disponíveis para as contas bancárias."
    model = BankInstitution
    code_field = None
    fields = [TableField("name", "Descrição", required=True, min_length=2, max_length=100)]
    extra_columns = [("accounts", "Contas bancárias")]
    local_only_reason = (
        "A Wintouch desta empresa não tem bancos registados e a estrutura do recurso não foi confirmada; "
        "a integração fica desligada até ser validada com dados reais."
    )

    def values(self, ctx, row):
        return {"name": row.name, "active": row.active}

    def labels(self, ctx, rows):
        return {row.id: {"accounts": str(len(bank_accounts(ctx.db, row)))} for row in rows}

    def unique_checks(self, ctx, values, row=None):
        clash = ctx.db.scalar(
            select(BankInstitution.id).where(
                func.lower(BankInstitution.name) == values["name"].lower(),
                *([BankInstitution.id != row.id] if row else []),
            )
        )
        if clash:
            raise HTTPException(409, "Esta instituição financeira já está registada.")

    def update(self, ctx, row, values):
        rename_bank(ctx.db, row, values["name"])
        ctx.db.flush()

    def dependencies(self, ctx, row):
        return [("contas bancárias", len(bank_accounts(ctx.db, row)))]


# -- Caixas ------------------------------------------------------------------------------------


class RegistersSpec(TableSpec):
    key = "registers"
    title = "Caixas"
    singular = "caixa"
    feminine = True
    description = "Caixas configuradas por estabelecimento. Uma caixa inativa não abre novas folhas de caixa."
    model = Register
    code_field = None
    fields = [
        TableField("name", "Descrição", required=True, min_length=2, max_length=100),
        TableField("store_id", "Estabelecimento", "store", required=True),
    ]
    local_only_reason = (
        "As caixas associam-se às contas de caixa da Wintouch em Configurações → Integrações; "
        "o conector não cria nem altera contas de caixa no ERP (têm turnos e estado operacional)."
    )

    def rows(self, ctx):
        from .access import scope

        allowed = set(scope(ctx.user, ctx.db))
        return [row for row in ctx.db.scalars(select(Register)) if row.store_id in allowed]

    def values(self, ctx, row):
        return {"name": row.name, "store_id": row.store_id, "active": row.active}

    def labels(self, ctx, rows):
        stores = dict(ctx.db.execute(select(Store.id, Store.name)).all())
        return {row.id: {"store_id": stores.get(row.store_id, "")} for row in rows}

    def unique_checks(self, ctx, values, row=None):
        return None

    def update(self, ctx, row, values):
        if values["store_id"] != row.store_id:
            reason = next((label for label, count in register_dependencies(ctx.db, row.id) if count), None)
            if reason:
                raise HTTPException(
                    409,
                    f"A caixa «{row.name}» tem {reason}. Pode editar o nome, mas não mudar o estabelecimento.",
                )
        super().update(ctx, row, values)

    def dependencies(self, ctx, row):
        return register_dependencies(ctx.db, row.id)

    def delete(self, ctx, row):
        from .models import AccountBalanceReference

        reference = ctx.db.get(AccountBalanceReference, "cash:" + row.id)
        if reference:
            ctx.db.delete(reference)
        super().delete(ctx, row)


# -- Rubricas ----------------------------------------------------------------------------------

RUBRIC_ICONS = [
    ("tag", "Etiqueta"),
    ("receipt", "Documento"),
    ("wallet", "Caixa"),
    ("droplet", "Água"),
    ("zap", "Eletricidade"),
    ("phone", "Comunicações"),
    ("shopping-cart", "Compras"),
    ("landmark", "Banco / impostos"),
    ("shield", "Seguros"),
    ("users", "Pessoas"),
    ("package", "Mercadorias"),
    ("truck", "Transportes"),
    ("briefcase", "Trabalho"),
    ("coffee", "Refeições"),
    ("fuel", "Combustível"),
]


class RubricLinkStore(ErpLinkStore):
    """Ligações das rubricas, guardadas em category_integration_links (fluxo já validado)."""

    STATES = {"confirmado": "sincronizado", "a_enviar": "a_enviar", "por_verificar": "a_enviar", "rejeitado": "erro"}

    def _links(self, ctx):
        return ctx.db.scalars(
            select(CategoryIntegrationLink).where(
                CategoryIntegrationLink.provider == "wintouch",
                CategoryIntegrationLink.enterprise_id == enterprise_of(ctx),
            )
        )

    def all(self, ctx):
        names = dict(ctx.db.execute(select(Category.id, Category.name)).all())
        result = {}
        for link in self._links(ctx):
            payload = json.loads(link.payload or "{}")
            result[link.category_id] = LinkInfo(
                local_id=link.category_id,
                external_id=link.external_id,
                external_code=link.external_code,
                origin="local" if payload.get("Type") else "erp",
                state=self.STATES.get(link.status, "erro"),
                base={"name": payload.get("Name", names.get(link.category_id))},
                error="A Wintouch recusou o último envio." if link.status == "rejeitado" else "",
            )
        return result

    def row(self, ctx, local_id):
        return None

    def save(self, ctx, info):
        link = next((row for row in self._links(ctx) if row.category_id == info.local_id), None)
        if link is None:
            link = CategoryIntegrationLink(
                category_id=info.local_id, provider="wintouch", enterprise_id=enterprise_of(ctx)
            )
            ctx.db.add(link)
        payload = json.loads(link.payload or "{}") if link.payload else {}
        payload["Name"] = info.base.get("name")
        link.external_id, link.external_code = info.external_id, info.external_code or ""
        link.status, link.payload = "confirmado", json.dumps(payload, ensure_ascii=False)
        ctx.db.flush()

    def delete(self, ctx, local_id):
        for link in self._links(ctx):
            if link.category_id == local_id:
                ctx.db.delete(link)
        ctx.db.flush()


class RubricsBinding(ErpBinding):
    resource = "items"
    synced_fields = ["name"]
    match_field = None
    authority_resource = "rubrics"
    capability_keys = {"read": "read_rubrics", "create": "write_rubrics", "update": "write_rubrics"}
    capabilities = {"read": True, "create": True, "update": True, "delete": False, "activate": False, "deactivate": False}
    reasons = {
        "delete": "Eliminar rubricas na Wintouch não é suportado: podem estar usadas em documentos do ERP.",
        "activate": "As rubricas da Wintouch não têm estado ativo/inativo; o estado só existe na IAZELA.",
        "deactivate": "As rubricas da Wintouch não têm estado ativo/inativo; o estado só existe na IAZELA.",
    }

    def __init__(self, spec):
        super().__init__(spec)
        self.links = RubricLinkStore(self)

    def canonical(self, ctx, raw):
        return {"name": str(raw.get("Name") or "").strip()}

    def cannot_export(self, ctx, values):
        return None

    def execute(self, ctx, client, entry, action, options):
        raw = entry["_raw"] or {}
        if action in ("criar_local", "atualizar_local", "ligar"):
            name = self.canonical(ctx, raw)["name"]
            if action == "criar_local":
                category = Category(name=name)
                ctx.db.add(category)
                ctx.db.flush()
                local_id = category.id
            else:
                local_id = entry["local_id"]
                category = ctx.db.get(Category, local_id)
                if action == "atualizar_local":
                    clash = ctx.db.scalar(
                        select(Category.id).where(func.lower(Category.name) == name.lower(), Category.id != local_id)
                    )
                    if clash:
                        raise HTTPException(409, f"Já existe outra rubrica chamada «{name}».")
                    category.name = name
            self.links.save(
                ctx,
                LinkInfo(local_id, self.remote_id(raw), self.remote_code(raw), "erp", base={"name": category.name}),
            )
            return {"criar_local": "Rubrica criada a partir do ERP.", "ligar": "Rubrica ligada ao ERP."}.get(
                action, "Rubrica atualizada a partir do ERP."
            )
        if action == "ligar_atualizar_erp":
            self.links.save(
                ctx,
                LinkInfo(entry["local_id"], self.remote_id(raw), self.remote_code(raw), "erp", base=self.canonical(ctx, raw)),
            )
            ctx.db.commit()
            action = "atualizar_erp"
        if action in ("criar_erp", "atualizar_erp", "verificar"):
            # Fluxo de envio existente: código sequencial, identidade gravada antes do POST e GET de confirmação.
            outcome = ERP_ADAPTERS["wintouch"]["send"](
                entry["local_id"], SendItem(automatic=True, confirmed=True), ctx.user, ctx.db
            )
            link = next(r for r in self.links._links(ctx) if r.category_id == entry["local_id"])
            payload = json.loads(link.payload or "{}")
            payload["Name"] = ctx.db.get(Category, entry["local_id"]).name
            link.payload = json.dumps(payload, ensure_ascii=False)
            return f"Confirmado no ERP ({outcome.get('external_id', '')})."
        raise HTTPException(422, "Ação não suportada para rubricas.")


class CategoriesSpec(TableSpec):
    key = "categories"
    title = "Rubricas"
    singular = "rubrica"
    feminine = True
    description = "Categorias de receitas e despesas, com ícone e cor. Uma rubrica inativa não é proposta em novos registos."
    model = Category
    code_field = None
    fields = [
        TableField("name", "Descrição", required=True, min_length=2, max_length=100),
        TableField("icon", "Ícone", "icon", required=True, options=RUBRIC_ICONS),
        TableField("color", "Cor", "color", required=True, max_length=7),
        TableField(
            "vat_deduction_percent",
            "IVA dedutível estimado (%)",
            "number",
            minimum=0,
            maximum=100,
            column=False,
            help="Só para o mapa de IVA indicativo; vazio corresponde a 100%.",
        ),
        TableField(
            "cash_difference",
            "Usar nas diferenças de caixa",
            "boolean",
            column=False,
            help="Apenas uma rubrica pode estar selecionada.",
        ),
    ]

    def __init__(self):
        super().__init__()
        self.erp = RubricsBinding(self)

    def clean(self, ctx, data, row=None):
        values = super().clean(ctx, data, row)
        if values["icon"] not in {key for key, _ in RUBRIC_ICONS}:
            raise HTTPException(422, "Escolha um ícone válido.")
        if not values["color"].startswith("#") or len(values["color"]) != 7:
            raise HTTPException(422, "Escolha uma cor válida.")
        return values

    def unique_checks(self, ctx, values, row=None):
        clash = ctx.db.scalar(
            select(Category.id).where(
                func.lower(Category.name) == values["name"].lower(), *([Category.id != row.id] if row else [])
            )
        )
        if clash:
            raise HTTPException(409, "Já existe uma rubrica com esse nome.")

    def create(self, ctx, values):
        cash = values.pop("cash_difference", False)
        row = Category(**values)
        ctx.db.add(row)
        ctx.db.flush()
        set_cash_difference(ctx.db, row, cash)
        ctx.db.flush()
        return row

    def update(self, ctx, row, values):
        cash = values.pop("cash_difference", None)
        for name, value in values.items():
            setattr(row, name, value)
        set_cash_difference(ctx.db, row, cash)
        ctx.db.flush()

    def blocked_reason(self, ctx, row, operation):
        if operation == "deactivate" and row.cash_difference:
            return "Esta rubrica é usada nas diferenças de caixa. Escolha outra rubrica para essa função antes de a desativar."
        if operation == "remove":
            pending = ctx.db.scalar(
                select(CategoryIntegrationLink.id).where(
                    CategoryIntegrationLink.category_id == row.id,
                    CategoryIntegrationLink.status.in_(["a_enviar", "por_verificar"]),
                )
            )
            if pending:
                return "Verifique o envio pendente ao ERP antes de remover esta rubrica."
        return None

    def dependencies(self, ctx, row):
        labels = (
            (Movement, "movimentos"),
            (PlannedMovement, "movimentos previstos"),
            (PlannedAllocation, "repartições de movimentos previstos"),
            (ERPDocumentRubric, "documentos do ERP classificados"),
            (BusinessEntity, "clientes/fornecedores"),
        )
        return [
            (label, ctx.db.scalar(select(func.count()).select_from(model).where(model.category_id == row.id)))
            for model, label in labels
        ]

    def delete(self, ctx, row):
        self.erp.links.delete(ctx, row.id)
        super().delete(ctx, row)


# -- Clientes e fornecedores ---------------------------------------------------------------------

ENTITY_FIELDS = [
    "name",
    "tax_number",
    "email",
    "phone",
    "address",
    "locality",
    "postal_code",
    "category_id",
    "payment_term_id",
    "payment_method_id",
    "is_customer",
    "is_supplier",
]


class EntityLinkStore(ErpLinkStore):
    """Ligação das entidades: o espelho Wintouch (wt_entities).

    erp_links guarda apenas erros de envio e as criações em curso de entidades da IAZELA
    (identificador e código reservados antes do POST).
    """

    def _mirror(self, ctx):
        return {
            row.id: row
            for row in ctx.db.scalars(
                select(WintouchEntity).where(
                    WintouchEntity.enterprise_id == enterprise_of(ctx), WintouchEntity.erp_deleted_at.is_(None)
                )
            )
        }

    def all(self, ctx):
        errors = {row.local_id: row for row in ctx.db.scalars(self._query(ctx))}
        result = {}
        for mirror_id, mirror in self._mirror(ctx).items():
            local_id = "wt:" + mirror_id
            error = errors.get(local_id)
            result[local_id] = LinkInfo(
                local_id=local_id,
                external_id=mirror.external_id,
                external_code=mirror.code or "",
                origin="erp",
                state=error.state if error else "sincronizado",
                base=self.binding.mirror_canonical(ctx, mirror),
                synced_at=mirror.synced_at,
                error=error.error if error else "",
                version=mirror.source_version,
            )
        for local_id, pending in errors.items():
            if local_id.startswith("local:"):
                result[local_id] = LinkInfo(
                    local_id=local_id,
                    external_id=pending.external_id,
                    external_code=pending.external_code,
                    origin="local",
                    state=pending.state,
                    error=pending.error,
                    operation=json.loads(pending.operation) if pending.operation else None,
                )
        return result

    def row(self, ctx, local_id):
        row = super().row(ctx, local_id)
        if row is None and local_id.startswith("wt:"):
            mirror = ctx.db.get(WintouchEntity, local_id[3:])
            if mirror is not None:
                row = ErpLink(
                    table_key=self.binding.spec.key,
                    local_id=local_id,
                    provider=self.binding.provider,
                    enterprise_id=enterprise_of(ctx),
                    external_id=mirror.external_id,
                    origin="erp",
                )
                ctx.db.add(row)
        return row

    def save(self, ctx, info):
        # Sucesso: o espelho já foi atualizado; limpa um erro anterior.
        self.delete(ctx, info.local_id)

    def delete(self, ctx, local_id):
        existing = ErpLinkStore.row(self, ctx, local_id)
        if existing is not None:
            ctx.db.delete(existing)
            ctx.db.flush()


class EntitiesBinding(ErpBinding):
    resource = "entities"
    synced_fields = [
        "name",
        "tax_number",
        "is_customer",
        "is_supplier",
        "active",
        "payment_term_id",
        "payment_method_id",
        "address",
        "locality",
        "postal_code",
        "email",
        "phone",
    ]
    match_field = "tax_number"
    # A Wintouch exige a morada principal em cada gravação da entidade.
    fetch_params = {"embed": "EntityAddresses"}
    authority_resource = "entities"
    erp_managed = ("name", "tax_number")
    capability_keys = {
        "read": "read_entities",
        "create": "write_entities",
        "update": "write_entities",
        "activate": "write_entities",
        "deactivate": "write_entities",
        "delete": "write_entities",
    }
    capabilities = {"read": True, "create": True, "update": True, "activate": True, "deactivate": True, "delete": True}
    reasons = {}

    def __init__(self, spec, role):
        super().__init__(spec)
        self.role = role
        self.links = EntityLinkStore(self)

    def term_key(self):
        return "SupplierPaymentTermID" if self.role == "supplier" else "PaymentTermID"

    def method_key(self):
        return "SupplierPaymentMethodID" if self.role == "supplier" else "PaymentMethodID"

    def local_method(self, ctx, external_method):
        """Meio da IAZELA associado ao meio da Wintouch (Configuração inicial → meios de pagamento)."""
        if not external_method:
            return None
        local = ctx.db.scalar(
            select(WintouchPaymentMethod.local_payment).where(
                WintouchPaymentMethod.enterprise_id == enterprise_of(ctx),
                WintouchPaymentMethod.external_id == external_method,
            )
        )
        return local or "erp:" + external_method

    def external_method(self, ctx, method):
        """Meio da Wintouch para o meio da IAZELA; exige uma associação única."""
        if not method:
            return None
        if method.startswith("erp:"):
            return method[4:]
        matches = list(
            ctx.db.scalars(
                select(WintouchPaymentMethod.external_id).where(
                    WintouchPaymentMethod.enterprise_id == enterprise_of(ctx),
                    WintouchPaymentMethod.local_payment == method,
                )
            )
        )
        if len(matches) != 1:
            from .cash_local import method_catalog

            name = next((m["name"] for m in method_catalog(ctx.db) if m["id"] == method), method)
            raise ErpRejected(
                f"O meio «{name}» "
                + ("não está associado a nenhum meio da Wintouch" if not matches else "está associado a vários meios da Wintouch")
                + ": reveja a associação em Configurações → Parâmetros → Configuração inicial."
            )
        return matches[0]

    def local_term(self, ctx, external_term):
        if not external_term:
            return None
        link = ctx.db.scalar(
            select(ErpLink.local_id).where(
                ErpLink.table_key == "payment_terms",
                ErpLink.provider == "wintouch",
                ErpLink.enterprise_id == enterprise_of(ctx),
                ErpLink.external_id == external_term,
            )
        )
        # Prazo do ERP ainda não importado na tabela de prazos.
        return link or "erp:" + external_term

    def remote_rows(self, ctx, client):
        # Sem embed na lista (a Wintouch recusa-o com muitas entidades): moradas lidas à parte.
        from .wintouch_sync import addresses_by_entity, with_addresses

        return with_addresses(client.list(self.resource), addresses_by_entity(client.list("addresses")))

    def canonical(self, ctx, raw):
        main = main_address(raw.get("EntityAddresses")) or {}
        lines = [str(main.get(k) or "").strip() for k in ("Address1", "Address2")]
        return {
            "name": str(raw.get("Name") or "").strip(),
            "tax_number": str(raw.get("VATNumber") or "").strip(),
            "is_customer": bool(raw.get("IsClient")),
            "is_supplier": bool(raw.get("IsSupplier")),
            "active": raw.get("Enabled") is not False,
            "payment_term_id": self.local_term(ctx, raw.get(self.term_key())),
            "payment_method_id": self.local_method(ctx, raw.get(self.method_key())),
            "address": "\n".join(line for line in lines if line),
            "locality": str(main.get("City") or "").strip(),
            "postal_code": str(main.get("PostalCode") or "").strip(),
            "email": str(raw.get("Email") or main.get("Email") or "").strip(),
            "phone": str(raw.get("Telephone") or main.get("Telephone") or main.get("MobilePhone") or "").strip(),
        }

    def active_payload(self, raw, active):
        return {**raw, "Enabled": active}

    def with_main_address(self, raw, values, country_id, address_id):
        """Moradas da entidade com a principal atualizada pelos valores da IAZELA."""
        addresses = [dict(a) for a in raw.get("EntityAddresses") or [] if isinstance(a, dict)]
        main = main_address(addresses)
        fields = {
            "Address1": values.get("address") or None,
            "Address2": "",
            "City": values.get("locality") or None,
            "PostalCode": values.get("postal_code") or None,
            "Email": values.get("email") or None,
            "Telephone": values.get("phone") or None,
        }
        if main is None:
            addresses.append(
                {"ID": address_id, "Name": values["name"], "CountryID": country_id, "IsMainAddress": True, "IsActive": True, **fields}
            )
        else:
            main.update(fields)
        return addresses

    def mirror_canonical(self, ctx, mirror):
        term = (
            mirror.supplier_payment_term_external_id if self.role == "supplier" else mirror.payment_term_external_id
        )
        return {
            "name": (mirror.name or "").strip(),
            "tax_number": (mirror.tax_number or "").strip(),
            "is_customer": bool(mirror.is_customer),
            "is_supplier": bool(mirror.is_supplier),
            "active": mirror.active is not False,
            "payment_term_id": self.local_term(ctx, term),
            "payment_method_id": self.local_method(
                ctx,
                mirror.supplier_payment_method_external_id if self.role == "supplier" else mirror.payment_method_external_id,
            ),
            **{name: (getattr(mirror, name) or "").strip() for name in ("address", "locality", "postal_code", "email", "phone")},
        }

    def local_canonical(self, ctx, row):
        row = row if "values" in row else self.spec._row(row)
        values = {name: row["values"].get(name) for name in self.synced_fields}
        values["active"] = row["active"]
        for name in ("address", "locality", "postal_code", "email", "phone"):
            values[name] = (values.get(name) or "").strip()
        if row["id"].startswith("wt:") and (values["payment_term_id"] is None or values["payment_method_id"] is None):
            # Sem prazo/meio escolhido na IAZELA, a entidade segue os da Wintouch.
            mirror = ctx.db.get(WintouchEntity, row["id"][3:])
            if mirror is not None:
                remote = self.mirror_canonical(ctx, mirror)
                for name in ("payment_term_id", "payment_method_id"):
                    if values[name] is None:
                        values[name] = remote[name]
        return values

    def cannot_export(self, ctx, values):
        if not (values.get("name") or "").strip():
            return "Indique o nome antes de enviar para o ERP."
        return None

    def external_term(self, ctx, term):
        """ID Wintouch do prazo escolhido; o prazo tem de existir no ERP antes da entidade."""
        if not term:
            return None
        if term.startswith("erp:"):
            return term[4:]
        external = ctx.db.scalar(
            select(ErpLink.external_id).where(
                ErpLink.table_key == "payment_terms",
                ErpLink.provider == "wintouch",
                ErpLink.enterprise_id == enterprise_of(ctx),
                ErpLink.local_id == term,
            )
        )
        if not external:
            name = ctx.db.get(PaymentTerm, term).name
            raise ErpRejected(f"O prazo «{name}» ainda não existe no ERP: envie-o primeiro em Prazos de pagamento.")
        return external

    # -- Criação na Wintouch ---------------------------------------------------------------

    def _country(self, ctx, client, tax_number):
        """País pelo prefixo do NIF (ex.: ES…); sem prefixo, Portugal."""
        countries = getattr(ctx, "_wintouch_countries", None)
        if countries is None:
            countries = {str(c.get("Code") or "").upper(): c for c in client.list("countries")}
            ctx._wintouch_countries = countries
        prefix = tax_number[:2].upper() if len(tax_number) > 2 and tax_number[:2].isalpha() else "PT"
        country = countries.get(prefix) or countries.get("PT")
        if not country or not country.get("ID"):
            raise ErpRejected("Não foi encontrado o país na Wintouch (lista «countries»).")
        return country

    def _generated_code(self, client):
        """Código proposto pela própria Wintouch (numeração automática), quando a API o devolve."""
        path = f"{self.resource}/generate_code_supplier" if self.role == "supplier" else f"{self.resource}/generate_code"
        try:
            response = client.client.get(f"{client.url}/{path}", headers=client.headers)
        except httpx.HTTPError:
            return None
        if response.status_code != 200:
            return None
        try:
            value = response.json()
        except ValueError:
            value = response.text
        if isinstance(value, dict):
            value = value.get("Code") or value.get("code") or value.get("Value") or value.get("value")
        value = str(value).strip().strip('"') if value is not None else ""
        return value if value and value.lower() != "null" and len(value) <= 200 else None

    def _next_code(self, ctx, remote, reserved=None, client=None):
        """Código para uma entidade nova.

        Usa a numeração automática da Wintouch quando a API a devolve; caso contrário (a API desta
        empresa devolvia vazio/erro), o próximo número da série já usada para o mesmo tipo (ex.: F0105).
        Em ambos os casos evita códigos existentes e os reservados em criações em curso.
        """
        flag = "IsSupplier" if self.role == "supplier" else "IsClient"
        series = [re.match(r"^([A-Za-z]*)(\d+)$", str(r.get("Code") or "")) for r in remote if r.get(flag)]
        series = [m for m in series if m]
        if series:
            prefix = Counter(m.group(1) for m in series).most_common(1)[0][0]
            same = [m for m in series if m.group(1) == prefix]
            width = max(len(m.group(2)) for m in same)
            number = max(int(m.group(2)) for m in same)
        else:
            prefix, width, number = ("F" if self.role == "supplier" else "C"), 4, 0
        taken = {str(r.get("Code") or "").upper() for r in remote}
        taken |= {
            (code or "").upper()
            for code in ctx.db.scalars(
                select(ErpLink.external_code).where(
                    ErpLink.table_key.in_(("customers", "suppliers")),
                    ErpLink.provider == "wintouch",
                    ErpLink.enterprise_id == enterprise_of(ctx),
                    ErpLink.local_id.like("local:%"),
                )
            )
            if code != reserved
        }
        generated = self._generated_code(client) if client is not None else None
        if generated and generated.upper() not in taken:
            return generated
        while True:
            number += 1
            code = f"{prefix}{number:0{width}d}"
            if code.upper() not in taken:
                return code

    def _address(self, row, values, country_id, address_id):
        """Morada principal: a Wintouch exige exatamente uma, mesmo sem dados de morada."""
        return self.with_main_address({}, values, country_id, address_id)[0]

    def create_in_erp(self, ctx, client, entry, action):
        local_id = entry["local_id"]
        row = self.spec.get(ctx, local_id)
        values = self.local_canonical(ctx, row)
        link = ErpLinkStore.row(self.links, ctx, local_id)
        if action == "verificar" and link is not None:
            external_id = link.external_id
        else:
            operation = json.loads(link.operation) if link is not None and link.operation else {}
            nif = values["tax_number"]
            if nif:
                own = link.external_id if link is not None else ""
                twins = [r for r in client.rows(f"{self.resource}/by_vat/{nif}") if str(r.get("ID")) != own]
                if twins:
                    raise ErpRejected(
                        f"Já existe na Wintouch uma entidade com o NIF {nif} "
                        f"(«{twins[0].get('EntityName') or twins[0].get('Name') or ''}»). "
                        "Importe-a para a ligar, em vez de criar outra."
                    )
            remote = client.list(self.resource)
            external_id = link.external_id if link is not None else client.new_id()
            code = link.external_code if link is not None and link.external_code else ""
            if not code or any(
                str(r.get("Code") or "").upper() == code.upper() and str(r.get("ID")) != external_id for r in remote
            ):
                code = self._next_code(ctx, remote, reserved=code, client=client)
            if link is None:
                link = ErpLink(
                    table_key=self.spec.key,
                    local_id=local_id,
                    provider=self.provider,
                    enterprise_id=enterprise_of(ctx),
                    origin="local",
                    external_id=external_id,
                )
                ctx.db.add(link)
            address_id = operation.get("address_id") or client.new_id()
            # Identidade e código reservados antes do POST: uma repetição nunca cria uma segunda entidade.
            link.external_id, link.external_code, link.state = external_id, code, "a_enviar"
            link.operation = json.dumps({"kind": "create", "state": "sending", "address_id": address_id})
            link.error, link.error_at = "", None
            ctx.db.commit()
            if client.get(self.resource, external_id) is None:
                country = self._country(ctx, client, nif)
                address = self._address(row, values, country["ID"], address_id)
                payload = {
                    "ID": external_id,
                    "Code": code,
                    "Name": values["name"],
                    "VATNumber": nif or None,
                    "IsClient": values["is_customer"],
                    "IsSupplier": values["is_supplier"],
                    "Enabled": values["active"],
                    "IsFinalCustomer": False,
                    "IsSelfBillingEnabled": False,
                    "CountryID": country["ID"],
                    "CurrencyID": country.get("CurrencyID"),
                    # Valores das entidades criadas na própria Wintouch (sem controlo de crédito).
                    "CreditControlType": 4,
                    "OverduesControlType": 4,
                    "SupplierCreditControlType": 4,
                    self.term_key(): self.external_term(ctx, values["payment_term_id"]),
                    self.method_key(): self.external_method(ctx, values["payment_method_id"]),
                    "EntityAddresses": [address],
                }
                client.create(self.resource, payload)
        return self._confirm_creation(ctx, client, row, values, local_id, external_id, action)

    def _confirm_creation(self, ctx, client, row, values, local_id, external_id, action):
        created = client.get(self.resource, external_id, {"embed": "EntityAddresses"})
        link = ErpLinkStore.row(self.links, ctx, local_id)
        if created is None:
            if action == "verificar":
                link.state, link.operation = "erro", ""
                link.error = (
                    "A criação anterior não chegou à Wintouch. Pode voltar a enviar: "
                    "será usado o mesmo código e identificador."
                )
                link.error_at = now()
                ctx.db.commit()
                raise ErpRejected(link.error)
            raise ErpUncertain("A Wintouch não devolveu a entidade criada. Verifique antes de repetir.")
        received = self.canonical(ctx, created)
        contact = ("address", "locality", "postal_code", "email", "phone")
        mismatched = [k for k in self.synced_fields if k not in contact and received.get(k) != values.get(k)]
        if mismatched:
            raise ErpUncertain("A Wintouch não confirmou todos os campos enviados: " + ", ".join(mismatched) + ".")
        note = ""
        operation = json.loads(link.operation) if link.operation else {}
        address = self._address(row, values, created.get("CountryID"), operation.get("address_id") or client.new_id())
        addresses = created.get("EntityAddresses") or client.rows(f"{self.resource}/{external_id}/addresses")
        if not any(a.get("IsMainAddress") for a in addresses):
            # A morada não veio com a entidade: cria-a à parte, ligada à entidade.
            client.create("addresses", {**address, "EntityID": external_id})
            addresses = client.rows(f"{self.resource}/{external_id}/addresses")
            if not any(a.get("IsMainAddress") for a in addresses):
                note = " A morada não foi confirmada: verifique-a na Wintouch."
            created = {**created, "EntityAddresses": addresses}
        mirror = self._upsert_mirror(ctx, created, client)
        entity = ctx.db.get(BusinessEntity, local_id[6:])
        entity.wintouch_id = mirror.id
        entity.flags_local = False
        ctx.db.delete(link)
        ctx.db.flush()
        return f"Criado na Wintouch com o código {mirror.code}.{note}"

    # -- Eliminação na Wintouch ----------------------------------------------------------------

    def before_erp_delete(self, ctx, client, link):
        """Entidades com documentos na Wintouch não são eliminadas: propõe-se desativar."""
        if client.get(self.resource, link.external_id) is None:
            return  # Já não existe na Wintouch (ex.: apagada lá): só falta tratar o lado da IAZELA.
        try:
            response = client.client.get(f"{client.url}/{self.resource}/{link.external_id}/has_movements", headers=client.headers)
        except httpx.HTTPError:
            raise ErpUncertain("Não foi possível confirmar na Wintouch se a entidade tem documentos. Nada foi eliminado.") from None
        if response.status_code != 200 or response.text.strip().lower() not in ("true", "false"):
            raise ErpRejected("A Wintouch não confirmou se a entidade tem documentos. Nada foi eliminado.")
        if response.text.strip().lower() == "true":
            raise ErpRejected("A entidade tem documentos na Wintouch e não pode ser eliminada.")

    def after_erp_delete(self, ctx, row, keep_local):
        """Depois de eliminada na Wintouch: fica local («Remover do ERP») ou sai de ambos."""
        mirror = ctx.db.get(WintouchEntity, row["id"][3:]) if row["id"].startswith("wt:") else None
        if mirror is None:
            return False
        entity = ctx.db.scalar(select(BusinessEntity).where(BusinessEntity.wintouch_id == mirror.id))
        if keep_local:
            if entity is None:
                values = row["values"]
                entity = BusinessEntity(
                    name=values.get("name") or mirror.name,
                    tax_number=values.get("tax_number") or "",
                    email=values.get("email") or "",
                    phone=values.get("phone") or "",
                    address=values.get("address") or "",
                    locality=values.get("locality") or "",
                    postal_code=values.get("postal_code") or "",
                    is_customer=bool(values.get("is_customer")),
                    is_supplier=bool(values.get("is_supplier")),
                    active=bool(row["active"]),
                    category_id=values.get("category_id"),
                    payment_term_id=values.get("payment_term_id"),
                )
                ctx.db.add(entity)
            entity.wintouch_id = None
            entity.flags_local = False
        elif entity is not None:
            entity.removed, entity.active = True, False
        # O espelho fica (documentos já importados apontam para ele), marcado como eliminado no ERP.
        mirror.erp_deleted_at = now()
        mirror.active = False
        self.links.delete(ctx, row["id"])
        ctx.db.flush()
        return not keep_local

    def _upsert_mirror(self, ctx, raw, client=None):
        from .wintouch_sync import (
            SyncError,
            entity_code_twin,
            normalize,
            retire_entity,
            same_company,
        )

        try:
            values = normalize("entities", raw)
        except SyncError as exc:
            raise ErpRejected(str(exc)) from None
        mirror = ctx.db.scalar(
            select(WintouchEntity).where(
                WintouchEntity.enterprise_id == enterprise_of(ctx), WintouchEntity.external_id == values["external_id"]
            )
        )
        if mirror is None:
            # O código do ERP não se repete: a entidade com o mesmo código é atualizada em vez de duplicada.
            twin = entity_code_twin(ctx.db, enterprise_of(ctx), values.get("code"), values["external_id"])
            if twin is not None and client is not None and client.get(self.resource, twin.external_id) is not None:
                raise ErpRejected(
                    f"O código {values.get('code')} está repetido na Wintouch («{twin.name}» e «{values['name']}»). "
                    "Corrija o código na Wintouch antes de importar."
                )
            if twin is not None and same_company(twin, SimpleNamespace(tax_number=values.get("tax_number"))):
                mirror = twin
            elif twin is not None:
                retire_entity(ctx.db, twin, None)
        if mirror is None:
            mirror = WintouchEntity(enterprise_id=enterprise_of(ctx), **values)
            ctx.db.add(mirror)
        else:
            for name, value in values.items():
                setattr(mirror, name, value)
        mirror.synced_at = now()
        ctx.db.flush()
        return mirror

    def execute(self, ctx, client, entry, action, options):
        raw = entry["_raw"] or {}
        if action in ("criar_erp", "verificar") and (entry["local_id"] or "").startswith("local:"):
            return self.create_in_erp(ctx, client, entry, action)
        if action in ("criar_local", "atualizar_local", "ligar"):
            mirror = self._upsert_mirror(ctx, raw, client)
            local_id = entry["local_id"]
            if local_id and local_id.startswith("local:"):
                entity = ctx.db.get(BusinessEntity, local_id[6:])
                if ctx.db.scalar(select(BusinessEntity.id).where(BusinessEntity.wintouch_id == mirror.id)):
                    raise HTTPException(409, "Esta entidade do ERP já está associada a outra entidade da IAZELA.")
                entity.wintouch_id = mirror.id
            override = ctx.db.scalar(select(BusinessEntity).where(BusinessEntity.wintouch_id == mirror.id))
            if override is not None and action == "atualizar_local":
                remote = self.canonical(ctx, raw)
                for name in ("name", "tax_number", "is_customer", "is_supplier", "active", "address", "locality", "postal_code", "email", "phone"):
                    setattr(override, name, remote[name])
                override.flags_local = False
                override.contact_local = False
                term = remote["payment_term_id"]
                override.payment_term_id = None if not term or term.startswith("erp:") else term
                method = remote["payment_method_id"]
                override.payment_method_id = None if not method or method.startswith("erp:") else method
            self.links.save(ctx, LinkInfo(local_id="wt:" + mirror.id, external_id=mirror.external_id))
            return {"criar_local": "Importado do ERP.", "ligar": "Ligado à entidade do ERP."}.get(
                action, "Atualizado a partir do ERP."
            )
        if action == "ligar_atualizar_erp":
            mirror = self._upsert_mirror(ctx, raw)
            entity = ctx.db.get(BusinessEntity, entry["local_id"][6:])
            entity.wintouch_id = mirror.id
            ctx.db.flush()
            entry = {**entry, "local_id": "wt:" + mirror.id}
            action = "atualizar_erp"
        if action in ("atualizar_erp", "ativar_erp", "desativar_erp"):
            row = self.spec.get(ctx, entry["local_id"])
            values = self.local_canonical(ctx, row)
            fresh = client.get(self.resource, entry["external_id"], self.fetch_params)
            if fresh is None:
                raise ErpRejected("A entidade já não existe no ERP.")
            if entry["_raw"] and fresh.get("Version") != entry["_raw"].get("Version"):
                raise ErpRejected("A entidade mudou no ERP depois da pré-visualização. Volte a pré-visualizar.")
            external_term = self.external_term(ctx, values["payment_term_id"])
            payload = {
                **fresh,
                "IsClient": values["is_customer"],
                "IsSupplier": values["is_supplier"],
                "Enabled": values["active"],
                self.term_key(): external_term,
                self.method_key(): self.external_method(ctx, values["payment_method_id"]),
                "EntityAddresses": self.with_main_address(fresh, values, fresh.get("CountryID"), client.new_id()),
            }
            if not self.managed_fields(ctx):
                payload.update(Name=values["name"], VATNumber=values["tax_number"])
            client.update(self.resource, entry["external_id"], payload)
            confirmed = client.get(self.resource, entry["external_id"], self.fetch_params)
            if confirmed is None:
                raise ErpRejected("O ERP não devolveu a entidade depois da atualização.")
            expected = {**values, **({} if not self.managed_fields(ctx) else {k: self.canonical(ctx, fresh)[k] for k in self.erp_managed})}
            received = self.canonical(ctx, confirmed)
            mismatched = [k for k in self.synced_fields if received.get(k) != expected.get(k)]
            if mismatched:
                raise ErpUncertain("O ERP não confirmou todos os campos enviados: " + ", ".join(mismatched) + ".")
            mirror = self._upsert_mirror(ctx, confirmed)
            override = ctx.db.scalar(select(BusinessEntity).where(BusinessEntity.wintouch_id == mirror.id))
            if override is not None:
                override.flags_local = False
                override.contact_local = False
            self.links.save(ctx, LinkInfo(local_id="wt:" + mirror.id, external_id=mirror.external_id))
            return "Atualizado no ERP e confirmado."
        raise HTTPException(422, "Ação não suportada para entidades.")


class EntitiesSpec(TableSpec):
    model = BusinessEntity
    code_field = None
    self_audited = True
    fields = [
        TableField("tax_number", "NIF", "nif", max_length=40, tab="Dados gerais", group="Identificação e contactos", span=2),
        TableField("name", "Nome", required=True, min_length=2, max_length=300, tab="Dados gerais", group="Identificação e contactos", span=4),
        TableField("email", "Email", "email", max_length=200, tab="Dados gerais", group="Identificação e contactos", span=4),
        TableField("phone", "Telefone", max_length=60, tab="Dados gerais", group="Identificação e contactos", span=2),
        TableField("address", "Morada", "textarea", max_length=500, column=False, tab="Dados gerais", group="Localização", span=6),
        TableField("postal_code", "Código postal", max_length=20, column=False, tab="Dados gerais", group="Localização", span=2),
        TableField("locality", "Localidade", max_length=120, column=False, tab="Dados gerais", group="Localização", span=4),
        # Tipo de entidade assumido pela tabela (Clientes/Fornecedores); quem já tem os dois mantém-nos.
        TableField("is_customer", "Cliente", "boolean", column=False, hidden=True),
        TableField("is_supplier", "Fornecedor", "boolean", column=False, hidden=True),
        TableField("category_id", "Rubrica", "category", create_required=True, tab="Condições comerciais", span=6),
        TableField("payment_term_id", "Prazo de pagamento", "payment_term", create_required=True, tab="Condições comerciais", span=3),
        TableField("payment_method_id", "Meio de pagamento", "payment_method", create_required=True, tab="Condições comerciais", span=3),
    ]

    def __init__(self, key, title, singular, role):
        super().__init__()
        self.key, self.title, self.singular, self.role = key, title, singular, role
        self.description = "Entidades da empresa, importadas da Wintouch ou registadas na IAZELA."
        self.erp = EntitiesBinding(self, role)

    def describe(self, ctx):
        from .cash_local import method_catalog

        result = super().describe(ctx)
        options = [{"value": m["id"], "label": m["name"], "active": m["active"]} for m in method_catalog(ctx.db)]
        for field in result["fields"]:
            if field["type"] == "payment_method":
                field["options"] = options
        return result

    def rows(self, ctx):
        flag = "is_supplier" if self.role == "supplier" else "is_customer"
        return [row for row in entity_routes.list_entities(ctx.user, ctx.db) if row[flag]]

    def record_id(self, row):
        return row["id"]

    def get(self, ctx, record_id):
        row = next((r for r in entity_routes.list_entities(ctx.user, ctx.db) if r["id"] == record_id), None)
        if row is None:
            raise HTTPException(404, "Entidade não encontrada.")
        return self._row(row)

    def _row(self, row):
        return {"id": row["id"], "active": row["active"], "values": {f: row.get(f) for f in ENTITY_FIELDS}, "raw": row}

    def store_of(self, row):
        return None

    def is_active(self, row):
        row = row if "values" in row else self._row(row)
        return bool(row["active"])

    def values(self, ctx, row):
        row = row if "values" in row else self._row(row)
        references = ("category_id", "payment_term_id", "payment_method_id")
        return {**{k: (v if v is not None else ("" if k not in references else None)) for k, v in row["values"].items()}, "active": row["active"]}

    def labels(self, ctx, rows):
        from .cash_local import method_catalog

        terms = dict(ctx.db.execute(select(PaymentTerm.id, PaymentTerm.name)).all())
        categories = dict(ctx.db.execute(select(Category.id, Category.name)).all())
        methods = {m["id"]: m["name"] for m in method_catalog(ctx.db)}
        result = {}
        for row in rows:
            row = row if "values" in row else self._row(row)
            raw = row.get("raw") or row
            result[row["id"]] = {
                "category_id": categories.get(row["values"].get("category_id"), ""),
                "payment_term_id": terms.get(row["values"].get("payment_term_id"))
                or raw.get(f"erp_{self.role}_payment_term")
                or "",
                "roles": " / ".join(n for n, f in (("Cliente", "is_customer"), ("Fornecedor", "is_supplier")) if row["values"].get(f)),
                "erp_payment_method": raw.get(f"erp_{self.role}_payment_method") or "",
                "payment_method_id": methods.get(row["values"].get("payment_method_id"))
                or raw.get(f"erp_{self.role}_payment_method")
                or "",
            }
        return result

    def clean(self, ctx, data, row=None):
        values = super().clean(ctx, data, row)
        if row is None:
            # Ao criar, o tipo é o da tabela onde se está.
            values["is_customer" if self.role == "customer" else "is_supplier"] = True
        else:
            # Ao editar, mantém os tipos que a entidade já tem (pode ser cliente e fornecedor).
            values["is_customer"] = bool(row["values"].get("is_customer"))
            values["is_supplier"] = bool(row["values"].get("is_supplier"))
            values["is_customer" if self.role == "customer" else "is_supplier"] = True
        if values.get("category_id") and not ctx.db.get(Category, values["category_id"]):
            raise HTTPException(422, "Rubrica não encontrada.")
        from .entities import check_payment_method

        check_payment_method(ctx.db, values.get("payment_method_id"), row["values"].get("payment_method_id") if row else None)
        term = values.get("payment_term_id")
        if term:
            current = row["values"].get("payment_term_id") if row else None
            found = ctx.db.get(PaymentTerm, term)
            if found is None:
                raise HTTPException(422, "Prazo de pagamento não encontrado.")
            if not found.active and term != current:
                raise HTTPException(422, f"O prazo «{found.name}» está inativo e não pode ser escolhido.")
        return values

    def unique_checks(self, ctx, values, row=None):
        return None

    def _edit(self, values, active):
        return entity_routes.EntityEdit(**{**values, "active": active, "payment_days": None})

    def create(self, ctx, values):
        saved = entity_routes.create_entity(self._edit(values, True), ctx.user, ctx.db)
        return self._row({**saved, "has_movements": False})

    def update(self, ctx, row, values):
        entity_routes.edit_entity(row["id"], self._edit(values, row["active"]), ctx.user, ctx.db)

    def set_active(self, ctx, row, active):
        values = {k: v for k, v in row["values"].items()}
        values = {k: ("" if v is None and k not in ("category_id", "payment_term_id", "payment_method_id") else v) for k, v in values.items()}
        entity_routes.edit_entity(row["id"], self._edit(values, active), ctx.user, ctx.db)

    def blocked_reason(self, ctx, row, operation):
        if operation == "remove" and row["id"].startswith("wt:") and row["raw"].get("editable") is False:
            return (
                "A entidade pertence ao ERP: use «Remover de ambos» para a eliminar também na Wintouch, "
                "ou desative-a na IAZELA."
            )
        return None

    def dependencies(self, ctx, row):
        return [("movimentos, encomendas ou folhas de caixa associados", 1 if row["raw"].get("has_movements") else 0)]

    def delete(self, ctx, row):
        if row["id"].startswith("wt:"):
            # Desativada no ERP e removida na IAZELA: deixa de aparecer, sem apagar o histórico.
            mirror = ctx.db.get(WintouchEntity, row["id"][3:])
            entity = ctx.db.scalar(select(BusinessEntity).where(BusinessEntity.wintouch_id == mirror.id))
            if entity is None:
                entity = BusinessEntity(wintouch_id=mirror.id, name=mirror.name, tax_number=mirror.tax_number or "")
                ctx.db.add(entity)
            entity.removed, entity.active = True, False
            ctx.db.flush()
            return
        entity_routes.remove_entity(row["id"], ctx.user, ctx.db)


# -- Tipos de documento de compra --------------------------------------------------------------


class DocumentTypesSpec(TableSpec):
    key = "document_types"
    title = "Tipos de Documentos"
    singular = "tipo de documento"
    description = "Tipos de documento de compra usados nas receções: definem a dívida, os previstos e os movimentos de caixa."
    model = PurchaseDocumentType
    code_field = "code"
    description_field = "description"
    filter_fields = ("nature", "payment_condition")
    fields = [
        TableField("code", "Código", required=True, max_length=20, help="Ex.: FC, FCD, NCC, NCCD", span=2),
        TableField("description", "Descrição", required=True, min_length=2, max_length=100, span=4),
        TableField("nature", "Natureza", "select", required=True, options=list(NATURES.items()), span=3),
        TableField("payment_condition", "Condição de pagamento", "select", required=True, options=list(CONDITIONS.items()), span=3),
        TableField("default_payment_method", "Meio de pagamento por omissão", "payment_method", column=False, span=3),
        TableField("default_due_days", "Dias de vencimento", "number", minimum=0, maximum=3650, column=False, span=3,
                   help="Só a crédito: vencimento = data do documento + dias."),
        TableField("series", "Série", max_length=40, column=False, span=2),
        TableField("creates_cash_movement", "Gera movimento de caixa", "boolean", column=False,
                   help="Por omissão, a dinheiro."),
        TableField("creates_debt", "Gera dívida", "boolean", column=False, help="Por omissão, a crédito."),
    ]
    extra_columns = [("sign_label", "Sinal"), ("effect", "Efeito"), ("documents", "Documentos")]

    def __init__(self):
        super().__init__()
        self.erp = DocumentTypesBinding(self)

    def describe(self, ctx):
        from .cash_local import method_catalog

        result = super().describe(ctx)
        options = [{"value": m["id"], "label": m["name"], "active": m["active"]} for m in method_catalog(ctx.db)]
        for field in result["fields"]:
            if field["type"] == "payment_method":
                field["options"] = options
        return result

    def rows(self, ctx):
        ensure_document_types(ctx.db)
        return super().rows(ctx)

    def labels(self, ctx, rows):
        usage = document_type_usage(ctx.db)
        result = {}
        for row in rows:
            effects = [label for flag, label in ((row.creates_debt, "dívida + previsto"), (row.creates_cash_movement, "movimento real")) if flag]
            result[row.id] = {
                "nature": NATURES.get(row.nature, row.nature),
                "payment_condition": CONDITIONS.get(row.payment_condition, row.payment_condition),
                "sign_label": "+1 (fatura)" if row.sign > 0 else "−1 (nota de crédito)",
                "effect": " · ".join(effects) or "sem efeito financeiro",
                "documents": str(usage.get(row.id, 0)),
            }
        return result

    def clean(self, ctx, data, row=None):
        values = super().clean(ctx, data, row)
        values["code"] = values["code"].upper()
        if any(c.isspace() for c in values["code"]):
            raise HTTPException(422, "O código não pode ter espaços.")
        values["sign"] = sign_of(values["nature"])
        if values["payment_condition"] == "dinheiro":
            values["default_due_days"] = None
        elif values["default_due_days"] is None:
            values["default_due_days"] = 30
        # Sem exceções indicadas, os indicadores seguem a condição de pagamento.
        if not values.get("creates_cash_movement") and not values.get("creates_debt"):
            values["creates_cash_movement"], values["creates_debt"] = derived_flags(values["payment_condition"])
        if values.get("default_payment_method") == "":
            values["default_payment_method"] = None
        values["series"] = values.get("series") or ""
        return values

    def dependencies(self, ctx, row):
        return [("documentos de compra que o usam", document_type_usage(ctx.db).get(row.id, 0))]


# -- Registo -------------------------------------------------------------------------------------

for spec in (
    EntitiesSpec("customers", "Clientes", "cliente", "customer"),
    EntitiesSpec("suppliers", "Fornecedores", "fornecedor", "supplier"),
    BanksSpec(),
    WarehousesSpec(),
    CategoriesSpec(),
    RegistersSpec(),
    PaymentTermsSpec(),
    DocumentTypesSpec(),
):
    register(spec)

# Inativos que não podem ser usados em novos registos (ver table_rules.py).
INACTIVE_MODELS = {
    "categories": Category, "registers": Register, "warehouses": StockWarehouse, "payment_terms": PaymentTerm,
    "document_types": PurchaseDocumentType,
}
