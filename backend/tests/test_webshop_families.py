# ruff: noqa: F811
"""Categorias da loja online = famílias e subfamílias da IAZELA: identificador estável em cada publicação, árvore
das famílias usadas nos artigos da ligação e mudança de nome enviada à loja sem mudar o identificador."""

from test_inventory import product, stock_case  # noqa: F401
from test_webshop import connection
from test_webshop_catalog import BASE, feed, ok, publish, report


def tree(c, headers):
    response = c.get(BASE + "/families", headers=headers)
    assert response.status_code == 200, response.text
    return response.json()


def test_shop_categories_follow_iazela_families_by_stable_id(stock_case):
    c = stock_case[0]
    vitamins = product(stock_case, name="Vitamina C", family="Suplementos", category="Vitaminas")
    immunity = product(stock_case, name="Própolis", family="Suplementos", category="Sistema Imunitário")
    product(stock_case, name="Champô fora da loja", family="Higiene")  # não publicado: não vai para a loja
    loose = product(stock_case, name="Artigo sem família")
    conn, headers = connection(stock_case)
    for article in (vitamins, immunity, loose):
        publish(stock_case, conn, article)
    sent = {item["article_id"]: item for item in feed(stock_case, headers)}
    family_id = sent[vitamins]["family_id"]
    assert family_id and sent[immunity]["family_id"] == family_id
    assert sent[vitamins]["category_id"] and sent[vitamins]["category_id"] != sent[immunity]["category_id"]
    assert (sent[loose]["family_id"], sent[loose]["category_id"]) == ("", "")
    families = tree(c, headers)["families"]
    assert [f["name"] for f in families] == ["Suplementos"]  # só as famílias dos artigos desta loja
    assert families[0]["id"] == family_id
    assert [s["name"] for s in families[0]["subfamilies"]] == ["Sistema Imunitário", "Vitaminas"]
    report(stock_case, headers, *(ok(item, 950 + i) for i, item in enumerate(sent.values())))
    assert feed(stock_case, headers) == []
    # Mudar o nome da família na IAZELA: a loja recebe o nome novo com o mesmo identificador.
    renamed = c.post(f"/api/tables/article_families/records/{family_id}", json={"values": {"name": "Suplementos Alimentares"}})
    assert renamed.status_code == 200, renamed.text
    again = {item["article_id"]: item for item in feed(stock_case, headers)}
    assert set(again) == {vitamins, immunity}
    assert again[vitamins]["family"] == "Suplementos Alimentares" and again[vitamins]["family_id"] == family_id
    assert tree(c, headers)["families"][0]["name"] == "Suplementos Alimentares"
    assert c.get(BASE + "/families").status_code == 401  # só com a credencial da ligação


def test_seo_keyword_is_sent_to_the_shop(stock_case):
    article = product(stock_case, name="Própolis spray", seo_keyword="própolis spray sem álcool",
                      seo_title="Própolis spray sem álcool | Marca", seo_description="Spray de própolis sem álcool.")
    conn, headers = connection(stock_case)
    publish(stock_case, conn, article)
    sent = feed(stock_case, headers)[0]
    assert (sent["seo_keyword"], sent["seo_title"]) == ("própolis spray sem álcool", "Própolis spray sem álcool | Marca")
