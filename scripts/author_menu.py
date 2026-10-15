#!/usr/bin/env python3
"""Оценка фиксированного авторского меню. Оптимизаторы не вызываются.

python scripts/author_menu.py
История A/B/C/B2 и первичная база не перезаписываются.
"""

import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd

if __package__:
    from . import economics as e
else:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    from scripts import economics as e

MENU_PATH = e.DATA / "author_selected_menu.csv"
# Только контроль авторского поручения; расчёт читает строки и цены из CSV.
EXPECTED_BASIC = frozenset(["C01", "C02", "C03", "C04", "C05", "C09", "C13", "C14"])
EXPECTED_PREMIUM = frozenset(["C06", "C12"])
SERVING_ML = 50
READY_PER_RECIPE_ML = 1000


def load_author_menu(path=None):
    menu = pd.read_csv(path or MENU_PATH, keep_default_na=False)
    columns = ["candidate_id", "normalized_flavor", "tier", "price_50ml_rub", "selection_status"]
    if list(menu.columns) != columns or len(menu) != 10 or not menu.candidate_id.is_unique:
        raise ValueError("Авторское меню должно содержать ровно 10 уникальных ID и пять заданных полей")
    if set(menu.candidate_id) != EXPECTED_BASIC | EXPECTED_PREMIUM:
        raise ValueError("Изменён точный состав авторского меню; автоматическая замена запрещена")
    if set(menu.loc[menu.tier.eq("basic"), "candidate_id"]) != EXPECTED_BASIC or set(menu.loc[menu.tier.eq("premium"), "candidate_id"]) != EXPECTED_PREMIUM:
        raise ValueError("Нарушено авторское разделение 8 основных / 2 премиальных")
    menu["price_50ml_rub"] = pd.to_numeric(menu.price_50ml_rub, errors="raise")
    if not menu.price_50ml_rub.eq(menu.tier.map({"basic": 250, "premium": 300})).all():
        raise ValueError("Нарушены авторские цены 250/300 ₽")
    if not menu.selection_status.eq("author_selected_preliminary").all():
        raise ValueError("Неверный статус авторского выбора")
    names = pd.read_csv(e.DATA / "candidate_roles.csv").set_index("candidate_id").normalized_flavor
    if not menu.normalized_flavor.eq(menu.candidate_id.map(names)).all():
        raise ValueError("Название не соответствует существующему ID и нормализации")
    return menu


def evaluate_items(menu, ingredients, config):
    """Цена и уровень берутся из menu, только рецептурные параметры — из config."""
    audit = pd.read_csv(e.DATA / "recipe_audit.csv").fillna("").set_index("candidate_id")
    matrix = pd.DataFrame(0.0, index=menu.candidate_id, columns=ingredients.index)
    rows = []
    for item in menu.to_dict("records"):
        cid = item["candidate_id"]
        model = config["recipe_scenarios"][cid]
        cost = e.recipe_cost(model["ingredients"], ingredients, model["nominal_product_ml"], model["loss_fraction"], SERVING_ML)
        margin, fraction = e.material_margin(item["price_50ml_rub"], cost["cost_50ml_rub"])
        row = {**item, **cost, "material_margin_50ml_rub": margin, "material_margin_fraction": fraction,
               "nominal_ml_assumed": model["nominal_product_ml"], "loss_fraction_assumed": model["loss_fraction"],
               "time_days": model["time_days"], "time_status": model["time_status"],
               "composition_status": audit.loc[cid, "composition_status"], "source_url": audit.loc[cid, "source_url"],
               "additional_reference_url": audit.loc[cid, "additional_reference_url"],
               "yield_status": "assumed_unmeasured", "loss_status": "assumed_unmeasured",
               "base_ingredient": "vodka", "base_abv_assumption_pct": 40,
               "final_abv_pct": np.nan, "final_abv_status": "unknown_requires_measurement",
               "shelf_life_days": np.nan, "shelf_life_status": "not_established",
               "checks_required": audit.loc[cid, "checks_required"],
               "stage3_note": audit.loc[cid, "stage3_note"],
               "checked_at": audit.loc[cid, "checked_at"]}
        # 40%-ная основа не гарантирует 40% в готовом напитке. Числовой ABV не выводим.
        for ingredient, amount in model["ingredients"].items():
            matrix.loc[cid, ingredient] = amount / cost["ready_ml"] * READY_PER_RECIPE_ML
        rows.append(row)
    return pd.DataFrame(rows).set_index("candidate_id", drop=False), matrix


