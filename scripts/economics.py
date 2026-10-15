#!/usr/bin/env python3
"""Воспроизводимый сценарный расчёт ассортимента; исходный Excel только читается.

python scripts/economics.py [--stage candidates]
Новые результаты записываются только в data/economics/results и figures/economics.
"""

import argparse
import hashlib
import itertools
import json
import math
import os
import sqlite3
import tempfile
from pathlib import Path

os.environ.setdefault("MPLCONFIGDIR", str(Path(tempfile.gettempdir()) / "nastoyka-mpl"))
os.environ.setdefault("XDG_CACHE_HOME", str(Path(tempfile.gettempdir()) / "nastoyka-xdg"))

import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
from openpyxl import load_workbook

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "data/economics"
OUT = DATA / "results"
FIG = ROOT / "figures/economics"


def read_config():
    return json.loads((DATA / "config.json").read_text(encoding="utf-8"))


def check_baseline():
    baseline = json.loads((DATA / "source_baseline.json").read_text())
    changed = [p for p, h in baseline["protected_files_sha256"].items()
               if not (ROOT / p).exists() or hashlib.sha256((ROOT / p).read_bytes()).hexdigest() != h]
    if changed:
        raise ValueError(f"Изменилась исходная база; перепроверьте источник и baseline: {changed}")
    return baseline


def workbook_frames():
    sheets = {"reviews": "Отзывы", "mentions": "Упоминания вкусов", "venues": "Заведения",
              "menu": "Меню - предыдущий сбор", "flavors": "Справочник вкусов",
              "flavor_summary": "Сводка вкусов"}
    wb = load_workbook(ROOT / "data/nastoyki_reviews.xlsx", read_only=True, data_only=True)
    result = {}
    for key, sheet in sheets.items():
        iterator = wb[sheet].values
        headers = list(next(iterator))
        result[key] = pd.DataFrame([r for r in iterator if any(v is not None for v in r)], columns=headers)
    wb.close()
    return result


def prepare_candidates():
    """SQLite JOIN и агрегации поверх Excel, не независимая правка старых CSV."""
    check_baseline()
    frames = workbook_frames()
    roles = pd.read_csv(DATA / "candidate_roles.csv").fillna("")
    if not roles.normalized_flavor.is_unique or not roles.candidate_id.is_unique:
        raise ValueError("Повтор кандидата")
    if not set(roles.normalized_flavor).issubset(set(frames["flavors"].normalized_flavor)):
        raise ValueError("Кандидат отсутствует в прежнем справочнике вкусов")
    conn = sqlite3.connect(":memory:")
    renames = {"ID отзыва": "review_id", "ID": "mention_id", "Тип свидетельства": "evidence_type"}
    for name in ["reviews", "mentions", "venues", "menu"]:
        frames[name].rename(columns=renames).fillna("").to_sql(name, conn, index=False)
    roles.to_sql("candidate_roles", conn, index=False)
    conn.executescript((ROOT / "scripts/candidates.sql").read_text(encoding="utf-8"))
    stats = pd.read_sql_query("SELECT * FROM candidate_stats ORDER BY candidate_id", conn)
    stats = stats.merge(frames["flavors"][["normalized_flavor", "category", "creamy_base"]],
                        on="normalized_flavor", validate="one_to_one")
    mention_evidence = pd.read_sql_query(
        "SELECT c.candidate_id,m.mention_id,m.review_id,m.venue_id,m.normalized_flavor,"
        "m.sentiment,m.evidence_type,m.strict_flag,m.event_key FROM verified_concrete m "
        "JOIN candidate_roles c USING(normalized_flavor) ORDER BY c.candidate_id,m.mention_id", conn)
    counts = pd.read_sql_query("SELECT COUNT(*) AS n,COUNT(DISTINCT normalized_flavor) AS f,"
                              "SUM(strict_flag) AS s FROM verified_concrete", conn).iloc[0]
    assert (int(counts.n), int(counts.f), int(counts.s)) == (198, 114, 143)
    # Сопоставление с прежней сводкой: одинаковые события и подтверждённые строки.
    old = frames["flavor_summary"].set_index("Вкус")
    for row in stats.itertuples():
        for new, previous in [("verified_mentions", "Проверенные упоминания"),
                              ("positive", "Положительные"), ("negative", "Отрицательные"),
                              ("mixed", "Смешанные"), ("strict_mentions", "Строгий текущий срез: база")]:
            assert getattr(row, new) == old.loc[row.normalized_flavor, previous], (row.normalized_flavor, new)
    conn.close()
    directions = ["ягодная", "фруктовая", "цитрусовая", "травяная", "пряная", "десертная", "сливочная", "овощная"]
    matrix = stats[["candidate_id", "normalized_flavor", "primary_role", "classic", "unusual", "creamy"]].copy()
    for role in directions:
        matrix[role] = stats.primary_role.eq(role).astype(int)
    matrix.loc[stats.candidate_id.isin(["C09", "C11"]), "травяная"] = 1
    matrix.loc[stats.candidate_id.eq("C10"), "овощная"] = 1
    matrix.loc[stats.candidate_id.eq("C07"), "ягодная"] = 1
    matrix.loc[stats.candidate_id.eq("C12"), "ягодная"] = 1
    matrix.loc[stats.candidate_id.eq("C12"), "десертная"] = 1
    matrix.loc[stats.creamy.eq(1), "сливочная"] = 1
    OUT.mkdir(parents=True, exist_ok=True)
    stats.to_csv(OUT / "candidates.csv", index=False)
    matrix.to_csv(OUT / "flavor_matrix.csv", index=False)
    mention_evidence.to_csv(OUT / "candidate_evidence.csv", index=False)
    return stats, matrix


