"""Mapped CSV/XLSX and CAMT.053 normalization. Never evaluate spreadsheet formulas."""
import csv
import hashlib
import io
import json
import re
import zipfile
from datetime import date, datetime, timedelta
from decimal import Decimal, InvalidOperation
from xml.etree import ElementTree as ET

from pydantic import Field
from .schemas import Strict


class Mapping(Strict):
    date: str = ""
    description: str = ""
    reference: str = ""
    value_date: str = ""
    amount: str = ""
    debit: str = ""
    credit: str = ""
    balance: str = ""
    account: str = ""
    currency: str = ""
    decimal_separator: str = Field(default=",", pattern=r"^[,.]$")
    order: str = Field(default="ascending", pattern=r"^(ascending|descending)$")


class MappingError(ValueError):
    def __init__(self, message, columns, mapping):
        super().__init__(message)
        self.preview = {"mapping_required": True, "columns": columns, "mapping": mapping.model_dump(),
            "rows": [], "new_count": 0, "duplicate_count": 0, "format": "Excel/CSV com mapeamento", "warnings": [message]}


def amount(value, separator):
    raw = str(value).strip().replace("\u00a0", "").replace(" ", "").replace("€", "")
    if not raw:
        return 0
    if raw.startswith("(") and raw.endswith(")"):
        raw = "-" + raw[1:-1]
    thousands = "." if separator == "," else ","
    if not re.fullmatch(r"[+-]?(?:\d+|[1-9]\d{0,2}(?:" + re.escape(thousands) + r"\d{3})+)(?:" + re.escape(separator) + r"\d{1,2})?", raw):
        raise ValueError("Formato de montante inválido. Reveja o separador decimal e o mapeamento.")
    if separator == ",":
        raw = raw.replace(".", "").replace(",", ".")
    else:
        raw = raw.replace(",", "")
    try:
        result = Decimal(raw) * 100
        if not result.is_finite() or result != result.to_integral_value() or abs(result) > 10**15:
            raise ValueError("Montante inválido ou com mais de duas casas decimais.")
        return int(result)
    except InvalidOperation as exc:
        raise ValueError("Montante inválido no extrato.") from exc


def stamp(value):
    raw = str(value).strip()
    for pattern in ("%Y-%m-%d", "%d/%m/%Y", "%d-%m-%Y", "%d.%m.%Y", "%Y-%m-%d %H:%M:%S"):
        try:
            return datetime.strptime(raw, pattern).date().isoformat()
        except ValueError:
            pass
    if re.fullmatch(r"\d{5}(?:\.\d+)?", raw):
        result = date(1899, 12, 30) + timedelta(days=int(Decimal(raw)))
        if 1900 <= result.year <= 2100:
            return result.isoformat()
    raise ValueError("Data inválida; use AAAA-MM-DD ou DD/MM/AAAA.")


def table(data, filename):
    if data.startswith(b"PK"):
        try:
            with zipfile.ZipFile(io.BytesIO(data)) as archive:
                if sum(i.file_size for i in archive.infolist()) > 20_000_000:
                    raise ValueError("Ficheiro Excel descomprimido demasiado grande.")
                shared = []
                if "xl/sharedStrings.xml" in archive.namelist():
                    shared = ["".join(node.itertext()) for node in ET.fromstring(archive.read("xl/sharedStrings.xml")).findall("{*}si")]
                sheet = ET.fromstring(archive.read("xl/worksheets/sheet1.xml"))
                rows = []
                for row in sheet.findall(".//{*}row"):
                    cells = {}
                    for cell in row.findall("{*}c"):
                        if cell.find("{*}f") is not None:
                            raise ValueError("O extrato contém fórmulas. Exporte apenas valores, sem fórmulas.")
                        index = 0
                        for char in re.sub(r"\d", "", cell.attrib["r"]):
                            index = index * 26 + ord(char) - 64
                        if not 1 <= index <= 256:
                            raise ValueError("O extrato excede o limite de 256 colunas.")
                        node = cell.find("{*}v")
                        value = node.text if node is not None and node.text is not None else "".join(cell.itertext())
                        cells[index - 1] = shared[int(value)] if cell.get("t") == "s" else value
                    if cells:
                        rows.append([cells.get(i, "") for i in range(max(cells) + 1)])
                return rows, True
        except (zipfile.BadZipFile, KeyError, ET.ParseError, IndexError) as exc:
            raise ValueError("Ficheiro XLSX inválido.") from exc
    if not filename.lower().endswith((".csv", ".txt")):
        raise ValueError("Selecione CSV, XLSX, CAMT.053 XML ou um dos formatos bancários suportados.")
    try:
        raw = data.decode("utf-8-sig")
    except UnicodeDecodeError:
        raw = data.decode("cp1252")
    try:
        dialect = csv.Sniffer().sniff(raw[:10000], delimiters=";,\t|")
    except csv.Error as exc:
        raise ValueError("Não foi possível identificar as colunas do CSV.") from exc
    return list(csv.reader(io.StringIO(raw), dialect)), False