def basket_for_menu(matrix, ingredients, label):
    basket = e.procurement(matrix.sum(), ingredients)
    basket["menu"] = label
    present = matrix.gt(0).sum()
    basket["recipes_using_ingredient"] = present.loc[basket.index]
    basket["candidate_ids"] = ["|".join(matrix.index[matrix[i].gt(0)]) for i in basket.index]
    basket["repeat_use_risk"] = basket.perishability.map({"high": "fresh_or_dairy_conditions_unvalidated", "medium": "frozen_or_open_pack_conditions_unvalidated", "low": "label_and_open_pack_conditions_to_check"})
    return basket


def sales_scenarios(items, label):
    rows = []
    for share in [.05, .20, .35]:
        weights = items.tier.map({"basic": (1-share)/8, "premium": share/2})
        price = float((weights * items.price_50ml_rub).sum())
        cost = float((weights * items.cost_50ml_rub).sum())
        margin, fraction = e.material_margin(price, cost)
        rows.append({"menu": label, "premium_sales_share": share,
                     "simple_menu_price_rub": float(items.price_50ml_rub.mean()),
                     "weighted_price_50ml_rub": price, "weighted_cost_50ml_rub": cost,
                     "material_margin_50ml_rub": margin, "material_margin_fraction": fraction,
                     "sales_status": "scenario_not_observed", "within_tier_distribution": "equal_assumption"})
    return pd.DataFrame(rows)


def stress_items(items, ingredients, config):
    rows = []
    cases = [("base", 0, 0), ("price_plus_10", .1, 0), ("price_plus_20", .2, 0), ("loss_plus_10pp", 0, .1)]
    for cid, item in items.iterrows():
        model = config["recipe_scenarios"][cid]
        for name, shock, extra_loss in cases:
            cost = e.recipe_cost(model["ingredients"], ingredients, model["nominal_product_ml"], model["loss_fraction"]+extra_loss, SERVING_ML)
            unit_cost = cost["cost_50ml_rub"]*(1+shock)
            margin, fraction = e.material_margin(item.price_50ml_rub, unit_cost)
            rows.append({"candidate_id": cid, "scenario": name, "price_shock": shock,
                         "loss_fraction_assumed": model["loss_fraction"]+extra_loss, "ready_ml": cost["ready_ml"],
                         "cost_50ml_rub": unit_cost, "material_margin_50ml_rub": margin,
                         "material_margin_fraction": fraction})
    return pd.DataFrame(rows)


def sharing_table(matrix, ingredients):
    """Совместное округление против независимой покупки полных упаковок, без скидки тарифа."""
    rows = []
    common = matrix.columns[matrix.gt(0).sum().ge(2) & ingredients.universal.eq(0)]
    for scale in [1, 5]:
        for ingredient in common:
            q = matrix[ingredient] * scale
            pack = ingredients.loc[ingredient, "package_base_quantity"]
            separate = int(sum(np.ceil(v/pack-1e-12) for v in q[q.gt(0)]))
            joint = int(np.ceil(q.sum()/pack-1e-12))
            rows.append({"ingredient_id": ingredient, "ready_liters_per_recipe": scale,
                         "scenario_status": "main_basis" if scale == 1 else "rounding_illustration_not_production_plan",
                         "needed_base_quantity": float(q.sum()), "base_unit": ingredients.loc[ingredient, "base_unit"],
                         "candidate_ids": "|".join(q.index[q.gt(0)]), "joint_packages": joint,
                         "separate_packages": separate, "purchase_saving_rub": (separate-joint)*ingredients.loc[ingredient, "package_price_rub"],
                         "consumed_cost_saving_rub": 0.0, "unit_price_discount": False})
    return pd.DataFrame(rows)


