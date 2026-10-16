"""Таблицы, графики и тексты из текущих расчётов; отдельная сборка Notebook."""
import json
import os
from pathlib import Path
import tempfile

os.environ.setdefault("MPLCONFIGDIR",str(Path(tempfile.gettempdir())/"nastoyka-mpl"))
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

from scripts import economics as e


def fmt(value, digits=2):
    if value is None:return "недоступно"
    shown=e.D(str(value)).quantize(e.D(1).scaleb(-digits),rounding=e.ROUND_HALF_UP)
    return f"{shown:,.{digits}f}".replace(","," ").replace(".",",")


def percent(value, digits=2):
    return "недоступно" if value is None else fmt(value*100,digits)+"%"


def ratio(left, right):
    return None if left is None or right is None else left/right


def yield_sensitivity_table(rows):
    return table(["Выход относительно плана", "Использовано, ₽", "Заказ, ₽",
                  "Цель готового объёма", "Доступность расчёта"], [
        [percent(r["yield_multiplier"],0), fmt(r["used_cost_rub"]), fmt(r["purchase_cost_rub"]),
         fmt(r["target_ready_ml"],0)+" мл",
         "нет обычной цены: "+r["missing_price_ingredients"]+" ("+r["unmet_threshold_pack_counts"]+")"
         if r["financial_status"] == "incomplete_missing_price" else "полный ценовой сценарий"]
        for r in rows if r["candidate_id"] == "ALL"])


def notebook_table(frame):
    """Округлять только отображение; исходные CSV/Decimal остаются точными."""
    import pandas as pd
    def formatter(column):
        def format_value(value):
            if pd.isna(value): return "недоступно"
            if isinstance(value, bool): return "да" if value else "нет"
            try:
                number=e.D(str(value))
            except ArithmeticError:
                return str(value)
            if not number.is_finite(): return "недоступно"
            if column.endswith("fraction") or column in {"sell_through","premium_share","requested_total_sell_through","price_change"}:
                return percent(number)
            if column.endswith("_rub"): return fmt(number)
            if column == "scale_factor": return fmt(number,6)
            if column in {"yield_multiplier"}: return fmt(number,2)
            return fmt(number,0 if number == number.to_integral_value() else 3)
        return format_value
    return frame.style.format({c:formatter(c) for c in frame.columns}).hide(axis="index")


def table(headers, rows):
    return "| "+" | ".join(headers)+" |\n| "+" | ".join(["---"]*len(headers))+" |\n"+"\n".join("| "+" | ".join(str(v).replace("|"," / ") for v in row)+" |" for row in rows)