def fingerprints(rows):
    counts = {}
    for row in rows:
        # Metadata about download time, file name and line position is not economic identity.
        payload = {k: row[k] for k in ("movement_date", "value_date", "description", "reference", "amount_cents", "balance_cents")}
        signature = hashlib.sha256(json.dumps(payload, sort_keys=True, ensure_ascii=False).encode()).hexdigest()
        ordinal = counts.get(signature, 0); counts[signature] = ordinal + 1
        row["fingerprint"] = hashlib.sha256(f"{signature}:{ordinal}".encode()).hexdigest() if ordinal else signature
    return rows


def parse_mapped(data, filename, mapping=None):
    raw, excel = table(data, filename)
    raw = [row for row in raw if any(str(cell).strip() for cell in row)]
    if len(raw) < 2 or len(raw) > 20001:
        raise ValueError("O extrato deve ter cabeçalho e entre 1 e 20000 movimentos.")
    columns = [str(value).strip() for value in raw[0]]
    if len(columns) > 256 or not all(columns) or len(set(columns)) != len(columns):
        raise ValueError("Os nomes das colunas devem ser preenchidos e únicos.")
    if mapping is None:
        aliases = {"date": ["data", "data movimento", "date"], "description": ["descrição", "descricao", "description"],
            "amount": ["valor", "montante", "amount"], "reference": ["referência", "referencia", "reference"],
            "balance": ["saldo", "balance"], "debit": ["débito", "debito", "debit"], "credit": ["crédito", "credito", "credit"],
            "account": ["conta", "iban", "account"], "currency": ["moeda", "currency", "divisa"],
            "value_date": ["data valor", "data-valor", "value date", "value_date"]}
        mapping = Mapping(**{key: next((column for column in columns if column.casefold() in names), "") for key, names in aliases.items()})
        if not excel and mapping.amount:
            position = columns.index(mapping.amount)
            if any(position < len(row) and re.search(r"\.\d{1,2}$", str(row[position]).strip()) for row in raw[1:21]):
                mapping.decimal_separator = "."
    if not mapping.date or not mapping.description or not (mapping.amount or mapping.debit and mapping.credit):
        return {"mapping_required": True, "columns": columns, "sample": raw[1:4], "mapping": mapping.model_dump(), "rows": [], "new_count": 0, "duplicate_count": 0, "format": "Excel/CSV com mapeamento"}
    fields = {key: columns.index(value) for key, value in mapping.model_dump().items() if key not in {"order", "decimal_separator"} and value in columns}
    for key, value in mapping.model_dump().items():
        if key not in {"order", "decimal_separator"} and value and value not in columns:
            raise MappingError(f"A coluna de {key} não existe no ficheiro.", columns, mapping)
    if mapping.amount and (mapping.debit or mapping.credit):
        raise MappingError("Mapeie o valor com sinal ou o par débito/crédito, não os dois modos.", columns, mapping)
    rows = []
    def parse_amount(value):
        return amount(value, "." if excel and "," not in str(value) else mapping.decimal_separator)
    for index, values in enumerate(raw[1:], 2):
        def get(key):
            position = fields.get(key)
            return str(values[position]).strip() if position is not None and position < len(values) else ""
        try:
            description = get("description")
            if not description or len(description) > 10000 or len(get("reference")) > 200:
                raise ValueError("Descrição em falta ou descrição/referência demasiado longa.")
            if mapping.amount:
                signed = parse_amount(get("amount"))
            else:
                debit = parse_amount(get("debit"))
                credit = parse_amount(get("credit"))
                if debit < 0 or credit < 0 or debit and credit:
                    raise ValueError("Débito e crédito devem ser positivos e apenas um pode estar preenchido por movimento.")
                signed = credit - debit
            if not signed:
                raise ValueError("Movimentos de valor zero não são importados.")
            day = stamp(get("date"))
            rows.append({"movement_date": day, "value_date": stamp(get("value_date")) if get("value_date") else day,
                "description": description, "reference": get("reference"), "amount_cents": signed,
                "balance_cents": parse_amount(get("balance")) if get("balance") else None,
                "exported_at": "", "source_position": len(raw) - index if mapping.order == "ascending" else index - 2,
                "mapped_account": get("account"), "mapped_currency": get("currency")})
        except ValueError as exc:
            raise MappingError(f"Linha {index}: {exc}", columns, mapping) from exc
    return {"mapping_required": False, "mapping": mapping.model_dump(), "columns": columns, "rows": fingerprints(rows), "format": "Excel/CSV com mapeamento"}


