"""CSV/XLSX purchase lists, with explicit validation and no formula execution."""
import csv
import io
import re
import unicodedata
import zipfile
from datetime import date, datetime, timedelta
from decimal import Decimal, InvalidOperation
from xml.etree import ElementTree as ET

NS = {"m": "http://schemas.openxmlformats.org/spreadsheetml/2006/main"}


def normal(value):
    return re.sub(r"[^a-z0-9]", "", "".join(c for c in unicodedata.normalize("NFD", value.lower()) if not unicodedata.combining(c)))


ALIASES = {
    "supplier_nif": ["NIF fornecedor", "NIF emitente", "NIF do emitente", "NIF", "Contribuinte", "NIF do fornecedor"],
    "supplier_name": ["Fornecedor", "Nome emitente", "Nome do emitente", "Designação", "Nome fornecedor", "Emitente"],
    "document": ["Documento", "Número documento", "Nº documento", "Número fatura", "Nº fatura", "Nº da fatura", "Número da fatura", "N.º Fatura", "Identificação da fatura", "Nº Fatura / ATCUD"],
    "document_type": ["Tipo", "Tipo documento", "Tipo de documento", "Tipo fatura", "Tipo de fatura"],
    "document_date": ["Data", "Data emissão", "Data de emissão", "Data documento", "Data da fatura", "Data fatura"],
    "amount_cents": ["Total", "Valor total", "Total documento", "Total com IVA", "Valor total da fatura", "Montante", "Valor da fatura"],
    "tax_cents": ["IVA", "Valor IVA", "Total IVA", "Imposto", "Valor do IVA"],
    "base_cents": ["Base Tributável", "Base tributaria", "Valor base", "Total sem IVA"],
}


def worksheet(data):
    try:
        with zipfile.ZipFile(io.BytesIO(data)) as archive:
            if sum(i.file_size for i in archive.infolist()) > 20_000_000:
                raise ValueError("O ficheiro descomprimido excede 20 MB.")
            def xml(path):
                raw = archive.read(path)
                if b"<!DOCTYPE" in raw.upper() or b"<!ENTITY" in raw.upper():
                    raise ValueError("O ficheiro XML não é permitido.")
                return ET.fromstring(raw)
            shared = []
            if "xl/sharedStrings.xml" in archive.namelist():
                shared = ["".join(x.itertext()) for x in xml("xl/sharedStrings.xml").findall("m:si", NS)]
            workbook = xml("xl/workbook.xml") if "xl/workbook.xml" in archive.namelist() else None
            props = workbook.find("m:workbookPr", NS) if workbook is not None else None
            epoch = date(1904, 1, 1) if props is not None and props.get("date1904") in {"1", "true"} else date(1899, 12, 30)
            rows = []
            for row in xml("xl/worksheets/sheet1.xml").findall(".//m:row", NS):
                values = {}
                for cell in row.findall("m:c", NS):
                    if cell.find("m:f", NS) is not None:
                        raise ValueError("O ficheiro contém fórmulas. Exporte uma lista com valores.")
                    letters = re.sub(r"\d", "", cell.attrib["r"])
                    index = 0
                    for char in letters:
                        index = index * 26 + ord(char) - 64
                    if not 1 <= index <= 256:
                        raise ValueError("O ficheiro tem demasiadas colunas.")
                    value = cell.find("m:v", NS)
                    values[index - 1] = (shared[int(value.text)] if cell.get("t") == "s" and value is not None else "".join(cell.itertext())).strip()
                if values:
                    rows.append([values.get(i, "") for i in range(max(values) + 1)])
            return rows, epoch
    except (zipfile.BadZipFile, ET.ParseError, KeyError, IndexError) as exc:
        raise ValueError("O ficheiro XLSX não é válido.") from exc


def money(value):
    value = value.strip().replace("€", "").replace("EUR", "").replace("\u00a0", "").replace(" ", "")
    if "," in value:
        value = value.replace(".", "").replace(",", ".")
    amount = Decimal(value) * 100
    if not amount.is_finite() or amount != amount.to_integral_value() or abs(amount) > 10**15:
        raise ValueError("Valor inválido")
    return int(amount)


