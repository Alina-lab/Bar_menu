"""Точное авторское поручение, независимость от отбора и материальная арифметика."""

import copy
import json

import numpy as np
import pandas as pd
import pytest

from scripts import author_menu as a
from scripts import economics as e


def test_exact_author_selection_and_prices():
    m = a.load_author_menu()
    assert set(m.candidate_id) == {"C01","C02","C03","C04","C05","C09","C13","C14","C06","C12"}
    assert m.tier.value_counts().to_dict() == {"basic":8,"premium":2}
    assert m.price_50ml_rub.mean() == 260
    assert "C14" in set(m.candidate_id)  # Ноль положительных отзывов не исключает клюкву.


@pytest.mark.parametrize("change",["replace","tier","price","duplicate","name","status"])
def test_corrupt_author_input_fails_without_repair(tmp_path, change):
    m = a.load_author_menu()
    if change == "replace":m.loc[0,"candidate_id"]="C10"
    if change == "tier":m.loc[0,"tier"]="premium"
    if change == "price":m.loc[0,"price_50ml_rub"]=230
    if change == "duplicate":m.loc[0]=m.loc[1]
    if change == "name":m.loc[0,"normalized_flavor"]="Иной вкус"
    if change == "status":m.loc[0,"selection_status"]="optimized"
    path=tmp_path/"menu.csv";m.to_csv(path,index=False)
    with pytest.raises(ValueError):a.load_author_menu(path)


def test_legacy_optimizer_cannot_change_author_results(monkeypatch,tmp_path):
    config=e.read_config();ingredients=e.load_ingredients(config);menu=a.load_author_menu()
    before,_=a.evaluate_items(menu,ingredients,config)
    changed=copy.deepcopy(config)
    changed.update(premium_pool=["C01"],basic_price_rub=1,premium_price_rub=2,menu_size=99,
                   balanced_weights={"review_evidence":1,"diversity":0,"sharing":0,"cash":0},
                   max_per_primary_role=0,author_anchor_candidates=["C07"])
    def forbidden(*args,**kwargs):raise AssertionError("Авторское меню не должно вызывать отбор")
    monkeypatch.setattr(e,"select_menus",forbidden)
    monkeypatch.setattr(e,"fixed_and_reviewed_menus",forbidden)
    monkeypatch.setattr(e,"read_config",lambda:changed)
    monkeypatch.setattr(e,"OUT",tmp_path)
    monkeypatch.setattr(e,"FIG",tmp_path/"figures")
    result=a.run()
    after=pd.read_csv(tmp_path/"author_items.csv").set_index("candidate_id")
    assert set(result["selected_ids"])==set(menu.candidate_id)
    np.testing.assert_allclose(after.loc[before.index,"cost_50ml_rub"],before.cost_50ml_rub)
    np.testing.assert_allclose(after.loc[before.index,"price_50ml_rub"],before.price_50ml_rub)


def test_shared_raspberry_uses_one_package_and_same_consumption_cost():
    share=pd.read_csv(e.OUT/"author_sharing.csv")
    r=share[(share.ingredient_id=="raspberry")&(share.ready_liters_per_recipe==1)].iloc[0]
    assert r.candidate_ids=="C03|C12"
    assert r.joint_packages==1 and r.separate_packages==2
    assert r.purchase_saving_rub==838 and r.consumed_cost_saving_rub==0
    r5=share[(share.ingredient_id=="raspberry")&(share.ready_liters_per_recipe==5)].iloc[0]
    assert r5.purchase_saving_rub==0


def test_author_procurement_conservation_and_full_matrix():
    q=pd.read_csv(e.OUT/"author_ingredient_matrix.csv",index_col="candidate_id")
    p=pd.read_csv(e.OUT/"author_procurement.csv");p=p[p.menu.eq("AUTHOR")].set_index("ingredient_id")
    assert q.shape==(10,21)
    np.testing.assert_allclose(q.sum().loc[p.index],p.needed_base_quantity)
    np.testing.assert_allclose(p.purchase_cash_rub,p.consumed_cost_rub+p.leftover_value_rub)
    assert p.packages.mod(1).eq(0).all() and p.leftover_base_quantity.ge(0).all()
    assert "ml" == p.loc["cream","base_unit"]


def test_author_sales_scenarios_and_premium_not_cost_rank():
    s=pd.read_csv(e.OUT/"author_price_scenarios.csv");s=s[s.menu.eq("AUTHOR")].sort_values("premium_sales_share")
    np.testing.assert_allclose(s.weighted_price_50ml_rub,[252.5,260,267.5])
    assert s.sales_status.eq("scenario_not_observed").all()
    r=pd.read_csv(e.OUT/"author_items.csv").set_index("candidate_id")
    assert r.loc["C06","cost_50ml_rub"]<r.loc["C05","cost_50ml_rub"]
    assert r.loc["C12","cost_50ml_rub"]<r.loc["C03","cost_50ml_rub"]
    assert r.final_abv_pct.isna().all() and r.shelf_life_days.isna().all()


def test_stress_losses_are_recomputed_and_prices_are_fixed():
    t=pd.read_csv(e.OUT/"author_item_stress.csv").set_index(["candidate_id","scenario"])
    for cid in a.load_author_menu().candidate_id:
        base=t.loc[(cid,"base")]
        assert t.loc[(cid,"price_plus_20"),"cost_50ml_rub"]==pytest.approx(base.cost_50ml_rub*1.2)
        assert t.loc[(cid,"loss_plus_10pp"),"cost_50ml_rub"]==pytest.approx(base.cost_50ml_rub*(1-base.loss_fraction_assumed)/(1-base.loss_fraction_assumed-.1))
        assert t.loc[(cid,"loss_plus_10pp"),"ready_ml"]<base.ready_ml
    s=pd.read_csv(e.OUT/"author_menu_stress.csv")
    assert len(s)==12
    assert s.sales_status.eq("scenario_not_observed").all()
    assert s.loc[s.premium_sales_share.eq(.2),"weighted_price_50ml_rub"].eq(260).all()


def test_unknown_delivery_is_not_free():
    s=pd.read_csv(e.OUT/"author_suppliers.csv")
    assert s.delivery_cost_rub.isna().all()
    assert s.delivery_low_rub.isna().sum()==4
    assert s.set_index("supplier").loc["ФрутСтар","delivery_low_rub"]==700
    c=pd.read_csv(e.OUT/"author_comparison.csv").set_index("menu")
    assert c.loc["AUTHOR","two_supplier_delivery_low_rub"]==1200
    assert c.loc["AUTHOR","two_supplier_delivery_high_rub"]==1400
    assert c.loc["AUTHOR","unknown_delivery_supplier_count"]==4


def test_historical_result_files_unchanged():
    import hashlib
    frozen=json.loads((e.DATA/"stage2_baseline.json").read_text())
    for path,sha in frozen["protected_stage2_sha256"].items():
        assert hashlib.sha256((e.ROOT/path).read_bytes()).hexdigest()==sha,path
