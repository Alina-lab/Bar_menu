"""Критические единицы, расходы, закупочные остатки и сохранность базы."""

import hashlib
import json
import subprocess
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from scripts import economics as e
from scripts import workbook

ROOT=Path(__file__).resolve().parents[1]


@pytest.fixture
def ingredients():
    # Искусственные числа нужны только для проверки арифметики, не являются ценами проекта.
    return pd.DataFrame([
        {"ingredient_id":"vodka","package_price_rub":180,"package_base_quantity":500,"rub_per_base_unit":.36},
        {"ingredient_id":"berry","package_price_rub":500,"package_base_quantity":1000,"rub_per_base_unit":.5},
    ]).set_index("ingredient_id")


def test_unit_dimensions():
    c=e.read_config()
    assert e.quantity_in_base(1,"kg",c)==(1000.,"g")
    assert e.quantity_in_base(1,"l",c)==(1000.,"ml")
    assert e.quantity_in_base(200,"g",c)==(200.,"g")
    assert e.quantity_in_base(200,"ml",c)==(200.,"ml")
    with pytest.raises(ValueError):e.quantity_in_base(1,"spoon",c)


def test_cost_with_yield_and_loss(ingredients):
    result=e.recipe_cost({"vodka":500,"berry":250},ingredients,1000,.20)
    assert result["batch_ingredient_cost_rub"]==305
    assert result["ready_ml"]==800
    assert result["cost_liter_rub"]==381.25
    assert result["cost_50ml_rub"]==19.0625


def test_extra_loss_is_nonlinear(ingredients):
    a=e.recipe_cost({"vodka":500},ingredients,1000,.10)
    b=e.recipe_cost({"vodka":500},ingredients,1000,.20)
    assert b["cost_50ml_rub"]/a["cost_50ml_rub"]==pytest.approx(.9/.8)


@pytest.mark.parametrize("loss",[-.1,1,1.1])
def test_invalid_loss_rejected(ingredients,loss):
    with pytest.raises(ValueError):e.recipe_cost({"vodka":500},ingredients,1000,loss)


def test_empty_or_unknown_price_rejected(ingredients):
    with pytest.raises(ValueError):e.recipe_cost({"unknown":5},ingredients,500,.1)
    ingredients.loc["vodka","rub_per_base_unit"]=np.nan
    with pytest.raises(ValueError):e.recipe_cost({"vodka":500},ingredients,500,.1)


def test_whole_packages_and_inventory(ingredients):
    basket=e.procurement({"berry":1500},ingredients)
    row=basket.loc["berry"]
    assert row.packages==2
    assert row.purchase_cash_rub==1000
    assert row.consumed_cost_rub==750
    assert row.leftover_base_quantity==500
    assert row.leftover_value_rub==250
    assert row.purchase_cash_rub==row.consumed_cost_rub+row.leftover_value_rub
    assert row.additional_cash_second_cycle_rub==500


def test_joint_procurement_avoids_double_rounding(ingredients):
    first=e.procurement({"berry":600},ingredients).purchase_cash_rub.sum()
    second=e.procurement({"berry":400},ingredients).purchase_cash_rub.sum()
    joint=e.procurement({"berry":1000},ingredients).purchase_cash_rub.sum()
    assert first+second==1000 and joint==500


def test_negative_demand_rejected(ingredients):
    with pytest.raises(ValueError):e.procurement({"berry":-10},ingredients)


def test_price_shock_changes_cash_and_used_cost(ingredients):
    a=e.procurement({"berry":1500},ingredients)
    b=e.procurement({"berry":1500},ingredients,1.2)
    assert b.purchase_cash_rub.sum()==pytest.approx(a.purchase_cash_rub.sum()*1.2)
    assert b.consumed_cost_rub.sum()==pytest.approx(a.consumed_cost_rub.sum()*1.2)


def test_material_margin():
    assert e.material_margin(200,50)==(150,.75)
    assert e.material_margin(100,120)==(-20,-.2)


@pytest.mark.parametrize("share,expected",[(.05,235),(.20,250),(.35,265)])
def test_sales_weighted_price(share,expected):
    assert e.weighted_price(230,330,share)==pytest.approx(expected)


def test_target_share_and_menu_average():
    assert (8*230+2*330)/10==250
    assert e.required_premium_share(230,330,250)==.2
    assert e.required_premium_share(230,330,400) is None
    assert e.weighted_price(230,330,.05)<250