def parse_camt(data):
    if b"<!DOCTYPE" in data.upper() or b"<!ENTITY" in data.upper():
        raise ValueError("XML com entidades externas não é permitido.")
    try:
        root = ET.fromstring(data)
        statements = root.findall(".//{*}Stmt")
        if len(statements) != 1:
            raise ValueError("O CAMT.053 deve conter um único extrato/conta.")
        statement = statements[0]
        iban = statement.findtext("{*}Acct/{*}Id/{*}IBAN")
        currency = statement.findtext("{*}Acct/{*}Ccy")
        if not iban or not currency:
            raise ValueError("O CAMT.053 não identifica IBAN e moeda.")
        rows = []
        for index, entry in enumerate(statement.findall("{*}Ntry")):
            indicator = entry.findtext("{*}CdtDbtInd")
            node = entry.find("{*}Amt")
            status = entry.findtext("{*}Sts/{*}Cd") or entry.findtext("{*}Sts")
            if indicator not in {"CRDT", "DBIT"} or node is None or node.get("Ccy", currency) != currency or status not in {"BOOK", None, ""}:
                raise ValueError("O CAMT contém um sentido, moeda ou estado não contabilizado que requer revisão.")
            day = entry.findtext("{*}BookgDt/{*}Dt") or (entry.findtext("{*}BookgDt/{*}DtTm") or "")[:10]
            value_date = entry.findtext("{*}ValDt/{*}Dt") or day
            description = " ".join(entry.itertext()).strip()
            description = entry.findtext("{*}AddtlNtryInf") or entry.findtext(".//{*}Ustrd") or description
            signed = amount(node.text, ".")
            if signed <= 0:
                raise ValueError("Montante CAMT inválido.")
            rows.append({"movement_date": stamp(day), "value_date": stamp(value_date), "description": description[:10000],
                "reference": (entry.findtext("{*}AcctSvcrRef") or entry.findtext("{*}NtryRef") or "")[:200],
                "amount_cents": signed if indicator == "CRDT" else -signed, "balance_cents": None,
                "exported_at": "", "source_position": index, "mapped_account": iban, "mapped_currency": currency})
        if not rows or len(rows) > 20000:
            raise ValueError("O CAMT deve conter entre 1 e 20000 movimentos contabilizados.")
        for index, row in enumerate(rows):
            row["source_position"] = len(rows) - index - 1
        for balance in statement.findall("{*}Bal"):
            if balance.findtext("{*}Tp/{*}CdOrPrtry/{*}Cd") != "CLBD":
                continue
            stamp_balance = balance.findtext("{*}Dt/{*}Dt")
            last = max(rows, key=lambda r: (r["movement_date"], -r["source_position"]))
            if stamp_balance == last["movement_date"]:
                if balance.find("{*}Amt") is None or balance.find("{*}Amt").get("Ccy", currency) != currency or balance.findtext("{*}CdtDbtInd") not in {"CRDT", "DBIT"}:
                    raise ValueError("O saldo final CAMT tem uma moeda ou sentido incompatível.")
                last["balance_cents"] = amount(balance.findtext("{*}Amt"), ".") * (1 if balance.findtext("{*}CdtDbtInd") == "CRDT" else -1)
        return {"rows": fingerprints(rows), "format": "CAMT.053", "mapping_required": False}
    except ET.ParseError as exc:
        raise ValueError("Ficheiro XML CAMT.053 inválido.") from exc