def quantity_in_base(quantity, unit, config):
    if unit not in config["unit_to_base"] or not np.isfinite(quantity) or quantity <= 0:
        raise ValueError(f"Некорректные количество или единица: {quantity} {unit}")
    conversion = config["unit_to_base"][unit]
    return float(quantity) * conversion["factor"], conversion["base"]


def load_ingredients(config):
    ingredients = pd.read_csv(DATA / "ingredients.csv").fillna("")
    if not ingredients.ingredient_id.is_unique:
        raise ValueError("Повтор ингредиента")
    for col in ["package_price_rub", "package_quantity"]:
        ingredients[col] = pd.to_numeric(ingredients[col], errors="raise")
        if not np.isfinite(ingredients[col]).all() or ingredients[col].le(0).any():
            raise ValueError(f"Нет положительной проверяемой цены/упаковки: {col}")
    if not ingredients.url.str.startswith("https://").all() or ingredients.checked_at.eq("").any():
        raise ValueError("Цена без источника или даты")
    pd.to_datetime(ingredients.checked_at, format="%Y-%m-%d", errors="raise")
    sizes = [quantity_in_base(q, u, config) for q, u in zip(ingredients.package_quantity, ingredients.unit)]
    ingredients["package_base_quantity"] = [x[0] for x in sizes]
    ingredients["base_unit"] = [x[1] for x in sizes]
    ingredients["rub_per_base_unit"] = ingredients.package_price_rub / ingredients.package_base_quantity
    return ingredients.set_index("ingredient_id", drop=False)


def recipe_cost(amounts, ingredients, nominal_ml, loss_fraction, serving_ml=50):
    """Сценарный выход отдельно от массы сырья; потери не списываются второй раз."""
    if not np.isfinite(nominal_ml) or nominal_ml <= 0 or not 0 <= loss_fraction < 1 or serving_ml <= 0:
        raise ValueError("Неверный выход или потери")
    if not amounts or any(not np.isfinite(q) or q <= 0 for q in amounts.values()):
        raise ValueError("Пустой состав или неверное количество")
    if not set(amounts).issubset(set(ingredients.index)):
        raise ValueError("Нет цены ингредиента")
    prices = ingredients.loc[list(amounts), "rub_per_base_unit"]
    if prices.isna().any() or prices.le(0).any():
        raise ValueError("Нет положительной цены")
    batch_cost = sum(q * float(prices.loc[k]) for k, q in amounts.items())
    output = nominal_ml * (1-loss_fraction)
    return {"batch_ingredient_cost_rub": batch_cost, "ready_ml": output,
            "cost_liter_rub": batch_cost/output*1000, "cost_50ml_rub": batch_cost/output*serving_ml}


def material_margin(price, cost):
    if not np.isfinite(price) or price <= 0 or not np.isfinite(cost) or cost < 0:
        raise ValueError("Неверные цена или затраты")
    return price-cost, (price-cost)/price


def weighted_price(basic_price, premium_price, premium_share):
    if not 0 <= premium_share <= 1 or min(basic_price, premium_price) <= 0:
        raise ValueError("Неверные доля или цена")
    return (1-premium_share)*basic_price + premium_share*premium_price


def required_premium_share(basic_price, premium_price, target):
    if premium_price <= basic_price:
        raise ValueError("Премиальная цена должна быть выше основной")
    share = (target-basic_price)/(premium_price-basic_price)
    return share if 0 <= share <= 1 else None


