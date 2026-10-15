#!/usr/bin/env python3
"""Авторский сценарий 60 л. Decimal; без выбора вкусов и сетевых запросов."""

import csv
import hashlib
import json
import sys
from datetime import date, timedelta
from decimal import Decimal, ROUND_CEILING, ROUND_HALF_UP, getcontext
from pathlib import Path

getcontext().prec = 50
D = Decimal
ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "data/economics"
OUT = DATA / "results"
FIG = ROOT / "figures/economics"
BASIC = frozenset(["C01","C02","C03","C04","C05","C09","C13","C14"])
PREMIUM = frozenset(["C06","C12"])
NAMES = {"C01":"Хреновуха","C02":"Вишня","C03":"Малина","C04":"Лимончелло","C05":"Щавель",
         "C09":"Яблоко — базилик","C13":"Облепиха","C14":"Клюква","C06":"Халва","C12":"Малина — сливки"}
CENT = D("0.01")


def read_csv(path):
    with path.open(newline="", encoding="utf-8-sig") as stream:
        return list(csv.DictReader(stream))


def read_json(path):
    return json.loads(path.read_text(), parse_float=D)


def money(value):
    return D(value).quantize(CENT, rounding=ROUND_HALF_UP)


def positive(value):
    result = D(str(value))
    if not result.is_finite() or result <= 0:
        raise ValueError("Количество/цена должны быть конечными и положительными")
    return result


def check_baseline():
    baseline = read_json(DATA / "source_baseline.json")
    for name, expected in baseline["protected_files_sha256"].items():
        if hashlib.sha256((ROOT/name).read_bytes()).hexdigest() != expected:
            raise ValueError("Изменён защищённый первичный файл: " + name)
    return baseline


def load_inputs(data=DATA):
    menu = read_csv(data / "author_selected_menu.csv")
    if len(menu) != 10 or len({x["candidate_id"] for x in menu}) != 10:
        raise ValueError("Нужны десять уникальных авторских позиций")
    if {x["candidate_id"] for x in menu} != BASIC | PREMIUM:
        raise ValueError("Изменён авторский ассортимент; автоматические замены запрещены")
    dictionary = {x["normalized_flavor"] for x in read_csv(ROOT/"data/processed/flavors.csv")}
    for row in menu:
        cid = row["candidate_id"]
        tier = "basic" if cid in BASIC else "premium"
        if row["tier"] != tier or positive(row["price_50ml_rub"]) != (250 if tier=="basic" else 300):
            raise ValueError("Нарушено авторское разделение 8+2 или цены 250/300")
        if row["selection_status"] != "author_selected_preliminary" or row["normalized_flavor"] not in dictionary or row["normalized_flavor"]!=NAMES[cid]:
            raise ValueError("Неверный статус/нормализованный вкус")
    recipes = read_json(data / "recipes.json")["recipes"]
    yields = read_csv(data / "yields.csv")
    ids = {r["candidate_id"] for r in menu}
    if set(recipes) != ids or {x["candidate_id"] for x in yields} != ids or len(yields)!=10:
        raise ValueError("Рецептуры и выходы должны покрывать ровно десять ID")
    y = {}
    for row in yields:
        positive(row["yield_ml"])
        if row["yield_status"] not in {"estimated","measured"} or (row["yield_status"]=="measured" and not row["measurement_note"].strip()):
            raise ValueError("Измеренный выход требует описания измерения")
        y[row["candidate_id"]] = row
    ingredients = read_csv(data / "ingredients.csv")
    if len(ingredients)!=len({r["ingredient_id"] for r in ingredients}):
        raise ValueError("Повтор ингредиента")
    catalog = {}
    for row in ingredients:
        if row["base_unit"] not in {"g","ml"}:
            raise ValueError("Внутренние единицы только g/ml; плотность не предполагается")
        price = positive(row["pack_price_rub"]); size = positive(row["pack_size_base"])
        if abs(price/size-positive(row["price_per_base_unit_rub"])) > D("1e-20"):
            raise ValueError("Цена базовой единицы расходится с фасовкой")
        if not row["supplier"] or not row["price_source_type"] or not row["confirmation_status"]:
            raise ValueError("Цена без происхождения/поставщика")
        date.fromisoformat(row["scenario_date"])
        catalog[row["ingredient_id"]] = row
    for cid, recipe in recipes.items():
        if recipe["composition_status"] != "author_supplied" or recipe["base_abv_pct"] != 40:
            raise ValueError("Рецептуры должны отражать авторский состав на водке 40%")
        for iid, qty in recipe["ingredients"].items():
            positive(qty)
            if iid not in catalog:
                raise ValueError("Нет цены ингредиента: " + iid)
    plan = read_json(data / "production_plan.json")
    positive(plan["servings_per_flavor_per_evening"]); positive(plan["serving_ml"])
    if plan["serving_ml"]!=50 or plan["servings_per_flavor_per_evening"]!=60 or plan["evenings"]!=["Пятница","Суббота"]:
        raise ValueError("Основной план автора: 50 мл × 60 порций × два заданных вечера")
    if any(not 0<D(x)<=1 for x in plan["sell_through_scenarios"]) or any(not 0<D(x)<1 for x in plan["premium_sales_share_scenarios"]):
        raise ValueError("Доли сценариев за пределами допустимого диапазона")
    for value in plan["yield_multipliers"]:positive(value)
    for value in plan["price_shocks"]:positive(1+D(value))
    if plan["launch_date"] and date.fromisoformat(plan["launch_date"]).weekday()!=4:
        raise ValueError("Дата запуска должна быть пятницей")
    return menu, recipes, y, catalog, plan