def menu_card_image(menu):
    """PNG-карточки сохраняют оформление и в Jupyter, и в просмотрщике GitHub."""
    from io import BytesIO
    from textwrap import fill
    from matplotlib.patches import FancyBboxPatch

    descriptions={
        "C01":"Хрен, имбирь и мёд · Пряная",
        "C02":"Вишня без косточек · Ягодная",
        "C03":"Малина · Ягодная",
        "C04":"Цедра и сок лимонов · Цитрусовая",
        "C05":"Щавель и мёд · Травяная",
        "C09":"Зелёные яблоки и базилик · Фруктово-травяная",
        "C13":"Облепиха · Ягодная",
        "C14":"Клюква · Ягодная",
        "C06":"Подсолнечная халва и мёд · Десертная",
        "C12":"Малина, молоко и сливки · Сливочная",
    }
    navy="#233342";cream="#fffcf7";muted="#64727b";gold="#9f5836"
    with plt.rc_context({"font.family":"DejaVu Sans"}):
        fig=plt.figure(figsize=(11,12),facecolor=cream)
        ax=fig.add_axes([0,0,1,1]);ax.set_axis_off()
        def panel(x,y,width,height,color,edge):
            ax.add_patch(FancyBboxPatch((x,y),width,height,boxstyle="round,pad=0.008,rounding_size=0.012",
                                       transform=ax.transAxes,facecolor=color,edgecolor=edge,linewidth=1))
        panel(.045,.89,.91,.075,navy,navy)
        ax.text(.065,.936,"НАСТОЙКА",fontsize=25,weight="bold",color="white",transform=ax.transAxes)
        ax.text(.065,.907,"САНКТ-ПЕТЕРБУРГ · АВТОРСКАЯ КОЛЛЕКЦИЯ · 50 МЛ",fontsize=10,color="#eacaa9",transform=ax.transAxes)
        for tier,label,top,heading in [("basic","ОСНОВНАЯ КОЛЛЕКЦИЯ · 8 ВКУСОВ",.81,.85),
                                       ("premium","ДЕСЕРТНАЯ КОЛЛЕКЦИЯ · 2 ПРЕМИАЛЬНЫХ ВКУСА",.205,.245)]:
            ax.text(.045,heading,label,fontsize=12,weight="bold",color=navy,transform=ax.transAxes)
            for index,row in enumerate(r for r in menu if r["tier"]==tier):
                x=.045+(index%2)*.47;y=top-.12-(index//2)*.135
                panel(x,y,.44,.12,"#f7f1e8" if tier=="premium" else "white","#e5d9ca")
                ax.text(x+.018,y+.084,row["normalized_flavor"],fontsize=15,weight="bold",color=navy,transform=ax.transAxes)
                ax.text(x+.421,y+.084,fmt(row["price_50ml_rub"],0)+" ₽",fontsize=17,weight="bold",color=gold,ha="right",transform=ax.transAxes)
                ax.text(x+.018,y+.049,fill(descriptions[row["candidate_id"]],35),fontsize=10,color=muted,
                        va="top",linespacing=1.5,transform=ax.transAxes)
        ax.text(.045,.04,"Порция 50 мл · Малина — сливки содержит молочные продукты",fontsize=10,color=muted,transform=ax.transAxes)
        buffer=BytesIO();fig.savefig(buffer,format="png",dpi=140,facecolor=cream);plt.close(fig)
    return buffer.getvalue()


def figures(inputs, base, tables):
    menu,recipes,yields,catalog,plan=inputs;items,lines,basket,s=base
    estimated=sum(r["yield_status"]=="estimated" for r in yields.values());measured=len(yields)-estimated
    e.FIG.mkdir(parents=True,exist_ok=True)
    plt.rcParams.update({"font.family":"DejaVu Sans","font.size":10,"axes.spines.top":False,"axes.spines.right":False,"figure.facecolor":"white","axes.titleweight":"bold"})
    colors=["#7466C7" if r["tier"]=="premium" else "#188B91" for r in items]
    names=[r["normalized_flavor"] for r in items]
    def save(fig,name,note):
        fig.text(.02,.015,note,fontsize=9,color="#526171")
        fig.tight_layout(rect=[0,.06,1,1]);fig.savefig(e.FIG/name,dpi=155);plt.close(fig)
    if s["financial_status"] == "incomplete_missing_price":
        # Не оставлять графики от старой полной корзины после ввода нового выхода.
        for name in ["cost_50ml.png","cost_6l.png","supplier_budget.png",
                     "used_vs_purchased.png","production_calendar.png","sell_through.png"]:
            fig,ax=plt.subplots(figsize=(10,5))
            ax.axis("off")
            ax.text(.5,.6,"Полные финансовые графики недоступны",ha="center",fontsize=16)
            ax.text(.5,.45,"Нет применимой цены: "+s["missing_price_ingredients"],ha="center")
            save(fig,name,"Количество сырья и календарь доступны в CSV; неполный финансовый итог не изображён как точный.")
        return
    for field,name,title,xlabel in [("cost_50ml_rub","cost_50ml.png","Сырьё одной порции: 21–37 ₽ при заданных выходах","Расход сырья на 50 мл, ₽"),("used_cost_rub","cost_6l.png","Сырьё для 6 л каждой настойки","Расход сырья партии, ₽")]:
        fig,ax=plt.subplots(figsize=(11,6.4));values=[float(r[field]) for r in items];ax.barh(names,values,color=colors)
        for i,value in enumerate(values):ax.text(value+max(values)*.015,i,fmt(value),va="center",fontsize=9)
        if field=="cost_50ml_rub":title=f"Сырьё одной порции: {fmt(min(values))}–{fmt(max(values))} ₽"
        ax.set(xlabel=xlabel,title=title,xlim=(0,max(values)*1.20));ax.invert_yaxis()
        save(fig,name,f"Сценарий автора 16.10.2026; основные — бирюзовые, премиальные — фиолетовые. Оценочных выходов {estimated}/10.")
    suppliers=tables["suppliers.csv"];fig,ax=plt.subplots(figsize=(9.5,4.8));values=[float(r["purchase_cost_rub"]) for r in suppliers]
    ax.barh([r["supplier"] for r in suppliers],values,color=["#188B91","#E79A3F","#7466C7"])
    for i,value in enumerate(values):ax.text(value+250,i,f"{fmt(value)} ₽ · {fmt(value/float(s['purchase_cost_rub'])*100,1)}%",va="center")
    ax.set(xlabel="Первоначальная закупка сырья, ₽",xlim=(0,max(values)*1.45),title="Три поставщика и целые закупочные единицы");ax.invert_yaxis()
    save(fig,"supplier_budget.png","Цены автора — сценарий, не гарантия наличия или договорных условий. Доставка не включена.")
    fig,ax=plt.subplots(figsize=(10,5.5));used=float(s["used_cost_rub"]);stock=float(s["leftover_value_rub"])
    ax.bar(["Расход на производство","Первый заказ"],[used,used],color="#188B91",label="Использованное сырьё")
    ax.bar(["Расход на производство","Первый заказ"],[0,stock],bottom=[used,used],color="#E79A3F",label="Остаток сырья")
    ax.text(0,used+700,fmt(used)+" ₽",ha="center");ax.text(1,used+stock+700,fmt(used+stock)+" ₽",ha="center")
    ax.set(ylabel="Рубли",ylim=(0,(used+stock)*1.22),title="Округление упаковок связывает часть денег в запасе");ax.legend(frameon=False,loc="upper left")
    save(fig,"used_vs_purchased.png","Запас сырья отделён от фильтрационных потерь и остатков готовых напитков. Его пригодность не гарантирована.")
    fig,ax=plt.subplots(figsize=(12,6.5));positions={r["candidate_id"]:i for i,r in enumerate(items)}
    for row in tables["production_calendar.csv"]:
        y=positions[row["candidate_id"]];start=row["start_day_relative"];end=row["end_day_relative"]
        if row["duration_days"] is not None:ax.barh(y,end-start,left=start,color="#7466C7" if row["phase"]=="refrigerated_rest" else "#188B91",height=.55)
        else:ax.scatter([start],[y],marker="D" if row["phase"]=="filtration" else "*",color="#E79A3F",s=45,zorder=4)
    ax.axvline(0,color="#243D58",linestyle="--");ax.axvline(1,color="#243D58",linestyle=":")
    ax.set(yticks=list(positions.values()),yticklabels=names,xlabel="Дни относительно запуска: пятница D0, суббота D+1",xlim=(-32,2),title="Подготовку начинают не позднее D−30 по известным срокам");ax.invert_yaxis();ax.grid(axis="x",alpha=.15)
    save(fig,"production_calendar.png","Бирюзовый — настаивание, фиолетовый — холодильник C12; ромбы/звезда — операции с неизвестной длительностью.")
    sales=tables["sales_scenarios.csv"];labels=[fmt(r["sell_through"]*100,0)+"%" for r in sales];fig,axes=plt.subplots(1,2,figsize=(12,5.3))
    revenue=[float(r["realized_revenue_rub"]) for r in sales];sold=[float(r["sold_portions"]) for r in sales];unsold=[float(r["unsold_portions"]) for r in sales]
    axes[0].bar(labels,revenue,color="#188B91");axes[0].axhline(float(s["used_cost_rub"]),color="#D56C4E",linestyle="--",label="Сырьё на все 60 л")
    axes[0].set(ylabel="Полученная выручка, ₽",title="Выручка относится только к проданному");axes[0].legend(frameon=False,fontsize=8)
    axes[1].bar(labels,sold,color="#188B91",label="Продано");axes[1].bar(labels,unsold,bottom=sold,color="#E7B760",label="Не продано")
    axes[1].set(ylabel="Порции по 50 мл",title="Все 1 200 порций уже произведены");axes[1].legend(frameon=False,fontsize=8)
    save(fig,"sell_through.png","Продажи равномерны по вкусам только по допущению. Непроданные порции не включаются в выручку; это не прибыль.")


def render(inputs, base, tables):
    menu,recipes,yields,catalog,plan=inputs;items,lines,basket,s=base
    estimated=sum(r["yield_status"]=="estimated" for r in yields.values());measured=len(yields)-estimated
    figures(inputs,base,tables)
    item_table=table(["ID","Настойка","Выход партии, мл*","Коэффициент","Сырьё 6 л, ₽","50 мл, ₽","Цена, ₽","Сырьевая маржинальность"],[[r['candidate_id'],r['normalized_flavor'],fmt(r['planned_yield_ml'],0),fmt(r['scale_factor'],6),fmt(r['used_cost_rub']),fmt(r['cost_50ml_rub']),r['price_50ml_rub'],percent(r['material_margin_fraction'])] for r in items])
    basket_table=table(["Сырьё","Потребность","Покупаем","Единиц","Заказ, ₽","Использовано, ₽","Остаток","Остаток, ₽"],[[r['name'],fmt(r['needed_quantity'],3)+' '+r['base_unit'],fmt(r['purchased_quantity'],0)+' '+r['base_unit'],r['packs_to_buy'],fmt(r['purchase_cost_rub']),fmt(r['used_cost_rub']),fmt(r['leftover_quantity'],3)+' '+r['base_unit'],fmt(r['leftover_value_rub'])] for r in basket])
    supplier_table=table(["Поставщик","Заказ, ₽","Использовано, ₽","Остаток, ₽"],[[r['supplier'],fmt(r['purchase_cost_rub']),fmt(r['used_cost_rub']),fmt(r['leftover_value_rub'])] for r in tables['suppliers.csv']])
    recipe_sections=[]
    for m in menu:
        cid=m['candidate_id'];r=recipes[cid];ingredients='; '.join(catalog[i]['name']+' — '+fmt(q,0)+' '+catalog[i]['base_unit'] for i,q in r['ingredients'].items())
        steps=' '.join(f"{k+1}. {v}" for k,v in enumerate(r['technology']))
        note=' Хрен 90–100 г; для экономики выбрана середина 95 г.' if cid=='C01' else ''
        if cid=='C12':note=' Наблюдение автора о 14 днях хранения сохранено отдельно; безопасный коммерческий срок не установлен.'
        recipe_sections.append(f"### {cid} — {m['normalized_flavor']}\n\n{ingredients}.\n\n{steps}{note}\n\nПлановый выход исходной партии: **{fmt(yields[cid]['yield_ml'],0)} мл**, статус `{yields[cid]['yield_status']}`. Состав — `author_supplied`.\n")
    sales_table=table(["Реализация","Продано / осталось, порций","Выручка, ₽","Сырьё всех 60 л, ₽","Выручка минус всё сырьё, ₽","Готовый остаток по сырью, ₽"],[[fmt(r['sell_through']*100,0)+'%',fmt(r['sold_portions'],0)+' / '+fmt(r['unsold_portions'],0),fmt(r['realized_revenue_rub']),fmt(r['all_production_material_rub']),fmt(r['revenue_minus_all_production_material_rub']),fmt(r['unsold_finished_material_value_rub'])] for r in tables['sales_scenarios.csv']])
    mix_table=table(["Премиальные продажи","Средняя цена, ₽","Среднее сырьё 50 мл, ₽","Мат. маржинальность","Максимум продаж при этом mix*"],[[fmt(r['premium_share']*100,0)+'%',fmt(r['weighted_price_rub']),fmt(r['weighted_material_cost_rub']),percent(r['material_margin_fraction']),fmt(r['max_feasible_total_portions'],2)] for r in tables['premium_mix.csv'] if r['requested_total_sell_through']==e.D('0.5')])
    stress_table=table(["Изменение цены сырья","Использовано, ₽","Заказ, ₽","Мат. маржинальность при полной реализации"],[[fmt(r['price_change']*100,0)+'%',fmt(r['used_cost_rub']),fmt(r['purchase_cost_rub']),percent(r['material_margin_fraction'])] for r in tables['price_sensitivity.csv'] if r['candidate_id']=='ALL'])
    yield_table=yield_sensitivity_table(tables['yield_sensitivity.csv'])
    calendar_rows=[]
    for r in items:
        phases=recipes[r['candidate_id']]['phases'];inf=sum(x['duration_days'] or 0 for x in phases if x['phase']=='infusion');cool=sum(x['duration_days'] or 0 for x in phases if x['phase']=='refrigerated_rest')
        calendar_rows.append([r['candidate_id'],r['normalized_flavor'],'D−'+fmt(r['known_preparation_days'],0),inf,cool or '—','длительность не задана; до D0'])
    calendar_table=table(["ID","Вкус","Начало по известным срокам","Настаивание, дней","Холодильник, дней","Фильтрация / прочее"],calendar_rows)
    vodka=next(x for x in basket if x['ingredient_id']=='vodka');rasp=next(x for x in basket if x['ingredient_id']=='raspberry')
    largest_stock=sorted((r for r in basket if r['leftover_value_rub'] is not None),key=lambda r:r['leftover_value_rub'],reverse=True)[:3]
    stock_text='; '.join(r['name']+' — '+fmt(r['leftover_value_rub'])+' ₽' for r in largest_stock)
    body=f'''# Экономика запуска настоечного бара: авторский сценарий 60 литров

## Бизнес-задача

Бар Санкт-Петербурга готовит ассортимент для пятницы и субботы: по {plan['servings_per_flavor_per_evening']} порций каждого вкуса за вечер, порция {plan['serving_ml']} мл. Нужно оценить сырьё, стартовый платёж поставщикам и последствия неполных продаж. Это план, а не наблюдавшийся спрос.

Автор выбрал десять напитков, их составы, технологии и цены: восемь основных по 250 ₽ и две премиальные по 300 ₽. Оптимизатор не выбирает вкусы и не предлагает замены. [Ассортимент](../data/economics/author_selected_menu.csv) остался прежним; [задание автора](author_specification.md) является источником новых рецептур и цен. Предыдущие A/B/C/B2 и расчёт по 1 л сохранены [в истории](../data/economics/history/README.md), они не участвуют в действующей экономике.

## От исследования рынка к авторским рецептурам

Основа портфолио — прежнее исследование меню и публичных отзывов: 368 отзывов, 468 строк упоминаний, 198 подтверждённых конкретных упоминаний и 114 нормализованных вкусов. Люди, оставляющие отзывы, не представляют всех гостей; публикации не равны продажам. Из 368 публикаций проверены 358; десять остались неподтверждёнными. Строгий срез — 143 упоминания из 84 отзывов. Хреновуха и щавель встречаются по семь раз, по пять оценок положительные. У простой томатной шесть положительных упоминаний из одного заведения, в строгом срезе одно: это показывает влияние концентрации источников. [Сводка рынка](../data/processed/summary.json), [вкусовые базы](../data/processed/flavor_summary.csv), [меню конкурентов](../data/processed/menu.csv), [журнал сбора](collection_report.md) и [правила нормализации](methodology.md) доступны отдельно. Первичная книга, старые CSV, методика сбора и ID сохранены побайтно. Клюква остаётся решением автора, а не результатом положительных дегустационных голосов.

Все десять позиций имеют статус выбора `author_selected_final`: автор окончательно согласовал вкусы, составы, поставщиков, цены продажи и план по 6 л. Происхождение рецептур отдельно обозначено `author_supplied`. Ранее применявшиеся адаптации и модельные дозировки заменены. Факт предоставления рецепта автором не означает лабораторную проверку выхода, безопасности или срока хранения. Исходные выходы автора — `estimated`; в текущем наборе оценочных **{estimated}**, измеренных **{measured}**. Их можно заменить в [yields.csv](../data/economics/yields.csv), указав `measured` и описание измерения, затем повторить расчёт без правки кода.

## Технологические и финансовые допущения

Основной план — **{fmt(s['total_ready_ml']/1000,0)} л**, {fmt(s['total_portions'],0)} порций, строго по 6 л каждого вкуса. `scale_factor = 6000 / yield_ml`, количество сырья = дозировка исходной партии × коэффициент. Промежуточные количества и денежные суммы не округляются; используется Decimal с точностью 50 значащих цифр. Округляются реальные упаковки вверх и итоговое отображение.

Плановый готовый выход уже учитывает фильтрацию; второй процент потерь поверх него не накладывается. Готовый объём не равен объёму водки: в него переходят сок, растворённый сахар и другие компоненты, часть жидкости остаётся в сырье/фильтрах. Массу и миллилитры не складываем и не переводим через придуманную плотность. 40% относится к водке «Царская Оригинальная», а конечная крепость остаётся неизвестной.

Рецептурный источник один — [recipes.json](../data/economics/recipes.json), цены — [ingredients.csv](../data/economics/ingredients.csv), производственный план — [production_plan.json](../data/economics/production_plan.json). Дата ценового сценария — **16.10.2026**. Старые 726 ₽/0,5 л, вода в лимончелло, замороженное яблоко, прежняя молочная рецептура и выход халвы 722,5 мл в текущем расчёте не используются.

## Выбор поставщиков

Водка Ладоги стоит в сценарии автора 380 ₽/л, плановая единица 1 л. METRO поставляет все четыре замороженные ягоды, лимоны, щавель, базилик, сахар, мёд, халву, сливки и молоко. Агропарк СПб — только свежий корень хрена, имбирь и свежие зелёные яблоки. Лимоны и зелень не перенесены к другому поставщику.

Все цены сохранены как предоставленный автором сценарий. В поисковых представлениях каталога METRO найдены совпадающие объёмные ориентиры для вишни, малины, облепихи, клюквы и халвы, но прямое открытие карточек недоступно или требует JavaScript; они **не названы независимым подтверждением текущей цены магазина СПб**. Индекс указывает пороги от трёх упаковок ягод и от пятнадцати упаковок халвы. Основная корзина эти пороги проходит. Это не дополнительные скидки: в расчёте всё равно фиксированные цены автора. Доступность, акции, выбор торгового центра и способ получения нужно подтвердить.

Для хрена открытая карточка Агропарка противоречит названию: содержит воду, уксус и соль, неоднозначную фасовку. Это не подтверждение необработанного корня. В модели сохранены авторские 653 ₽/кг и плановый шаг 1 кг, но поставщику нужен заказ именно свежего корня, не соуса. Цена яблок 69 ₽/кг предоставлена автором; упаковка 1 кг для яблок/имбиря также является плановым шагом. URL, тип источника и статусы сохранены в справочнике. Тарифы доставки и минимальные суммы новых поставщиков неизвестны и не заменены нулями; бюджет ниже относится только к сырью.

## Производственный календарь

{calendar_table}

Пятница — D0, суббота — D+1. Конкретную дату запуска автор не задавал. Максимальный известный цикл — 30 дней; время подготовки, фильтрации, нагрева и розлива полностью не нормировано и может потребовать дополнительного времени. Маркеры фильтрации на диаграмме не означают нулевую длительность операции.

![Календарь](../figures/economics/production_calendar.png)

C12 требует 7 дней малиновой основы, фильтрации, приготовления и охлаждения молочной части, затем примерно 2 дней в холодильнике. Наблюдение автора `author_observed_storage_days = 14` сохранено, но **не является установленным безопасным сроком коммерческого хранения**. Нужны надлежащие условия холодильного хранения и проверка требований пищевой безопасности. Новые рецептурные эксперименты не проводились.

## Закупочная корзина

{basket_table}

Сначала суммируется расход всех десяти рецептур, затем `ceil(total_needed / pack_size)`. Число целых исходных рецептур не округляется. Водки требуется **{fmt(vodka['needed_quantity']/1000,6)} л**, покупаем {fmt(vodka['purchased_quantity']/1000,0)} л. Малины для C03 и C12 вместе — **{fmt(rasp['needed_quantity']/1000,6)} кг**, покупаем {rasp['packs_to_buy']} кг. При независимом округлении рецептов малины получилось бы {rasp['separate_recipe_packs']} упаковок; разница первой кассы от совместного округления — **{fmt(rasp['joint_rounding_saving_rub'])} ₽**. В исходном сценарии и вместе, и раздельно нужны шесть упаковок: наличие общего ингредиента само по себе не создаёт экономии. Суммирование остаётся необходимым для корректной общей потребности и единого остатка. Использованная масса и её стоимость от этого не меняются.

Сырьё исходных партий и точная потребность — [recipe_ingredients.csv](../data/economics/results/recipe_ingredients.csv); матрица 10×16 — [ingredient_matrix.csv](../data/economics/results/ingredient_matrix.csv); фасовки и остатки — [procurement.csv](../data/economics/results/procurement.csv).

{supplier_table}

**Первоначальный платёж за сырьё — {fmt(s['purchase_cost_rub'])} ₽**. Стоимость использованного сырья — **{fmt(s['used_cost_rub'])} ₽**, остаток по закупочной ставке — **{fmt(s['leftover_value_rub'])} ₽**. Остаток не является производственной потерей или автоматически списанным убытком. Свежие хрен, имбирь, яблоки, зелень, лимоны и открытая молочная продукция нуждаются в подходящем хранении; замороженные ягоды — в холодовой цепи. Годность повторного использования не предполагается без условий и фактической потребности.

В сырьевом запасе остаётся {fmt(e.times(ratio(s['leftover_value_rub'],s['purchase_cost_rub']),100))}% первой кассы. Самые крупные остатки по стоимости: {stock_text}. Их повторное использование нельзя считать гарантированным покрытием следующих продаж; сначала нужны подходящий режим хранения и фактическая потребность.

![Бюджет поставщиков](../figures/economics/supplier_budget.png)
![Расход и закупка](../figures/economics/used_vs_purchased.png)

Водка связывает {fmt(e.times(ratio(vodka['purchase_cost_rub'],s['purchase_cost_rub']),100),1)}% стартового бюджета. Это объясняет основной финансовый эффект замены прежней розничной котировки авторской, но не подтверждает условия поставки. Общая водка, сахар и мёд также объединяются перед округлением; универсальные ингредиенты не выдаются за доказательство вкусового разнообразия.

## Себестоимость напитков и цены

{item_table}

\\* Оценочных выходов {estimated}, измеренных {measured}. Измерение выхода не подтверждает безопасность хранения. Подробные показатели включают стоимость исходной партии, сырьё 6 л, литра и 50 мл, доход на порцию и потенциальную выручку от 120 порций — [recipe_costs.csv](../data/economics/results/recipe_costs.csv).

![Сырьё 50 мл](../figures/economics/cost_50ml.png)
![Сырьё партий 6 л](../figures/economics/cost_6l.png)

При продаже всех порций потенциальная выручка — **{fmt(s['potential_revenue_rub'])} ₽**. После стоимости израсходованного сырья остаётся **{fmt(s['potential_material_contribution_rub'])} ₽**, сырьевая маржинальность — **{fmt(e.times(s['material_margin_fraction'],100))}%**. Это не чистая прибыль: труд, аренда, налоги, эквайринг, оборудование, доставка, хранение и списания готового продукта не рассчитаны. Разница выручки и первоначальной закупки — {fmt(s['cash_after_ingredient_purchase_rub'])} ₽ — отдельный денежный показатель, тоже не прибыль.

Небольшая сверка округления: сумма строк расходов на 6 л, уже показанных с двумя знаками, равна **{fmt(s['displayed_item_costs_sum_rub'])} ₽**. Сумма неокруглённых вычислений даёт **{fmt(s['used_cost_rub'])} ₽**; разница отображения **{fmt(e.minus(None if s['used_cost_rub'] is None else e.money(s['used_cost_rub']),s['displayed_item_costs_sum_rub']))} ₽**. При исходных выходах автора это объясняет отличие связанных итогов от его контроля 36 357,60 ₽ на копейку. После изменения выхода контроль исходного сценария не применяется к новому результату. Расчётные агрегаты получены до округления, а не сложением видимых копеек; подгонки нет.

Разделение 8+2 — авторская ценовая архитектура. Премиальные цены 300 ₽ сохранены. Сырьё халвы и малины — сливок стоит примерно столько же или меньше, чем обычной малины: сырьевая стоимость сама по себе не обосновывает премиальную наценку. Более высокая или низкая себестоимость не доказывает готовность гостя платить. Равный план выпуска не подтверждает равномерный спрос; отзывы не использовались как фактические веса продаж.

## Реализация и остаток готовой продукции

{sales_table}

Во всех строках уже произведены все 60 л. Вариант 50% не уменьшает задним числом сырьевой расход на производство. Выручка включает только проданное; оставшиеся порции и их сырьевая стоимость показаны отдельно от остатка закупленных ингредиентов. В CSV дополнительно есть вклад после сырья только проданных порций и денежный остаток после всей закупки. Различия отражают разные вопросы, а не разные оценки чистой прибыли.

![Сценарии продаж](../figures/economics/sell_through.png)

{mix_table}

\\* Средний чек здесь относится только к порциям настоек. Внутри каждого уровня доли вкусов равны по допущению. Доступно 960 основных и 240 премиальных порций. При 35% премиальных продаж нельзя продать все 1 200 порций без изменения запаса. [premium_mix.csv](../data/economics/results/premium_mix.csv) проверяет сочетания с 50/70/90/100% реализации; невыполнимым сценариям не назначается выручка от отсутствующих порций. Дополнительные 7 л и новые вкусы не подставляются.

## Чувствительность к ценам, выходу и упаковкам

{stress_table}

{yield_table}

\\* Цель всегда остаётся 60 л. Снижение выхода требует больше сырья для того же готового объёма. Это отдельный сценарий, не факт потерь. При изменении выхода пересчитываются коэффициенты и целые упаковки; индексные пороги оптовых цен могут перестать выполняться. Ставка выбирается после округления общего заказа: оптовая действует только при выполнении порога. Ниже порога применяется обычная цена из regular_pack_price_rub только при наличии источника regular_price_source и даты regular_price_date. Если она неизвестна, затронутые себестоимости и финансовый итог всего меню недоступны; CSV хранит пустые значения, статус incomplete_missing_price и ID сырья. Для выхода +10% это вишня: две упаковки вместо трёх, обычная цена отсутствует. Другие девять вкусов рассчитаны, но не выданы за полный итог. Потребность не увеличивается ради скидки. Ввод измерения в yields.csv позволяет повторить весь расчёт без правки Python.

Количество упаковок влияет на стартовую кассу, запас и доступность тарифного порога. Более высокий выход уменьшает физический расход на 50 мл при прочих равных, но итоговая экономия в рублях зависит также от доступного тарифа. В procurement.csv для каждого ингредиента сохранены раздельное округление рецептов и совместное округление; новые скидки не добавлены. Чувствительность по каждому вкусу — [price_sensitivity.csv](../data/economics/results/price_sensitivity.csv) и [yield_sensitivity.csv](../data/economics/results/yield_sensitivity.csv).

## Авторские технологии исходных партий

Количество ниже относится к одной исходной партии, а не к 6 л. Источник всех составов и операций — автор проекта. Поля источника и наблюдаемого хранения сохранены отдельно от подтверждённых измерений.

{chr(10).join(recipe_sections)}

Для C04 1 900 г — масса закупаемых целых лимонов, не цедры: используются вся жёлтая цедра и весь сок, сахар вводится с самого начала, вода отсутствует. C09 использует только свежие зелёные яблоки 525 г, не прежние замороженные 350 г. У C06 консервативный выход 450 мл; старые 722,5 мл не применяются. C02 — 250 г вишни без косточек, C03 — 200 г сахара. C12 имеет две отдельные основы и молоко, которых не было в прежнем расчёте.

## Что требуется для запуска и пределы выводов

Нужно подтвердить поставку водки по авторской цене, магазин/акции METRO, подходящий необработанный хрен, фактические весовые единицы Агропарка и условия трёх поставщиков, включая доставку. Перед производством важны наличие холодильных ёмкостей, рабочие операции и время фильтрации/розлива. После производства следует внести измеренные выходы; наблюдение о хранении молочной настойки не заменяет проверки требований безопасности и условий хранения.

Заданная потребность в 120 порций каждого вкуса — план автора, а не подтверждённый спрос. Средний расход около {fmt(e.times(s['used_cost_rub'],1/s['total_portions']))} ₽ сырья на 50 мл оставляет большой материальный вклад в данном ценовом сценарии, но не отвечает на вопрос о полной прибыльности бара. Упаковочные остатки и непроданные готовые напитки требуют разного управления. На этом этапе ассортимент и технология не менялись, эксперименты с новыми рецептами не проводились.

## Воспроизведение и контроль качества

`python scripts/economics.py` заново формирует таблицы, шесть графиков, README и этот отчёт. Notebook вызывает тот же расчёт, не содержит собственного набора рецептур или цен. Manifest сохраняет SHA256 входов и выходных таблиц. [Протокол свежих проверок](validation.md) отдельно фиксирует входы, новый запуск тестов и выполнение Notebook; прошлые статусы не считаются новым прогоном. SHA256 первичной базы проверяются при каждом запуске, исторический архив имеет собственный manifest.
'''
    (e.ROOT/"docs/economics_report.md").write_text(body,encoding="utf-8",newline="\n")
    research=e.read_json(e.ROOT/"data/processed/summary.json")
    menu_table=table(["ID","Авторское меню","Цена 50 мл, ₽","Сырьё 50 мл, ₽","Рецептура / выход"],
        [[r['candidate_id'],r['normalized_flavor'],fmt(r['price_50ml_rub']),fmt(r['cost_50ml_rub']),
          'авторская / '+('оценочный' if r['yield_status']=='estimated' else 'измеренный')] for r in items])
    public_menu_table=table(["Настойка","Цена за 50 мл","Сырьё на 50 мл"],[[r["normalized_flavor"],fmt(r["price_50ml_rub"],0)+" ₽",fmt(r["cost_50ml_rub"])+" ₽"] for r in items])
    public_supplier_table=table(["Поставщик","Плановая закупка"],[[r["supplier"],fmt(r["purchase_cost_rub"])+" ₽"] for r in tables["suppliers.csv"]])
    readme_template='''# Nastoyka · авторское меню настоек

**Личный проект: от исследования отзывов о петербургских барах до меню из десяти настоек с рассчитанными закупками и сырьевой себестоимостью.**

**[Посмотреть готовое меню](nastoyka_menu.ipynb)** · **[Таблица закупок](data/economics/menu_raw_material_costs.csv)** · [Исходная база отзывов](data/nastoyki_reviews.xlsx) · [Методика исследования](docs/methodology.md)

## Что получилось

| Итог | Значение |
| --- | ---: |
| Авторское меню | **10 вкусов**: 8 основных и 2 премиальных |
| Порция | **50 мл** |
| План производства | **60 л** — по 6 л каждого вкуса |
| Порций на два вечера | **1 200** |
| Закупка всех ингредиентов | **@@PURCHASE@@ ₽** |
| Стоимость израсходованного сырья | **@@USED@@ ₽** |
| Остаток сырья после производства | **@@LEFTOVER@@ ₽** |
| Потенциальная выручка при полной продаже | **@@REVENUE@@ ₽** |
| Разница выручки и расхода сырья | **@@MARGIN@@ ₽** |

Это **плановый сценарий**, не фактические продажи и не расчёт чистой прибыли. Цель проекта — подобрать ассортимент и определить закупочные расходы, без моделирования аренды, зарплат и прочих затрат бара.

## Итоговое меню

Восемь основных настоек продаются по **250 ₽**, две десертные премиальные — по **300 ₽** за 50 мл. Названия и цены выбраны автором; здесь приведены только стоимость порции и сырьевой расход, **без полных рецептур и дозировок**.

@@MENU_TABLE@@

[Открыть презентационное меню с карточками напитков и графиками →](nastoyka_menu.ipynb)

## Как выбирали вкусы

В исследовании собраны **368 публикаций**, из них **358 проверены**. Из отзывов выделены **198 подтверждённых конкретных упоминаний вкусов из 108 отзывов**; названия нормализованы до **114 вариантов**. Изучались предложения и впечатления гостей в барах и рюмочных Санкт-Петербурга.

Хреновуха и щавель получили по 5 положительных упоминаний из 7 конкретных. Данные помогли изучить ассортимент и составить варианты меню, но **отзывы не равны статистике продаж** и не доказывают оптимальность десяти выбранных вкусов. Окончательное меню — авторское решение.

[Исходный Excel](data/nastoyki_reviews.xlsx) · [Сводка](data/processed/summary.json) · [Справочник вкусов](data/processed/flavor_summary.csv) · [Сбор данных](docs/collection_report.md)

## Закупки и себестоимость

Поставщики: **Ладога** (водка), **METRO** (ягоды и большая часть остальных продуктов), **Агропарк СПб** (хрен, имбирь и зелёные яблоки). Одинаковое сырьё суммируется по всем напиткам, затем закупка округляется до целых упаковок.

@@SUPPLIER_TABLE@@

**[Скачать / открыть агрегированную таблицу закупок](data/economics/menu_raw_material_costs.csv)** — 16 видов сырья, общая потребность, упаковки, цены, расходы и остатки. В ней нет распределения ингредиентов по отдельным рецептам.

Наценка и рассчитанная сырьевая маржинальность зависят от **оценочных**, а не измеренных выходов настоек. Стоимость закупки и стоимость израсходованных ингредиентов — разные показатели; остатки не списываются автоматически.

![Сырьевой расход на порцию 50 мл](figures/economics/cost_50ml.png)

## Файлы проекта

| Материал | Для чего |
| --- | --- |
| **[Финальный Notebook](nastoyka_menu.ipynb)** | Презентация меню, итоги исследования, экономика и графики |
| **[Таблица сырья](data/economics/menu_raw_material_costs.csv)** | Готовая общая закупочная ведомость без рецептов |
| [Технический Notebook](assortment_economics.ipynb) | Подробные расчёты и технологии, для автора проекта |
| [Экономический отчёт](docs/economics_report.md) | Допущения, анализ и ограничения |
| [Исходное исследование](data/nastoyki_reviews.xlsx) | Отзывы и конкурентный ассортимент |

## Воспроизводимость

Результаты считаются из CSV/JSON, а таблица закупок создаётся автоматически. На Python 3.12:

    python -m pip install -r requirements.txt
    python scripts/economics.py
    python -m pytest -q
    python scripts/verify.py

GitHub Actions проверяет тесты, исходные данные и выполнение обоих Notebook в чистом окружении. [Проверка CI](.github/workflows/ci.yml) · [Протокол](docs/validation.md).

**Ограничения:** рецептуры выбраны автором, выходы ещё не измерены; цены и наличие необходимо подтверждать перед закупкой. Для молочной настойки срок безопасного коммерческого хранения не установлен. По содержанию задача составления меню и расчёта сырьевой экономики завершена.
'''
    readme=(readme_template.replace("@@PURCHASE@@",fmt(s["purchase_cost_rub"]))
                         .replace("@@USED@@",fmt(s["used_cost_rub"]))
                         .replace("@@LEFTOVER@@",fmt(s["leftover_value_rub"]))
                         .replace("@@REVENUE@@",fmt(s["potential_revenue_rub"],0))
                         .replace("@@MARGIN@@",fmt(s["potential_material_contribution_rub"]))
                         .replace("@@MENU_TABLE@@",public_menu_table)
                         .replace("@@SUPPLIER_TABLE@@",public_supplier_table))
    (e.ROOT/"README.md").write_text(readme,encoding="utf-8",newline="\n")
    (e.ROOT/"docs/economics_methodology.md").write_text('''# Методика текущей экономики

Исходные числа и технологии предоставлены автором в author_specification.md. Активные источники: author_selected_menu.csv, recipes.json, yields.csv, ingredients.csv, production_plan.json. Прежняя экономика находится только в data/economics/history/stage3_snapshot.zip и не импортируется кодом. SHA256 первичной базы фиксирует source_baseline.json; первичные отзывы и их методика не меняются.

Статус author_selected_final фиксирует окончательное авторское согласование десяти вкусов, составов, поставщиков, цен продажи и плана по 6 л. Это не статус лабораторной валидации. Восемь основных и две премиальные позиции фиксированы. Проверка отклоняет неверный ID, уровень, цену или отсутствие рецепта/выхода. Используются g/ml; килограммы и литры поставщиков представлены через размер закупочной единицы в базовых единицах. Плотность не предполагается. Водка только одного текущего товара.

Целевой объём = 50 мл × 60 порций каждого вкуса × 2 вечера = 6000 мл на вкус. Коэффициент = целевой объём / выход исходной партии; расход каждого ингредиента = дозировка × коэффициент. Число исходных партий дробное в аналитической модели; ингредиенты не округляются. Выход уже означает пригодный продукт после фильтрации. Второй вычет потерь не применяется. Выход можно отредактировать в yields.csv, сменив estimated на measured и указав описание измерения.

Все деньги и коэффициенты считаются Decimal с 50 значащими цифрами. Себестоимость партии = сумма количества × цена базовой единицы. Стоимость литра и 50 мл вычисляется через заданный выход. Сначала суммируется потребность по десяти вкусам, затем округляется количество упаковок через ROUND_CEILING. Цена упаковки не получает дополнительную скидку. Первый заказ = использованный расход + стоимость неиспользованного сырья; массы и объёмы разных ингредиентов не суммируются в общий физический остаток.

Финансовые агрегаты складываются до округления. Отображение — два денежных знака, HALF_UP. Сумма уже округлённых строк может отличаться на копейку: это отдельно показано, исходные формулы не подгоняются под контроль. Тесты проверяют как точную закупку, так и допустимое расхождение округления в авторских ориентирах.

Все 60 л считаются произведёнными в базовых сценариях 50/70/90/100% реализации. Полный расход сырья на производство постоянен; выручка и себестоимость проданной части меняются; готовый непроданный запас оценивается отдельно от сырьевого остатка. Два варианта вкладов — после сырья проданной части и после сырья всех 60 л — подписаны отдельно. Ни один не называется чистой прибылью. Денежная разница выручки и полной закупки не является ни одним из них.

Mix 5/20/35% премиальных продаж предполагает равные доли вкусов внутри уровня. Средняя цена и средний расход — взвешенные. Ограничение запасов: всего 960 основных и 240 премиальных порций. Максимум проданных при доле p = min(960/(1-p),240/p). Если запрошенное число больше, выручка оставлена пустой: непроданные/непроизведённые порции не превращаются в продажи.

Чувствительность к ценам меняет все тарифы одинаковым множителем, не создаёт новую скидку. Чувствительность к выходу сохраняет цель 6000 мл каждого вкуса и пересчитывает расход, закупочные упаковки и остатки. Ценовые пороги проверяются после суммирования потребности и округления упаковок в каждом calculate(), включая ввод нового выхода в CSV. При нарушении порога применима только известная обычная цена с источником и датой. При её отсутствии цена, затронутые расходы и общий финансовый итог равны None (пусто в CSV, null в JSON, «недоступно» в отчёте); частичный итог не выдаётся за полную стоимость. Текущие обычные цены неизвестны. Закупка лишних упаковок ради скидки не выполняется. Сравнение с отдельными заказами также проверяет тариф каждого заказа; неизвестный тариф делает экономию недоступной.

Календарь относительный: пятница D0 и суббота D+1. Известные сроки отсчитываются назад; неизвестные длительности фильтрации, нагрева и розлива представлены маркерами размещения, а не нулевыми операциями. При отсутствии даты конкретные календарные числа не назначаются.

Авторское наблюдение хранения C12=14 дней отделено от validated_shelf_life_days, которое пусто. Конечная крепость также не вычисляется как 40%: удержание жидкости, сок и растворённый сахар меняют объём, потери этанола не измерены. Цены являются авторским сценарием на 16.10.2026; доставка, минимумы и наличие не определены и не заменены нулями.

economics.py формирует CSV и summary.json; report.py создаёт из тех же вычисленных таблиц шесть графиков, отчёт и README. run_manifest.json фиксирует входные и выходные SHA256. Notebook вызывает ту же функцию run и показывает те же файлы. Протокол тестов фиксируется отдельно после фактического свежего прогона и относится к указанным хешам.
''',encoding="utf-8",newline="\n")
    (e.ROOT/"docs/project_plan.md").write_text('''# Текущий план проекта — авторский сценарий 16.10.2026

Задание автора заменило прежнюю оптимизацию ассортимента. Фиксированы десять ID, 8 основных по 250 ₽ и 2 премиальные по 300 ₽. Главный сценарий — пятница/суббота, по 60 порций каждого вкуса за вечер, 6000 мл каждого вкуса, всего 60 л. Дата запуска не задана.

Текущий результат: канонические авторские составы и технологии, редактируемые оценочные выходы, 16 ингредиентов трёх поставщиков, суммарное округление закупок, себестоимость, сценарии неполных продаж/mix, чувствительность, относительный календарь, Notebook, отчёт и тесты. Отбор альтернативных вкусов, новые рецептурные эксперименты и изменение ассортимента не входят в этап.

Плановые выходы и цены не становятся измерениями/гарантиями поставки. Нужно подтвердить свежий необработанный хрен, торговый центр/акции METRO, авторскую цену водки, единицы заказа и доставку; после производства внести измеренные выходы. Для молочного варианта требуются надлежащие условия хранения и проверка требований безопасности; наблюдение автора о 14 днях не является установленным сроком.

Первичная база, методика отзывов, ID и нормализация сохраняются. Предыдущие планы и A/B/C/B2 доступны в историческом архиве и прежних Git-коммитах. Текущая ветка analysis/menu-economics, PR №1 остаётся Draft; merge в main без отдельного прямого разрешения запрещён.
''',encoding="utf-8",newline="\n")


def make_notebook():
    import nbformat as n
    cells=[]
    def md(s):cells.append(n.v4.new_markdown_cell(s))
    def code(s):cells.append(n.v4.new_code_cell(s))
    md('''# Nastoyka: запуск бара, 60 л и экономика двух вечеров

Автор окончательно согласовал десять вкусов, составы, поставщиков, цены и план по 6 л (`author_selected_final`). План: по 60 порций каждого вкуса в пятницу и субботу, порция 50 мл. Это сценарий, а не подтверждённый спрос. Восемь основных по 250 ₽, две премиальные по 300 ₽. Исходные выходы автора оценочные; текущие статусы показаны в таблице. Деньги в представлении округлены до двух знаков, проценты до двух, количества до трёх; исходные CSV и расчёты не округляются. «Недоступно» означает отсутствие применимой цены или неприменимое поле; причина указана в статусах.

Предыдущие этапы исследования рынка и A/B/C/B2 сохранены [в истории](data/economics/history/README.md); текущий расчёт не импортирует старые дозировки и цены. [Отчёт](docs/economics_report.md) · [Авторское задание](docs/author_specification.md) · [Методика](docs/economics_methodology.md).''')
    code('''from pathlib import Path
import json, hashlib
import pandas as pd
from IPython.display import display, Image, Markdown
from scripts import economics as e
ROOT = Path.cwd()
result = e.run()
inputs = e.load_inputs()
out = ROOT / "data/economics/results"
def read(name): return pd.read_csv(out/name)
def fig(name): display(Image(filename=str(ROOT/"figures/economics"/name)))
from scripts.report import notebook_table, fmt
def show(frame): display(notebook_table(frame))
print("Защищённых первичных файлов:", len(e.check_baseline()["protected_files_sha256"]))
show(pd.DataFrame([
    {"Показатель": label, "Значение, ₽": fmt(result[key])}
    for label,key in [("Первая закупка","purchase_cost_rub"),
                      ("Сырьё 60 л","used_cost_rub"),("Остаток сырья","leftover_value_rub"),
                      ("Выручка полной реализации","potential_revenue_rub"),
                      ("Доход после сырья","potential_material_contribution_rub")]
]))''')
    md('''## 1. Ассортимент и происхождение данных

Предшествующая база: 43 карточки заведений, текущий срез 30. Из 368 отзывов проверены 358; из 468 упоминаний подтверждены 198 конкретных, из 108 отзывов, 114 вкусов. Нормализация сохраняет исходное имя, объединяет явные синонимы и разделяет разные сочетания ингредиентов. Хреновуха и щавель: по семь упоминаний и пять положительных; у томатной шесть положительных из одного заведения, в строгом срезе одно. [Результаты сбора](docs/collection_report.md) · [Методика](docs/methodology.md) · [Вкусовые базы](data/processed/flavor_summary.csv). Целью изучения баров и рюмочных Петербурга было найти ассортиментные сигналы и причины конкретных оценок. Связь заведение → отзыв → упоминание сохраняет независимую базу: несколько вкусов из одного отзыва не превращаются в нескольких гостей. Явные синонимы нормализованы, составные вкусы сохранены отдельно; общая оценка заведения не перенесена на напитки. Публичные отзывы не равны продажам и не представляют всех гостей. Варианты A/B/C/B2 были промежуточными; десять вкусов и разделение 8+2 автор выбрал самостоятельно, а не на основании доказанного оптимума. Клюква сохранена прямым решением автора. Новые отзывы не собирались.

Текущие источники: ассортимент CSV, recipes.json, yields.csv, ingredients.csv и production_plan.json. Все составы author_supplied, а не прежние адаптации. Цены предоставлены автором; часть имеет только индексные каталожные ориентиры, не подтверждение магазина СПб.''')
    code('''show(pd.DataFrame(inputs[0]))
show(pd.DataFrame(inputs[2].values()))
show(pd.DataFrame(inputs[3].values())[["ingredient_id","name","supplier","pack_price_rub","pack_size_base","base_unit","price_source_type","confirmation_status","reference_url"]])''')
    md('''## 2. Производственный план и технологии

Каждому вкусу нужно 6000 мл. Коэффициент 6000 / выход исходной партии не округляется; исходные рецепты не округляются до целых партий. Выход уже включает фильтрацию. 40% — крепость водки, не готовой настойки. Технологии ниже воспроизводят данные автора; отсутствующие длительности операций не придуманы.''')
    code('''for row in inputs[0]:
    cid = row["candidate_id"]
    recipe = inputs[1][cid]
    display(Markdown("**" + cid + " — " + row["normalized_flavor"] + "**"))
    show(pd.DataFrame([{"ingredient_id": key, "quantity": value, "unit": inputs[3][key]["base_unit"]} for key,value in recipe["ingredients"].items()]))
    display(Markdown("\\n".join(str(i+1)+". "+s for i,s in enumerate(recipe["technology"]))))
print("C12: наблюдение автора 14 дней; подтверждённый безопасный срок хранения не установлен.")''')
    md('''C04 использует весь сок и жёлтую цедру 1900 г целых лимонов; вода отсутствует. C09 — свежие зелёные яблоки 525 г. C06 имеет консервативный выход 450 мл. C12 — 7 дней малиновой основы, отдельная нагретая без кипения и остуженная молочная основа, затем около двух дней в холодильнике. Наблюдение автора о 14 днях не подтверждает безопасность коммерческого хранения.

## 3. Относительный календарь

Пятница D0, суббота D+1. Максимальный известный цикл 30 дней. Ромбы/звезда обозначают операции с неизвестной длительностью, а не нулевое время. Конкретная дата запуска не назначена.''')
    code('''show(read("production_calendar.csv"))
fig("production_calendar.png")''')
    md('''## 4. Закупка целыми упаковками

Потребность общего ингредиента сначала суммируется по десяти вкусам. Только потом применяется округление упаковок вверх. Расход сырья, первый платёж и остаток различаются. Совместное округление и отдельные заказы сравниваются с учётом порога каждого заказа; при неизвестном тарифе экономия недоступна. В базовой закупке малины упаковочной экономии нет.''')
    code('''basket = read("procurement.csv")
show(read("ingredient_matrix.csv"))
show(basket[["ingredient_id","needed_quantity","base_unit","recipes_using","packs_to_buy","purchased_quantity","purchase_cost_rub","used_cost_rub","leftover_quantity","leftover_value_rub","joint_rounding_saving_rub"]])
show(read("suppliers.csv"))
fig("supplier_budget.png")
fig("used_vs_purchased.png")''')
    md('''Доставка новых трёх поставщиков и минимумы не установлены; они не равны нулю и не включены в сырьевой бюджет. Авторская ставка водки 380 ₽/л сохранена. Хрен в публичной карточке имеет противоречивый состав с уксусом и солью — нужен именно необработанный корень. Для METRO показаны лишь индексные ориентиры оптовых условий; текущая доступность и выбранный магазин требуют подтверждения.

## 5. Себестоимость и потенциальная выручка

Базовый план 60 л соответствует 1200 порциям. Все суммы вычислены Decimal без промежуточного округления. Материальная маржинальность не включает труд, аренду, налоги, оборудование, доставку и списания, поэтому не является чистой прибылью.''')
    code('''costs = read("recipe_costs.csv")
show(costs[["candidate_id","normalized_flavor","source_batch_cost_rub","planned_yield_ml","scale_factor","used_cost_rub","cost_50ml_rub","price_50ml_rub","material_margin_50ml_rub","material_margin_fraction","potential_revenue_rub","potential_material_contribution_rub"]])
fig("cost_50ml.png")
fig("cost_6l.png")
print("Агрегат до округления:", e.money(result["used_cost_rub"]))
print("Сумма показанных округлённых строк:", e.money(result["displayed_item_costs_sum_rub"]))''')
    md('''Контрольный бюджет 40 002,80 ₽ и разбивка по поставщикам сходятся точно. Сумма округлённых строк сырья 36 357,60 ₽; сумма до округления даёт 36 357,61 ₽. Поэтому вклад после сырья 275 642,39 ₽ и остаток 3 645,19 ₽ отличаются от авторских ориентиров на копейку. Формулы не подгонялись.

## 6. Частичная реализация

Во всех сценариях уже произведены все 60 л. Выросшая доля непроданного не уменьшает потраченные ингредиенты. Выручка только от проданных порций; стоимость сырья проданного, всех произведённых порций и остаток готовой продукции разделены.''')
    code('''show(read("sales_scenarios.csv"))
fig("sell_through.png")''')
    md('''## 7. Доля премиальных продаж и предел запаса

При 5/20/35% средние цены 252,50/260/267,50 ₽. Доли вкусов внутри уровня равны по допущению. Премиальных порций всего 240, поэтому 35% не совместимы с реализацией всех 1200 порций. Невыполнимые сочетания помечены и не получают выручку от отсутствующих порций.''')
    code('''mix = read("premium_mix.csv")
show(mix)
assert mix.loc[~mix.stock_feasible,"realized_revenue_if_feasible_rub"].isna().all()''')
    md('''## 8. Цены, выходы и округление упаковок

Изменение цен не является новой договорной котировкой. Изменение выхода сохраняет готовую цель 6000 мл каждого вкуса, пересчитывая расход и упаковки. Тариф проверяется после округления упаковок: ниже порога нужна обычная цена с источником и датой. Без неё затронутые финансовые значения пусты, а итог меню недоступен. В сценарии +10% выхода для вишни нужны две упаковки при пороге три; подтверждённой обычной цены нет. Дополнительная упаковка ради скидки не покупается. Измеренный выход вводится только в yields.csv, без правки кода.''')
    code('''show(read("price_sensitivity.csv").loc[lambda x: x.candidate_id.eq("ALL")])
show(read("yield_sensitivity.csv").loc[lambda x: x.candidate_id.eq("ALL")])
show(basket[["ingredient_id","packs_to_buy","separate_recipe_packs","joint_rounding_saving_rub"]])''')
    md('''## 9. Что нужно подтвердить

Поставка водки по авторской ставке, акции/торговый центр METRO, свежий необработанный хрен, единицы заказа Агропарка, доставка и минимумы остаются предметом согласования. Нужны надлежащие условия хранения молочного варианта и проверка требований пищевой безопасности. Измеренные выходы следует внести после производства. Новые рецептурные эксперименты в этот этап не включены.

60 порций каждого вкуса за вечер — план автора, а не доказанный спрос. Упаковочные остатки и непроданные напитки — разные риски. Высокая сырьевая маржинальность не устанавливает полную прибыльность бара. Состав сохранён; PR остаётся Draft, main не изменяется без отдельного разрешения.''')
    code('''assert e.check_baseline()
manifest = json.loads((out/"run_manifest.json").read_text())
for name,digest in manifest["inputs_sha256"].items():
    assert hashlib.sha256((ROOT/"data/economics"/name).read_bytes()).hexdigest() == digest
assert result["total_ready_ml"] == 60000 and result["total_portions"] == 1200
assert e.money(result["purchase_cost_rub"]) == e.money(sum(e.D(str(x)) for x in read("procurement.csv").purchase_cost_rub))
assert not result["optimizer_called"]
print("Текущая модель, 60 л, 1200 порций и защищённая база проверены. Тесты запускаются отдельно свежим pytest.")''')
    nb=n.v4.new_notebook(cells=cells,metadata={"kernelspec":{"name":"python3","display_name":"Python 3","language":"python"},"language_info":{"name":"python","version":"3.12"}})
    for i,cell in enumerate(nb.cells): cell.id=f"cell-{i:02d}"
    n.write(nb,e.ROOT/"assortment_economics.ipynb")


if __name__=="__main__":
    import argparse
    parser=argparse.ArgumentParser();parser.add_argument("--notebook",action="store_true");args=parser.parse_args()
    if args.notebook:make_notebook()
    else:e.run()
