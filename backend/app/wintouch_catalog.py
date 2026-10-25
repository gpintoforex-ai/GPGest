"""Conector Wintouch para as tabelas: leitura e escrita explícitas, sempre confirmadas por GET.

As operações declaradas em cada `ErpBinding` correspondem a recursos documentados da API v1
(GET/POST/PUT/DELETE). Uma escrita só é considerada concluída quando um GET devolve os valores
enviados; sem essa confirmação o registo fica «por confirmar» e nunca é recriado às cegas.
"""

import json
from contextlib import contextmanager
from decimal import Decimal
from uuid import uuid4

import httpx
from cryptography.fernet import InvalidToken
from fastapi import HTTPException

from .platform import cipher, validate_url
from .table_framework import ErpBinding, ErpRejected, ErpUncertain, TableField
from .wintouch_settlement import encode_payload, rejection_message

MAX_ROWS = 5000


class ErpClient:
    def __init__(self, client, url, enterprise, key):
        self.client = client
        self.url = url.rstrip("/")
        self.headers = {
            "Authorization": "ApiKey " + key,
            "Accept": "application/json",
            "x-current-enterprise": enterprise,
        }

    @staticmethod
    def new_id():
        return str(uuid4())

    @staticmethod
    def _rows(payload):
        if isinstance(payload, list):
            return payload
        if isinstance(payload, dict):
            for key in ("items", "Items", "data", "Data"):
                if isinstance(payload.get(key), list):
                    return payload[key]
        return None

    def list(self, resource, params=None):
        items = []
        while True:
            try:
                response = self.client.get(
                    f"{self.url}/{resource}",
                    params={"limit": 200, "skip": len(items), **(params or {})},
                    headers=self.headers,
                )
            except httpx.HTTPError:
                raise HTTPException(502, "Falha de comunicação com o ERP. Nada foi alterado.") from None
            if response.status_code != 200:
                raise HTTPException(502, f"O ERP respondeu com HTTP {response.status_code} ao consultar «{resource}».")
            try:
                rows = self._rows(json.loads(response.text, parse_float=Decimal))
            except ValueError:
                rows = None
            if rows is None:
                raise HTTPException(502, f"O ERP devolveu um formato não reconhecido em «{resource}».")
            if not rows:
                return items
            items.extend(row for row in rows if isinstance(row, dict))
            if len(items) > MAX_ROWS:
                raise HTTPException(502, f"«{resource}» excede {MAX_ROWS} registos.")

    def get(self, resource, identifier, params=None):
        try:
            response = self.client.get(f"{self.url}/{resource}/{identifier}", params=params, headers=self.headers)
        except httpx.HTTPError:
            raise ErpUncertain("Falha de comunicação ao confirmar no ERP.") from None
        if response.status_code == 404:
            return None
        if response.status_code != 200:
            raise ErpUncertain(f"O ERP respondeu com HTTP {response.status_code} ao confirmar.")
        try:
            data = json.loads(response.text, parse_float=Decimal)
        except ValueError:
            raise ErpUncertain("O ERP devolveu uma resposta inválida ao confirmar.") from None
        return data if isinstance(data, dict) and data.get("ID") else None

    def _write(self, method, path, payload=None):
        try:
            response = self.client.request(
                method,
                f"{self.url}/{path}",
                content=encode_payload(payload).encode() if payload is not None else None,
                headers={**self.headers, "Content-Type": "application/json"},
            )
        except httpx.HTTPError:
            raise ErpUncertain("Falha de comunicação durante a escrita no ERP; o resultado é desconhecido.") from None
        if 200 <= response.status_code < 300:
            return response
        if response.status_code in (400, 401, 403, 404, 409, 422):
            raise ErpRejected(f"O ERP recusou a operação (HTTP {response.status_code}): {rejection_message(response)}")
        raise ErpUncertain(f"Resposta inesperada do ERP (HTTP {response.status_code}); o resultado é desconhecido.")

    def create(self, resource, payload):
        return self._write("POST", resource, payload)

    def rows(self, path):
        """GET de uma lista sem paginação (ex.: moradas de uma entidade, pesquisa por NIF)."""
        try:
            response = self.client.get(f"{self.url}/{path}", headers=self.headers)
        except httpx.HTTPError:
            raise ErpUncertain("Falha de comunicação com o ERP.") from None
        if response.status_code == 404:
            return []
        if response.status_code != 200:
            raise ErpUncertain(f"O ERP respondeu com HTTP {response.status_code} ao consultar «{path}».")
        try:
            data = json.loads(response.text, parse_float=Decimal)
        except ValueError:
            raise ErpUncertain("O ERP devolveu uma resposta inválida.") from None
        rows = self._rows(data)
        return [row for row in rows or [] if isinstance(row, dict)]

    def update(self, resource, identifier, payload):
        return self._write("PUT", f"{resource}/{identifier}", payload)

    def delete(self, resource, identifier):
        try:
            return self._write("DELETE", f"{resource}/{identifier}")
        except ErpRejected as exc:
            if "HTTP 404" in str(exc):
                return None
            raise