def target_volume(plan):
    return positive(plan["serving_ml"])*positive(plan["servings_per_flavor_per_evening"])*len(plan["evenings"])


def pack_count(quantity, size):
    q = D(quantity); s = positive(size)
    if not q.is_finite() or q < 0:
        raise ValueError("Некорректная потребность")
    return int((q/s).to_integral_value(rounding=ROUND_CEILING))


def calculate(inputs, price_multiplier=D(1), yield_multiplier=D(1)):
    menu, recipes, yields, catalog, plan = inputs
    price_multiplier=positive(price_multiplier);yield_multiplier=positive(yield_multiplier)
    target=target_volume(plan);serving=positive(plan["serving_ml"])
    portions=target/serving
    items=[];lines=[]
    for m in menu:
        cid=m["candidate_id"];r=recipes[cid];output=positive(yields[cid]["yield_ml"])*yield_multiplier
        scale=target/output;batch=D(0)
        for iid, amount in r["ingredients"].items():
            amount=positive(amount);raw=catalog[iid]
            rate=D(raw["pack_price_rub"])/D(raw["pack_size_base"])*price_multiplier
            contribution=amount*rate;batch+=contribution
            lines.append({"candidate_id":cid,"ingredient_id":iid,"name":raw["name"],"base_unit":raw["base_unit"],
                          "recipe_quantity":amount,"required_quantity":amount*scale,"price_per_base_unit_rub":rate,
                          "used_cost_rub":contribution*scale})
        used=batch*scale;price=positive(m["price_50ml_rub"]);unit_cost=batch/output*serving
        revenue=price*portions
        items.append({**m,"composition_status":"author_supplied","source_batch_cost_rub":batch,
                      "planned_yield_ml":output,"yield_status":yields[cid]["yield_status"],"scale_factor":scale,
                      "target_ready_ml":target,"portions":portions,"used_cost_rub":used,"cost_liter_rub":batch/output*1000,
                      "cost_50ml_rub":unit_cost,"material_margin_50ml_rub":price-unit_cost,
                      "material_margin_fraction":(price-unit_cost)/price,"potential_revenue_rub":revenue,
                      "potential_material_contribution_rub":revenue-used,
                      "known_preparation_days":sum(D(str(x["duration_days"])) for x in r["phases"] if x["duration_days"] is not None),
                      "final_abv_pct":None,"validated_shelf_life_days":None,
                      "author_observed_storage_days":r["author_observed_storage_days"]})
    basket=[]
    for iid, raw in catalog.items():
        part=[x for x in lines if x["ingredient_id"]==iid]
        need=sum((x["required_quantity"] for x in part),D(0))
        size=D(raw["pack_size_base"]);price=D(raw["pack_price_rub"])*price_multiplier
        packs=pack_count(need,size);used=need/size*price;cash=packs*price;left=packs*size-need
        separate=sum(pack_count(x["required_quantity"],size) for x in part)
        minpacks=int(raw["catalog_rate_min_packs"] or 0)
        basket.append({**raw,"needed_quantity":need,"recipes_using":len(part),"candidate_ids":"|".join(x["candidate_id"] for x in part),
                       "packs_to_buy":packs,"purchased_quantity":packs*size,"purchase_cost_rub":cash,"used_cost_rub":used,
                       "leftover_quantity":left,"leftover_value_rub":cash-used,"separate_recipe_packs":separate,
                       "joint_rounding_saving_rub":(separate-packs)*price,
                       "catalog_index_rate_threshold_met":None if not minpacks else packs>=minpacks})
    cash=sum((x["purchase_cost_rub"] for x in basket),D(0));used=sum((x["used_cost_rub"] for x in items),D(0))
    revenue=sum((x["potential_revenue_rub"] for x in items),D(0))
    summary={"total_ready_ml":target*len(menu),"total_portions":portions*len(menu),"purchase_cost_rub":cash,
             "used_cost_rub":used,"leftover_value_rub":cash-used,"potential_revenue_rub":revenue,
             "potential_material_contribution_rub":revenue-used,"material_margin_fraction":(revenue-used)/revenue,
             "cash_after_ingredient_purchase_rub":revenue-cash,"simple_menu_price_rub":sum(positive(x["price_50ml_rub"]) for x in menu)/len(menu),
             "displayed_item_costs_sum_rub":sum(money(x["used_cost_rub"]) for x in items),
             "delivery_cost_rub":None,"other_operating_costs_rub":None,"net_profit_rub":None}
    return items,lines,basket,summary