def supplier_costs(basket):
    terms = pd.read_csv(e.DATA / "supplier_terms.csv")
    result = basket.groupby("supplier").purchase_cash_rub.sum().rename("goods_cash_rub").reset_index().merge(terms, on="supplier", validate="one_to_one")
    if len(result) != basket.supplier.nunique():
        raise ValueError("Поставщик отсутствует в проверке условий заказа")
    for index, row in result.iterrows():
        if pd.notna(row.free_delivery_threshold_rub) and row.goods_cash_rub >= row.free_delivery_threshold_rub:
            result.loc[index,"delivery_low_rub"] = 0
            if row.free_delivery_scope == "city_all":
                result.loc[index,"delivery_high_rub"] = 0
    result["passes_known_minimum"] = result.apply(lambda x: "unknown" if pd.isna(x.min_order_rub) else "yes" if x.goods_cash_rub >= x.min_order_rub else "no", axis=1)
    result["delivery_cost_rub"] = np.nan
    result["delivery_status"] = "published_conditional_bounds" 
    result.loc[result.delivery_low_rub.isna(),"delivery_status"] = "unknown_not_zero"
    return result


def menu_metrics(label, items, basket, scenarios, ingredients):
    nonuniversal = basket.universal.eq(0)
    costs = basket.loc[nonuniversal, "consumed_cost_rub"]
    shared = basket.recipes_using_ingredient.ge(2) & nonuniversal
    cash = float(basket.purchase_cash_rub.sum())
    base = scenarios[scenarios.premium_sales_share.eq(.2)].iloc[0]
    result = {"menu": label, "basis_ready_ml_per_recipe": READY_PER_RECIPE_ML,
              "basis_total_ready_ml": READY_PER_RECIPE_ML*len(items),
              "purchase_cash_rub": cash, "consumed_cost_rub": float(basket.consumed_cost_rub.sum()),
              "leftover_value_rub": float(basket.leftover_value_rub.sum()),
              "cash_in_stock_fraction": float(basket.leftover_value_rub.sum()/cash),
              "high_risk_leftover_value_rub": float(basket.loc[basket.perishability.eq("high"), "leftover_value_rub"].sum()),
              "ingredient_count": len(basket), "shared_nonuniversal_count": int(shared.sum()),
              "shared_nonuniversal_ids": "|".join(basket.index[shared]),
              "unique_nonuniversal_count": int((nonuniversal & basket.recipes_using_ingredient.eq(1)).sum()),
              "unique_nonuniversal_ids": "|".join(basket.index[nonuniversal & basket.recipes_using_ingredient.eq(1)]),
              "shared_nonuniversal_cost_fraction": float(basket.loc[shared,"consumed_cost_rub"].sum()/costs.sum()),
              "simple_mean_cost_50ml_rub": float(items.cost_50ml_rub.mean()),
              "base_weighted_cost_50ml_rub": float(base.weighted_cost_50ml_rub),
              "base_weighted_price_50ml_rub": float(base.weighted_price_50ml_rub),
              "base_material_margin_fraction": float(base.material_margin_fraction),
              "published_recipe_count": int(items.composition_status.eq("published_quantities").sum()),
              "adapted_recipe_count": int(items.composition_status.eq("model_adaptation").sum()),
              "model_recipe_count": int(items.composition_status.eq("model_only").sum()),
              "measured_yield_count": 0, "suppliers": int(basket.supplier.nunique()),
              "delivery_cost_rub": None, "delivery_status": "zone_and_order_terms_unknown"}
    # Найденные городские тарифы двух поставщиков. Адрес/зона неизвестны;
    # оставшиеся четыре поставщика не получают автоматически нулевую доставку.
    suppliers = supplier_costs(basket)
    low = float(suppliers.delivery_low_rub.sum(min_count=1))
    high = float(suppliers.delivery_high_rub.sum(min_count=1))
    result["two_supplier_delivery_low_rub"] = low
    result["two_supplier_delivery_high_rub"] = high
    result["cash_plus_two_supplier_delivery_low_rub"] = cash+low
    result["cash_plus_two_supplier_delivery_high_rub"] = cash+high
    result["unknown_delivery_supplier_count"] = int(suppliers.delivery_low_rub.isna().sum())
    result["other_suppliers_delivery_status"] = "unknown_not_zero"
    return result


