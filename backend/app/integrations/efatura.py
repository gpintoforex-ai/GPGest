"""Read-only AT Invoices operation; contract: bundled official fatshareInvoices.wsdl."""
import base64
import os
import ssl
from datetime import date, datetime, timezone
from decimal import Decimal
from pathlib import Path
from xml.etree import ElementTree as ET

import httpx
from cryptography import x509
from cryptography.hazmat.primitives import padding, serialization
from cryptography.hazmat.primitives.asymmetric import padding as rsa_padding
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes

SOAP = "http://schemas.xmlsoap.org/soap/envelope/"
SEC = "http://schemas.xmlsoap.org/ws/2002/12/secext"
NS = "http://factemi.at.min_financas.pt/fatshareInvoices"
ENDPOINTS = {
    "testes": "https://servicos.portaldasfinancas.gov.pt:725/fatshare/ws/fatshareFaturas",
    "producao": "https://servicos.portaldasfinancas.gov.pt:425/fatshare/ws/fatshareFaturas",
}


class EfaturaError(ValueError):
    pass


def configured(environment):
    suffix = "TEST" if environment == "testes" else "PROD"
    return all(os.getenv(f"EFATURA_{name}_{suffix}") and Path(os.environ[f"EFATURA_{name}_{suffix}"]).is_file() for name in ("CERT", "KEY", "PUBLIC_KEY"))


def security(root, username, password, public_key):
    key = os.urandom(16)
    def encrypt(value):
        padder = padding.PKCS7(128).padder()
        padded = padder.update(value.encode()) + padder.finalize()
        encoder = Cipher(algorithms.AES(key), modes.ECB()).encryptor()
        return base64.b64encode(encoder.update(padded) + encoder.finalize()).decode()
    token = ET.SubElement(ET.SubElement(ET.SubElement(root, f"{{{SOAP}}}Header"), f"{{{SEC}}}Security"), f"{{{SEC}}}UsernameToken")
    values = {"Username": username, "Password": encrypt(password), "Nonce": base64.b64encode(public_key.encrypt(key, rsa_padding.PKCS1v15())).decode(), "Created": encrypt(datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z"))}
    for name, value in values.items():
        ET.SubElement(token, f"{{{SEC}}}{name}").text = value


def request_xml(nif, username, password, public_key, start, end, page):
    root = ET.Element(f"{{{SOAP}}}Envelope")
    security(root, username, password, public_key)
    request = ET.SubElement(ET.SubElement(root, f"{{{SOAP}}}Body"), f"{{{NS}}}InvoicesRequest")
    for name, value in (("CustomerTaxID", nif), ("StartDate", start), ("EndDate", end)):
        ET.SubElement(request, f"{{{NS}}}{name}").text = str(value)
    pagination = ET.SubElement(request, f"{{{NS}}}Pagination")
    ET.SubElement(pagination, f"{{{NS}}}nPage").text = str(page)
    ET.SubElement(pagination, f"{{{NS}}}nDocsPage").text = "300"
    return ET.tostring(root, encoding="utf-8", xml_declaration=True)


def parse_response(raw):
    if len(raw) > 20_000_000 or b"<!DOCTYPE" in raw.upper() or b"<!ENTITY" in raw.upper():
        raise EfaturaError("Resposta inválida da AT.")
    try:
        root = ET.fromstring(raw)
        def text(node, name):
            found = node.find(f".//{{*}}{name}")
            return found.text.strip() if found is not None and found.text else ""
        if root.find(f".//{{{SOAP}}}Fault") is not None:
            raise EfaturaError("A AT recusou a consulta. Verifique as credenciais e permissões.")
        status = text(root, "EstadoOperacao")
        if status != "200":
            raise EfaturaError(f"A AT recusou a consulta (código {status or 'desconhecido'}). Verifique as credenciais e o período.")
        rows = []
        for invoice in root.findall(".//{*}Invoice"):
            kind = text(invoice, "InvoiceType")
            def cents(name):
                value = Decimal(text(invoice, name)) * 100
                if not value.is_finite() or value != value.to_integral_value() or not 0 <= value <= 10**15:
                    raise ValueError("Montante inválido")
                return int(value)
            nif = text(invoice, "TaxRegistrationNumber")
            rows.append({"supplier_nif": nif, "supplier_name": nif, "document": text(invoice, "InvoiceNo"), "document_type": kind, "document_date": date.fromisoformat(text(invoice, "InvoiceDate")).isoformat(), "amount_cents": cents("GrossTotal") * (-1 if kind in {"NC", "RE"} else 1), "tax_cents": cents("TaxPayable")})
        pages = max(1, int(text(root, "totalPages") or "1")) if not rows else int(text(root, "totalPages") or "1")
        page = int(text(root, "nPage") or "1")
        if not 1 <= pages <= 100 or not 1 <= page <= pages:
            raise EfaturaError("A consulta excede o limite. Selecione um período menor.")
        return rows, pages, page
    except (ET.ParseError, ArithmeticError, ValueError) as exc:
        if isinstance(exc, EfaturaError):
            raise
        raise EfaturaError("A resposta da AT não tem o formato esperado.") from exc


def fetch_invoices(config, password, start, end):
    environment = config.environment
    if not configured(environment):
        raise EfaturaError("Falta configurar no servidor o certificado, a chave privada e a chave pública da AT para este ambiente.")
    suffix = "TEST" if environment == "testes" else "PROD"
    raw = Path(os.environ[f"EFATURA_PUBLIC_KEY_{suffix}"]).read_bytes()
    try:
        public_key = serialization.load_pem_public_key(raw)
    except ValueError:
        try:
            public_key = x509.load_pem_x509_certificate(raw).public_key()
        except ValueError:
            public_key = x509.load_der_x509_certificate(raw).public_key()
    context = ssl.create_default_context()
    context.load_cert_chain(os.environ[f"EFATURA_CERT_{suffix}"], os.environ[f"EFATURA_KEY_{suffix}"], password=os.getenv(f"EFATURA_KEY_PASSWORD_{suffix}"))
    rows = []
    with httpx.Client(verify=context, timeout=45, follow_redirects=False) as client:
        for page in range(1, 101):
            response = client.post(ENDPOINTS[environment], content=request_xml(config.nif, config.username, password, public_key, start, end, page), headers={"Content-Type": "text/xml; charset=utf-8", "SOAPAction": '""'})
            if response.status_code != 200:
                raise EfaturaError("Não foi possível consultar a AT. Verifique o certificado e tente novamente.")
            result, pages, returned_page = parse_response(response.content)
            if returned_page != page:
                raise EfaturaError("A paginação devolvida pela AT é inconsistente.")
            rows.extend(result)
            if page >= pages:
                return rows
    raise EfaturaError("Selecione um período menor para a consulta.")