def procurement(demand, ingredients, price_multiplier=1.0):
    """Закупка целыми единицами; остаток — запас, а не уже понесённый убыток."""
    if price_multiplier <= 0 or not np.isfinite(price_multiplier):
        raise ValueError("Неверный множитель цен")
    demand = pd.Series(demand, dtype=float)
    if demand.isna().any() or (~np.isfinite(demand)).any() or demand.lt(0).any():
        raise ValueError("Неверная потребность")
    if not set(demand.index).issubset(ingredients.index):
        raise ValueError("Нет цены ингредиента")
    result = ingredients.loc[demand[demand.gt(0)].index].copy()
    result["needed_base_quantity"] = demand.loc[result.index]
    result["packages"] = np.ceil(result.needed_base_quantity/result.package_base_quantity-1e-12).astype(int)
    result["purchased_base_quantity"] = result.packages*result.package_base_quantity
    result["leftover_base_quantity"] = result.purchased_base_quantity-result.needed_base_quantity
    result["purchase_cash_rub"] = result.packages*result.package_price_rub*price_multiplier
    result["consumed_cost_rub"] = result.needed_base_quantity*result.rub_per_base_unit*price_multiplier
    result["leftover_value_rub"] = result.leftover_base_quantity*result.rub_per_base_unit*price_multiplier
    result["additional_cash_second_cycle_rub"] = (
        np.ceil(np.maximum(0, 2*result.needed_base_quantity-result.purchased_base_quantity)
                /result.package_base_quantity-1e-12)*result.package_price_rub*price_multiplier)
    result["cycles_of_demand_in_remaining_stock"] = result.leftover_base_quantity/result.needed_base_quantity
    return result


def recipe_tables(candidates, ingredients, config):
    source = pd.DataFrame(json.loads((DATA / "recipe_sources.json").read_text())["recipes"]).set_index("candidate_id")
    rows = []
    quantities = pd.DataFrame(0.0, index=candidates.candidate_id, columns=ingredients.index)
    for row in candidates.itertuples():
        recipe = config["recipe_scenarios"][row.candidate_id]
        cost = recipe_cost(recipe["ingredients"], ingredients, recipe["nominal_product_ml"],
                           recipe["loss_fraction"], config["serving_ml"])
        price = config["premium_price_rub"] if row.candidate_id in config["premium_pool"] else config["basic_price_rub"]
        margin, fraction = material_margin(price, cost["cost_50ml_rub"])
        rows.append({"candidate_id": row.candidate_id, "normalized_flavor": row.normalized_flavor,
                     "price_rub": price, "tier": "premium" if row.candidate_id in config["premium_pool"] else "basic",
                     **cost, "material_margin_50ml_rub": margin, "material_margin_fraction": fraction,
                     "meets_margin_threshold": fraction >= config["min_material_margin_fraction"],
                     "nominal_product_ml": recipe["nominal_product_ml"], "loss_fraction": recipe["loss_fraction"],
                     "time_days": recipe["time_days"], "time_status": recipe["time_status"],
                     "composition_status": source.loc[row.candidate_id,"composition_status"],
                     "source_url": source.loc[row.candidate_id,"source_url"], "yield_status": "assumed_unmeasured"})
        for ingredient, qty in recipe["ingredients"].items():
            quantities.loc[row.candidate_id, ingredient] = qty/cost["ready_ml"]*config["starting_output_ml_per_recipe"]
    return pd.DataFrame(rows).set_index("candidate_id", drop=False), quantities