def run():
    e.check_baseline()
    menu = load_author_menu()
    config = e.read_config()
    ingredients = e.load_ingredients(config)
    # Frozen previous B2 IDs and tiers/prices, never the optimizer's current result.
    history = json.loads((e.DATA / "stage2_baseline.json").read_text())
    ids = history["summary"]["menus"]["B2"]["selected"]
    roles = pd.read_csv(e.DATA / "candidate_roles.csv").set_index("candidate_id")
    old = history["config"]
    b2 = roles.loc[ids, ["normalized_flavor"]].reset_index()
    b2["tier"] = b2.candidate_id.map(lambda x: "premium" if x in old["premium_pool"] else "basic")
    b2["price_50ml_rub"] = b2.tier.map({"basic": old["basic_price_rub"], "premium": old["premium_price_rub"]})
    b2["selection_status"] = "historical_B2_same_current_inputs"
    comparisons = []; scenarios = []; baskets = []
    for label, selected in [("AUTHOR", menu), ("B2", b2)]:
        items, matrix = evaluate_items(selected, ingredients, config)
        basket = basket_for_menu(matrix, ingredients, label)
        sales = sales_scenarios(items, label)
        comparisons.append(menu_metrics(label, items, basket, sales, ingredients))
        baskets.append(basket); scenarios.append(sales)
        if label == "B2":
            # Только сопоставление: отделить эффект состава от смены 230/330 на 250/300.
            aligned = items.copy()
            aligned["price_50ml_rub"] = aligned.tier.map(menu.groupby("tier").price_50ml_rub.first())
            aligned_sales = sales_scenarios(aligned, "B2_at_author_prices")
            scenarios.append(aligned_sales)
            comparisons.append(menu_metrics("B2_at_author_prices", aligned, basket, aligned_sales, ingredients))
        if label == "AUTHOR":
            items.to_csv(e.OUT / "author_items.csv", index=False)
            matrix.to_csv(e.OUT / "author_ingredient_matrix.csv", index_label="candidate_id")
            stress = stress_items(items, ingredients, config)
            stress.to_csv(e.OUT / "author_item_stress.csv", index=False)
            menu_stress = []
            for case, group in stress.groupby("scenario", sort=False):
                stressed = items.copy()
                stressed["cost_50ml_rub"] = group.set_index("candidate_id").cost_50ml_rub
                frame = sales_scenarios(stressed, "AUTHOR")
                frame["stress_scenario"] = case
                menu_stress.append(frame)
            pd.concat(menu_stress).to_csv(e.OUT / "author_menu_stress.csv", index=False)
            sharing_table(matrix, ingredients).to_csv(e.OUT / "author_sharing.csv", index=False)
            author_basket = basket
    pd.concat(baskets).to_csv(e.OUT / "author_procurement.csv", index=False)
    pd.concat(scenarios).to_csv(e.OUT / "author_price_scenarios.csv", index=False)
    comparison = pd.DataFrame(comparisons)
    comparison.to_csv(e.OUT / "author_comparison.csv", index=False)
    make_figure(pd.read_csv(e.OUT / "author_items.csv"), comparison)
    # Qty/source audit: exactly the modeled source batch, not a measured production card.
    recipe_rows = []
    author_items = pd.read_csv(e.OUT / "author_items.csv").set_index("candidate_id")
    for cid in menu.candidate_id:
        for iid, qty in config["recipe_scenarios"][cid]["ingredients"].items():
            recipe_rows.append({"candidate_id": cid, "ingredient_id": iid, "ingredient": ingredients.loc[iid,"name"],
                               "source_batch_quantity": qty, "base_unit": ingredients.loc[iid,"base_unit"],
                               "batch_cost_contribution_rub": qty*ingredients.loc[iid,"rub_per_base_unit"],
                               "composition_status": author_items.loc[cid,"composition_status"],
                               "source_url": author_items.loc[cid,"source_url"],
                               "quantity_status": "published_amount_raw_material_caveats" if author_items.loc[cid,"composition_status"] == "published_quantities" else "model_or_adapted_parameter_not_measured"})
    pd.DataFrame(recipe_rows).to_csv(e.OUT / "author_recipe_ingredients.csv", index=False)
    supplier = supplier_costs(author_basket)
    supplier.to_csv(e.OUT / "author_suppliers.csv", index=False)
    result = {"selection_source": "data/economics/author_selected_menu.csv", "selection_status": "author_selected_preliminary",
              "optimizer_called": False, "selected_ids": menu.candidate_id.tolist(),
              "recipes_or_yields_technologically_approved": False, "comparison": comparisons,
              "protected_source_files": len(e.check_baseline()["protected_files_sha256"])}
    (e.OUT / "author_summary.json").write_text(json.dumps(result, ensure_ascii=False, indent=2, allow_nan=False)+"\n")
    return result


