"""Проверки доступной публичной части, без обращения к закрытым рецептурам."""
import hashlib
import json
import re

import nbformat
import pytest
from PIL import Image
from pypdf import PdfReader

from technical.scripts import portfolio as p

NAMES = {
    "C01": "Хреновуха", "C02": "Вишня", "C03": "Малина", "C04": "Лимончелло",
    "C05": "Щавель", "C09": "Яблоко — базилик", "C13": "Облепиха", "C14": "Клюква",
    "C06": "Халва", "C12": "Малина — сливки",
}
PUBLIC_FILES = {
    ".gitattributes", ".gitignore", "README.md",
    "bar_menu_analysis.ipynb", "technical/requirements.txt",
    "technical/data/nastoyki_reviews.xlsx", "technical/data/flavor_summary.csv",
    "technical/data/menu_economics.csv", "technical/data/menu_raw_material_costs.csv",
    "technical/results/menu_preview.png", "technical/results/cost_50ml.png",
    "technical/results/nastoyka_menu.pdf", "technical/scripts/portfolio.py",
    "technical/tests/test_portfolio.py",
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
        "data/menu_economics.csv": "08c72caa747a82de0c29c5fff0a548eb5f50e005f4879d0a6b75ee78ebbc23ed",
        "data/menu_raw_material_costs.csv": "c36e9ab276e8d1cc38d1802b19b56b4937244a54ba4d70e08e5296c99cb09742",
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


def test_pdf_taste_groups_and_requested_copy():
    text = PdfReader(p.ROOT/"results/nastoyka_menu.pdf").pages[0].extract_text()
    text = re.sub(r"\s+", " ", text)
    expected = [
        ("СЛАДКИЕ И ДЕСЕРТНЫЕ", ["Вишня", "Малина", "Халва", "Малина — сливки"]),
        ("КИСЛЫЕ И ОСВЕЖАЮЩИЕ", ["Лимончелло", "Щавель", "Облепиха", "Клюква"]),
        ("СВЕЖИЕ И ТРАВЯНЫЕ", ["Яблоко — базилик"]),
        ("ОСТРЫЕ И ПРЯНЫЕ", ["Хреновуха"]),
    ]
    for index, (heading, names) in enumerate(expected):
        section = text.split(heading, 1)[1]
        if index+1 < len(expected):
            section = section.split(expected[index+1][0], 1)[0]
        positions = [section.index(name) for name in names]
        assert positions == sorted(positions)
        assert section.count("₽") == len(names)
    descriptions = {
        "C02": "Вишнёвая кислинка.",
        "C03": "Ароматная малина.",
        "C06": "Десертная настойка с подсолнечной халвой и мёдом.",
        "C12": "Малина на нежной основе из молока и сливок.",
        "C04": "Концентрированный цитрус.",
        "C05": "Свежий щавель с мёдом.",
        "C13": "Насыщенная облепиха с кислинкой.",
        "C14": "Терпкая клюква.",
        "C09": "Кислое яблоко и зеленый базилик.",
        "C01": "Хрен, имбирь и мёд с пряным характером.",
    }
    assert p.MENU_DESCRIPTIONS == descriptions
    for description in descriptions.values():
        assert text.count(description) == 1
    for obsolete in ("Санкт-Петербург", "С А Н К Т", "порция", "Авторская коллекция",
                     "ОСНОВНАЯ КОЛЛЕКЦИЯ", "ДЕСЕРТНАЯ КОЛЛЕКЦИЯ", "содержит молочные продукты"):
        assert obsolete not in text


def test_pdf_print_layout_and_vector_artwork():
    from matplotlib.font_manager import FontProperties
    from matplotlib.textpath import TextPath
    from pypdf.generic import ContentStream

    page = PdfReader(p.ROOT/"results/nastoyka_menu.pdf").pages[0]
    name_baselines, price_baselines, description_baselines = set(), [], set()
    price_positions = set()
    description_right_edges = []
    def inspect(text, matrix, text_matrix, font, size):
        text = text.strip()
        if not text:
            return
        x, y = matrix[4]+text_matrix[4], matrix[5]+text_matrix[5]
        family = "DejaVu Serif" if "Serif" in str(font.get("/BaseFont")) else "DejaVu Sans"
        bounds = TextPath((x, y), text, size=size, prop=FontProperties(family=family)).get_extents()
        assert bounds.x0 >= 25 and bounds.x1 <= float(page.mediabox.width)-25
        assert bounds.y0 >= 25 and bounds.y1 <= float(page.mediabox.height)-25
        if size == 15.2:
            name_baselines.add(y)
        if size == 9.2:
            description_baselines.add(y)
            description_right_edges.append(bounds.x1)
        if text in {"250", "300"}:
            price_baselines.append(y)
            price_positions.add(x)
    page.extract_text(visitor_text=inspect)
    baselines = sorted(name_baselines)
    assert len(baselines) == len(price_baselines) == 10
    assert all(abs(b-a-60) < .01 for a, b in zip(baselines, baselines[1:]))
    assert sorted(price_baselines) == baselines
    assert len(price_positions) == 1
    assert max(description_right_edges) < min(price_positions)-12
    assert len(description_baselines) == 10
    assert all(abs(name-description-17) < .01
               for name, description in zip(baselines, sorted(description_baselines)))
    artwork = page["/Resources"].get("/XObject")
    assert artwork is None or not artwork.get_object()  # No raster images in the PDF.
    assert any(op == b"c" for _, op in ContentStream(page.get_contents(), page.pdf).operations)


def test_analysis_focus_and_current_paths():
    notebook = nbformat.read(p.PROJECT_ROOT/"bar_menu_analysis.ipynb", as_version=4)
    sources = "\n".join(c.source for c in notebook.cells)
    prose = sources
    assert not re.search(r"демограф|пол посетител|возраст посетител|город проживания", prose, re.I)
    headings = re.findall(r"^## (\d+)\.", sources, re.M)
    assert headings == [str(i) for i in range(1, 10)]
    for old_path in ("nastoyka_menu.ipynb", "figures/", "menu/nastoyka_menu.pdf"):
        assert old_path not in sources


def test_agreed_notebook_text_is_preserved():
    notebook = nbformat.read(p.PROJECT_ROOT/"bar_menu_analysis.ipynb", as_version=4)
    markdown = [c.source.replace("](technical/data/", "](data/").replace("](technical/results/", "](results/")
                for c in notebook.cells if c.cell_type == "markdown"]
    assert hashlib.sha256("\n".join(markdown).encode("utf-8")).hexdigest() == (
        "d67aff1e681e553f2d61f3824b58e94ff731a5123a300505b63a66af54f98524")


def test_public_schemas_exclude_recipe_details():
    menu = p.read_csv("data/menu_economics.csv")
    assert set(menu[0]) == {"candidate_id", "normalized_flavor", "tier", "price_50ml_rub",
                            "target_ready_ml", "portions", "used_cost_rub", "cost_50ml_rub"}
    purchases = p.read_csv("data/menu_raw_material_costs.csv")
    assert not {"candidate_id", "candidate_ids", "recipe_quantity", "scale_factor"} & set(purchases[0])
    nb = nbformat.read(p.PROJECT_ROOT/"bar_menu_analysis.ipynb", as_version=4)
    text = json.dumps(nb, ensure_ascii=False)
    assert all(s not in text for s in ["recipe_ingredients.csv", "recipes.json", "planned_yield_ml", "recipe_quantity"])


def test_current_public_tree_has_no_internal_archives():
    skip = {".git", ".pytest_cache", "__pycache__", ".ipynb_checkpoints", ".venv"}
    files = {f.relative_to(p.PROJECT_ROOT).as_posix() for f in p.PROJECT_ROOT.rglob("*")
             if f.is_file() and not set(f.relative_to(p.PROJECT_ROOT).parts) & skip}
    assert files == PUBLIC_FILES


def test_notebook_saved_outputs_and_links():
    nb = nbformat.read(p.PROJECT_ROOT/"bar_menu_analysis.ipynb", as_version=4)
    nbformat.validate(nb)
    cells = [c for c in nb.cells if c.cell_type == "code"]
    assert [c.execution_count for c in cells] == list(range(1, len(cells)+1))
    assert not any(o.output_type == "error" for c in cells for o in c.outputs)
    assert sum("image/png" in o.get("data", {}) for c in cells for o in c.outputs) == 3
    texts = [(p.PROJECT_ROOT/"README.md", (p.PROJECT_ROOT/"README.md").read_text(encoding="utf-8"))]
    texts += [(p.PROJECT_ROOT/"bar_menu_analysis.ipynb", c.source) for c in nb.cells if c.cell_type == "markdown"]
    for path, text in texts:
        links = re.findall(r'\]\(([^)]+)\)|src="([^"]+)"', text)
        for markdown, html in links:
            url = markdown or html
            own_repo = "https://github.com/Alina-lab/Bar_menu/blob/main/"
            if url.startswith(own_repo):
                assert (p.PROJECT_ROOT/url[len(own_repo):]).exists(), url
            elif "://" not in url and not url.startswith("#"):
                assert (path.parent/url.split("#")[0]).exists(), url
