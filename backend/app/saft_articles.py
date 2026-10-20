"""Proposta de artigos a partir dos produtos do SAF-T, com confirmação explícita.

Só cria fichas de artigo e a associação código SAF-T → artigo. Nunca cria movimentos de stock:
as saídas por vendas SAF-T continuam a exigir a política explícita de stock.
"""

import json
from collections import Counter, defaultdict
from decimal import Decimal as D
from decimal import InvalidOperation
from typing import Literal

from fastapi import Depends
from pydantic import Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from .db import db_session
from .inventory import ArticleInput, dump, fail, lock, permissions, require, save_article, settings
from .models import StockArticle, StockSettings, WintouchSaleRecord
from .schemas import Strict

TYPES = {"P": "Produto", "S": "Serviço", "O": "Outro", "E": "Imposto especial", "I": "Imposto/taxa"}


def source(db):
    nif = str(db.info.get("company", {}).get("nif") or "").strip()
    return "saft:" + nif if len(nif) == 9 and nif.isdigit() else None


def number(value):
    try:
        return D(str(value))
    except (InvalidOperation, TypeError):
        return None


def barcode(value, code):
    text = str(value or "").strip()
    return text if text and text != code and text.isdigit() and 8 <= len(text) <= 14 else ""


def proposals(db):
    enterprise = source(db)
    if not enterprise:
        return []
    mapping = settings(db)["saft_mapping"]
    products = {}
    for row in db.scalars(select(WintouchSaleRecord).where(
        WintouchSaleRecord.enterprise_id == enterprise, WintouchSaleRecord.kind == "saft_product", WintouchSaleRecord.active.is_(True)
    )):
        data = json.loads(row.data)
        raw = data.get("raw", {})
        code = str(data.get("code") or raw.get("ProductCode") or "").strip()
        if code and code not in mapping:
            products[code] = raw
    if not products:
        return []
    # Unidade, IVA e preço mais recentes a partir das linhas das faturas (apenas proposta).
    usage, units, rates, prices = Counter(), defaultdict(Counter), defaultdict(Counter), {}
    for row in db.scalars(select(WintouchSaleRecord).where(
        WintouchSaleRecord.enterprise_id == enterprise, WintouchSaleRecord.kind == "saft_invoice", WintouchSaleRecord.active.is_(True)
    ).order_by(WintouchSaleRecord.document_date)):
        lines = json.loads(row.data).get("raw", {}).get("Line", [])
        for line in [lines] if isinstance(lines, dict) else lines:
            code = str(line.get("ProductCode", "")).strip()
            if code not in products:
                continue
            usage[code] += 1
            if line.get("UnitOfMeasure"):
                units[code][str(line["UnitOfMeasure"])[:20]] += 1
            tax = line.get("Tax", {})
            rate = number(tax.get("TaxPercentage") if isinstance(tax, dict) else None)
            if rate is not None and 0 <= rate <= 100:
                rates[code][str(rate)] += 1
            price = number(line.get("UnitPrice"))
            if price is not None and price >= 0:
                prices[code] = price
    articles = list(db.scalars(select(StockArticle)))
    by_code = {a.code.casefold(): a for a in articles}
    by_barcode = {}
    for a in articles:
        for value in json.loads(a.data or "{}").get("barcodes", []):
            by_barcode[value] = a
    result = []
    for code, raw in sorted(products.items()):
        kind = str(raw.get("ProductType", "P"))
        ean = barcode(raw.get("ProductNumberCode"), code)
        match = by_code.get(code.casefold()) or (by_barcode.get(ean) if ean else None)
        name = str(raw.get("ProductDescription") or code).strip()[:300]
        warnings = []
        if kind != "P":
            warnings.append(f"{TYPES.get(kind, 'Tipo ' + kind)}: normalmente não é controlado em stock.")
        if not usage[code]:
            warnings.append("Sem linhas de venda nos documentos importados.")
        if code in by_code and match and match.code != code:
            warnings.append("Existe um artigo com o mesmo código noutra capitalização.")
        result.append({
            "code": code,
            "name": name,
            "type": kind,
            "type_name": TYPES.get(kind, kind),
            "barcode": ean,
            "unit": units[code].most_common(1)[0][0] if units[code] else "un",
            "vat_rate": rates[code].most_common(1)[0][0] if rates[code] else "23",
            "sale_net": str(prices.get(code, D(0)).quantize(D("0.000001"))),
            "lines": usage[code],
            "action": "link" if match else "create",
            "article_id": match.id if match else None,
            "article_name": match.name if match else None,
            "reason": ("Mesmo código" if match and match.code.casefold() == code.casefold() else "Mesmo código de barras") if match else "Sem artigo correspondente",
            "selected": bool(match) or kind == "P",
            "warnings": warnings,
        })
    return result