def test_original_files_and_normalization_unchanged():
    e.check_baseline()
    frames=e.workbook_frames()
    assert len(frames["reviews"])==368 and len(frames["mentions"])==468
    assert frames["reviews"]["ID отзыва"].is_unique
    assert frames["mentions"]["ID"].is_unique
    dictionary=frames["flavors"].fillna("").to_dict(orient="records")
    assert workbook.normalize_flavor("кокос с бананом",dictionary)=="Банан — кокос"


def test_old_refresh_works_on_copy(tmp_path):
    copy=tmp_path/"copy.xlsx"
    original=ROOT/"data/nastoyki_reviews.xlsx"
    copy.write_bytes(original.read_bytes())
    result=subprocess.run([sys.executable,str(ROOT/"scripts/workbook.py"),"refresh",
                           "--workbook",str(copy),"--export-dir",str(tmp_path/"processed")],
                          capture_output=True,text=True,check=True)
    report=json.loads(result.stdout)
    assert report["stats"]["reviews_total"]==368
    assert report["stats"]["concrete_mentions_verified"]==198
    assert report["quality"]["errors"]==[]
    old=pd.read_csv(ROOT/"data/processed/reviews.csv")
    new=pd.read_csv(tmp_path/"processed/reviews.csv")
    assert set(old["ID отзыва"])==set(new["ID отзыва"])
    e.check_baseline()


def test_candidates_match_existing_summary():
    candidates,_=e.prepare_candidates()
    assert len(candidates)==15
    assert candidates.positive.sum()>0
    row=candidates.set_index("normalized_flavor").loc["Томатная"]
    assert row.positive==6 and row.strict_positive==1 and row.unique_venues==1
    row=candidates.set_index("normalized_flavor").loc["Клюква"]
    assert row.positive==0
    assert (candidates[["positive","negative","mixed","neutral","mention_only"]].sum(axis=1)==candidates.unique_events).all()


def test_menu_constraints_and_procurement_conservation():
    config=e.read_config()
    summary=json.loads((e.OUT/"summary.json").read_text())
    roles=pd.read_csv(e.DATA/"candidate_roles.csv").set_index("candidate_id")
    for menu in summary["menus"].values():
        selected=menu["selected"]
        assert len(set(selected))==10
        assert len(set(selected)&set(config["premium_pool"]))==2
        assert menu["expensive_specific_recipes"]<=2
        assert set(config["required_primary_roles"]).issubset(roles.loc[selected,"primary_role"])
        assert menu["purchase_cash_rub"]==pytest.approx(menu["consumed_cost_rub"]+menu["leftover_value_rub"])
    assert "C07" in summary["menus"]["C"]["selected"]
    assert len({tuple(x["selected"]) for x in summary["menus"].values()})==3
    assert summary["recipes_with_measured_yield"]==0
    p=pd.read_csv(e.OUT/"procurement.csv")
    assert (p.packages%1==0).all() and p.leftover_base_quantity.ge(-1e-8).all()
    np.testing.assert_allclose(p.purchase_cash_rub,p.consumed_cost_rub+p.leftover_value_rub)


def test_scenarios_are_not_sales_and_shocks_are_monotonic():
    s=pd.read_csv(e.OUT/"price_scenarios.csv")
    assert s.sales_status.eq("scenario_not_observed").all()
    assert s.loc[s.price_pair.eq(0)&s.scenario.eq("low"),"weighted_price_rub"].eq(235).all()
    t=pd.read_csv(e.OUT/"sensitivity.csv")
    base=t[t.scenario.eq("base")].set_index(["menu","candidate_id"]).cost_50ml_rub
    shock=t[t.scenario.eq("price_plus_20")].set_index(["menu","candidate_id"]).cost_50ml_rub
    np.testing.assert_allclose(shock.loc[base.index],base*1.2)


def test_notebook_and_key_report_numbers():
    nb=json.loads((ROOT/"assortment_economics.ipynb").read_text())
    cells=[c for c in nb["cells"] if c["cell_type"]=="code"]
    assert [c["execution_count"] for c in cells]==list(range(1,len(cells)+1))
    assert not any(o["output_type"]=="error" for c in cells for o in c["outputs"])
    for c in cells:compile("".join(c["source"]),"notebook_cell","exec")
    text=(ROOT/"README.md").read_text()
    for value in ["16 993", "17 009", "18 311", "20%", "235", "265"]:
        assert value in text