def basket_metrics(selected, candidates, recipes, quantities, ingredients, config):
    quantities_menu = quantities.loc[list(selected)]
    demand = quantities_menu.sum(axis=0)
    basket = procurement(demand, ingredients)
    universal = ingredients.universal.eq(1)
    nonuniversal = ingredients.index[~universal]
    present_count = quantities_menu.gt(0).sum(axis=0)
    share = quantities_menu.div(demand.replace(0, np.nan), axis=1).fillna(0)
    threshold = config["expensive_package_threshold_rub_override"]
    if threshold is None:
        threshold = float(ingredients.loc[~universal, "package_price_rub"].quantile(config["expensive_package_quantile"]))
    expensive = ingredients.package_price_rub.ge(threshold) & ~universal
    affected = (share.ge(config["specific_ingredient_dominance"]) & expensive).any(axis=1)
    part_cost = demand*ingredients.rub_per_base_unit
    eligible_cost = part_cost.loc[nonuniversal].sum()
    shared_cost = part_cost[(present_count.ge(2)) & ~universal].sum()
    c = candidates.set_index("candidate_id").loc[list(selected)]
    r = recipes.loc[list(selected)]
    used_high_risk = basket.perishability.eq("high")
    return {"selected": tuple(sorted(selected)), "purchase_cash_rub": float(basket.purchase_cash_rub.sum()),
            "consumed_cost_rub": float(basket.consumed_cost_rub.sum()),
            "leftover_value_rub": float(basket.leftover_value_rub.sum()),
            "high_risk_leftover_value_rub": float(basket.loc[used_high_risk,"leftover_value_rub"].sum()),
            "suppliers": int(basket.supplier.nunique()), "ingredient_count": len(basket),
            "shared_nonuniversal_fraction": float(shared_cost/eligible_cost) if eligible_cost else 0,
            "unique_nonuniversal_ingredients": int(((present_count.eq(1)) & ~universal).sum()),
            "expensive_specific_recipes": int(affected.sum()), "expensive_threshold_rub": threshold,
            "primary_roles": int(c.primary_role.nunique()), "classic_count": int(c.classic.sum()),
            "unusual_count": int(c.unusual.sum()), "creamy_count": int(c.creamy.sum()),
            "min_material_margin_fraction": float(r.material_margin_fraction.min()),
            "review_evidence_score": float((np.log1p(c.strict_event_positive)-0.5*np.log1p(c.strict_event_negative+c.strict_event_mixed)).sum()),
            "strict_mentions_sum": int(c.strict_mentions.sum()),
            "expensive_recipe_ids": "|".join(affected[affected].index)}


def select_menus(candidates, recipes, quantities, ingredients, config):
    base = [x for x in candidates.candidate_id if x not in config["premium_pool"]]
    premium = config["premium_pool"]
    index = candidates.set_index("candidate_id")
    feasible = []
    for basic in itertools.combinations(base, config["basic_count"]):
        for prem in itertools.combinations(premium, config["premium_count"]):
            chosen = tuple(sorted(basic+prem))
            c = index.loc[list(chosen)]
            if not set(config["required_primary_roles"]).issubset(c.primary_role): continue
            if c.primary_role.value_counts().max() > config["max_per_primary_role"]: continue
            if c.creamy.sum() > config["max_creamy"] or c.classic.sum() < config["min_classics"] or c.unusual.sum() < config["min_unusual"]: continue
            if recipes.loc[list(chosen),"material_margin_fraction"].min() < config["min_material_margin_fraction"]: continue
            metrics = basket_metrics(chosen, candidates, recipes, quantities, ingredients, config)
            if metrics["expensive_specific_recipes"] <= config["max_expensive_specific_recipes"]:
                feasible.append(metrics)
    if not feasible: raise ValueError("Нет допустимого меню при заданных ограничениях")
    a = min(feasible, key=lambda x:(x["purchase_cash_rub"], x["consumed_cost_rub"], x["selected"]))
    for row in feasible:
        row["balanced_score"] = 0.0
    w = config["balanced_weights"]
    for metric, weight, reverse in [("review_evidence_score",w["review_evidence"],False),
                                    ("primary_roles",w["diversity"],False),
                                    ("shared_nonuniversal_fraction",w["sharing"],False),
                                    ("purchase_cash_rub",w["cash"],True)]:
        lo,hi = min(x[metric] for x in feasible),max(x[metric] for x in feasible)
        for row in feasible:
            value = (row[metric]-lo)/(hi-lo) if hi>lo else 0
            row["balanced_score"] += weight*(1-value if reverse else value)
    # Три разные альтернативы; при совпадении идеалов выбирается следующая допустимая.
    balanced = [x for x in feasible if x["selected"] != a["selected"] and
                x["purchase_cash_rub"] <= a["purchase_cash_rub"]*config["budget_multiplier_balanced"]]
    b = max(balanced,key=lambda x:(x["balanced_score"],-x["purchase_cash_rub"])) if balanced else None
    author = [x for x in feasible if x["selected"] not in [a["selected"], b["selected"] if b else ()] and
              x["purchase_cash_rub"] <= a["purchase_cash_rub"]*config["budget_multiplier_author"] and
              set(config["author_anchor_candidates"]).issubset(x["selected"])]
    c = max(author,key=lambda x:(x["unusual_count"],x["review_evidence_score"],-x["purchase_cash_rub"])) if author else None
    if b is None or c is None: raise ValueError("Недостаточно разных допустимых альтернатив внутри бюджетов")
    return {"A":a,"B":b,"C":c}, feasible


