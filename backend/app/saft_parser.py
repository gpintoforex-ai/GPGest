"""Offline AT XSD 1.1 validation and bounded, incremental extraction."""

import hashlib
import json
from contextlib import closing
from datetime import date
from decimal import Decimal
from functools import lru_cache
from pathlib import Path

import xmlschema
from lxml import etree

VERSION = "1.04_01"
NAMESPACE = "urn:OECD:StandardAuditFile-Tax:PT_" + VERSION
NS = "{" + NAMESPACE + "}"
SCHEMA = Path(__file__).with_name("saft_schemas") / "SAFTPT1.04_01.xsd"
PAYMENT_CODES = {"CC", "CD", "CH", "CI", "CO", "CS", "DE", "LC", "MB", "NU", "OU", "PR", "TB", "TR"}
INVOICE_TYPES = {"FT", "FS", "FR", "ND", "NC"}


class SaftError(ValueError):
    pass


def canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def digest(value):
    return hashlib.sha256(canonical(value).encode()).hexdigest()


def text(node, path, default=""):
    value = node.find("/".join(NS + part for part in path.split("/")))
    return (value.text or "").strip() if value is not None else default


def children(node, path):
    return node.findall("/".join(NS + part for part in path.split("/")))


def raw_tree(node):
    if not len(node):
        return (node.text or "").strip()
    result = {}
    for child in node:
        if not isinstance(child.tag, str):
            continue
        key = etree.QName(child).localname
        value = raw_tree(child)
        result.setdefault(key, []).append(value)
    return {key: values[0] if len(values) == 1 else values for key, values in result.items()}


def cents(value):
    try:
        number = Decimal(value)
        if (
            not number.is_finite()
            or number < 0
            or number > Decimal("999999999999")
            or number != number.quantize(Decimal("0.01"))
        ):
            raise ValueError()
        return int(number * 100)
    except (ValueError, ArithmeticError):
        raise SaftError(
            "Montante inválido no SAF-T. Os totais devem ser positivos, em euros e cêntimos."
        ) from None


@lru_cache(maxsize=1)
def schema():
    return xmlschema.XMLSchema11(SCHEMA, allow="local", defuse="always", use_fallback=False)


def validate(path):
    try:
        # File-like input + allow=none forbids instance location hints/resources.
        # Complete the validator iteration before closing its input: xmlschema
        # lazy identity counters own nested generators which need to reach EOF.
        first_error = None
        with open(path, "rb") as stream:
            resource = xmlschema.XMLResource(stream, lazy=2, defuse="always", allow="none")
            if resource.root.tag != NS + "AuditFile":
                raise SaftError(
                    "Namespace ou versão não suportados. Selecione SAF-T (PT) 1.04_01 completo."
                )
            with closing(schema().iter_errors(resource, use_location_hints=False)) as errors:
                for error in errors:
                    if first_error is None:
                        first_error = ((error.path or "XML")[:250], str(error.reason)[:500])
        if first_error:
            location, reason = first_error
            raise SaftError(
                f"Estrutura SAF-T incompatível com o XSD oficial 1.04_01 em {location}: {reason}"
            )
    except SaftError:
        raise
    except Exception:
        raise SaftError(
            "XML inválido ou inseguro. Não são permitidos DTD, entidades externas ou recursos remotos."
        ) from None


def payment(node, date_fallback=""):
    return {
        "code": text(node, "PaymentMechanism"),
        "amount_cents": cents(text(node, "PaymentAmount")),
        "date": text(node, "PaymentDate") or date_fallback,
    }


def document(node, kind):
    invoice = kind == "invoice"
    number = text(node, "InvoiceNo" if invoice else "PaymentRefNo")
    typ = text(node, "InvoiceType" if invoice else "PaymentType")
    issued = text(node, "InvoiceDate" if invoice else "TransactionDate")
    lines = children(node, "Line")
    if len(lines) > 10000:
        raise SaftError("Um documento excede 10 000 linhas. Divida o ficheiro na origem.")
    # GrossTotal is unsigned. NC has fiscal negative meaning; receipt direction
    # comes from its debit/credit lines, never from the referenced invoice type.
    net_direction = sum(
        Decimal(text(line, "CreditAmount", "0")) - Decimal(text(line, "DebitAmount", "0"))
        for line in lines
    )
    direction = (
        (-1 if typ == "NC" else 1)
        if invoice
        else (1 if net_direction > 0 else -1 if net_direction < 0 else 0)
    )
    return {
        "kind": kind,
        "document": number,
        "type": typ,
        "series": number.rsplit("/", 1)[0],
        "number": number.rsplit("/", 1)[-1],
        "date": issued,
        "customer_id": text(node, "CustomerID"),
        "status": text(
            node, "DocumentStatus/InvoiceStatus" if invoice else "DocumentStatus/PaymentStatus"
        ),
        "gross_cents": cents(text(node, "DocumentTotals/GrossTotal")),
        "net_cents": cents(text(node, "DocumentTotals/NetTotal")),
        "tax_cents": cents(text(node, "DocumentTotals/TaxPayable")),
        "direction": direction,
        "line_count": len(lines),
        "payments": [
            payment(item)
            for item in children(node, "DocumentTotals/Payment" if invoice else "PaymentMethod")
        ],
        "references": list(
            dict.fromkeys(
                text(item, "OriginatingON")
                for line in lines
                for item in children(line, "SourceDocumentID" if not invoice else "References")
                if text(item, "OriginatingON")
            )
        ),
        "raw": raw_tree(node),
    }