def make_figure(items, comparison):
    fig, axes = e.plt.subplots(1, 2, figsize=(13, 6.5), gridspec_kw={"width_ratios":[1.6,1]})
    colors = items.tier.map({"basic":"#628DA5", "premium":"#D38D50"})
    axes[0].barh(items.normalized_flavor, items.cost_50ml_rub, color=colors)
    for k, item in items.reset_index(drop=True).iterrows():
        axes[0].text(item.cost_50ml_rub+1, k, f"{item.cost_50ml_rub:.2f} ₽ · цена {item.price_50ml_rub:.0f} ₽", va="center", fontsize=9)
    axes[0].invert_yaxis()
    axes[0].set(xlim=(0,145), xlabel="Использованное сырьё на 50 мл, ₽", title="Авторские уровни цен сохранены")
    c = comparison[comparison.menu.isin(["AUTHOR","B2"])]
    axes[1].bar(c.menu, c.consumed_cost_rub, label="Использовано", color="#3B827F")
    axes[1].bar(c.menu, c.leftover_value_rub, bottom=c.consumed_cost_rub, label="Запас", color="#D7B878")
    axes[1].set(ylabel="Первая закупка сырья, ₽", title="Одинаковая база: 10 л")
    axes[1].legend(frameon=False)
    for x, v in enumerate(c.purchase_cash_rub):axes[1].text(x,v+180,f"{v:,.0f} ₽".replace(","," "),ha="center")
    axes[1].set_ylim(0,c.purchase_cash_rub.max()*1.12)
    fig.text(.02,.02,"Выходы и потери модельные; доставка и прочие расходы не включены. Запас не равен убытку.",fontsize=9)
    fig.tight_layout(rect=[0,.07,1,1])
    e.FIG.mkdir(parents=True,exist_ok=True)
    fig.savefig(e.FIG / "author_economics.png",dpi=160)
    e.plt.close(fig)


if __name__ == "__main__":
    # Compact output; deliverable tables contain full detail.
    value = run()
    print(json.dumps({"selected_ids": value["selected_ids"], "optimizer_called": False}, ensure_ascii=False))
