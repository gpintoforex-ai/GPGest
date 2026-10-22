"""Conservative table extraction: unsupported layouts remain explicitly unreviewed."""

import re
import unicodedata
from decimal import Decimal, InvalidOperation


def normalized(value):
    return "".join(
        c for c in unicodedata.normalize("NFKD", value.lower()) if not unicodedata.combining(c)
    ).strip()


def number(value):
    value = re.sub(r"(?:EUR|€|%|\s)", "", value, flags=re.I)
    if "," in value:
        value = value.replace(".", "").replace(",", ".")
    try:
        result = Decimal(value)
        if (
            not result.is_finite()
            or result < 0
            or result > 10**9
            or result.as_tuple().exponent < -6
        ):
            raise ValueError()
        return str(result)
    except (InvalidOperation, ValueError):
        return None


def extract_lines(text):
    aliases = {
        "reference": {"ref", "referencia", "codigo", "artigo", "ean"},
        "description": {"descricao", "designacao", "produto"},
        "quantity": {"qtd", "quantidade", "quant"},
        "price": {"preco", "preco unitario", "p unit", "p.unit", "valor unitario"},
        "discount": {"desconto", "desc", "desc %"},
        "vat": {"iva", "iva %", "taxa"},
        "total": {"total", "liquido", "valor"},
    }
    header, result, warnings = None, [], []
    for raw in text[:20000].splitlines():
        parts = [p.strip() for p in re.split(r"\s*[;|\t]\s*|\s{2,}", raw.strip())]
        keys = [
            next((key for key, words in aliases.items() if normalized(p).rstrip(".") in words), "")
            for p in parts
        ]
        if all(k in keys for k in ("description", "quantity", "price", "vat")):
            header = keys
            continue
        if not header or len(parts) != len(header):
            continue
        item = {key: value for key, value in zip(header, parts) if key}
        if not item.get("description") or normalized(item["description"]).startswith(
            ("total", "subtotal")
        ):
            continue
        for key in ("quantity", "price", "discount", "vat"):
            item[key] = number(item.get(key, "0"))
        if (
            any(item[key] is None for key in ("quantity", "price", "discount", "vat"))
            or Decimal(item["quantity"]) <= 0
            or Decimal(item["vat"]) > 100
            or Decimal(item["discount"]) > 100
        ):
            warnings.append("Linha com números ambíguos: " + raw[:160])
            continue
        result.append(
            {
                **item,
                "reference": item.get("reference", ""),
                "article_id": None,
                "evidence": raw[:1000],
                "confidence": "proposta",
                "source": "texto_ocr",
            }
        )
        if len(result) >= 200:
            break
    if not result:
        warnings.append(
            "Não foi possível interpretar as linhas com segurança. Transcreva e confira o original."
        )
    return {"lines": result, "warnings": warnings, "review_required": True}