@contextmanager
def erp_client(ctx, write=False, confirm_demo=False):
    config = ctx.integration
    company = ctx.company
    if config.get("mode") != "integrated" or not company:
        raise HTTPException(409, "A empresa está em modo local: não existe ligação ao ERP.")
    if config.get("provider") != "wintouch" or not config.get("active"):
        raise HTTPException(409, "A ligação Wintouch está desativada em Configurações → Integrações.")
    if write and not config.get("writes"):
        raise HTTPException(409, "A escrita no ERP não está autorizada em Configurações → Integrações.")
    if getattr(company, "mode", "") == "demonstracao" and not confirm_demo:
        raise HTTPException(422, "Confirme expressamente a utilização do ERP a partir da empresa de demonstração.")
    url = validate_url(company.api_url)
    try:
        key = cipher().decrypt(company.api_key_ciphertext.encode()).decode()
    except (InvalidToken, AttributeError):
        raise HTTPException(503, "Não foi possível decifrar a chave API.") from None
    with httpx.Client(timeout=25, follow_redirects=False) as http:
        yield ErpClient(http, url, company.api_enterprise, key)


def decimal_text(value):
    if value is None or value == "":
        return "0.00"
    return f"{Decimal(str(value)):.2f}"


# -- Prazos de pagamento -------------------------------------------------------------------

TERM_TYPES_IN = {"0": "apos", "3": "fim_mes", "4": "fim_mes_seguinte"}
TERM_TYPES_OUT = {value: int(key) for key, value in TERM_TYPES_IN.items()}


class PaymentTermsBinding(ErpBinding):
    resource = "payment_terms"
    synced_fields = ["code", "name", "term_type", "days", "discount_percent"]
    capabilities = {"read": True, "create": True, "update": True, "delete": True, "activate": False, "deactivate": False}
    reasons = {
        "activate": "Os prazos da Wintouch não têm estado ativo/inativo; o estado só existe na IAZELA.",
        "deactivate": "Os prazos da Wintouch não têm estado ativo/inativo: desative-o na IAZELA ou elimine-o no ERP.",
    }

    def canonical(self, ctx, raw):
        term_type = TERM_TYPES_IN.get(str(raw.get("TermType")), "outro")
        return {
            "code": str(raw.get("Code") or "").strip().upper(),
            "name": str(raw.get("Name") or "").strip(),
            "term_type": term_type,
            "days": raw.get("Days") if term_type in ("apos", "outro") else None,
            "discount_percent": decimal_text(raw.get("Discount")),
        }

    def _fields(self, values):
        if values["term_type"] not in TERM_TYPES_OUT:
            raise ErpRejected("Este tipo de prazo não pode ser enviado para a Wintouch.")
        discount = Decimal(values["discount_percent"] or "0")
        return {
            "Code": values["code"],
            "Name": values["name"],
            "TermType": TERM_TYPES_OUT[values["term_type"]],
            "Days": values["days"] if values["term_type"] == "apos" else None,
            "Discount": discount if discount else None,
        }

    def create_payload(self, ctx, values, external_id):
        return {"ID": external_id, **self._fields(values)}

    def update_payload(self, ctx, raw, values):
        return {**raw, **self._fields(values)}


# -- Armazéns --------------------------------------------------------------------------------


class WarehousesBinding(ErpBinding):
    resource = "warehouses"
    synced_fields = ["code", "name", "active"]
    capabilities = {"read": True, "create": True, "update": True, "delete": True, "activate": True, "deactivate": True}
    import_options = [TableField("store_id", "Estabelecimento dos armazéns importados", "store", required=False)]

    def canonical(self, ctx, raw):
        return {
            "code": str(raw.get("Code") or "").strip(),
            "name": str(raw.get("Name") or "").strip(),
            "active": not bool(raw.get("IsDisabled")),
        }

    def create_values(self, ctx, canonical, options):
        from sqlalchemy import select

        from .models import Store

        stores = list(ctx.db.scalars(select(Store.id)))
        store_id = options.get("store_id") or (stores[0] if len(stores) == 1 else None)
        if store_id not in stores:
            raise HTTPException(422, "Escolha o estabelecimento dos armazéns importados.")
        return {**canonical, "store_id": store_id}

    def create_payload(self, ctx, values, external_id):
        return {"ID": external_id, "Code": values["code"], "Name": values["name"], "IsDisabled": not values["active"]}

    def update_payload(self, ctx, raw, values):
        return {**raw, "Code": values["code"], "Name": values["name"], "IsDisabled": not values["active"]}

    def active_payload(self, raw, active):
        return {**raw, "IsDisabled": not active}