def scenario_tables(menus, recipes, ingredients, quantities, candidates, config):
    records=[]
    sensitivity=[]
    for menu_id, menu in menus.items():
        ids=list(menu["selected"])
        r=recipes.loc[ids]
        cost_a=float(r.loc[r.tier.eq("basic"),"cost_50ml_rub"].mean())
        cost_b=float(r.loc[r.tier.eq("premium"),"cost_50ml_rub"].mean())
        for pair_id,(pa,pb) in enumerate([(config["basic_price_rub"],config["premium_price_rub"])]+config["alternative_price_pairs"]):
            for scenario,share in config["premium_sales_share_scenarios"].items():
                price=weighted_price(pa,pb,share)
                cost=(1-share)*cost_a+share*cost_b
                margin,margin_fraction=material_margin(price,cost)
                records.append({"menu":menu_id,"price_pair":pair_id,"scenario":scenario,"premium_share":share,
                                "basic_price_rub":pa,"premium_price_rub":pb,
                                "simple_menu_price_rub":(config["basic_count"]*pa+config["premium_count"]*pb)/config["menu_size"],
                                "weighted_price_rub":price,"weighted_material_cost_50ml_rub":cost,
                                "material_margin_50ml_rub":margin,"material_margin_fraction":margin_fraction,
                                "required_premium_share_for_250":required_premium_share(pa,pb,config["target_weighted_price_rub"]),
                                "target_met":price>=config["target_weighted_price_rub"],"sales_status":"scenario_not_observed"})
        stress_cases=[("base",0,0)]+[(f"price_plus_{shock*100:g}",shock,0) for shock in config["price_shocks"]]
        stress_cases.append((f"loss_plus_{config['extra_loss_fraction_points']*100:g}pp",0,config["extra_loss_fraction_points"]))
        for scenario,shock,loss_extra in stress_cases:
            for cid in ids:
                model=config["recipe_scenarios"][cid]
                loss=model["loss_fraction"]+loss_extra
                cc=recipe_cost(model["ingredients"],ingredients,model["nominal_product_ml"],loss,config["serving_ml"])
                cost=cc["cost_50ml_rub"]*(1+shock)
                price=float(recipes.loc[cid,"price_rub"])
                margin,frac=material_margin(price,cost)
                sensitivity.append({"menu":menu_id,"scenario":scenario,"candidate_id":cid,
                                    "cost_50ml_rub":cost,"cost_change_rub":cost-recipes.loc[cid,"cost_50ml_rub"],
                                    "material_margin_50ml_rub":margin,"material_margin_fraction":frac,
                                    "below_margin_threshold":frac<config["min_material_margin_fraction"]})
    return pd.DataFrame(records),pd.DataFrame(sensitivity)


def substitutions(menus, feasible, candidates, recipes, quantities, ingredients, config):
    """Обмен одной дорогой позиции с сохранением роли, уровня цены и всех ограничений."""
    rows=[]
    for name, menu in menus.items():
        selected=set(menu["selected"])
        for alt in feasible:
            altset=set(alt["selected"])
            removed=selected-altset; added=altset-selected
            if len(removed)!=1 or len(added)!=1:continue
            old,new=next(iter(removed)),next(iter(added))
            old_role=candidates.set_index("candidate_id").loc[old,"primary_role"]
            new_role=candidates.set_index("candidate_id").loc[new,"primary_role"]
            dessert_pair={old_role,new_role}.issubset({"десертная","сливочная"})
            if (old_role!=new_role and not dessert_pair) or recipes.loc[old,"tier"]!=recipes.loc[new,"tier"]:continue
            rows.append({"menu":name,"removed":old,"added":new,"role":old_role,
                         "cash_delta_rub":alt["purchase_cash_rub"]-menu["purchase_cash_rub"],
                         "consumed_delta_rub":alt["consumed_cost_rub"]-menu["consumed_cost_rub"],
                         "review_score_delta":alt["review_evidence_score"]-menu["review_evidence_score"]})
    return pd.DataFrame(rows)


def stress_menu_table(sensitivity, recipes, config):
    rows=[]
    joined=sensitivity.merge(recipes[["candidate_id","tier"]].reset_index(drop=True),on="candidate_id",validate="many_to_one")
    for (menu,stress),group in joined.groupby(["menu","scenario"]):
        costs=group.groupby("tier").cost_50ml_rub.mean()
        for name,share in config["premium_sales_share_scenarios"].items():
            price=weighted_price(config["basic_price_rub"],config["premium_price_rub"],share)
            cost=(1-share)*costs["basic"]+share*costs["premium"]
            margin,frac=material_margin(price,cost)
            rows.append({"menu":menu,"stress":stress,"mix_scenario":name,"premium_share":share,
                         "weighted_price_rub":price,"weighted_cost_50ml_rub":cost,
                         "material_margin_50ml_rub":margin,"material_margin_fraction":frac,
                         "positions_below_margin_threshold":int(group.below_margin_threshold.sum())})
    return pd.DataFrame(rows)


