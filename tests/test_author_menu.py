"""Контракт автора и пересчёт через редактируемые данные, без изменения Python."""
import copy
import csv
import hashlib
import json
import shutil

import pytest
from scripts import economics as e


@pytest.fixture
def data(tmp_path):
    folder=tmp_path/'inputs';folder.mkdir()
    for p in e.DATA.iterdir():
        if p.is_file():shutil.copyfile(p,folder/p.name)
    return folder


def save_csv(path,rows):
    with path.open('w',newline='') as f:
        w=csv.DictWriter(f,fieldnames=rows[0]);w.writeheader();w.writerows(rows)


def test_all_author_doses_and_yields(data):
    menu,r,y,prices,plan=e.load_inputs(data)
    assert r['C01']['ingredients']=={'vodka':500,'horseradish':95,'ginger':15,'honey':10}
    assert r['C01']['ingredient_ranges']['horseradish']=={'min_g':90,'max_g':100,'economic_choice':'midpoint'}
    assert r['C02']['ingredients']=={'vodka':500,'cherry':250,'sugar':250}
    assert 'без косточек' in prices['cherry']['name']
    assert r['C03']['ingredients']=={'vodka':500,'raspberry':300,'sugar':200}
    assert r['C04']['ingredients']=={'vodka':1000,'lemon':1900,'sugar':500}
    assert 'water' not in prices and 'water' not in r['C04']['ingredients']
    assert r['C05']['ingredients']=={'vodka':500,'sorrel':100,'honey':100}
    assert r['C06']['ingredients']=={'vodka':500,'halva':600,'honey':90}
    assert r['C09']['ingredients']=={'vodka':500,'apple':525,'basil':15,'sugar':80}
    assert 'свежие' in prices['apple']['name'].lower()
    assert r['C12']['ingredients']=={'vodka':300,'raspberry':300,'cream':150,'milk':150,'sugar':100}
    assert r['C13']['ingredients']=={'vodka':500,'sea_buckthorn':500,'sugar':100}
    assert r['C14']['ingredients']=={'vodka':500,'cranberry':400,'sugar':150}
    expected={'C01':470,'C02':690,'C03':680,'C04':1500,'C05':530,'C06':450,'C09':650,'C12':700,'C13':720,'C14':670}
    assert {key:int(row['yield_ml']) for key,row in y.items()}==expected
    assert all(row['yield_status']=='estimated' for row in y.values())
    assert all(prices[i]['supplier']=='METRO' for i in ['lemon','sorrel','basil'])
    assert len(prices)==16 and set(x['supplier'] for x in prices.values())=={'Ладога','METRO','Агропарк СПб'}


@pytest.mark.parametrize('field,value',[('candidate_id','C10'),('tier','premium'),('price_50ml_rub','330'),('normalized_flavor','Вишня'),('selection_status','optimized')])
def test_author_menu_changes_fail(data,field,value):
    path=data/'author_selected_menu.csv';rows=e.read_csv(path);rows[0][field]=value;save_csv(path,rows)
    with pytest.raises(ValueError):e.load_inputs(data)


@pytest.mark.parametrize('field,value',[('pack_price_rub','0'),('pack_price_rub','NaN'),('pack_size_base','-1'),('base_unit','kg'),('price_per_base_unit_rub','1')])
def test_bad_price_and_units_rejected(data,field,value):
    path=data/'ingredients.csv';rows=e.read_csv(path);rows[0][field]=value;save_csv(path,rows)
    with pytest.raises(ValueError):e.load_inputs(data)


def test_missing_ingredient_price_does_not_become_zero(data):
    path=data/'ingredients.csv';rows=e.read_csv(path);save_csv(path,[r for r in rows if r['ingredient_id']!='milk'])
    with pytest.raises(ValueError,match='Нет цены'):e.load_inputs(data)


def test_measured_yield_updates_every_required_quantity_without_code_edit(data,tmp_path):
    before=e.calculate(e.load_inputs(data));code_digest=hashlib.sha256((e.ROOT/'scripts/economics.py').read_bytes()).hexdigest()
    path=data/'yields.csv';rows=e.read_csv(path)
    for row in rows:
        if row['candidate_id']=='C06':row.update(yield_ml='400',yield_status='measured',measurement_note='Искусственное значение только для теста механизма ввода')
    save_csv(path,rows)
    after=e.calculate(e.load_inputs(data))
    old=next(r for r in before[0] if r['candidate_id']=='C06');new=next(r for r in after[0] if r['candidate_id']=='C06')
    assert new['target_ready_ml']==6000 and new['yield_status']=='measured'
    assert new['scale_factor']==15
    assert new['cost_50ml_rub']>old['cost_50ml_rub']
    assert next(r for r in after[2] if r['ingredient_id']=='halva')['packs_to_buy']==36
    assert next(r for r in after[2] if r['ingredient_id']=='vodka')['needed_quantity']>next(r for r in before[2] if r['ingredient_id']=='vodka')['needed_quantity']
    assert hashlib.sha256((e.ROOT/'scripts/economics.py').read_bytes()).hexdigest()==code_digest
    result=e.run(render=False,data=data,out=tmp_path/'output')
    assert not result['all_initial_yields_estimated'] and result['total_ready_ml']==60000


def test_measured_yield_without_measurement_note_rejected(data):
    path=data/'yields.csv';rows=e.read_csv(path);rows[0]['yield_status']='measured';save_csv(path,rows)
    with pytest.raises(ValueError):e.load_inputs(data)


def test_old_optimizer_inputs_have_no_effect_on_current_model(data):
    before=e.calculate(e.load_inputs(data))
    (data/'config.json').write_text(json.dumps({'premium_pool':['C10'],'basic_price_rub':1,'starting_output_ml_per_recipe':7000,'balanced_weights':{'cash':1}}))
    after=e.calculate(e.load_inputs(data))
    assert before==after


def test_main_plan_cannot_silently_become_seven_liters(data):
    path=data/'production_plan.json';plan=json.loads(path.read_text());plan['servings_per_flavor_per_evening']=70;path.write_text(json.dumps(plan))
    with pytest.raises(ValueError,match='Основной план'):e.load_inputs(data)


def test_current_notebook_executed_without_error():
    nb=json.loads((e.ROOT/'assortment_economics.ipynb').read_text());cells=[c for c in nb['cells'] if c['cell_type']=='code']
    assert [c['execution_count'] for c in cells]==list(range(1,len(cells)+1))
    assert not any(o['output_type']=='error' for c in cells for o in c['outputs'])
    source='\n'.join(''.join(c['source']) for c in cells)
    assert 'result = e.run()' in source
    assert 'history/stage3_snapshot' not in source  # Исторический код не исполняется.
