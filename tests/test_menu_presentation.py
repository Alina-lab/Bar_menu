"""Presentation notebook and aggregated purchasing sheet, without recipe disclosures."""
import csv
from decimal import Decimal
from pathlib import Path

import nbformat
from nbclient import NotebookClient

from scripts import economics as e

ROOT = Path(__file__).resolve().parents[1]


def rub(value):
    return Decimal(value.replace("\u00a0", "").replace(" ", "").replace(",", "."))


def test_menu_costs_are_aggregated_and_reconcile():
    path = ROOT / "data/economics/menu_raw_material_costs.csv"
    with path.open(encoding="utf-8-sig", newline="") as stream:
        rows = list(csv.DictReader(stream))
    assert len(rows) == 16
    assert "candidate_id" not in rows[0] and "recipe_quantity" not in rows[0]
    assert rub(str(sum(rub(r["Закупка, ₽"]) for r in rows))) == Decimal("40002.80")
    assert {r["Поставщик"] for r in rows} == {"Ладога", "METRO", "Агропарк СПб"}
    assert rub(next(r["Закупка, ₽"] for r in rows if "Царская" in r["Сырьё"])) == 18240


def test_presentational_notebook_runs_without_recipes():
    path = ROOT / "nastoyka_menu.ipynb"
    nb = nbformat.read(path, as_version=4)
    text = "\n".join("".join(c.source) for c in nb.cells)
    assert "recipes.json" not in text
    assert "recipe_ingredients.csv" not in text
    assert "47,300705" not in text  # Explicit author dosages are deliberately omitted.
    assert "menu_raw_material_costs.csv" in text
    assert len([c for c in nb.cells if c.cell_type == "code"]) == 2
    NotebookClient(nb, timeout=180, kernel_name="python3",
                   resources={"metadata": {"path": str(ROOT)}}).execute()
    for cell in nb.cells:
        if cell.cell_type == "code":
            assert cell.execution_count is not None
            assert not any(out.output_type == "error" for out in cell.outputs)