def ingredient_price_sensitivity(recipes, ingredients, config):
    rows=[]
    shock=max(config["price_shocks"])
    for cid,recipe in config["recipe_scenarios"].items():
        for ingredient,quantity in recipe["ingredients"].items():
            contribution=quantity*ingredients.loc[ingredient,"rub_per_base_unit"]
            increase=contribution*shock/recipes.loc[cid,"ready_ml"]*config["serving_ml"]
            rows.append({"candidate_id":cid,"ingredient_id":ingredient,"ingredient_price_increase":shock,
                         "ingredient_cost_share":contribution/recipes.loc[cid,"batch_ingredient_cost_rub"],
                         "cost_50ml_increase_rub":increase,
                         "new_cost_50ml_rub":recipes.loc[cid,"cost_50ml_rub"]+increase})
    return pd.DataFrame(rows)


def run(stage="all"):
    candidates, flavor_matrix = prepare_candidates()
    if stage == "candidates":
        print(candidates[["candidate_id","normalized_flavor","verified_mentions","positive","negative","mixed","strict_mentions","strict_positive"]].to_string(index=False))
        return
    config=read_config()
    ingredients=load_ingredients(config)
    recipes,quantities=recipe_tables(candidates,ingredients,config)
    menus,feasible=select_menus(candidates,recipes,quantities,ingredients,config)
    scenarios,sensitivity=scenario_tables(menus,recipes,ingredients,quantities,candidates,config)
    stress_menus=stress_menu_table(sensitivity,recipes,config)
    ingredient_stress=ingredient_price_sensitivity(recipes,ingredients,config)
    swaps=substitutions(menus,feasible,candidates,recipes,quantities,ingredients,config)
    procurements=[];items=[]
    for key,menu in menus.items():
        ids=list(menu["selected"])
        basket=procurement(quantities.loc[ids].sum(),ingredients)
        basket["menu"]=key
        basket["recipes_using_ingredient"]=quantities.loc[ids,basket.index].gt(0).sum().to_numpy()
        basket["used_by_candidates"]=["|".join(quantities.loc[ids,i].loc[lambda x:x.gt(0)].index) for i in basket.index]
        procurements.append(basket.reset_index(drop=True))
        group=recipes.loc[ids].copy();group["menu"]=key;items.append(group)
        menu["optional_delivery_scenario_rub"]=menu["suppliers"]*config["optional_delivery_rub_per_supplier"]
        menu["optional_consumables_scenario_rub"]=config["menu_size"]*config["optional_consumables_rub_per_batch"]
        menu["cash_with_optional_scenarios_rub"]=menu["purchase_cash_rub"]+menu["optional_delivery_scenario_rub"]+menu["optional_consumables_scenario_rub"]
        demand=quantities.loc[ids].sum()
        multiple=procurement(demand*config["procurement_reuse_cycles"],ingredients)
        menu["cash_three_cycles_average_rub"]=float(multiple.purchase_cash_rub.sum()/config["procurement_reuse_cycles"])
        menu["additional_cash_second_cycle_rub"]=float(basket.additional_cash_second_cycle_rub.sum())
    procurement_table=pd.concat(procurements,ignore_index=True)
    menu_items=pd.concat(items,ignore_index=True)
    step=config["weighed_order_step_scenario"]
    adjusted=ingredients.copy()
    qty,unit=quantity_in_base(step["quantity"],step["unit"],config)
    ingredient=step["ingredient_id"]
    if unit != adjusted.loc[ingredient,"base_unit"]:raise ValueError("Нельзя менять массовую единицу на объёмную")
    adjusted.loc[ingredient,"package_base_quantity"]=qty
    adjusted.loc[ingredient,"package_price_rub"]=qty*adjusted.loc[ingredient,"rub_per_base_unit"]
    packaging=[]
    for key,menu in menus.items():
        p=procurement(quantities.loc[list(menu["selected"])].sum(),adjusted)
        packaging.append({"menu":key,"ingredient_id":ingredient,"assumed_order_step":qty,"base_unit":unit,
                          "purchase_cash_rub":float(p.purchase_cash_rub.sum()),
                          "delta_cash_rub":float(p.purchase_cash_rub.sum()-menu["purchase_cash_rub"]),
                          "status":step["status"]})
    menu_summary=pd.DataFrame([{**v,"menu":k,"selected":"|".join(v["selected"])} for k,v in menus.items()])
    ingredients.reset_index(drop=True).to_csv(OUT/"price_catalog.csv",index=False)
    recipes.reset_index(drop=True).to_csv(OUT/"recipe_costs.csv",index=False)
    quantities.to_csv(OUT/"ingredient_matrix.csv",index_label="candidate_id")
    menu_items.to_csv(OUT/"menu_items.csv",index=False)
    menu_summary.to_csv(OUT/"menu_summary.csv",index=False)
    procurement_table.to_csv(OUT/"procurement.csv",index=False)
    scenarios.to_csv(OUT/"price_scenarios.csv",index=False)
    sensitivity.to_csv(OUT/"sensitivity.csv",index=False)
    stress_menus.to_csv(OUT/"stress_menu_scenarios.csv",index=False)
    ingredient_stress.to_csv(OUT/"ingredient_price_sensitivity.csv",index=False)
    swaps.to_csv(OUT/"substitutions.csv",index=False)
    pd.DataFrame(packaging).to_csv(OUT/"packaging_scenario.csv",index=False)
    result={"status":"preliminary_scenario_not_approved", "source_base_commit":check_baseline()["base_commit"],
            "source_counts":{"reviews":368,"verified_reviews":358,"mentions":468,"verified_concrete_mentions":198,
                             "verified_flavors":114,"strict_mentions":143,"menu_rows":95,"current_venues":30},
            "candidates":len(candidates),"ingredients_with_public_prices":len(ingredients),
            "published_quantity_recipes":int(recipes.composition_status.eq("published_quantities").sum()),
            "recipes_with_measured_yield":0,"feasible_menu_count":len(feasible),"menus":menus,
            "required_premium_share":required_premium_share(config["basic_price_rub"],config["premium_price_rub"],config["target_weighted_price_rub"])}
    make_figures(candidates,flavor_matrix,recipes,quantities,ingredients,menu_summary,scenarios,config)
    check_baseline()
    (OUT/"summary.json").write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding="utf-8")
    print(json.dumps({"feasible_menus":len(feasible),"menus":menus,"required_premium_share":result["required_premium_share"]},ensure_ascii=False,indent=2))
    return result


