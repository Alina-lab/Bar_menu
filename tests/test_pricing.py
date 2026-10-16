"""Порог цены проверяется по фактическому заказу во всех сценариях."""
import copy
import shutil
import subprocess
import sys
from decimal import Decimal as D

import pytest
from scripts import economics as e
from scripts.report import fmt, yield_sensitivity_table


def cherry(inputs):
    return inputs[3]["cherry"]


@pytest.mark.parametrize("packs,price,status", [
    (3, D(799), "discount_threshold_met"),
    (4, D(799), "discount_threshold_met"),
    (2, None, "missing_regular_price"),
    (1, None, "missing_regular_price"),
])
def test_threshold_boundary(packs, price, status):
    resolved = e.resolve_pack_price(cherry(e.load_inputs()), packs)
    assert resolved[0] == price and resolved[1] == status
    assert resolved[3] == (packs >= 3)


def test_known_regular_price_requires_provenance_and_is_used():
    raw = copy.deepcopy(cherry(e.load_inputs()))
    # Синтетическая цена исключительно для теста, не рыночная котировка.
    raw.update(regular_pack_price_rub="1234.56", regular_price_source="test://fixture",
               regular_price_date="2026-10-16")
    price, status, source, met = e.resolve_pack_price(raw, 2, D("1.1"))
    assert price == D("1358.016") and status == "regular_rate"
    assert source == "test://fixture" and met is False
    assert e.resolve_pack_price(raw, 3)[0] == 799


def test_yield_change_reprices_actual_packs_without_buying_extra():
    inputs = e.load_inputs()
    base = e.calculate(inputs)
    higher = e.calculate(inputs, yield_multiplier=D("1.1"))
    old = next(r for r in base[2] if r["ingredient_id"] == "cherry")
    new = next(r for r in higher[2] if r["ingredient_id"] == "cherry")
    assert old["packs_to_buy"] == 3 and old["effective_pack_price_rub"] == 799
    assert new["packs_to_buy"] == 2 and new["purchased_quantity"] == 2000
    assert new["effective_pack_price_rub"] is None
    assert new["purchase_cost_rub"] is None and new["used_cost_rub"] is None
    assert base[3]["purchase_cost_rub"] == D("40002.80")
    for k in ("used_cost_rub", "purchase_cost_rub", "leftover_value_rub",
              "potential_material_contribution_rub", "material_margin_fraction"):
        assert higher[3][k] is None
    assert higher[3]["potential_revenue_rub"] == 312000
    assert next(r for r in higher[0] if r["candidate_id"] == "C02")["cost_50ml_rub"] is None
    assert all(r["cost_50ml_rub"] is not None for r in higher[0] if r["candidate_id"] != "C02")


@pytest.mark.parametrize("multiplier", ["0.8", "0.9", "1", "1.1", "2", "3"])
def test_every_quantity_scenario_checks_all_discount_thresholds(multiplier):
    result = e.calculate(e.load_inputs(), yield_multiplier=D(multiplier))
    missing = []
    for row in result[2]:
        threshold = int(row["catalog_rate_min_packs"] or 0)
        if threshold and row["packs_to_buy"] < threshold:
            missing.append(row["ingredient_id"])
            assert row["effective_pack_price_rub"] is None
        else:
            assert row["effective_pack_price_rub"] == D(row["pack_price_rub"])
    assert (result[3]["purchase_cost_rub"] is None) == bool(missing)
    assert result[3]["missing_price_ingredients"].split("|") == (missing or [""])


def test_missing_prices_survive_csv_and_human_report(tmp_path):
    inputs = e.load_inputs()
    rows = e.scenario_tables(inputs, e.calculate(inputs))[3]
    path = tmp_path / "yield.csv"
    e.write_csv(path, rows)
    saved = next(r for r in e.read_csv(path) if r["candidate_id"] == "ALL" and D(r["yield_multiplier"]) == D("1.1"))
    assert saved["used_cost_rub"] == "" and saved["purchase_cost_rub"] == ""
    assert saved["financial_status"] == "incomplete_missing_price"
    assert saved["missing_price_ingredients"] == "cherry"
    text = yield_sensitivity_table(rows)
    line = next(s for s in text.splitlines() if "110%" in s)
    assert line.count("недоступно") >= 2 and "cherry" in line
    assert fmt(None) == "недоступно"


