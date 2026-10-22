"""Explicit Wintouch Cloud writes, using documented v1 contracts and one persisted attempt."""

import hashlib
from collections import Counter
from contextlib import contextmanager
from decimal import Decimal as D
from uuid import uuid4

import httpx
from fastapi import HTTPException

from .db import request_company
from .invoice_evidence import nif_key
from .platform import cipher, validate_url
from .wintouch_sales import Reader
from .wintouch_settlement import encode_payload, rejection_message


@contextmanager
def connection(db, user, write=False, resource=None, confirm_demo=False):
    from .operating_modes import policy

    config = policy(db)
    company = request_company(db)
    if (
        user.role != "administrador"
        or not company
        or config["mode"] != "integrated"
        or config["provider"] != "wintouch"
        or not config["active"]
        or (write and not config["writes"])
    ):
        raise HTTPException(
            409,
            "A operação exige administrador e ligação Wintouch Cloud ativa; a escrita tem de estar autorizada nas Integrações.",
        )
    if resource and resource not in config.get("resources", []):
        raise HTTPException(409, "Este recurso ERP está desativado nas Integrações.")
    if (
        write
        and resource
        and not config.get("capabilities", {}).get(
            "write_products" if resource == "products" else "write_purchase_documents"
        )
    ):
        raise HTTPException(409, "A capacidade de criação não está autorizada para este conector.")
    if write and company.mode == "demonstracao" and not confirm_demo:
        raise HTTPException(
            409,
            "Confirme expressamente a escrita no ERP externo a partir do ambiente de demonstração.",
        )
    url = validate_url(company.api_url)
    key = cipher().decrypt(company.api_key_ciphertext.encode()).decode()
    with httpx.Client(timeout=25, follow_redirects=False) as client:
        reader = Reader(client, url, company.api_enterprise, key)
        reader.origin_fingerprint = hashlib.sha256(
            (url + "|" + company.api_enterprise).encode()
        ).hexdigest()
        yield reader


def options(reader):
    result = {}
    for resource in (
        "stock_units",
        "vats",
        "product_1st_categories",
        "warehouses",
        "sectors",
        "currencies",
        "document_types",
        "document_series",
        "price_policies",
    ):
        result[resource] = list(reader.collection(resource))
    return result


def choose(options, resource, identifier):
    row = next((r for r in options[resource] if r.get("ID") == identifier), None)
    if (
        not row
        or row.get("Enabled") is False
        or row.get("IsEnabled") is False
        or row.get("DeletedDate")
    ):
        raise HTTPException(422, "Selecione uma opção ativa do Wintouch: " + resource)
    return row


def product_payload(article, mapping, catalogs, identifier):
    unit = choose(catalogs, "stock_units", mapping["unit_id"])
    vat = choose(catalogs, "vats", mapping["vat_id"])
    category = choose(catalogs, "product_1st_categories", mapping["category_id"])
    if str(unit.get("Code", "")).casefold() != article["unit"].casefold():
        raise HTTPException(
            422, "A unidade selecionada no ERP deve corresponder à unidade da proposta."
        )
    if D(str(vat["TaxRate"])) != D(article["vat_rate"]):
        raise HTTPException(422, "O IVA ERP não corresponde ao IVA do artigo proposto.")
    result = {
        "ID": identifier,
        "Code": article["code"],
        "Name": article["name"],
        "Enabled": True,
        "Product1ndCategoryID": category["ID"],
        "StockUnitID": unit["ID"],
        "SalesUnitID": unit["ID"],
        "PurchasesUnitID": unit["ID"],
        "VATSalesID": vat["ID"],
        "VATPurchasesID": vat["ID"],
        "IsService": False,
        "IsBatchesEnabled": bool(article.get("lots")),
        "ProductBarCodes": [
            {"ProductID": identifier, "BarCode": v, "UnitID": unit["ID"]}
            for v in article.get("barcodes", [])
        ],
    }
    if D(article.get("sale_net", 0)):
        price_policy = choose(catalogs, "price_policies", mapping["price_policy_id"])
        result["ProductPrices"] = [
            {
                "ProductID": identifier,
                "PricePolicyID": price_policy["ID"],
                "Row": 1,
                "PriceWithoutVAT": D(article["sale_net"]),
                "PriceWithVAT": D(article["sale_net"]) * (1 + D(article["vat_rate"]) / 100),
            }
        ]
    if vat.get("VATExemptionMotiveID"):
        result["VATExemptionMotiveID"] = vat["VATExemptionMotiveID"]
    return result


