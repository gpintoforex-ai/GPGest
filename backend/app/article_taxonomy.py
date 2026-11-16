"""Classificação comercial partilhada pelas fichas e pela publicação na loja."""

import json

from fastapi import HTTPException
from sqlalchemy import cast, func, select
from sqlalchemy.dialects.postgresql import JSONB

from .models import ArticleBrand, ArticleFamily, ArticleSubfamily, StockArticle
from .table_framework import TableField, TableSpec

MODELS = {"family": ArticleFamily, "category": ArticleSubfamily, "brand": ArticleBrand}


def catalog(db):
    return {
        key: [
            {"id": r.id, "name": r.name, "active": r.active,
             **({"family_id": r.family_id} if key == "category" else {})}
            for r in db.scalars(select(model).order_by(model.name))
        ]
        for key, model in MODELS.items()
    }


def adopt(db, data, old=None, *, write=True):
    """Importações e fichas antigas mantêm os nomes; os novos nomes entram nas tabelas.

    Uma subfamília antiga sem família mantém-se na ficha até ser classificada.
    A pré-visualização das importações nunca cria registos.
    """
    old = old or {}
    family = None
    for key in ("family", "brand", "category"):
        name = str(data.get(key) or "").strip()
        data[key] = name
        if not name or (key == "category" and family is None):
            continue
        if key == "category" and not family.active and old.get("category") != name:
            raise HTTPException(422, "Selecione uma família ativa antes de alterar a subfamília.")
        model = MODELS[key]
        rows = list(db.scalars(select(model)))
        row = next((r for r in rows if r.name.casefold() == name.casefold()
                    and (key != "category" or r.family_id == family.id)), None)
        unchanged = old.get(key) == name and (key != "category" or old.get("family") == data.get("family"))
        if row is not None:
            if not row.active and not unchanged:
                raise HTTPException(422, f"A classificação «{row.name}» está inativa.")
            data[key] = row.name
        elif write:
            row = model(name=name, **({"family_id": family.id} if key == "category" else {}))
            db.add(row)
            db.flush()
        if key == "family":
            family = row


class TaxonomySpec(TableSpec):
    code_field = None
    feminine = True
    fields = [TableField("name", "Designação", required=True, max_length=100)]
    extra_columns = [("articles", "Artigos")]
    description = "Classificação da ficha de artigo, enviada para o site na sincronização do artigo publicado."
    local_only_reason = "Gerida na IAZELA e enviada ao site com os artigos publicados."

    def __init__(self, key, title, singular, attribute):
        super().__init__()
        self.key, self.title, self.singular, self.attribute = key, title, singular, attribute
        self.model = MODELS[attribute]
        if attribute == "category":
            self.fields = [
                TableField("family_id", "Família", required=True, max_length=36),
                *self.fields,
            ]

    def describe(self, ctx):
        result = super().describe(ctx)
        result["erp"]["integrated"] = False
        if self.attribute == "brand":
            result["description"] = (
                "Marcas da ficha de artigo. As marcas existentes no site são importadas pelo conector "
                "Raizpura; a classificação dos artigos publicados segue para o site."
            )
        if self.attribute == "category":
            result["fields"][0].update(type="select", options=[
                {"value": r.id, "label": r.name, "active": r.active}
                for r in ctx.db.scalars(select(ArticleFamily).order_by(ArticleFamily.name))
            ])
        return result

    def clean(self, ctx, data, row=None):
        from .inventory import lock

        lock(ctx.db)
        values = super().clean(ctx, data, row)
        if self.attribute == "category":
            parent = ctx.db.get(ArticleFamily, values["family_id"])
            if parent is None or (not parent.active and (row is None or row.family_id != parent.id)):
                raise HTTPException(422, "Selecione uma família ativa.")
        return values

    def unique_checks(self, ctx, values, row=None):
        for other in self.rows(ctx):
            if row is not None and row.id == other.id:
                continue
            if other.name.casefold() == values["name"].casefold() and (
                self.attribute != "category" or other.family_id == values["family_id"]
            ):
                raise HTTPException(409, "Já existe uma classificação com esta designação.")

    def linked_articles(self, ctx, row):
        data = cast(StockArticle.data, JSONB)
        query = select(StockArticle).where(func.lower(func.trim(data[self.attribute].astext)) == row.name.lower())
        if self.attribute == "category":
            parent = ctx.db.get(ArticleFamily, row.family_id)
            query = query.where(func.lower(func.trim(data["family"].astext)) == parent.name.lower())
        return [(a, json.loads(a.data)) for a in ctx.db.scalars(query)]

    def labels(self, ctx, rows):
        return {r.id: {"articles": str(len(self.linked_articles(ctx, r))),
                       **({"family_id": ctx.db.get(ArticleFamily, r.family_id).name}
                          if self.attribute == "category" else {})} for r in rows}

    def dependencies(self, ctx, row):
        result = [("artigos", len(self.linked_articles(ctx, row)))]
        if self.attribute == "family":
            result.append(("subfamílias", len(list(ctx.db.scalars(
                select(ArticleSubfamily).where(ArticleSubfamily.family_id == row.id))))))
        return result

    def update(self, ctx, row, values):
        # As fichas mantêm o contrato de nomes usado pelo conector WooCommerce.
        # Atualizar a revisão impede que uma ficha aberta reponha o nome anterior.
        linked = self.linked_articles(ctx, row)
        revisions = []
        for article, data in linked:
            data[self.attribute] = values["name"]
            if self.attribute == "category":
                data["family"] = ctx.db.get(ArticleFamily, values["family_id"]).name
            article.data = json.dumps(data, ensure_ascii=False)
            revisions.append({"id": article.id, "before": article.revision, "after": article.revision + 1})
            article.revision += 1
        super().update(ctx, row, values)
        if linked:
            ctx.audit("artigos_classificacao_alterada", row.id,
                      {"artigos": [a.id for a, _ in linked], "valores": values})
        return {"article_revisions": revisions}

    def set_active(self, ctx, row, active):
        from .inventory import lock

        lock(ctx.db)
        super().set_active(ctx, row, active)

    def delete(self, ctx, row):
        from .inventory import lock

        lock(ctx.db)
        if any(count for _, count in self.dependencies(ctx, row)):
            raise HTTPException(409, "A classificação está em utilização. Pode desativá-la.")
        super().delete(ctx, row)