def scenario_tables(inputs, base):
    menu,recipes,yields,catalog,plan=inputs;items,lines,basket,summary=base
    sales=[];mix=[];price_stress=[];yield_stress=[]
    for value in plan["sell_through_scenarios"]:
        fraction=D(value);sold=summary["total_portions"]*fraction;revenue=summary["potential_revenue_rub"]*fraction
        sold_material=summary["used_cost_rub"]*fraction
        sales.append({"sell_through":fraction,"produced_portions":summary["total_portions"],"sold_portions":sold,
                      "unsold_portions":summary["total_portions"]-sold,"unsold_ready_ml":summary["total_ready_ml"]*(1-fraction),
                      "realized_revenue_rub":revenue,"all_production_material_rub":summary["used_cost_rub"],
                      "sold_portions_material_rub":sold_material,"unsold_finished_material_value_rub":summary["used_cost_rub"]-sold_material,
                      "contribution_after_sold_material_rub":revenue-sold_material,
                      "revenue_minus_all_production_material_rub":revenue-summary["used_cost_rub"],
                      "cash_after_ingredient_purchase_rub":revenue-summary["purchase_cost_rub"],"sales_status":"assumed_uniform_by_flavor"})
    for share in plan["premium_sales_share_scenarios"]:
        p=D(share);basic=[x for x in items if x["tier"]=="basic"];premium=[x for x in items if x["tier"]=="premium"]
        avg_price=(1-p)*D(basic[0]["price_50ml_rub"])+p*D(premium[0]["price_50ml_rub"])
        avg_cost=(1-p)*sum(x["cost_50ml_rub"] for x in basic)/len(basic)+p*sum(x["cost_50ml_rub"] for x in premium)/len(premium)
        basic_stock=sum(x["portions"] for x in basic);premium_stock=sum(x["portions"] for x in premium)
        max_sold=min(basic_stock/(1-p),premium_stock/p)
        for fraction in map(D,plan["sell_through_scenarios"]):
            desired=summary["total_portions"]*fraction;feasible=desired<=max_sold
            mix.append({"premium_share":p,"requested_total_sell_through":fraction,"weighted_price_rub":avg_price,
                        "weighted_material_cost_rub":avg_cost,"material_margin_fraction":(avg_price-avg_cost)/avg_price,
                        "requested_sold_portions":desired,"max_feasible_total_portions":max_sold,"stock_feasible":feasible,
                        "realized_revenue_if_feasible_rub":avg_price*desired if feasible else None,
                        "sales_status":"scenario_not_observed_equal_within_tier"})
    for shock in map(D,plan["price_shocks"]):
        alternate=calculate(inputs,price_multiplier=1+shock)
        for row in alternate[0]:price_stress.append({"price_change":shock,"candidate_id":row["candidate_id"],"cost_50ml_rub":row["cost_50ml_rub"],"used_cost_rub":row["used_cost_rub"],"material_margin_fraction":row["material_margin_fraction"]})
        price_stress.append({"price_change":shock,"candidate_id":"ALL","cost_50ml_rub":alternate[3]["used_cost_rub"]/summary["total_portions"],"used_cost_rub":alternate[3]["used_cost_rub"],"purchase_cost_rub":alternate[3]["purchase_cost_rub"],"material_margin_fraction":alternate[3]["material_margin_fraction"]})
    for mult in map(D,plan["yield_multipliers"]):
        alternate=calculate(inputs,yield_multiplier=mult)
        for row in alternate[0]:yield_stress.append({"yield_multiplier":mult,"candidate_id":row["candidate_id"],"changed_yield_ml":row["planned_yield_ml"],"target_ready_ml":row["target_ready_ml"],"cost_50ml_rub":row["cost_50ml_rub"],"used_cost_rub":row["used_cost_rub"]})
        unmet=[x["ingredient_id"] for x in alternate[2] if x["catalog_index_rate_threshold_met"] is False]
        yield_stress.append({"yield_multiplier":mult,"candidate_id":"ALL","target_ready_ml":summary["total_ready_ml"],"used_cost_rub":alternate[3]["used_cost_rub"],"purchase_cost_rub":alternate[3]["purchase_cost_rub"],"material_margin_fraction":alternate[3]["material_margin_fraction"],"indexed_rate_thresholds_met":not unmet,"unmet_indexed_threshold_ingredients":"|".join(unmet)})
    return sales,mix,price_stress,yield_stress