def purchase_payload(saved, mapping, catalogs, entity, products, identifier):
    typ = choose(catalogs, "document_types", mapping["document_type_id"])
    serie = choose(catalogs, "document_series", mapping["document_serie_id"])
    warehouse = choose(catalogs, "warehouses", mapping["warehouse_id"])
    sector = choose(catalogs, "sectors", mapping["sector_id"])
    currency = choose(catalogs, "currencies", mapping["currency_id"])
    # Class 32 is the purchase invoice already used by the existing read adapter.
    if (
        typ.get("Class") != 32
        or typ.get("IsRefund")
        or serie.get("DocumentTypeID") != typ["ID"]
        or currency.get("Code") != "EUR"
        or saved["document_type"] != "FT"
    ):
        raise HTTPException(
            422, "Use uma fatura de compra de classe 32, série correspondente e EUR."
        )
    if (
        entity.get("IsSupplier") is not True
        or entity.get("Enabled") is False
        or nif_key(entity.get("VATNumber")) != nif_key(saved["supplier_nif"])
    ):
        raise HTTPException(422, "Fornecedor ERP inativo ou NIF divergente.")
    result = {
        "ID": identifier,
        "DocumentTypeID": typ["ID"],
        "DocumentSerieID": serie["ID"],
        "EntityID": entity["ID"],
        "SectorID": sector["ID"],
        "CurrencyID": currency["ID"],
        "VATIncluded": False,
        "ExchangeRate": D(1),
        "Date": saved["document_date"] + "T12:00:00",
        "DueDate": saved["due_date"] + "T12:00:00",
        "ExternalDocument": saved["document"],
        "Notes": saved["note"],
        "Payments": [],
        "ProductDocumentDetails": [],
    }
    for i, line in enumerate(saved["lines"]):
        product = products[line["article_id"]]
        if (
            product.get("Enabled") is False
            or product.get("IsBatchesEnabled")
            or any(product.get("Dimension" + str(n) + "ID") for n in (1, 2, 3))
        ):
            raise HTTPException(
                422,
                "Artigo descontinuado ou com lotes/dimensões: lance a compra no Wintouch até existir mapeamento validado desses detalhes.",
            )
        unit_id = product.get("PurchasesUnitID") or product.get("StockUnitID")
        choose(catalogs, "stock_units", unit_id)
        if unit_id != product.get("StockUnitID"):
            raise HTTPException(
                422, "Conversão entre unidade de compra e stock ainda não validada."
            )
        vat = choose(catalogs, "vats", product.get("VATPurchasesID"))
        if D(str(vat["TaxRate"])) != D(line["vat"]):
            raise HTTPException(422, "O IVA faturado difere da configuração ERP do artigo.")
        net = D(line["price"]) * (1 - D(line["discount"]) / 100)
        line_id = str(uuid4())
        detail = {
            "ID": line_id,
            "ProductDocumentMasterID": identifier,
            "Row": i + 1,
            "ProductID": product["ID"],
            "Name": line["description"],
            "WharehouseID": warehouse["ID"],
            "UnitID": unit_id,
            "StockUnitFactor": D(1),
            "FormulaVariable1": D(line["quantity"]),
            "UnitPriceWithoutVAT": net,
            "UnitPriceWithVAT": net * (1 + D(line["vat"]) / 100),
            "RowUnitPrice": net,
            "VATID": vat["ID"],
            "VATTax": D(line["vat"]),
            "ProductDocumentDetailsDimensions": [
                {
                    "ID": str(uuid4()),
                    "ProductDocumentDetailID": line_id,
                    "Quantity": D(line["quantity"]),
                    "StockQuantity": D(line["quantity"]),
                }
            ],
        }
        if vat.get("VATExemptionMotiveID"):
            detail["VATExemptionMotiveID"] = vat["VATExemptionMotiveID"]
        result["ProductDocumentDetails"].append(detail)
    return result


