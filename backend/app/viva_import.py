"""Read Viva Wallet's Portuguese HTML-as-XLS statement without executing content."""

import hashlib
import json
import re
from datetime import datetime
from decimal import Decimal
from html.parser import HTMLParser

HEADERS = ["Data da Transação", "Data Valor", "Descrição", "Montante", "Saldo"]


class StatementTable(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.tables = []
        self.depth = 0
        self.row = None
        self.cell = None
        self.hidden = 0

    def handle_starttag(self, tag, attrs):
        if tag in {"script", "style"}:
            self.hidden += 1
        if tag == "table":
            if self.depth or "table-results" in dict(attrs).get("class", "").split():
                self.depth += 1
                if self.depth == 1:
                    self.tables.append([])
        if self.depth == 1:
            if tag == "tr":
                self.row = []
            elif tag in {"td", "th"}:
                self.cell = []
            elif tag == "br" and self.cell is not None:
                self.cell.append(" ")

    def handle_data(self, data):
        if self.cell is not None and not self.hidden:
            self.cell.append(data)

    def handle_endtag(self, tag):
        if tag in {"script", "style"}:
            self.hidden = max(0, self.hidden - 1)
        if self.depth == 1:
            if tag in {"td", "th"} and self.cell is not None:
                if self.row is not None:
                    self.row.append(" ".join("".join(self.cell).split()))
                self.cell = None
            elif tag == "tr" and self.row is not None:
                self.tables[-1].append(self.row)
                self.row = None
        if tag == "table" and self.depth:
            self.depth -= 1


def cents(value):
    # The original export uses decimal commas and non-breaking grouping spaces.
    value = value.replace("−", "-")
    if not re.fullmatch(r"[+-]?(?:\d+|\d{1,3}(?:[ .]\d{3})+),\d{2}", value):
        raise ValueError("Montante ou saldo inválido no extrato Viva Wallet.")
    amount = Decimal(value.replace(" ", "").replace(".", "").replace(",", ".")) * 100
    if abs(amount) > 10**15:
        raise ValueError("Montante demasiado elevado no extrato Viva Wallet.")
    return int(amount)


def parse_viva(data: bytes):
    try:
        content = data.decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        raise ValueError("Selecione o extrato Viva Wallet original em .xls (HTML UTF-8).") from exc
    parser = StatementTable()
    parser.feed(content)
    parser.close()
    if parser.depth or len(parser.tables) != 1:
        raise ValueError("O ficheiro não contém uma tabela de extrato Viva Wallet válida.")
    table = parser.tables[0]
    if not table or table[0] != HEADERS:
        raise ValueError("As colunas do extrato Viva Wallet não são reconhecidas.")
    rows = []
    opening = closing = None
    previous_date = ""
    occurrences = {}
    for number, cells in enumerate(table[1:], 2):
        if not cells:
            continue
        if len(cells) == 2 and cells[0] == "Saldo Anterior" and not rows and opening is None:
            opening = cents(cells[1])
            continue
        if len(cells) == 2 and cells[0] == "Saldo a Transitar" and closing is None:
            closing = cents(cells[1])
            continue
        if len(cells) != 5 or closing is not None or opening is None:
            raise ValueError(f"A linha {number} do extrato Viva Wallet está incompleta ou fora de ordem.")
        try:
            if not all(re.fullmatch(r"\d{2}/\d{2}/\d{4}", date) for date in cells[:2]):
                raise ValueError
            movement_date, value_date = [
                datetime.strptime(date, "%d/%m/%Y").date().isoformat() for date in cells[:2]
            ]
        except ValueError as exc:
            raise ValueError(f"Data inválida na linha {number} do extrato Viva Wallet.") from exc
        if not cells[2] or movement_date < previous_date:
            raise ValueError(f"Descrição ou ordem de datas inválida na linha {number} do extrato Viva Wallet.")
        item = {
            "movement_date": movement_date,
            "value_date": value_date,
            "description": cells[2],
            "amount_cents": cents(cells[3]),
            "balance_cents": cents(cells[4]),
        }
        previous_balance = rows[-1]["balance_cents"] if rows else opening
        if previous_balance + item["amount_cents"] != item["balance_cents"]:
            raise ValueError(f"O saldo não corresponde ao movimento na linha {number} do extrato Viva Wallet.")
        fingerprint = hashlib.sha256(
            json.dumps(item, sort_keys=True, ensure_ascii=False).encode()
        ).hexdigest()
        occurrence = occurrences.get(fingerprint, 0)
        occurrences[fingerprint] = occurrence + 1
        item["fingerprint"] = (
            hashlib.sha256(f"{fingerprint}:{occurrence}".encode()).hexdigest()
            if occurrence else fingerprint
        )
        rows.append(item)
        previous_date = movement_date
    if not rows or closing is None or closing != rows[-1]["balance_cents"]:
        raise ValueError("O extrato Viva Wallet não tem movimentos ou o saldo final está incorreto.")
    for index, item in enumerate(rows):
        # No export timestamp/account identifier exists in this format. Use the
        # statement's last movement day as the internal ordering reference, and
        # number positions newest-first, matching BPI's running-balance ordering.
        item["exported_at"] = rows[-1]["movement_date"] + "T00:00:00"
        item["source_position"] = len(rows) - index - 1
    return "viva_wallet:EUR", rows
