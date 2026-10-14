"""Read BPI Net Empresas XLSX exports without evaluating spreadsheet formulas."""
import hashlib
import io
import json
import re
import zipfile
from datetime import datetime
from decimal import Decimal
from xml.etree import ElementTree as ET

NS = {"m": "http://schemas.openxmlformats.org/spreadsheetml/2006/main"}


def parse_bpi(data: bytes):
    try:
        with zipfile.ZipFile(io.BytesIO(data)) as archive:
            if sum(i.file_size for i in archive.infolist()) > 20_000_000:
                raise ValueError("O ficheiro descomprimido é demasiado grande.")
            shared = []
            if "xl/sharedStrings.xml" in archive.namelist():
                shared = ["".join(x.itertext()) for x in ET.fromstring(archive.read("xl/sharedStrings.xml")).findall("m:si", NS)]
            sheet = ET.fromstring(archive.read("xl/worksheets/sheet1.xml"))
            rows = []
            account = ""
            exported_at = ""
            header = False
            bpi = False
            occurrences = {}
            for row in sheet.findall(".//m:row", NS):
                cells = {}
                for cell in row.findall("m:c", NS):
                    value = cell.find("m:v", NS)
                    cells[re.sub(r"\d", "", cell.attrib["r"])] = (shared[int(value.text)] if cell.attrib.get("t") == "s" and value is not None else "".join(cell.itertext())).strip()
                bpi = bpi or cells.get("A") == "BPI Net Empresas"
                if cells.get("A") == "Conta e Moeda":
                    account = cells.get("C", "")
                if cells.get("A") == "Data/Hora":
                    exported_at = datetime.strptime(cells.get("C", "").replace(" Horas", ""), "%d-%m-%Y %H:%M").isoformat()
                if cells.get("A") == "Data Mov.":
                    if [cells.get(k) for k in "ABCDE"] != ["Data Mov.", "Data Valor", "Descrição do Movimento", "Valor em EUR", "Saldo em EUR"]:
                        raise ValueError("As colunas do extrato BPI não são reconhecidas.")
                    header = True
                    continue
                if not header or not re.fullmatch(r"\d{2}-\d{2}-\d{4}", cells.get("A", "")):
                    continue
                def cents(value):
                    amount = Decimal(value.replace(" ", "").replace("\u00a0", "").replace(".", "").replace(",", ".")) * 100
                    if not amount.is_finite() or amount != amount.to_integral_value() or abs(amount) > 10**15:
                        raise ValueError("Montante inválido no extrato.")
                    return int(amount)
                item = {
                    "movement_date": datetime.strptime(cells["A"], "%d-%m-%Y").date().isoformat(),
                    "value_date": datetime.strptime(cells["B"], "%d-%m-%Y").date().isoformat(),
                    "description": cells["C"],
                    "amount_cents": cents(cells["D"]),
                    "balance_cents": cents(cells["E"]),
                }
                item["fingerprint"] = hashlib.sha256(json.dumps(item, sort_keys=True, ensure_ascii=False).encode()).hexdigest()
                fingerprint = item["fingerprint"]
                occurrence = occurrences.get(fingerprint, 0)
                occurrences[fingerprint] = occurrence + 1
                if occurrence:
                    item["fingerprint"] = hashlib.sha256(f"{fingerprint}:{occurrence}".encode()).hexdigest()
                item["exported_at"] = exported_at
                item["source_position"] = len(rows)
                rows.append(item)
            if not bpi or not header or not rows or not account or not exported_at or "(EUR)" not in account:
                raise ValueError("Selecione um extrato BPI Net Empresas em euros com movimentos.")
            return account, rows
    except (zipfile.BadZipFile, KeyError, ET.ParseError, IndexError, ArithmeticError) as exc:
        raise ValueError("O ficheiro não é um extrato XLSX BPI válido.") from exc