def verify_result(operation, remote):
    expected = operation["payload"]
    if (
        not isinstance(remote, dict)
        or remote.get("ID") != expected["ID"]
        or remote.get("DeletedDate")
    ):
        return False
    if operation["kind"] == "product":
        fields = (
            "Code",
            "Name",
            "StockUnitID",
            "PurchasesUnitID",
            "VATPurchasesID",
            "VATSalesID",
            "Product1ndCategoryID",
        )
        codes = {b.get("BarCode") for b in remote.get("ProductBarCodes", [])}
        prices = {
            (p.get("PricePolicyID"), D(str(p.get("PriceWithoutVAT", -1))))
            for p in remote.get("ProductPrices", [])
        }
        return (
            all(remote.get(k) == expected.get(k) for k in fields)
            and remote.get("Enabled") is True
            and all(b["BarCode"] in codes for b in expected.get("ProductBarCodes", []))
            and all(
                (p["PricePolicyID"], D(str(p["PriceWithoutVAT"]))) in prices
                for p in expected.get("ProductPrices", [])
            )
        )
    if any(
        remote.get(k) != expected[k]
        for k in (
            "EntityID",
            "DocumentTypeID",
            "DocumentSerieID",
            "CurrencyID",
            "SectorID",
            "ExternalDocument",
        )
    ):
        return False
    if any(str(remote.get(k, ""))[:10] != expected[k][:10] for k in ("Date", "DueDate")) or nif_key(
        remote.get("VATNumber") or (remote.get("CEntity") or {}).get("VATNumber")
    ) != nif_key(operation["supplier_nif"]):
        return False

    def lines(data):
        return Counter(
            (
                r.get("ProductID"),
                r.get("WharehouseID"),
                r.get("UnitID"),
                D(str(r.get("UnitPriceWithoutVAT", -1))),
                D(str(r.get("VATTax", -1))),
                sum(
                    (
                        D(str(v.get("Quantity", 0)))
                        for v in r.get("ProductDocumentDetailsDimensions", [])
                    ),
                    D(0),
                ),
            )
            for r in data.get("ProductDocumentDetails", [])
        )

    if lines(remote) != lines(expected):
        return False

    def cents(prefix):
        return int(sum((D(str(remote.get(prefix + str(i), 0))) for i in range(1, 5)), D(0)) * 100)

    return (
        cents("BaseValue") + cents("VATValue") == operation["total_cents"]
        and cents("VATValue") == operation["tax_cents"]
    )


def dispatch_once(reader, operation, persist, reconcile=False):
    """persist must durably save sending BEFORE POST. A GET is the only recovery action."""
    if operation["origin"] != reader.origin_fingerprint:
        raise HTTPException(409, "A ligação/empresa ERP mudou. Recupere a origem desta operação.")
    if operation["state"] == "confirmed":
        return operation
    resource = "products" if operation["kind"] == "product" else "product_documents"
    if operation["state"] == "prepared" and not reconcile:
        operation["state"] = "sending"
        persist(operation)
        try:

            def numeric(value):
                keys = {
                    "PriceWithoutVAT",
                    "PriceWithVAT",
                    "ExchangeRate",
                    "StockUnitFactor",
                    "FormulaVariable1",
                    "UnitPriceWithoutVAT",
                    "UnitPriceWithVAT",
                    "RowUnitPrice",
                    "VATTax",
                    "Quantity",
                    "StockQuantity",
                }
                if isinstance(value, list):
                    return [numeric(v) for v in value]
                if isinstance(value, dict):
                    return {k: D(str(v)) if k in keys else numeric(v) for k, v in value.items()}
                return value

            response = reader.client.post(
                reader.url + "/" + resource,
                content=encode_payload(numeric(operation["payload"])).encode(),
                headers={**reader.headers, "Content-Type": "application/json"},
            )
            if response.status_code not in (200, 201, 202, 204, 409):
                operation["state"] = "failed" if 400 <= response.status_code < 500 else "uncertain"
                operation["error"] = rejection_message(response)
                persist(operation)
                return operation
        except httpx.HTTPError:
            operation["state"], operation["error"] = (
                "uncertain",
                "Resposta de escrita incerta. Verifique o mesmo identificador; não repita a criação.",
            )
            persist(operation)
            return operation
    try:
        remote = reader.get(
            resource + "/" + operation["payload"]["ID"],
            {
                "embed": "CEntity,ProductDocumentDetails,ProductDocumentDetails.ProductDocumentDetailsDimensions"
            }
            if resource == "product_documents"
            else {"embed": "ProductBarCodes,ProductPrices"},
        )
        if verify_result(operation, remote):
            operation["state"], operation["error"] = "confirmed", ""
            operation["external_id"] = remote["ID"]
            if operation["kind"] == "product":
                operation["verified_product"] = {
                    k: remote[k]
                    for k in ("ID", "Code", "Name", "Enabled", "LastCostGlobal", "IsBatchesEnabled")
                    if k in remote
                }
        else:
            operation["state"], operation["error"] = (
                "uncertain",
                "O ERP não confirmou todos os campos e totais preparados. Reveja o documento na origem.",
            )
    except Exception:
        operation["state"], operation["error"] = (
            "uncertain",
            "Não foi possível confirmar o resultado por consulta. Nenhuma nova criação será repetida.",
        )
    persist(operation)
    return operation
