"""Linhas de artigos de faturas de compra (texto do PDF ou OCR), sem depender do formato.

Cada fornecedor tem a sua tabela e o OCR não mantém colunas. Em vez de posições, procura-se em
cada linha a combinação de números em que quantidade × preço × (1 − desconto) = valor da linha.
Essa conferência aritmética separa a quantidade de números da descrição («BIO 25») e de códigos
(EAN, lotes). Um desconto comum a todas as linhas (desconto global de 20 %, ou 5 % de pronto
pagamento) é detetado pela razão partilhada. Nada é inventado: uma linha sem conta certa não é
proposta, e a soma das linhas é comparada com a base tributável do documento.
"""

import re
import unicodedata
from collections import Counter
from datetime import date
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation
from itertools import combinations

D = Decimal
MONTHS = {
    "jan": 1, "fev": 2, "feb": 2, "mar": 3, "abr": 4, "apr": 4, "mai": 5, "may": 5, "jun": 6,
    "jul": 7, "ago": 8, "aug": 8, "set": 9, "sep": 9, "out": 10, "oct": 10, "nov": 11, "dez": 12,
    "dec": 12,
}
# Primeira palavra de linhas de resumo/rodapé que nunca são artigos.
FOOTER = (
    "total", "subtotal", "sub-total", "incid", "base", "resumo", "taxa", "valor", "iva", "desconto",
    "descontos", "mercadoria", "portes", "saldo", "quadro", "observ", "liquido", "líquido", "a transportar",
    "transporte", "pagamento", "vencimento", "nib", "iban", "referente",
)
HEADER_WORDS = {
    "quantity": ("qtd", "quant", "quantidade", "quantity", "qty"),
    "description": ("descricao", "designacao", "description", "produto", "artigo"),
    "price": ("preco", "p.unit", "punit", "p.venda", "prunitario", "unitario", "unit", "price"),
    "lot": ("lote", "batch"),
    "expiry": ("validade", "expirat"),
    "bonus": ("bonus", "oferta"),
}
UNITS = {"un", "uni", "und", "unid", "uds", "ud", "kg", "gr", "g", "lt", "l", "cx", "emb", "pc", "pcs", "par", "ml"}
VAT_RATES = {D(0), D(4), D(5), D(6), D(9), D(12), D(13), D(16), D(18), D(22), D(23)}
DATE = re.compile(r"^(\d{1,2})[/.-](\d{1,2}|[a-zA-Z]{3})[/.-](\d{2,4})$|^(\d{4})-(\d{2})-(\d{2})$")
NUMBER = re.compile(r"^-?\d{1,3}(?:[.\s]\d{3})*(?:,\d+)?$|^-?\d+(?:[.,]\d+)?$")


def plain(value):
    return "".join(
        c for c in unicodedata.normalize("NFKD", value.lower()) if not unicodedata.combining(c)
    )


def number(token):
    """Número português ou com ponto decimal; None se não for número."""
    token = token.strip().rstrip("%").strip()
    if not token or not NUMBER.match(token):
        return None
    if "," in token and "." in token:
        token = token.replace(".", "").replace(",", ".") if token.rfind(",") > token.rfind(".") else token.replace(",", "")
    elif "," in token:
        token = token.replace(".", "").replace(",", ".")
    try:
        value = D(token.replace(" ", ""))
    except InvalidOperation:
        return None
    return value if value.is_finite() else None


def parse_date(token):
    match = DATE.match(token.strip())
    if not match:
        return None
    try:
        if match.group(4):
            return date(int(match.group(4)), int(match.group(5)), int(match.group(6))).isoformat()
        day, month, year = match.group(1), match.group(2), match.group(3)
        month = MONTHS.get(plain(month)[:3]) if month.isalpha() else int(month)
        year = int(year) + (2000 if len(year) == 2 else 0)
        return date(year, month, int(day)).isoformat() if month else None
    except ValueError:
        return None


def ean_valid(code):
    if not code.isdigit() or len(code) not in (8, 12, 13, 14):
        return False
    digits = [int(c) for c in code]
    check = digits.pop()
    total = sum(d * (3 if i % 2 == 0 else 1) for i, d in enumerate(reversed(digits)))
    return (10 - total % 10) % 10 == check


def clean(token):
    return token.strip("[](){}|;:'\"`´”“’‘_—–$§#»«").strip()


def fmt(value):
    """Decimal sem notação científica («100», não «1E+2»)."""
    text = format(value.normalize(), "f")
    return text.rstrip("0").rstrip(".") if "." in text else text