def extract(path):
    with open(path, "rb") as stream:
        yield from _extract(stream)


def _extract(stream):
    """Yield complete records only. Clear siblings to bound memory to one document."""
    header, found_sales, count, depth = None, False, 0, 0
    expected, observed = {}, {"SalesInvoices": 0, "Payments": 0}
    try:
        for event, node in etree.iterparse(
            stream,
            events=("start", "end"),
            resolve_entities=False,
            load_dtd=False,
            no_network=True,
            huge_tree=False,
            remove_comments=True,
        ):
            if event == "start":
                depth += 1
                if depth > 64:
                    raise SaftError("XML demasiado profundo.")
                if node.getparent() is None and node.getroottree().docinfo.doctype:
                    raise SaftError("Não são permitidos DTD ou entidades no XML.")
                continue
            depth -= 1
            name = etree.QName(node).localname
            parent = etree.QName(node.getparent()).localname if node.getparent() is not None else ""
            item = None
            if name == "NumberOfEntries" and parent in observed:
                expected[parent] = int(node.text)
            if name == "Header" and parent == "AuditFile":
                header = raw_tree(node)
                if header["AuditFileVersion"] != VERSION or header["CurrencyCode"] != "EUR":
                    raise SaftError("São suportados apenas SAF-T (PT) 1.04_01 com totais em EUR.")
                if header["StartDate"] > header["EndDate"]:
                    raise SaftError("O período do cabeçalho é inválido.")
                yield "header", header
            elif (name, parent) in {("Customer", "MasterFiles"), ("Product", "MasterFiles")}:
                kind = "customer" if name == "Customer" else "product"
                item = {
                    "kind": kind,
                    "code": text(node, "CustomerID" if kind == "customer" else "ProductCode"),
                    "raw": raw_tree(node),
                }
            elif (name, parent) in {("Invoice", "SalesInvoices"), ("Payment", "Payments")}:
                observed[parent] += 1
                item = document(node, "invoice" if name == "Invoice" else "receipt")
                if not header or not header["StartDate"] <= item["date"] <= header["EndDate"]:
                    raise SaftError("Um documento tem data fora do período indicado no cabeçalho.")
                date.fromisoformat(item["date"])
            elif name == "SalesInvoices":
                found_sales = True
            if name in observed and expected.get(name) != observed[name]:
                raise SaftError(
                    "A quantidade de documentos não corresponde ao NumberOfEntries do ficheiro. Volte a exportar na origem."
                )
            if item:
                count += 1
                if count > 200000:
                    raise SaftError("O ficheiro excede 200 000 registos. Exporte períodos menores.")
                identity = [
                    item["kind"],
                    item.get("type", ""),
                    item.get("document", item.get("code")),
                ]
                encoded = canonical(item)
                if len(encoded.encode()) > 5_000_000:
                    raise SaftError("Um registo excede 5 MB.")
                yield "record", (item["kind"] + ":" + digest(identity), digest(item), item)
            if item or name in {
                "Header",
                "StockMovement",
                "WorkDocument",
                "Transaction",
                "Supplier",
                "TaxTableEntry",
            }:
                node.clear()
                while node.getprevious() is not None:
                    del node.getparent()[0]
        if not header or not found_sales:
            raise SaftError(
                "O ficheiro não contém Header e SourceDocuments/SalesInvoices. Não é um SAF-T de vendas completo suportado."
            )
    except SaftError:
        raise
    except (etree.XMLSyntaxError, ValueError, KeyError):
        raise SaftError(
            "Não foi possível ler a estrutura obrigatória do SAF-T. Nenhum documento foi importado."
        ) from None
