"""Проверки доступной публичной части, без обращения к закрытым рецептурам."""
import hashlib
import json
import re

import nbformat
import pytest
from PIL import Image
from pypdf import PdfReader

from scripts import portfolio as p

NAMES = {
    "C01": "Хреновуха", "C02": "Вишня", "C03": "Малина", "C04": "Лимончелло",
    "C05": "Щавель", "C09": "Яблоко — базилик", "C13": "Облепиха", "C14": "Клюква",
    "C06": "Халва", "C12": "Малина — сливки",
}
PUBLIC_FILES = {
    ".gitattributes", ".gitignore", ".github/workflows/ci.yml", "README.md", "requirements.txt",
    "bar_menu_analysis.ipynb", "data/nastoyki_reviews.xlsx", "data/flavor_summary.csv",
    "data/menu_economics.csv", "data/menu_raw_material_costs.csv", "docs/methodology.md",
    "results/menu_preview.png", "results/cost_50ml.png", "results/nastoyka_menu.pdf",
    "scripts/portfolio.py", "tests/test_portfolio.py",
}


@pytest.mark.parametrize("cid", list(NAMES))
def test_author_menu_is_preserved(cid):
    rows = p.read_csv("data/menu_economics.csv")
    assert len(rows) == 10 and {r["candidate_id"] for r in rows} == set(NAMES)
    row = next(r for r in rows if r["candidate_id"] == cid)
    assert row["normalized_flavor"] == NAMES[cid]
    premium = cid in p.PREMIUM
    assert row["tier"] == ("premium" if premium else "basic")
    assert p.number(row["price_50ml_rub"]) == (300 if premium else 250)
    assert p.number(row["target_ready_ml"]) == 6000 and p.number(row["portions"]) == 120
    assert abs(p.number(row["cost_50ml_rub"]) * 120-p.number(row["used_cost_rub"])) < p.D("1e-38")


def test_financial_controls():
    summary = p.economics_summary()
    for key, expected in {"ready_liters": "60", "portions": "1200", "purchase_rub": "40002.80",
                          "used_rub": "36357.61", "leftover_rub": "3645.19", "revenue_rub": "312000",
                          "material_contribution_rub": "275642.39"}.items():
        assert p.number(p.fmt(summary[key])) == p.D(expected)


def test_aggregate_purchases_and_inventory():
    rows = p.read_csv("data/menu_raw_material_costs.csv")
    assert len(rows) == 16 and len({r["Сырьё"] for r in rows}) == 16
    totals = {}
    for row in rows:
        cash = p.number(row["Закупка, ₽"])
        assert cash == p.number(row["Цена упаковки, ₽"]) * int(row["Количество упаковок"])
        assert p.number(row["Плановая закупка"]) >= p.number(row["Плановая потребность"])
        assert p.number(row["Остаток, количество"]) >= 0
        assert abs(cash-p.number(row["Израсходованное сырьё, ₽"])-p.number(row["Остаток сырья, ₽"])) <= p.D(".01")
        totals[row["Поставщик"]] = totals.get(row["Поставщик"], 0)+cash
    assert totals == {"Ладога": p.D("18240"), "METRO": p.D("19781.80"), "Агропарк СПб": p.D("1981")}
    assert abs(sum(p.number(r["Израсходованное сырьё, ₽"]) for r in rows)-p.economics_summary()["used_rub"]) <= p.D(".08")


def test_research_bases_and_preserved_sources():
    assert p.research_summary() == {
        "venues_total": 43, "current_sample": 30, "reviews_total": 368, "reviews_verified": 358,
        "mentions_total": 468, "concrete_mentions": 198, "concrete_reviews": 108,
        "flavors": 114, "strict_mentions": 143, "strict_reviews": 84,
    }
    hashes = {
        "data/nastoyki_reviews.xlsx": "fcc10acb01335fe5d273be5d7ad425fe3365625d8f23d1be70ae6b0f5ae27d52",
        "data/flavor_summary.csv": "a0cfe4118427c38b4eaaff9cc202b36b633f9e406d17aa693d0ce69da168c768",
    }
    for name, expected in hashes.items():
        assert hashlib.sha256((p.ROOT/name).read_bytes()).hexdigest() == expected


def test_one_page_pdf_contains_ten_drinks_and_correct_prices():
    reader = PdfReader(p.ROOT/"results/nastoyka_menu.pdf")
    assert len(reader.pages) == 1
    page = reader.pages[0]
    assert abs(float(page.mediabox.width)-595.276) < .1
    assert abs(float(page.mediabox.height)-841.89) < .1
    text = page.extract_text()
    assert all(name in text for name in NAMES.values())
    assert text.count("250") == 8 and text.count("300") == 2 and "50 мл" in text
    for ref in page["/Resources"]["/Font"].values():
        font = ref.get_object()["/DescendantFonts"][0].get_object()
        assert "/FontFile2" in font["/FontDescriptor"]


def test_images_are_readable_exports():
    with Image.open(p.ROOT/"results/menu_preview.png") as image:
        assert image.width >= 1400 and image.height >= 2000
    with Image.open(p.ROOT/"results/cost_50ml.png") as image:
        assert image.width >= 1400 and image.height >= 800


def test_public_schemas_exclude_recipe_details():
    menu = p.read_csv("data/menu_economics.csv")
    assert set(menu[0]) == {"candidate_id", "normalized_flavor", "tier", "price_50ml_rub",
                            "target_ready_ml", "portions", "used_cost_rub", "cost_50ml_rub"}
    purchases = p.read_csv("data/menu_raw_material_costs.csv")
    assert not {"candidate_id", "candidate_ids", "recipe_quantity", "scale_factor"} & set(purchases[0])
    nb = nbformat.read(p.ROOT/"bar_menu_analysis.ipynb", as_version=4)
    text = json.dumps(nb, ensure_ascii=False)
    assert all(s not in text for s in ["recipe_ingredients.csv", "recipes.json", "planned_yield_ml", "recipe_quantity"])


def test_current_public_tree_has_no_internal_archives():
    skip = {".git", ".pytest_cache", "__pycache__", ".ipynb_checkpoints", ".venv"}
    files = {str(f.relative_to(p.ROOT)) for f in p.ROOT.rglob("*")
             if f.is_file() and not set(f.relative_to(p.ROOT).parts) & skip}
    assert files == PUBLIC_FILES


def test_notebook_saved_outputs_and_links():
    nb = nbformat.read(p.ROOT/"bar_menu_analysis.ipynb", as_version=4)
    nbformat.validate(nb)
    cells = [c for c in nb.cells if c.cell_type == "code"]
    assert [c.execution_count for c in cells] == list(range(1, len(cells)+1))
    assert not any(o.output_type == "error" for c in cells for o in c.outputs)
    assert sum("image/png" in o.get("data", {}) for c in cells for o in c.outputs) == 3
    texts = [(p.ROOT/"README.md", (p.ROOT/"README.md").read_text(encoding="utf-8")),
             (p.ROOT/"docs/methodology.md", (p.ROOT/"docs/methodology.md").read_text(encoding="utf-8"))]
    texts += [(p.ROOT/"bar_menu_analysis.ipynb", c.source) for c in nb.cells if c.cell_type == "markdown"]
    for path, text in texts:
        links = re.findall(r'\]\(([^)]+)\)|src="([^"]+)"', text)
        for markdown, html in links:
            url = markdown or html
            if "://" not in url and not url.startswith("#"):
                assert (path.parent/url.split("#")[0]).exists(), url
