"""Презентационный экспорт обновляется из модели и не раскрывает дозировки."""
import hashlib
import shutil
from decimal import Decimal as D

import nbformat
import pytest
from scripts import economics as e


def rub(value):
    return D(value.replace('\u00a0','').replace(' ','').replace(',','.'))


def test_menu_costs_are_aggregated_and_reconcile():
    rows=e.read_csv(e.DATA/'menu_raw_material_costs.csv')
    basket=e.read_csv(e.OUT/'procurement.csv')
    assert len(rows)==len(basket)==16
    assert set(rows[0])=={
        'Поставщик','Сырьё','Плановая потребность','Плановая закупка','Единица','Упаковка',
        'Количество упаковок','Цена упаковки, ₽','Закупка, ₽','Израсходованное сырьё, ₽',
        'Остаток, количество','Остаток сырья, ₽','Статус цены','Применение цены','Источник цены',
    }
    for row,raw in zip(rows,basket):
        assert row['Сырьё']==raw['product'] and row['Поставщик']==raw['supplier']
        assert int(row['Количество упаковок'])==int(raw['packs_to_buy'])
        assert abs(rub(row['Плановая потребность'])-D(raw['needed_quantity'])/1000)<=D('.0005')
        assert rub(row['Плановая закупка'])==D(raw['purchased_quantity'])/1000
        assert abs(rub(row['Остаток, количество'])-D(raw['leftover_quantity'])/1000)<=D('.0005')
        for field,key in [('Цена упаковки, ₽','effective_pack_price_rub'),('Закупка, ₽','purchase_cost_rub'),
                          ('Израсходованное сырьё, ₽','used_cost_rub'),('Остаток сырья, ₽','leftover_value_rub')]:
            assert rub(row[field])==e.money(raw[key])
        assert row['Применение цены']==raw['applied_price_status']
    assert sum(rub(r['Закупка, ₽']) for r in rows)==D('40002.80')
    assert {r['Поставщик'] for r in rows}=={'Ладога','METRO','Агропарк СПб'}
    manifest=e.read_json(e.OUT/'run_manifest.json')
    assert 'menu_raw_material_costs.csv' not in manifest['inputs_sha256']
    assert hashlib.sha256((e.DATA/'menu_raw_material_costs.csv').read_bytes()).hexdigest()==manifest['menu_raw_material_costs_sha256']


@pytest.mark.parametrize('change',['price','quantity'])
def test_export_updates_with_price_or_procurement_quantity(tmp_path,change):
    data=tmp_path/'inputs';data.mkdir()
    for p in e.DATA.iterdir():
        if p.is_file():shutil.copyfile(p,data/p.name)
    if change=='price':
        path=data/'ingredients.csv';rows=e.read_csv(path)
        milk=next(r for r in rows if r['ingredient_id']=='milk')
        milk['pack_price_rub']='86.90';milk['price_per_base_unit_rub']='0.0869'
    else:
        path=data/'yields.csv';rows=e.read_csv(path)
        next(r for r in rows if r['candidate_id']=='C06')['yield_ml']='400'
    e.write_csv(path,rows)
    before=(e.DATA/'menu_raw_material_costs.csv').read_bytes()
    out=tmp_path/'fresh';result=e.run(render=False,data=data,out=out)
    exported=e.read_csv(out/'menu_raw_material_costs.csv')
    assert (e.DATA/'menu_raw_material_costs.csv').read_bytes()==before
    assert sum(rub(r['Закупка, ₽']) for r in exported)==result['purchase_cost_rub']
    if change=='price':
        milk=next(r for r in exported if r['Сырьё']=='Молоко 3.2%')
        assert rub(milk['Цена упаковки, ₽'])==D('86.90')
        assert rub(milk['Закупка, ₽'])==D('173.80')
    else:
        halva=next(r for r in exported if r['Сырьё']=='Халва подсолнечная')
        assert int(halva['Количество упаковок'])==36
        assert rub(halva['Плановая закупка'])==9


def test_unavailable_price_remains_empty_in_public_export():
    basket=e.calculate(e.load_inputs(),yield_multiplier=D('1.1'))[2]
    rows=e.menu_raw_material_rows(basket)
    cherry=next(r for r in rows if r['Сырьё']=='Вишня замороженная без косточек')
    assert cherry['Количество упаковок']==2
    assert cherry['Применение цены']=='missing_regular_price'
    assert all(cherry[k]=='' for k in ['Цена упаковки, ₽','Закупка, ₽','Израсходованное сырьё, ₽','Остаток сырья, ₽'])


def test_presentational_notebook_runs_without_recipes():
    nb=nbformat.read(e.ROOT/'nastoyka_menu.ipynb',as_version=4)
    nbformat.validate(nb)
    text='\n'.join(c.source for c in nb.cells)
    assert all(s not in text for s in ['recipes.json','recipe_ingredients.csv','recipe_quantity','scale_factor'])
    assert 'menu_raw_material_costs.csv' in text and 'result = e.run()' in text
    cells=[c for c in nb.cells if c.cell_type=='code']
    assert [c.execution_count for c in cells]==list(range(1,len(cells)+1))
    assert not any(o.output_type=='error' for c in cells for o in c.outputs)
    outputs='\n'.join(str(o.get('data',{})) for c in cells for o in c.outputs)
    for row in e.read_csv(e.DATA/'author_selected_menu.csv'):
        assert row['normalized_flavor'] in outputs
    assert sum('image/png' in o.get('data',{}) for c in cells for o in c.outputs)>=3
    assert '[Посмотреть готовое меню](nastoyka_menu.ipynb)' in (e.ROOT/'README.md').read_text(encoding='utf-8')