def tokens(line):
    """Palavras da linha e o lote marcado («61477792(*)NOME» ou «61474214 (+) NOME»)."""
    result, marked_lot = [], ""
    for raw in re.split(r"\s+|\|", line.strip()):
        marked = re.match(r"^([A-Za-z0-9\-/]+)\([*+]\)(.*)$", raw)
        if marked:
            result.append(marked.group(1))
            marked_lot = marked_lot or marked.group(1)
            if marked.group(2):
                result.append(marked.group(2))
            continue
        if re.fullmatch(r"\([*+]\)", raw.strip()):
            marked_lot = marked_lot or (result[-1] if result else "")
            continue
        token = clean(raw)
        if token:
            result.append(token)
    return result, marked_lot


def header_info(lines):
    """Colunas anunciadas no cabeçalho da tabela (para lote, validade e bónus)."""
    for index, line in enumerate(lines):
        words = plain(line)
        found = {key for key, names in HEADER_WORDS.items() if any(n in words for n in names)}
        if {"quantity", "description"} <= found or {"quantity", "price"} <= found:
            return index, found
    return None, set()


def vat_codes(text):
    """«Taxa 1 6% Taxa 2 13%»: alguns fornecedores indicam o IVA por código na linha."""
    return {
        int(code): D(rate.replace(",", "."))
        for code, rate in re.findall(r"taxa\s*(\d)\s*[:\-]?\s*(\d{1,2}(?:[.,]\d+)?)\s*%", plain(text))
    }


def tolerance(total):
    return max(D("0.011"), abs(total) * D("0.004"))


def candidates(words):
    """Combinações (quantidade, preço, desconto, total) com a razão total/(q×p×(1−d))."""
    numbers = []
    for i, word in enumerate(words):
        value = number(word)
        pct = word.endswith("%") or (i + 1 < len(words) and words[i + 1] == "%")
        if value is None or parse_date(word):
            continue
        digits = re.sub(r"\D", "", word)
        if not pct and len(digits) >= 7 and "," not in word and "." not in word:
            continue  # EAN, lotes e referências longas não são quantidades nem valores.
        numbers.append((i, value, pct))
    found = []
    plain_numbers = [n for n in numbers if not n[2]]
    decimal = {i for i, w in enumerate(words) if re.search(r"\d[.,]\d", w)}
    for (qi, q, _), (pi, p, _) in combinations(plain_numbers, 2):
        if q <= 0 or p <= 0 or q > 100000 or p > 100000:
            continue
        for ti, t, _ in plain_numbers:
            if ti <= pi or not ({pi, ti} & decimal):
                continue
            discounts = [(None, D(0))] + [
                (di, d) for di, d, _ in numbers if pi < di < ti and D(0) <= d <= 100
            ]
            for di, d in discounts:
                expected = q * p * (1 - d / 100)
                if expected == 0:
                    if t == 0:
                        found.append({"q": (qi, q), "p": (pi, p), "d": (di, d), "t": (ti, t), "ratio": D(1)})
                    continue
                ratio = t / expected
                if D("0.3") <= ratio <= D("1.01"):
                    found.append({"q": (qi, q), "p": (pi, p), "d": (di, d), "t": (ti, t), "ratio": ratio})
    return found


def best(options, factor):
    """A combinação mais exata para a razão pretendida (1 ou o desconto comum do documento)."""
    scored = []
    for option in options:
        q, p, d, t = option["q"][1], option["p"][1], option["d"][1], option["t"][1]
        error = abs(q * p * (1 - d / 100) * factor - t)
        if error <= tolerance(t):
            # Preferir: menor erro, desconto explícito quando existe, quantidade mais à direita.
            fractional = q != q.to_integral()
            scored.append((money(error, "0.001"), fractional, option["d"][0] is None, -option["q"][0], -option["t"][0], option))
    return min(scored, key=lambda s: s[:5])[5] if scored else None


def describe(words, qi, info, marked_lot=""):
    """Códigos à esquerda (referência, EAN, lote, validade) e a descrição até à quantidade."""
    reference = barcode = lot = expiry = ""
    description, codes = [], []
    for word in words[:qi]:
        when = parse_date(word)
        is_code = not description and (when or (re.search(r"\d", word) and re.fullmatch(r"[A-Za-z0-9.\-/]+", word)))
        if when and not description:
            expiry = expiry or when
        elif is_code:
            codes.append(word)
        else:
            description.append(word)
    position = next((i for i, code in enumerate(codes) if ean_valid(code)), None)
    if position is not None:
        barcode = codes[position]
        before, after = codes[:position], codes[position + 1 :]
    else:
        before, after = codes[:1], codes[1:]
    reference = before[0] if before else barcode
    if marked_lot:
        lot = marked_lot
    elif "lot" in info and after:
        lot = after[-1]
    while description and plain(description[-1]) in UNITS:
        description.pop()
    return reference, barcode, lot, expiry, " ".join(description).strip(" -*+")