def parse_purchases(data, filename):
    if filename.lower().endswith(".xlsx"):
        rows, epoch = worksheet(data)
    elif filename.lower().endswith(".csv"):
        try:
            text = data.decode("utf-8-sig")
        except UnicodeDecodeError:
            text = data.decode("cp1252")
        try:
            dialect = csv.Sniffer().sniff(text[:10000], delimiters=";,\t")
            rows = list(csv.reader(io.StringIO(text), dialect))
        except csv.Error:
            rows = list(csv.reader(io.StringIO(text), delimiter=";"))
        epoch = None
    else:
        raise ValueError("Selecione um ficheiro CSV ou XLSX.")
    if len(rows) > 10020:
        raise ValueError("O limite é 10 000 compras por ficheiro.")
    required = {"supplier_nif", "document", "document_date", "amount_cents"}
    columns = {}
    header = -1
    for position, row in enumerate(rows[:20]):
        normalized = [normal(value) for value in row]
        found = {key: next((i for i, name in enumerate(normalized) if name in {normal(v) for v in aliases}), -1) for key, aliases in ALIASES.items()}
        combined_issuer = normalized.index("emitente") if "emitente" in normalized and found["supplier_nif"] < 0 else -1
        if combined_issuer >= 0:
            found["supplier_nif"] = combined_issuer
        if all(found[k] >= 0 for k in required):
            columns, header = found, position
            break
    if header < 0:
        raise ValueError("Não foi possível reconhecer as colunas. Use o modelo CSV: NIF fornecedor;Fornecedor;Documento;Tipo;Data;Total;IVA.")
    result = []
    for line, row in enumerate(rows[header + 1:], header + 2):
        if not any(value.strip() for value in row):
            continue
        def value(key):
            index = columns[key]
            return row[index].strip() if 0 <= index < len(row) else ""
        try:
            nif = value("supplier_nif")
            issuer_name = ""
            if combined_issuer >= 0:
                issuer = re.fullmatch(r"(\d{9})\s*-\s*(.+)", nif)
                if not issuer:
                    raise ValueError("Emitente deve indicar NIF e nome do fornecedor")
                nif, issuer_name = issuer.groups()
            if nif.startswith("PT"):
                nif = nif[2:]
            if not re.fullmatch(r"\d{9}", nif):
                raise ValueError("NIF do fornecedor deve ter nove dígitos")
            document = value("document")
            if normal(rows[header][columns["document"]]) == normal("Nº Fatura / ATCUD"):
                document = document.rsplit(" / ", 1)[0].strip()
            if not 1 <= len(document) <= 100:
                raise ValueError("Número de documento inválido")
            kind = value("document_type").upper()
            kind = {"FATURA": "FT", "FACTURA": "FT", "FATURA SIMPLIFICADA": "FS", "FATURA-RECIBO": "FR", "NOTA DE CRÉDITO": "NC", "NOTA DE DÉBITO": "ND"}.get(kind, kind)
            if not kind:
                kind = document.split()[0] if document.split()[0] in {"FT", "FS", "FR", "NC", "ND"} else "FT"
            if kind not in {"FT", "FS", "FR", "NC", "ND", "RP", "RE", "CS", "LD", "RA"}:
                raise ValueError("Tipo de documento não reconhecido")
            date_text = value("document_date")
            parsed = None
            for fmt in ("%Y-%m-%d", "%d/%m/%Y", "%d-%m-%Y", "%Y/%m/%d"):
                try:
                    parsed = datetime.strptime(date_text, fmt).date()
                    break
                except ValueError:
                    pass
            if parsed is None and epoch and re.fullmatch(r"\d+(?:\.0+)?", date_text):
                parsed = epoch + timedelta(days=int(float(date_text)))
            if parsed is None:
                raise ValueError("Data inválida")
            amount = money(value("amount_cents"))
            if not amount:
                raise ValueError("O total deve ser diferente de zero")
            if kind in {"NC", "RE"}:
                amount = -abs(amount)
            elif amount < 0:
                raise ValueError("Valor negativo apenas permitido para notas de crédito ou estornos")
            tax = money(value("tax_cents")) if value("tax_cents") else 0
            base = money(value("base_cents")) if value("base_cents") else None
            if base is not None and kind in {"NC", "RE"}:
                base = -abs(base)
            name = issuer_name or value("supplier_name") or nif
            if len(name) > 300:
                raise ValueError("Nome do fornecedor demasiado longo")
            result.append({"supplier_nif": nif, "supplier_name": name, "document": document, "document_type": kind, "document_date": parsed.isoformat(), "amount_cents": amount, "base_cents": base, "tax_cents": abs(tax), "tax_provided": bool(value("tax_cents")), "line": line})
        except (ValueError, InvalidOperation, OverflowError) as exc:
            raise ValueError(f"Linha {line}: {exc}. Nenhuma compra foi importada.") from exc
    if not result:
        raise ValueError("O ficheiro não contém compras.")
    return result