def calendar(inputs):
    menu,recipes,yields,catalog,plan=inputs;rows=[]
    for item in menu:
        phases=recipes[item["candidate_id"]]["phases"]
        total=sum(int(x["duration_days"]) for x in phases if x["duration_days"] is not None);cursor=-total
        for phase in phases:
            duration=phase["duration_days"];start=cursor
            if duration is not None:cursor+=int(duration)
            start_date=(date.fromisoformat(plan["launch_date"])+timedelta(days=start)).isoformat() if plan["launch_date"] else None
            rows.append({"candidate_id":item["candidate_id"],"flavor":item["normalized_flavor"],**phase,
                         "start_day_relative":start,"end_day_relative":cursor,"start_date":start_date,
                         "time_interpretation":"placement_marker_not_zero_duration" if duration is None else "author_duration",
                         "ready_by_day_relative":0,"launch_reference":"Friday D0; Saturday D+1"})
    return rows


def write_csv(path, rows):
    columns=list(dict.fromkeys(k for row in rows for k in row))
    with path.open("w",newline="",encoding="utf-8") as stream:
        writer=csv.DictWriter(stream,fieldnames=columns);writer.writeheader()
        writer.writerows([{k:"" if v is None else str(v) if isinstance(v,D) else v for k,v in row.items()} for row in rows])


def run(render=True, data=DATA, out=OUT):
    check_baseline();inputs=load_inputs(data);base=calculate(inputs);items,lines,basket,summary=base
    out.mkdir(parents=True,exist_ok=True)
    tables={"recipe_costs.csv":items,"recipe_ingredients.csv":lines,"procurement.csv":basket,"production_calendar.csv":calendar(inputs)}
    matrices=[]
    for m in inputs[0]:
        row={"candidate_id":m["candidate_id"]}
        row.update({i:sum((x["required_quantity"] for x in lines if x["candidate_id"]==m["candidate_id"] and x["ingredient_id"]==i),D(0)) for i in inputs[3]})
        matrices.append(row)
    tables["ingredient_matrix.csv"]=matrices
    suppliers=[]
    for supplier in dict.fromkeys(x["supplier"] for x in basket):
        group=[x for x in basket if x["supplier"]==supplier]
        suppliers.append({"supplier":supplier,**{key:sum((x[key] for x in group),D(0)) for key in ["purchase_cost_rub","used_cost_rub","leftover_value_rub"]},"delivery_cost_rub":None,"minimum_order_rub":None,"terms_status":"not_confirmed_for_author_scenario"})
    tables["suppliers.csv"]=suppliers
    for name, rows in zip(["sales_scenarios.csv","premium_mix.csv","price_sensitivity.csv","yield_sensitivity.csv"],scenario_tables(inputs,base)):tables[name]=rows
    for name,rows in tables.items():write_csv(out/name,rows)
    payload={"scenario_date":inputs[4]["scenario_date"],"selection_source":"author_selected_menu.csv","recipes_source":"recipes.json","yields_source":"yields.csv","prices_source":"ingredients.csv","production_source":"production_plan.json","optimizer_called":False,"all_initial_yields_estimated":all(x["yield_status"]=="estimated" for x in inputs[2].values()),**summary}
    (out/"summary.json").write_text(json.dumps(payload,ensure_ascii=False,indent=2,default=str)+"\n")
    manifest={"inputs_sha256":{p.name:hashlib.sha256(p.read_bytes()).hexdigest() for p in data.glob('*') if p.is_file()},"outputs_sha256":{p.name:hashlib.sha256(p.read_bytes()).hexdigest() for p in out.glob('*') if p.is_file() and p.name!='run_manifest.json'}}
    (out/"run_manifest.json").write_text(json.dumps(manifest,ensure_ascii=False,indent=2)+"\n")
    if render:
        from scripts import report
        report.render(inputs,base,tables)
        rendered=[ROOT/"README.md",ROOT/"docs/economics_report.md",ROOT/"docs/economics_methodology.md",ROOT/"docs/project_plan.md"]+sorted(FIG.glob('*.png'))
        manifest["rendered_files_sha256"]={str(p.relative_to(ROOT)):hashlib.sha256(p.read_bytes()).hexdigest() for p in rendered}
        (out/"run_manifest.json").write_text(json.dumps(manifest,ensure_ascii=False,indent=2)+"\n")
    check_baseline()
    return payload


if __name__=="__main__":
    sys.path.insert(0,str(ROOT))
    result=run()
    print(json.dumps(result,ensure_ascii=False,indent=2,default=str))