def test_editable_yield_and_all_sales_tables_propagate_missing_price():
    inputs = copy.deepcopy(e.load_inputs())
    inputs[2]["C02"]["yield_ml"] = "759"
    base = e.calculate(inputs)
    assert base[3]["financial_status"] == "incomplete_missing_price"
    sales, mix, price_stress, yield_stress = e.scenario_tables(inputs, base)
    assert all(r["cash_after_ingredient_purchase_rub"] is None for r in sales)
    assert all(r["weighted_material_cost_rub"] is None for r in mix)
    assert all(r["purchase_cost_rub"] is None for r in price_stress if r["candidate_id"] == "ALL")
    assert all(r["weighted_price_rub"] > 0 for r in mix)


def test_all_real_regular_prices_remain_unknown():
    assert all(r["regular_pack_price_rub"] == "" and r["regular_price_source"] == ""
               for r in e.load_inputs()[3].values())


def test_regular_rate_reprices_the_recipe_and_complete_total():
    inputs = copy.deepcopy(e.load_inputs())
    cherry(inputs).update(regular_pack_price_rub="1234.56", regular_price_source="test://fixture",
                          regular_price_date="2026-10-16")
    result = e.calculate(inputs, yield_multiplier=D("1.1"))
    row = next(r for r in result[2] if r["ingredient_id"] == "cherry")
    assert row["packs_to_buy"] == 2 and row["purchase_cost_rub"] == D("2469.12")
    assert row["applied_price_source"] == "test://fixture"
    ingredient = next(r for r in result[1] if r["ingredient_id"] == "cherry")
    assert ingredient["price_per_base_unit_rub"] == D("1.23456")
    assert result[3]["financial_status"] == "complete_price_scenario"
    assert result[3]["purchase_cost_rub"] > 0


def test_human_money_rounding_is_half_up():
    assert fmt(D("1.005")) == "1,01"


def test_full_report_handles_incomplete_current_basket(tmp_path, monkeypatch):
    from scripts import report
    inputs = copy.deepcopy(e.load_inputs())
    inputs[2]["C02"]["yield_ml"] = "759"
    base = e.calculate(inputs)
    tables = dict(zip(["sales_scenarios.csv", "premium_mix.csv", "price_sensitivity.csv",
                       "yield_sensitivity.csv"], e.scenario_tables(inputs, base)))
    tables["suppliers.csv"] = [{"supplier": "test fixture", "purchase_cost_rub": None,
                                "used_cost_rub": None, "leftover_value_rub": None}]
    (tmp_path/"docs").mkdir()
    (tmp_path/"data/processed").mkdir(parents=True)
    shutil.copyfile(e.ROOT/"data/processed/summary.json", tmp_path/"data/processed/summary.json")
    monkeypatch.setattr(e, "ROOT", tmp_path)
    monkeypatch.setattr(report, "figures", lambda *args: None)
    report.render(inputs, base, tables)
    text = (tmp_path/"docs/economics_report.md").read_text(encoding="utf-8")
    assert "недоступно" in text and "incomplete_missing_price" in text
    assert "Первоначальный платёж за сырьё — недоступно" in text


def test_offline_guard_blocks_external_network_before_connecting():
    program = """from scripts.verify import install_offline_guard
import socket
install_offline_guard()
try:
    socket.create_connection(('203.0.113.1', 80), timeout=0.01)
except RuntimeError as exc:
    assert 'внешней сети' in str(exc)
else:
    raise AssertionError('external connection was allowed')
"""
    subprocess.run([sys.executable, "-c", program], cwd=e.ROOT, check=True)