def extra_lot(line):
    """Linha seguinte «Lote: 605406A - Data de Validade: 28-dez-2031»."""
    text = plain(line)
    lot = re.search(r"lote\s*[:.]?\s*([a-z0-9][a-z0-9\-/]*)", text)
    expiry = None
    match = re.search(r"validade\s*[:.]?\s*(\S+)", text)
    if match:
        expiry = parse_date(match.group(1).strip(",;"))
    original = re.search(r"[Ll][Oo][Tt][Ee]\s*[:.]?\s*([A-Za-z0-9][A-Za-z0-9\-/]*)", line)
    return (original.group(1) if original else (lot.group(1) if lot else "")), expiry


def money(value, places="0.01"):
    return value.quantize(D(places), rounding=ROUND_HALF_UP)


def extract(text, base_cents=None):
    lines = [line for line in text[:60000].splitlines() if line.strip()]
    start, info = header_info(lines)
    codes = vat_codes(text)
    parsed = []
    for index, line in enumerate(lines):
        if start is not None and index <= start:
            continue
        words, marked_lot = tokens(line)
        if not words:
            continue
        lot, expiry = extra_lot(line) if "lote" in plain(line) or "validade" in plain(line) else ("", None)
        if (lot or expiry) and parsed and index - parsed[-1]["index"] <= 3 and not candidates(words):
            last = parsed[-1]
            last["lot"] = last["lot"] or lot
            last["expiry"] = last["expiry"] or expiry or ""
            continue
        first = next((plain(w) for w in words if re.search(r"[a-zA-Z]", w)), "")
        if any(first.startswith(f) for f in FOOTER) and not re.search(r"\d{5,}", words[0]):
            continue
        options = candidates(words)
        if options:
            parsed.append({"index": index, "line": line, "words": words, "options": options, "lot": "", "expiry": "", "marked": marked_lot})
    # Desconto comum: a razão mais frequente (≠ 1) partilhada por várias linhas.
    ratios = Counter(
        ratio
        for item in parsed
        for ratio in {money(o["ratio"], "0.001") for o in item["options"] if o["ratio"] < D("0.995")}
    )
    common = [r for r, n in ratios.most_common(3) if n >= max(2, len(parsed) // 2)]
    result, warnings = [], []
    for item in parsed:
        choice, factor = best(item["options"], D(1)), D(1)
        if not choice:
            for ratio in common:
                choice = best(item["options"], ratio)
                if choice:
                    factor = ratio
                    break
        if not choice:
            continue
        words = item["words"]
        qi, q = choice["q"]
        pi, p = choice["p"]
        di, d = choice["d"]
        ti, t = choice["t"]
        reference, barcode, lot, expiry, description = describe(words, qi, info, item["marked"])
        if len(re.sub(r"[^a-zA-Z]", "", description)) < 3:
            continue
        between = [number(w) for w in words[qi + 1 : pi]]
        bonus = next(
            (b for b in between if "bonus" in info and b is not None and b == b.to_integral() and 0 < b <= q * 10),
            D(0),
        )
        vat = ""
        for word in words[pi + 1 :]:
            value = number(word)
            if value is None or words.index(word) in (di, ti):
                continue
            if (word.endswith("%") or value in VAT_RATES) and value in VAT_RATES and value != d:
                vat = fmt(value)
                break
            if value == value.to_integral() and int(value) in codes:
                vat = fmt(codes[int(value)])
                break
        received = q + bonus
        result.append({
            "reference": reference,
            "barcode": barcode,
            "lot": item["lot"] or lot,
            "expiry": item["expiry"] or expiry,
            "description": description[:500],
            "quantity": fmt(q),
            "bonus": fmt(bonus),
            "price": fmt(p),
            "discount": fmt(d),
            # Portes e envios não são mercadoria: propostos sem entrada em stock.
            "service": bool(re.search(r"\b(portes|envio|env[ií]o|shipping|transporte|frete)\b", plain(description))),
            "vat": vat,
            "total": str(money(t)),
            # Custo unitário real (descontos e ofertas incluídos), para o stock.
            "unit_cost": str((t / received).quantize(D("0.000001"))) if received else "0",
            "global_discount": str(money((1 - factor) * 100)) if factor != 1 else "0",
            "confidence": "conferida" if factor == 1 else "desconto_global",
            "evidence": item["line"].strip()[:500],
        })
        if len(result) >= 300:
            break
    lines_total = sum((D(r["total"]) for r in result), D(0))
    check = {"lines_total_cents": int(lines_total * 100), "document_base_cents": base_cents, "matches": None}
    if base_cents is not None and result:
        check["matches"] = abs(int(lines_total * 100) - base_cents) <= max(2, len(result))
        if not check["matches"]:
            warnings.append(
                f"A soma das linhas lidas ({lines_total:.2f} €) difere da base do documento "
                f"({D(base_cents) / 100:.2f} €): podem faltar linhas ou existir portes/descontos de rodapé."
            )
    if not result:
        warnings.append("Não foi possível ler linhas de artigos com segurança. Preencha-as a partir do original.")
    return {"lines": result, "warnings": warnings, "check": check}
