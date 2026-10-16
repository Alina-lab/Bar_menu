"""Контрольные результаты автора и различие производства, закупки и продаж."""
import hashlib
import json
import zipfile
from decimal import Decimal as D

import pytest
from scripts import economics as e


@pytest.fixture(scope="module")
def inputs():
    return e.load_inputs()


@pytest.fixture(scope="module")
def base(inputs):
    return e.calculate(inputs)


def test_exact_production_basis(inputs,base):
    assert len(inputs[0])==10
    assert sum(r['tier']=='basic' for r in inputs[0])==8
    assert sum(r['tier']=='premium' for r in inputs[0])==2
    assert e.target_volume(inputs[4])==6000
    assert base[3]['total_ready_ml']==60000 and base[3]['total_portions']==1200
    assert all(r['target_ready_ml']==6000 and r['portions']==120 for r in base[0])
    assert e.D(inputs[3]['vodka']['pack_price_rub'])/e.D(inputs[3]['vodka']['pack_size_base'])*1000==380
    assert inputs[3]['vodka']['product']=='Водка Царская Оригинальная 40%'


@pytest.mark.parametrize('iid,expected', [('vodka',48),('raspberry',6),('sea_buckthorn',5),('cranberry',4),('cherry',3),('apple',5),('sugar',10),('honey',3),('lemon',8),('sorrel',12),('halva',32),('basil',3),('cream',2),('milk',2),('ginger',1),('horseradish',2)])
def test_all_procurement_controls(base,iid,expected):
    row=next(r for r in base[2] if r['ingredient_id']==iid)
    assert row['packs_to_buy']==expected


@pytest.mark.parametrize('cid,expected', [('C01','3322.66'),('C02','3547.61'),('C03','4449.53'),('C04','3026.20'),('C05','4096.98'),('C06','4428.93'),('C09','2554.68'),('C12','3965.91'),('C13','3306.58'),('C14','3658.52')])
def test_each_6l_cost_control(base,cid,expected):
    row=next(r for r in base[0] if r['candidate_id']==cid)
    assert e.money(row['used_cost_rub'])==D(expected)
    assert abs(row['cost_50ml_rub']*120-row['used_cost_rub'])<D('1e-40')


def test_aggregate_controls_and_rounding_reconciliation(base):
    s=base[3]
    assert s['purchase_cost_rub']==D('40002.80')
    assert s['potential_revenue_rub']==312000
    assert e.money(s['used_cost_rub'])==D('36357.61')
    assert s['displayed_item_costs_sum_rub']==D('36357.60')
    assert e.money(s['potential_material_contribution_rub'])==D('275642.39')
    assert e.money(s['leftover_value_rub'])==D('3645.19')
    # Авторские ориентиры суммируют уже округлённые строки; отклонение ровно копейка.
    assert abs(e.money(s['used_cost_rub'])-D('36357.60'))<=D('.01')
    assert abs(e.money(s['potential_material_contribution_rub'])-D('275642.40'))<=D('.01')
    assert abs(s['material_margin_fraction']-D('.8835'))<D('.0001')
    assert s['net_profit_rub'] is None and s['delivery_cost_rub'] is None


def test_supplier_controls(base):
    totals={name:sum(r['purchase_cost_rub'] for r in base[2] if r['supplier']==name) for name in {r['supplier'] for r in base[2]}}
    assert totals=={'Ладога':D('18240'),'METRO':D('19781.80'),'Агропарк СПб':D('1981')}


def test_quantities_and_shared_raspberry(base):
    b={r['ingredient_id']:r for r in base[2]}
    assert abs(b['vodka']['needed_quantity']/1000-D('47.300705335'))<D('.000000001')
    assert abs(b['raspberry']['needed_quantity']/1000-D('5.218487395'))<D('.000000001')
    r=b['raspberry']
    assert set(r['candidate_ids'].split('|'))=={'C03','C12'}
    assert r['separate_recipe_packs']==6 and r['packs_to_buy']==6
    assert r['joint_rounding_saving_rub']==0


def test_no_negative_inventory_and_exact_material_balance(base):
    for r in base[2]:
        assert r['leftover_quantity']>=0
        assert r['purchase_cost_rub']==r['used_cost_rub']+r['leftover_value_rub']
        assert r['purchased_quantity']>=r['needed_quantity']
        assert r['packs_to_buy']==e.pack_count(r['needed_quantity'],r['pack_size_base'])
    assert abs(sum(r['used_cost_rub'] for r in base[2])-base[3]['used_cost_rub'])<D('1e-40')