class ProposalLine(Strict):
    code: str = Field(min_length=1, max_length=100)
    action: Literal["create", "link"]
    article_id: str | None = Field(default=None, max_length=36)
    name: str = Field(default="", max_length=300)
    unit: str = Field(default="un", max_length=20)
    vat_rate: D = Field(default=D(23), ge=0, le=100, max_digits=6, decimal_places=3)
    sale_net: D = Field(default=D(0), ge=0, le=10**9, max_digits=18, decimal_places=6)
    barcode: str = Field(default="", max_length=14)


class ProposalApply(Strict):
    confirmed: Literal[True]
    items: list[ProposalLine] = Field(min_length=1, max_length=2000)


def register(app, current, authorize, audit):
    @app.get("/api/stock/saft-articles")
    def article_proposals(user=Depends(current), db: Session = Depends(db_session)):
        authorize(user, db, roles=["administrador", "gestor"])
        rights = permissions(user, settings(db))
        return {
            "proposals": proposals(db),
            "can_create": bool(rights.get("adjust_stock")),
            "can_set_price": bool(rights.get("change_price")),
            "note": "Propostas a partir dos produtos e linhas do SAF-T. Criar ou associar artigos não movimenta stock; as saídas por vendas SAF-T continuam a exigir a política de stock.",
        }

    @app.post("/api/stock/saft-articles")
    def apply_proposals(data: ProposalApply, user=Depends(current), db: Session = Depends(db_session)):
        authorize(user, db, roles=["administrador", "gestor"])
        lock(db)
        config = settings(db)
        require(user, config, "adjust_stock")
        current_codes = {p["code"]: p for p in proposals(db)}
        if len({item.code for item in data.items}) != len(data.items):
            fail("Um código SAF-T está repetido na seleção.")
        mapping = dict(config["saft_mapping"])
        created, linked = [], []
        for item in data.items:
            proposal = current_codes.get(item.code)
            if not proposal:
                fail(f"O código {item.code} já foi associado ou não existe no SAF-T importado. Atualize as propostas.", 409)
            if item.action == "link":
                target = db.get(StockArticle, item.article_id or "")
                if not target or not target.active:
                    fail(f"Escolha um artigo ativo para associar ao código {item.code}.")
                mapping[item.code] = target.id
                linked.append(item.code)
                continue
            if db.scalar(select(StockArticle.id).where(StockArticle.code == item.code)):
                fail(f"Já existe um artigo com o código {item.code}. Associe-o em vez de criar outro.", 409)
            # O preço de venda só é guardado por quem pode alterar preços.
            price = item.sale_net if permissions(user, config).get("change_price") else D(0)
            row = save_article(db, user, ArticleInput(
                code=item.code, name=(item.name or proposal["name"]).strip() or item.code, unit=(item.unit or "un").strip() or "un",
                vat_rate=item.vat_rate, sale_net=price, barcodes=[item.barcode] if item.barcode else [],
                description="Criado a partir do SAF-T importado.",
            ), config=config)
            mapping[item.code] = row.id
            created.append(row.id)
        stored = db.get(StockSettings, "default") or StockSettings(id="default")
        stored.data = dump({**{k: v for k, v in config.items() if k != "revision"}, "saft_mapping": mapping})
        stored.revision = config["revision"] + 1
        db.add(stored)
        audit(db, user, "stock_saft_articles", "default", {"criados": created, "associados": linked, "movimentos_stock": 0})
        db.commit()
        return {"created": len(created), "linked": len(linked), "message": "Artigos criados/associados. O stock não foi movimentado."}
