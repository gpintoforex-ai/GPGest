"""Extratos BPI em PDF («EXTRACTO DE CONTA» mensal): lidos pelo texto, com os saldos validados linha a linha.

O PDF não é uma fonte estruturada, por isso a leitura só é aceite quando tudo bate certo: conta e moeda
identificadas, período do extrato, saldo anterior + cada movimento = saldo dessa linha, e o último saldo igual ao
saldo final do extrato. Qualquer falha recusa o ficheiro inteiro; nada é importado parcialmente.

As linhas ficam no mesmo formato do extrato XLSX do BPI Net Empresas (a mesma conta e as mesmas descrições), para a
deteção de duplicados funcionar entre os dois formatos.
"""

import hashlib
import io
import json
import logging
import re
from datetime import date
from decimal import Decimal, InvalidOperation

MAX_PAGES = 60
AMOUNT = re.compile(r"-?\d{1,3}(?: \d{3})*,\d{2}")
DAY = re.compile(r"\d{2}/\d{2}")
PERIOD = re.compile(r"Per\S*odo\s+De\s+(\d{2})/(\d{2})/(\d{4})\s+a\s+(\d{2})/(\d{2})/(\d{4})")
ACCOUNT = re.compile(r"\bConta\s+(\d)-(\d{7})-(\d{3})-(\d{3})\b")
CURRENCY = re.compile(r"CONTA\s.*N\S*:\s*\d-\d{7}-\d{3}-\d{3}\s+([A-Z]{3})\s*$")


def cents(value):
    try:
        amount = Decimal(value.replace(" ", "").replace(".", "").replace(",", ".")) * 100
    except InvalidOperation as exc:
        raise ValueError("Montante inválido no extrato PDF.") from exc
    if not amount.is_finite() or amount != amount.to_integral_value() or abs(amount) > 10**15:
        raise ValueError("Montante inválido no extrato PDF.")
    return int(amount)


def dated(day, start, end):
    """dd/mm → data no período do extrato (um extrato de dezembro a janeiro tem dois anos)."""
    d, m = int(day[:2]), int(day[3:])
    options = []
    for year in sorted({start.year, end.year}):
        try:
            options.append(date(year, m, d))
        except ValueError:
            continue
    if not options:
        raise ValueError(f"Data inválida no extrato PDF: {day}.")
    return min(options, key=lambda value: 0 if start <= value <= end else min(abs((value - start).days), abs((value - end).days)))


def lines_of(data):
    from pypdf import PdfReader
    from pypdf.errors import PdfReadError
    logging.getLogger("pypdf").setLevel(logging.ERROR)
    try:
        reader = PdfReader(io.BytesIO(data))
        # Os extratos do BPI vêm cifrados só com restrições do autor (palavra-passe de abertura vazia).
        if reader.is_encrypted and not reader.decrypt(""):
            raise ValueError("O PDF está protegido por palavra-passe. Exporte o extrato sem proteção ou use o XLSX do BPI Net Empresas.")
        if len(reader.pages) > MAX_PAGES:
            raise ValueError(f"O extrato PDF tem mais de {MAX_PAGES} páginas. Importe períodos mais curtos.")
        return [line for page in reader.pages for line in page.extract_text(extraction_mode="layout").splitlines()]
    except (PdfReadError, KeyError, TypeError, AttributeError, IndexError) as exc:
        raise ValueError("Não foi possível ler o texto do PDF. Use o extrato em XLSX do BPI Net Empresas.") from exc


def parse_bpi_pdf(data: bytes):
    """(conta no formato do XLSX, linhas, resumo) de um extrato BPI em PDF, ou ValueError."""
    lines = lines_of(data)
    text = "\n".join(lines)
    if "BPI" not in text and "EXTRACTO DE CONTA" not in text:
        raise ValueError("O PDF não parece ser um extrato de conta do BPI.")
    account = ACCOUNT.search(text)
    period = PERIOD.search(text)
    if not account or not period:
        raise ValueError("O extrato PDF não identifica a conta e o período. Use o extrato em XLSX do BPI Net Empresas.")
    currencies = {match.group(1) for line in lines if (match := CURRENCY.search(line))}
    if currencies != {"EUR"}:
        raise ValueError("Só são aceites extratos PDF de contas em euros.")
    start = date(int(period.group(3)), int(period.group(2)), int(period.group(1)))
    end = date(int(period.group(6)), int(period.group(5)), int(period.group(4)))
    a, b, c, d = account.groups()
    source = f"{a}-{b}.{c}.{d} (EUR)"
    opening = closing = None
    rows, occurrences = [], {}
    movement_day = None
    balance = None
    for line in lines:
        tokens = [token for token in re.split(r"\s{2,}", line.strip()) if token]
        if len(tokens) < 2 or not AMOUNT.fullmatch(tokens[-1]):
            continue
        label = " ".join(tokens[:-1]).upper()
        if label.startswith("SALDO ANTERIOR"):
            opening = balance = cents(tokens[-1])
            continue
        if label.startswith("SALDO ACTUAL CONTABIL") or label.startswith("SALDO ATUAL CONTABIL"):
            closing = cents(tokens[-1])
            continue
        if label.startswith("SALDO ACTUAL DISPON") or label.startswith("SALDO ATUAL DISPON"):
            continue
        # Movimento: [data mov.] data valor, descrição (pode ter espaços duplos), valor, saldo.
        dates = []
        while tokens and DAY.fullmatch(tokens[0]) and len(dates) < 2:
            dates.append(tokens.pop(0))
        if not dates or len(tokens) < 3 or not AMOUNT.fullmatch(tokens[-2]):
            continue
        if balance is None:
            raise ValueError("O extrato PDF não tem o saldo anterior antes dos movimentos.")
        if len(dates) == 2:
            movement_day = dates[0]
        value_day = dates[-1]
        if movement_day is None:
            raise ValueError("O primeiro movimento do extrato PDF não tem data de movimento.")
        amount, line_balance = cents(tokens[-2]), cents(tokens[-1])
        description = " ".join(" ".join(tokens[:-2]).split())
        if balance + amount != line_balance:
            raise ValueError(f"Os saldos do extrato PDF não batem certo em «{description}» ({value_day}): o ficheiro não foi importado. "
                             "Use o extrato em XLSX do BPI Net Empresas.")
        balance = line_balance
        item = {
            "movement_date": dated(movement_day, start, end).isoformat(),
            "value_date": dated(value_day, start, end).isoformat(),
            "description": description,
            "amount_cents": amount,
            "balance_cents": line_balance,
        }
        fingerprint = hashlib.sha256(json.dumps(item, sort_keys=True, ensure_ascii=False).encode()).hexdigest()
        occurrence = occurrences.get(fingerprint, 0)
        occurrences[fingerprint] = occurrence + 1
        item["fingerprint"] = hashlib.sha256(f"{fingerprint}:{occurrence}".encode()).hexdigest() if occurrence else fingerprint
        item["exported_at"] = f"{end.isoformat()}T00:00:00"
        item["source_position"] = len(rows)
        rows.append(item)
    if opening is None or closing is None:
        raise ValueError("O extrato PDF não tem o saldo anterior e o saldo final. Use o extrato em XLSX do BPI Net Empresas.")
    if not rows:
        raise ValueError("O extrato PDF não tem movimentos.")
    if balance != closing:
        raise ValueError("O último saldo dos movimentos não corresponde ao saldo final do extrato PDF: o ficheiro não foi importado.")
    summary = {"period": [start.isoformat(), end.isoformat()], "opening_cents": opening, "closing_cents": closing, "count": len(rows)}
    return source, rows, summary