def test_partial_sales_do_not_reverse_production(inputs,base):
    sales=e.scenario_tables(inputs,base)[0]
    half=next(r for r in sales if r['sell_through']==D('.5'))
    assert half['realized_revenue_rub']==156000
    assert half['sold_portions']==600 and half['unsold_ready_ml']==30000
    assert half['all_production_material_rub']==base[3]['used_cost_rub']
    assert abs(half['unsold_finished_material_value_rub']-base[3]['used_cost_rub']/2)<D('1e-40')
    assert half['revenue_minus_all_production_material_rub']==156000-base[3]['used_cost_rub']
    assert half['contribution_after_sold_material_rub']>half['revenue_minus_all_production_material_rub']


def test_mix_cannot_sell_nonexistent_premium_stock(inputs,base):
    mix=e.scenario_tables(inputs,base)[1]
    for share,price in [(D('.05'),D('252.5')),(D('.2'),D('260')),(D('.35'),D('267.5'))]:
        rows=[r for r in mix if r['premium_share']==share]
        assert all(r['weighted_price_rub']==price for r in rows)
    invalid=next(r for r in mix if r['premium_share']==D('.35') and r['requested_total_sell_through']==1)
    assert not invalid['stock_feasible'] and invalid['realized_revenue_if_feasible_rub'] is None
    assert invalid['max_feasible_total_portions']<686
    assert all(r['stock_feasible'] for r in mix if r['premium_share']==D('.2'))


def test_price_and_yield_sensitivity(inputs,base):
    up=e.calculate(inputs,price_multiplier=D('1.2'))
    assert up[3]['purchase_cost_rub']==base[3]['purchase_cost_rub']*D('1.2')
    assert abs(up[3]['used_cost_rub']-base[3]['used_cost_rub']*D('1.2'))<D('1e-40')
    lower=e.calculate(inputs,yield_multiplier=D('.8'))
    assert lower[3]['total_ready_ml']==60000
    assert abs(lower[3]['used_cost_rub']-base[3]['used_cost_rub']/D('.8'))<D('1e-40')
    assert lower[3]['purchase_cost_rub']>base[3]['purchase_cost_rub']


@pytest.mark.parametrize('quantity,size,expected', [('1000','1000',1),('1000.000000000000001','1000',2),('999.999999999999999','1000',1),('0','1000',0)])
def test_decimal_pack_boundaries(quantity,size,expected):
    assert e.pack_count(D(quantity),D(size))==expected


def test_relative_calendar_and_storage_status(inputs):
    rows=e.calendar(inputs)
    assert min(r['start_day_relative'] for r in rows)==-30
    assert all(r['start_date'] is None for r in rows)
    c12=[r for r in rows if r['candidate_id']=='C12']
    assert next(r for r in c12 if r['phase']=='infusion')['start_day_relative']==-9
    cool=next(r for r in c12 if r['phase']=='refrigerated_rest')
    assert (cool['start_day_relative'],cool['end_day_relative'])==(-2,0)
    assert all(r['time_interpretation']=='placement_marker_not_zero_duration' for r in rows if r['duration_days'] is None)
    assert inputs[1]['C12']['author_observed_storage_days']==14
    assert inputs[1]['C12']['validated_shelf_life_days'] is None
    assert all(r['final_abv_pct'] is None for r in inputs[1].values())


def test_original_files_and_history_archive():
    assert len(e.check_baseline()['protected_files_sha256'])==14
    manifest=e.read_json(e.DATA/'history/manifest.json')
    with zipfile.ZipFile(e.DATA/'history/stage3_snapshot.zip') as archive:
        for name,digest in manifest['files_sha256'].items():
            assert hashlib.sha256(archive.read(name)).hexdigest()==digest


def test_saved_tables_and_report_come_from_current_inputs():
    manifest=e.read_json(e.OUT/'run_manifest.json')
    for name,digest in manifest['inputs_sha256'].items():
        assert hashlib.sha256((e.DATA/name).read_bytes()).hexdigest()==digest
    for name,digest in manifest['outputs_sha256'].items():
        assert hashlib.sha256((e.OUT/name).read_bytes()).hexdigest()==digest
    for name,digest in manifest['rendered_files_sha256'].items():
        assert hashlib.sha256((e.ROOT/name).read_bytes()).hexdigest()==digest


def test_clean_generation_uses_inputs_without_saved_results(tmp_path):
    target=tmp_path/'fresh_results'
    assert not target.exists()
    result=e.run(render=False,out=target)
    assert e.money(result['purchase_cost_rub'])==D('40002.80')
    for name in e.read_json(e.OUT/'run_manifest.json')['outputs_sha256']:
        assert (target/name).read_bytes()==(e.OUT/name).read_bytes(),name