def make_figures(candidates,flavor_matrix,recipes,quantities,ingredients,menus,scenarios,config):
    FIG.mkdir(parents=True,exist_ok=True)
    plt.rcParams.update({"font.family":"DejaVu Sans","font.size":10,"axes.spines.top":False,
                         "axes.spines.right":False,"axes.titleweight":"bold","figure.facecolor":"white"})
    palette=["#3B827F","#C65F57","#D7A452","#AAB6C3"]
    def save(fig,name,note):
        fig.text(.02,.02,note,fontsize=9,color="#596674",va="bottom")
        fig.tight_layout(rect=[0,.08,1,1]);fig.savefig(FIG/name,dpi=160);plt.close(fig)
    labels=candidates.normalized_flavor.tolist()
    fig,ax=plt.subplots(figsize=(11,7))
    left=np.zeros(len(candidates))
    for col,color,label in zip(["positive","negative","mixed","mention_only"],palette,
                               ["Положительные","Отрицательные","Смешанные","Нейтральные / перечисления"]):
        values=(candidates[col]+candidates.neutral if col=="mention_only" else candidates[col]).to_numpy()
        ax.barh(labels,values,left=left,label=label,color=color);left+=values
    for i,n in enumerate(candidates.unique_events):ax.text(left[i]+.08,i,f"n={n}",va="center",fontsize=9)
    ax.set(xlim=(0,8.2),xlabel="Уникальные события с подтверждённым упоминанием вкуса",title="Небольшие базы отзывов: доли не доказывают популярность")
    ax.set_ylim(len(labels)+1.5,-.8);ax.legend(ncol=2,frameon=False,loc="lower right",fontsize=9)
    save(fig,"reviews.png","Исторические и текущие отзывы вместе; простое перечисление не считается положительной оценкой.")
    dirs=["ягодная","фруктовая","цитрусовая","травяная","пряная","десертная","сливочная","овощная"]
    fig,ax=plt.subplots(figsize=(10,7));ax.imshow(flavor_matrix[dirs].to_numpy(),cmap="Blues",vmin=0,vmax=1,aspect="auto")
    ax.set(yticks=range(len(labels)),yticklabels=labels,xticks=range(len(dirs)),xticklabels=dirs,title="Матрица ролей: классика, десерты и несладкие направления")
    ax.tick_params(axis="x",rotation=40)
    save(fig,"flavor_matrix.png","Экспертная классификация расчётных вариантов; она не меняет прежний справочник вкусов.")
    shared=quantities.gt(0).sum()
    cols=ingredients.index[ingredients.universal.eq(0)]
    fig,ax=plt.subplots(figsize=(13,7))
    present=quantities[cols].gt(0).astype(int).to_numpy();ax.imshow(present,cmap="GnBu",vmin=0,vmax=1,aspect="auto")
    short_names={"honey":"Мёд","horseradish":"Хрен","lemon":"Лимоны","cherry":"Вишня",
                 "raspberry":"Малина","sorrel":"Щавель","halva":"Халва","blueberry":"Черника",
                 "cream_cheese":"Творожный сыр","passionfruit":"Маракуйя","apple":"Яблоко",
                 "basil":"Базилик","beet":"Свёкла","strawberry":"Клубника","cream":"Сливки",
                 "sea_buckthorn":"Облепиха","cranberry":"Клюква","tomato":"Томаты"}
    ax.set(yticks=range(len(labels)),yticklabels=labels,xticks=range(len(cols)),
           xticklabels=[short_names.get(i,ingredients.loc[i,"name"]) for i in cols],title="Совместимость закупок: составы на 1 л сценарного выхода")
    ax.tick_params(axis="x",rotation=65,labelsize=9)
    save(fig,"ingredient_use.png","Цвет показывает присутствие; количества в г/мл — в ingredient_matrix.csv. Водка, сахар и вода исключены.")
    fig,ax=plt.subplots(figsize=(10,7));ax.barh(recipes.normalized_flavor,recipes.cost_50ml_rub,color="#628DA5")
    for i,v in enumerate(recipes.cost_50ml_rub):ax.text(v+1,i,f"{v:.1f} ₽",va="center",fontsize=9)
    ax.set(xlabel="Стоимость использованных ингредиентов на 50 мл, ₽",xlim=(0,recipes.cost_50ml_rub.max()*1.22),
           title="Материальная себестоимость зависит от неизмеренного выхода");ax.invert_yaxis()
    save(fig,"costs.png","Все выходы и потери сценарные. Розничные публичные цены; труд, налоги и прочие расходы не включены.")
    fig,axes=plt.subplots(1,2,figsize=(11,5.3));x=np.arange(3)
    axes[0].bar(x,menus.consumed_cost_rub,label="Использовано",color="#3B827F")
    axes[0].bar(x,menus.leftover_value_rub,bottom=menus.consumed_cost_rub,label="Остаток сырья",color="#D7B878")
    axes[0].set(xticks=x,xticklabels=["A · закупка","B · баланс","C · авторское"],ylabel="Первоначальная закупка, ₽",title="Закупка больше расхода сырья")
    axes[0].set_ylim(0,menus.purchase_cash_rub.max()*1.23)
    axes[0].legend(frameon=False)
    axes[1].bar(["A","B","C"],[8]*3,label="8 основных × 230 ₽",color="#628DA5")
    axes[1].bar(["A","B","C"],[2]*3,bottom=[8]*3,label="2 премиальные × 330 ₽",color="#D38D50")
    axes[1].set(ylim=(0,11.5),ylabel="Позиции меню",title="Структура каждого меню: 8 + 2");axes[1].legend(frameon=False,loc="upper center",fontsize=8)
    save(fig,"procurement_and_tiers.png","Остаток — потенциально пригодный запас, не автоматически потеря. Доставка и расходники показаны отдельно в таблицах.")
    fig,ax=plt.subplots(figsize=(10,5.5));shares=np.linspace(0,.5,101)
    for (pa,pb),color in zip([(config["basic_price_rub"],config["premium_price_rub"])]+config["alternative_price_pairs"],["#3B827F","#628DA5","#D38D50"]):
        ax.plot(shares*100,[weighted_price(pa,pb,s) for s in shares],label=f"{pa}/{pb} ₽",color=color,linewidth=2)
    ax.axhline(config["target_weighted_price_rub"],linestyle="--",color="#6F7F8C",label="Цель 250 ₽")
    for share in config["premium_sales_share_scenarios"].values():ax.axvline(share*100,color="#DCE2E7",linewidth=1,zorder=0)
    ax.set(xlabel="Доля премиальных порций в продажах, %",ylabel="Средняя цена проданной порции, ₽",
           title="Средняя цена 250 ₽ требует 20% премиальных продаж при 230/330 ₽");ax.legend(frameon=False,ncol=2)
    save(fig,"price_mix.png","Линии — арифметические сценарии. Реальные продажи и реакция спроса на изменение цены не измерены.")


if __name__=="__main__":
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--stage",choices=["candidates","all"],default="all")
    args=parser.parse_args();run(args.stage)
