#!/usr/bin/env python3
"""Append audited batches to the existing workbook; validate, summarize and export.

No web scraping. Input JSON contains human-reviewed facts, never search snippets.
Run `python scripts/workbook.py --help` from the repository root.
"""
import argparse
import copy
import csv
import hashlib
import json
import os
import re
import tempfile
import unicodedata
from collections import Counter, defaultdict
from datetime import date, datetime
from pathlib import Path
from urllib.parse import urlparse

from openpyxl import load_workbook
from openpyxl.styles import Alignment, Font, PatternFill

ROOT = Path(__file__).resolve().parents[1]
DEFAULT = ROOT / "data/nastoyki_reviews.xlsx"
VERIFIED = {"verified"}
STATUSES = {"pending", "verified", "unavailable", "ambiguous", "not_found", "contradicted", "secondary_only"}
SENTIMENTS = {"positive", "negative", "mixed", "neutral", "mention_only", "unclear"}
CATEGORIES = {"ягодная", "фруктовая", "цитрусовая", "травяная", "пряная", "десертная", "сливочная", "другая"}
SPECIFICITY = {"concrete", "category", "general", "ambiguous"}
EXTRA = {
    "Заведения": ["venue_id", "chain", "district", "menu_url", "tinctures_current_menu", "checked_at", "operating_status", "source_status", "operating_status_url", "sample_current", "reviews_verified", "review_page_url", "audit_note", "menu_status", "menu_checked_at"],
    "Отзывы": ["venue_id", "platform", "platform_review_id", "link_type", "checked_at", "source_status", "rating", "rating_scale", "source_locator", "evidence", "text_hash", "duplicate_of", "event_group", "provenance", "date_kind", "source_updated_at", "publication_date", "platform_verification", "audit_note", "text_completeness", "bias_flag"],
    "Авторы": ["source_status", "id_scope", "audit_note"],
    "Упоминания вкусов": ["venue_id", "raw_flavor", "normalized_flavor", "flavor_category", "specificity", "sentiment", "evidence", "confidence", "source_status", "checked_at", "audit_note"],
    "Меню - предыдущий сбор": ["menu_id", "venue_id", "normalized_flavor", "price_50ml", "checked_at", "source_status", "audit_note", "product_type"],
}
DICT_HEADERS = ["normalized_flavor", "main_ingredient", "additional_ingredients", "category", "creamy_base", "combination_preliminary", "aliases", "note"]
SOURCE_HEADERS = ["source_id", "batch", "venue_id", "url", "checked_at", "purpose", "result", "records_scanned", "note"]


def rows(ws):
    headers = [c.value for c in ws[1]]
    return [dict(zip(headers, r)) for r in ws.iter_rows(min_row=2, values_only=True) if any(v is not None for v in r)]


def append(ws, record):
    unknown = set(record) - {c.value for c in ws[1]}
    if unknown:
        raise ValueError(f"{ws.title}: unknown columns {sorted(unknown)}")
    # Prevent an imported review/summary from becoming an Excel formula.
    values = []
    for c in ws[1]:
        v = record.get(c.value)
        if isinstance(v, str) and v.startswith(("=", "+", "@")):
            v = "'" + v
        values.append(v)
    ws.append(values)


def patch_row(ws, key, value, changes):
    headers = {c.value: c.column for c in ws[1]}
    if set(changes) - set(headers):
        raise ValueError(f"{ws.title}: unknown patch columns {set(changes) - set(headers)}")
    found = []
    for i in range(2, ws.max_row + 1):
        if ws.cell(i, headers[key]).value == value:
            found.append(i)
    if len(found) != 1:
        raise ValueError(f"{ws.title}: expected one {key}={value}, found {len(found)}")
    for k, v in changes.items():
        if isinstance(v, str) and v.startswith(("=", "+", "@")):
            v = "'" + v
        ws.cell(found[0], headers[k], v)
        if v is None:
            ws.cell(found[0], headers[k]).value = None


def next_id(records, key, prefix):
    n = max((int(m.group(1)) for r in records if (m := re.fullmatch(re.escape(prefix) + r"(\d+)", str(r.get(key) or "")))), default=0)
    return f"{prefix}{n + 1:03d}"


def platform(url):
    host = urlparse(url or "").hostname or ""
    if "restoclub" in host: return "Restoclub"
    if "2gis" in host: return "2ГИС"
    if "yandex" in host: return "Яндекс Карты"
    if "tripadvisor" in host: return "Tripadvisor"
    if "flamp" in host: return "Flamp"
    if "href.ru" in host: return "href.ru (перепубликация)"
    if "tbank.ru" in host: return "Т-Банк Отзывы"
    return host or "unknown"


def text_hash(text):
    text = re.sub(r"\W+", " ", text.casefold().replace("ё", "е")).strip()
    return hashlib.sha256(text.encode()).hexdigest()


def source_key(r):
    """Stable fallback for manually curated list pages without a public review ID."""
    if r.get("platform_review_id"):
        return (r.get("platform"), r["platform_review_id"])
    return (r.get("venue_id"), r.get("platform"), r.get("URL"), r.get("source_locator"))


def alias_key(value):
    value = unicodedata.normalize("NFKC", str(value)).casefold().replace("ё", "е")
    value = re.sub(r"\s*[-‐‑–—]\s*", " - ", value)
    return re.sub(r"\s+", " ", value.strip(' \"«»'))


def normalize_flavor(raw, dictionary):
    """Resolve only explicit dictionary aliases. Never merge by shared ingredients."""
    found = set()
    for item in dictionary:
        aliases = [item["normalized_flavor"]] + str(item.get("aliases") or "").split(" | ")
        if any(alias_key(a) == alias_key(raw) for a in aliases if a):
            found.add(item["normalized_flavor"])
    if len(found) > 1:
        raise ValueError(f"Ambiguous flavor alias {raw!r}: {sorted(found)}")
    if not found:
        raise ValueError(f"Unknown flavor {raw!r}: supply normalized_flavor explicitly and update the dictionary")
    return found.pop()


def sentiment_legacy(v):
    return {"положительно": "positive", "отрицательно": "negative", "смешанно": "mixed", "нейтрально": "neutral", "неясно": "unclear"}.get(v, "mention_only")


def flavor_meta(flavor):
    """Conservative fallback: category only; no invented recipes/cream bases."""
    f = (flavor or "").casefold()
    category = "другая"
    for cat, words in [
        ("сливочная", ["сливк", "сливоч", "мороженое"]),
        ("десертная", ["карамел", "шоколад", "ириск", "крем-брюле", "пирог", "сгущ", "печень", "рафаэл", "нутел"]),
        ("ягодная", ["вишн", "малин", "смородин", "клюкв", "брусник", "облепих", "черник", "клубник", "ежевик", "рябин", "черноплод", "бузин"]),
        ("цитрусовая", ["лимон", "апельсин", "мандарин", "грейпфрут", "кумкват", "лайм"]),
        ("фруктовая", ["банан", "манго", "ананас", "киви", "груш", "яблок", "слив", "маракуй", "арбуз", "дын", "абрикос", "персик"]),
        ("травяная", ["щавел", "укроп", "кинз", "ревен", "тархун", "зубров", "шишк", "лемонграс", "мят"]),
        ("пряная", ["кориц", "перц", "хрен", "прян", "гвоздик", "имбир", "кардамон"]),
    ]:
        if any(x in f for x in words):
            category = cat
            break
    cream = "yes" if "сливк" in f or "сливоч" in f else "unknown"
    combination = "combination" if " — " in (flavor or "") else "undetermined"
    parts = (flavor or "").split(" — ")
    return {"category": category, "creamy_base": cream, "combination_preliminary": combination,
            "main_ingredient": parts[0] if len(parts) > 1 else None,
            "additional_ingredients": "; ".join(parts[1:]) if len(parts) > 1 else None,
            "aliases": flavor,
            "note": "Категория и компоненты предварительно по названию, не подтверждённый состав или рецепт."}


def migrate(wb):
    if "venue_id" in [c.value for c in wb["Заведения"][1]]:
        for sheet, names in EXTRA.items():
            existing = {c.value for c in wb[sheet][1]}
            for name in names:
                if name not in existing: wb[sheet].cell(1, wb[sheet].max_column + 1, name)
        return
    for sheet, names in EXTRA.items():
        ws = wb[sheet]
        for name in names:
            ws.cell(1, ws.max_column + 1, name)
    for sheet, headers in [("Справочник вкусов", DICT_HEADERS), ("Источники", SOURCE_HEADERS)]:
        wb.create_sheet(sheet).append(headers)
    venues = rows(wb["Заведения"])
    aliases = {r["Заведение / филиал"]: f'V{r["ID"]:03d}' for r in venues}
    aliases.update({"Твоя остановочка (Невский)": "V001", "Room 13": "V006"})
    for v in venues:
        i = v["ID"]
        patch_row(wb["Заведения"], "ID", i, {
            "venue_id": f"V{i:03d}", "source_status": "pending", "operating_status": "unknown",
            "sample_current": "no", "tinctures_current_menu": "unknown",
            "review_page_url": v["Ссылка для проверки"],
            "audit_note": "Исходный кандидат; работа и актуальное меню требуют проверки."})
    for r in rows(wb["Отзывы"]):
        pub = r["Дата"] if re.fullmatch(r"\d{4}-\d{2}-\d{2}", str(r["Дата"])) else None
        patch_row(wb["Отзывы"], "ID отзыва", r["ID отзыва"], {
            "venue_id": aliases[r["Заведение"]], "platform": platform(r["URL"]),
            "source_status": "pending", "link_type": "repost" if "href.ru" in r["URL"] else "list",
            "provenance": "legacy", "date_kind": "legacy_unverified", "publication_date": pub,
            "Дата": pub, "platform_verification": "unknown",
            "audit_note": "Импортировано из v1; отдельный отзыв не подтверждён повторно."})
    for a in rows(wb["Авторы"]):
        patch_row(wb["Авторы"], "ID автора*", a["ID автора*"], {
            "source_status": "pending", "id_scope": "review_participant",
            "Пол: прямое сообщение": "неизвестно", "Возраст: прямое сообщение": "неизвестно",
            "Местный / приезжий": "неизвестно", "Основание из текста": None,
            "audit_note": "ID участника отзыва из v1; уникальность человека не установлена. Демография v1 не подтверждена."})
    reviews = {r["ID отзыва"]: r for r in rows(wb["Отзывы"])}
    for m in rows(wb["Упоминания вкусов"]):
        f = m["Вкус / категория"]
        specificity = "general" if f == "Не назван" else "category" if "без уточнения" in f else "ambiguous" if "неяс" in f or m["Оценка именно вкуса"] == "неясно" else "concrete"
        normalized = f if specificity == "concrete" else None
        patch_row(wb["Упоминания вкусов"], "ID", m["ID"], {
            "venue_id": reviews[m["ID отзыва"]]["venue_id"], "normalized_flavor": normalized,
            "flavor_category": flavor_meta(f)["category"], "specificity": specificity,
            "sentiment": sentiment_legacy(m["Оценка именно вкуса"]), "confidence": "low",
            "source_status": "pending", "Дата": reviews[m["ID отзыва"]]["Дата"],
            "audit_note": "Исходная разметка сохранена; исходное написание в отзыве ещё не проверено."})
    for i, m in enumerate(rows(wb["Меню - предыдущий сбор"]), 1):
        patch_row(wb["Меню - предыдущий сбор"], "Вкус как в меню", m["Вкус как в меню"], {
            "menu_id": f"N{i:03d}", "venue_id": aliases[m["Заведение"]], "normalized_flavor": m["Вкус как в меню"],
            "source_status": "pending", "audit_note": "Цена из v1; актуальность требует повторной проверки."})
    wb["Методика"].append(["Схема v2", "Исходные 9 листов сохранены; добавлены машинные поля, Справочник вкусов и Источники. См. docs/methodology.md."])


def verified_reviews(wb):
    return [r for r in rows(wb["Отзывы"]) if r["source_status"] in VERIFIED and not r["duplicate_of"]]


def refresh(wb):
    vr = verified_reviews(wb)
    valid_ids = {r["ID отзыва"] for r in vr}
    all_mentions = rows(wb["Упоминания вкусов"])
    vm = [m for m in all_mentions if m["ID отзыва"] in valid_ids and m["source_status"] in VERIFIED]
    concrete = [m for m in vm if m["specificity"] == "concrete"]
    venue_map = {v["venue_id"]: v for v in rows(wb["Заведения"])}
    review_map = {r["ID отзыва"]: r for r in vr}
    conservative = [m for m in concrete if m["confidence"] == "high"
                    and venue_map[m["venue_id"]]["sample_current"] == "yes"
                    and review_map[m["ID отзыва"]]["platform_verification"] != "not_passed"
                    and not review_map[m["ID отзыва"]].get("bias_flag")]
    counts = Counter(r["venue_id"] for r in vr)
    totals = Counter(r["venue_id"] for r in rows(wb["Отзывы"]))
    for v in rows(wb["Заведения"]):
        patch_row(wb["Заведения"], "venue_id", v["venue_id"], {"reviews_verified": counts[v["venue_id"]], "Отзывов разобрано": totals[v["venue_id"]]})
    known = {r["normalized_flavor"] for r in rows(wb["Справочник вкусов"])}
    flavors = {m["normalized_flavor"] for m in all_mentions if m["normalized_flavor"]}
    flavors |= {m["normalized_flavor"] for m in rows(wb["Меню - предыдущий сбор"]) if m["normalized_flavor"]}
    for f in sorted(flavors - known):
        append(wb["Справочник вкусов"], {"normalized_flavor": f, **flavor_meta(f)})
    ws = wb["Сводка вкусов"]
    ws.delete_rows(1, ws.max_row)
    ws.append(["Вкус", "Проверенные упоминания", "Положительные", "Отрицательные", "Смешанные", "Нейтральные", "Просто упоминание", "Уникальные отзывы", "Заведения", "База явно оценённых", "Доля положительных среди явно оценённых", "Неподтверждённые упоминания", "Примечание", "Строгий текущий срез: база", "Строгий текущий срез: положительные"])
    summary = []
    for f in sorted({m["normalized_flavor"] for m in all_mentions if m["specificity"] == "concrete" and m["normalized_flavor"]}):
        mm = [m for m in concrete if m["normalized_flavor"] == f]
        # One source review/event contributes at most one vote per flavor.
        rmap = {r["ID отзыва"]: r for r in vr}
        dedup = {}
        for m in mm:
            r = rmap[m["ID отзыва"]]
            key = r["event_group"] or r["ID отзыва"]
            if key in dedup and dedup[key]["sentiment"] != m["sentiment"]:
                dedup[key] = {**m, "sentiment": "mixed"}
            else: dedup[key] = m
        sc = Counter(m["sentiment"] for m in dedup.values())
        base = sum(sc[k] for k in ("positive", "negative", "mixed", "neutral"))
        pending = sum(m["normalized_flavor"] == f and (m["source_status"] not in VERIFIED or m["ID отзыва"] not in valid_ids) for m in all_mentions)
        strict = [m for m in conservative if m["normalized_flavor"] == f]
        values = [f, len(mm), sc["positive"], sc["negative"], sc["mixed"], sc["neutral"], sc["mention_only"], len({m["ID отзыва"] for m in mm}), len({m["venue_id"] for m in mm}), base, sc["positive"] / base if base else None, pending, "Публичные мнения; не продажи. Исторические отзывы включены, текущий рынок фильтровать отдельно.", len(strict), sum(m["sentiment"] == "positive" for m in strict)]
        ws.append(values)
        summary.append(dict(zip([c.value for c in ws[1]], values)))
    summary_stats = {
        "venues_total": len(rows(wb["Заведения"])),
        "venues_examined": sum(bool(v["checked_at"]) for v in rows(wb["Заведения"])),
        "venues_verified_open": sum(v["operating_status"] == "open" and v["source_status"] == "verified" for v in rows(wb["Заведения"])),
        "current_sample": sum(v["sample_current"] == "yes" for v in rows(wb["Заведения"])),
        "reviews_total": len(rows(wb["Отзывы"])), "reviews_verified_unique": len(vr),
        "reviews_unverified": sum(r["source_status"] not in VERIFIED for r in rows(wb["Отзывы"])),
        "duplicate_rows": sum(bool(r["duplicate_of"]) for r in rows(wb["Отзывы"])),
        "mentions_total": len(all_mentions), "mentions_verified": len(vm), "concrete_mentions_verified": len(concrete),
        "normalized_flavors_verified": len({m["normalized_flavor"] for m in concrete}),
        "concrete_sentiment": dict(Counter(m["sentiment"] for m in concrete)),
        "reviews_by_platform_total": dict(Counter(r["platform"] for r in rows(wb["Отзывы"]))),
        "reviews_by_platform_verified": dict(Counter(r["platform"] for r in vr)),
        "menu_total": len(rows(wb["Меню - предыдущий сбор"])),
        "menu_verified": sum(m["source_status"] == "verified" for m in rows(wb["Меню - предыдущий сбор"])),
        "reviews_with_concrete_flavors": len({m["ID отзыва"] for m in concrete}),
        "verified_reviews_with_platform_warning": sum(r["platform_verification"] == "not_passed" for r in vr),
        "verified_reviews_by_date_kind": dict(Counter(r["date_kind"] for r in vr)),
        "verified_reviews_by_text_completeness": dict(Counter(r.get("text_completeness") or "unknown" for r in vr)),
        "known_residency": sum(a["Местный / приезжий"] != "неизвестно" and a["source_status"] == "verified" for a in rows(wb["Авторы"])),
        "known_gender": sum(a["Пол: прямое сообщение"] != "неизвестно" and a["source_status"] == "verified" for a in rows(wb["Авторы"])),
        "known_age": sum(a["Возраст: прямое сообщение"] != "неизвестно" and a["source_status"] == "verified" for a in rows(wb["Авторы"])),
        "concrete_mentions_by_confidence": dict(Counter(m["confidence"] for m in concrete)),
        "current_sample_review_count": sum(venues_r["sample_current"] == "yes" for r in vr for venues_r in rows(wb["Заведения"]) if venues_r["venue_id"] == r["venue_id"]),
        "verified_reviews_with_bias_flag": sum(bool(r.get("bias_flag")) for r in vr),
        "conservative_concrete_mentions": len(conservative),
        "conservative_reviews_with_concrete_flavors": len({m["ID отзыва"] for m in conservative}),
    }
    # Preserve the original overview layout; place the audited counters below it.
    overview = wb["Обзор"]
    for row, label, value, note in [
        (6, "Всего сохранённых отзывов", summary_stats["reviews_total"], "Проверенные и неподтверждённые разделяются по source_status"),
        (7, "Всего строк упоминаний", len(all_mentions), "Конкретные вкусы, категории и общие упоминания раздельно"),
        (8, "Проверенные положительные оценки конкретного вкуса", sum(m["sentiment"] == "positive" for m in concrete), "Не число покупателей и не продажи"),
        (9, "Заведений с проверенными отзывами", len(counts), "Сюда входят исторические локации; текущий срез отдельно"),
        (10, "Заведений в текущей выборке", summary_stats["current_sample"], "Работа по публичной карточке; текущие позиции меню могут быть не подтверждены"),
        (11, "Известный статус приезжего / местного", summary_stats["known_residency"], "Только прямое сообщение; остальных считать неизвестными"),
        (12, "Известный пол или возраст", summary_stats["known_gender"] + summary_stats["known_age"], "Никаких выводов по имени, фото или грамматике"),
    ]:
        overview.cell(row, 1).value, overview.cell(row, 2).value, overview.cell(row, 3).value = label, value, note
    for row in range(24, max(60, overview.max_row + 1)):
        for col in range(1, 3): overview.cell(row, col).value = None
    overview.cell(24, 1, "Проверенные показатели v2 (обновляются скриптом)")
    for i, (k, v) in enumerate(summary_stats.items(), 25):
        overview.cell(i, 1, k)
        overview.cell(i, 2, json.dumps(v, ensure_ascii=False) if isinstance(v, dict) else v)
    return summary_stats, summary


def validate(wb):
    errors, warnings = [], []
    tables = {s: rows(wb[s]) for s in EXTRA}
    keys = {"Заведения": "venue_id", "Отзывы": "ID отзыва", "Авторы": "ID автора*", "Упоминания вкусов": "ID", "Меню - предыдущий сбор": "menu_id"}
    for sheet, records in tables.items():
        counter = Counter(r.get(keys[sheet]) for r in records)
        for key, n in counter.items():
            if not key or n > 1: errors.append(f"{sheet}: missing/duplicate {keys[sheet]} {key!r} (n={n})")
        for r in records:
            ident = r.get(keys[sheet])
            status = r.get("source_status")
            if status not in STATUSES: errors.append(f"{sheet} {ident}: invalid source_status {status}")
            if status != "verified": warnings.append(f"{sheet} {ident}: source {status}; excluded from verified summaries")
            for field in ("checked_at", "publication_date", "Дата"):
                v = r.get(field)
                if v is None: continue
                try:
                    d = date.fromisoformat(str(v))
                    if d > date.today(): raise ValueError("future")
                except ValueError: errors.append(f"{sheet} {ident}: invalid/future {field} {v}")
    venues = {v["venue_id"]: v for v in tables["Заведения"]}
    reviews = {r["ID отзыва"]: r for r in tables["Отзывы"]}
    authors = {a["ID автора*"]: a for a in tables["Авторы"]}
    for vid, v in venues.items():
        if v["operating_status"] not in {"open", "closed", "temporarily_closed", "unknown"}: errors.append(f"{vid}: invalid operating status")
        if v["tinctures_current_menu"] not in {"yes", "no", "unknown"}: errors.append(f"{vid}: invalid menu presence")
        if v["sample_current"] not in {"yes", "no"}: errors.append(f"{vid}: invalid sample flag")
        if v["sample_current"] == "yes" and (v["operating_status"] != "open" or v["source_status"] != "verified"):
            errors.append(f"{vid}: unverified/closed venue in current sample")
    for sheet in ("Отзывы", "Упоминания вкусов", "Меню - предыдущий сбор"):
        for r in tables[sheet]:
            if r["venue_id"] not in venues: errors.append(f"{sheet}: broken venue_id {r['venue_id']}")
            u = r.get("URL")
            if not u or urlparse(u).scheme not in {"http", "https"}: errors.append(f"{sheet}: missing/invalid URL {r.get(keys[sheet])}")
    fingerprints, hashes, source_keys = {}, {}, {}
    for r in tables["Отзывы"]:
        rid = r["ID отзыва"]
        a = r["ID автора*"]
        if a and (a not in authors or authors[a]["ID отзыва"] != rid): errors.append(f"{rid}: author link broken")
        if r["duplicate_of"] and (r["duplicate_of"] not in reviews or r["duplicate_of"] == rid): errors.append(f"{rid}: invalid duplicate_of")
        if r["link_type"] not in {"direct", "list", "repost"}: errors.append(f"{rid}: invalid link_type")
        if r["source_status"] == "verified" and not (r["checked_at"] and r["source_locator"]): errors.append(f"{rid}: verified without check date/locator")
        if r.get("text_completeness") not in {None, "full", "visible_excerpt", "unknown"}: errors.append(f"{rid}: invalid text completeness")
        if r.get("date_kind") not in {"legacy_unverified", "displayed_full", "displayed_partial", "displayed_or_updated", "publication", "relative_unknown", "unknown"}: errors.append(f"{rid}: invalid date kind")
        if r["rating"] is not None:
            if not isinstance(r["rating"], (int, float)) or not r["rating_scale"] or not 0 <= r["rating"] <= r["rating_scale"]: errors.append(f"{rid}: invalid rating")
        for key, bucket in [((r["platform"], r["platform_review_id"]) if r["platform_review_id"] else None, fingerprints), (r["text_hash"], hashes)]:
            if key:
                if key in bucket and not r["duplicate_of"]: errors.append(f"{rid}: duplicate source/text with {bucket[key]}")
                bucket[key] = rid
        skey = source_key(r)
        if r.get("source_locator") and skey in source_keys and not r["duplicate_of"]:
            errors.append(f"{rid}: repeated source locator with {source_keys[skey]}")
        source_keys[skey] = rid
    seen = set()
    for m in tables["Упоминания вкусов"]:
        mid, rid = m["ID"], m["ID отзыва"]
        if rid not in reviews: errors.append(f"{mid}: broken review_id {rid}")
        elif m["venue_id"] != reviews[rid]["venue_id"]: errors.append(f"{mid}: venue differs from review")
        if m["sentiment"] not in SENTIMENTS: errors.append(f"{mid}: invalid sentiment")
        if m["flavor_category"] not in CATEGORIES: errors.append(f"{mid}: invalid category")
        if m["specificity"] not in SPECIFICITY: errors.append(f"{mid}: invalid specificity")
        if m["confidence"] not in {"high", "medium", "low"}: errors.append(f"{mid}: invalid confidence")
        if m["specificity"] == "concrete" and not m["normalized_flavor"]: errors.append(f"{mid}: missing normalized flavor")
        if m["specificity"] != "concrete" and m["normalized_flavor"]: errors.append(f"{mid}: category/general entered into flavor dictionary")
        if m["source_status"] == "verified":
            if rid in reviews and reviews[rid]["source_status"] != "verified": errors.append(f"{mid}: verified mention on unverified review")
            if not m["checked_at"]: errors.append(f"{mid}: verified without check date")
            if m["sentiment"] in {"positive", "negative", "mixed", "neutral"} and not (m["evidence"] or m["Пояснение"]): errors.append(f"{mid}: explicit judgment without evidence/reason")
        key = (rid, m["normalized_flavor"] or m["Вкус / категория"])
        if key in seen: errors.append(f"{mid}: repeated flavor in one review {key}")
        seen.add(key)
    for a in tables["Авторы"]:
        rid = a["ID отзыва"]
        if rid not in reviews or reviews[rid]["ID автора*"] != a["ID автора*"]: errors.append(f"{a['ID автора*']}: reverse author link broken")
        known = any(a[k] not in {None, "неизвестно"} for k in ["Пол: прямое сообщение", "Возраст: прямое сообщение", "Местный / приезжий"])
        if known and (a["source_status"] != "verified" or not a["Основание из текста"]): errors.append(f"{a['ID автора*']}: demographic value without self-report evidence")
    for m in tables["Меню - предыдущий сбор"]:
        volume, price = m["Объем, мл"], m["Цена, ₽"]
        if volume is not None and (not isinstance(volume, (int, float)) or volume <= 0): errors.append(f"{m['menu_id']}: invalid volume")
        expected = round(price * 50 / volume, 2) if isinstance(price, (int, float)) and isinstance(volume, (int, float)) and volume > 0 else None
        if m["price_50ml"] != expected: errors.append(f"{m['menu_id']}: incorrect/made-up price_50ml")
        if m["source_status"] == "verified" and not m["checked_at"]: errors.append(f"{m['menu_id']}: verified without date")
    dictionary = rows(wb["Справочник вкусов"])
    dc = Counter(d["normalized_flavor"] for d in dictionary)
    for flavor, n in dc.items():
        if not flavor or n > 1: errors.append(f"dictionary: missing/duplicate flavor {flavor!r}")
    for d in dictionary:
        if d["category"] not in CATEGORIES: errors.append(f"dictionary: invalid category {d['normalized_flavor']}")
    for m in tables["Упоминания вкусов"] + tables["Меню - предыдущий сбор"]:
        if m["normalized_flavor"] and m["normalized_flavor"] not in dc: errors.append(f"dictionary: absent flavor {m['normalized_flavor']}")
    return {"errors": errors, "warnings": warnings, "error_count": len(errors), "warning_count": len(warnings)}


def add_batch(wb, batch):
    """Transaction is committed only after validation; repeated source IDs are skipped."""
    receipt = {"added_reviews": 0, "skipped_duplicate_reviews": 0, "added_mentions": 0, "added_menu": 0, "skipped_duplicate_menu": 0}
    for v in batch.get("venues", []):
        if v.get("venue_id"):
            vid = v["venue_id"]
            patch_row(wb["Заведения"], "venue_id", vid, v)
        else:
            existing = rows(wb["Заведения"])
            vid = next_id(existing, "venue_id", "V")
            append(wb["Заведения"], {"ID": int(vid[1:]), "venue_id": vid, "source_status": "pending", "operating_status": "unknown", "sample_current": "no", "tinctures_current_menu": "unknown", **v})
    for item in batch.get("patches", []):
        patch_row(wb[item["sheet"]], item["key"], item["id"], item["values"])
    for item in batch.get("dictionary", []):
        f = item["normalized_flavor"]
        if any(r["normalized_flavor"] == f for r in rows(wb["Справочник вкусов"])):
            patch_row(wb["Справочник вкусов"], "normalized_flavor", f, item)
        else: append(wb["Справочник вкусов"], item)
    for item in batch.get("reviews", []):
        item = copy.deepcopy(item)
        if item.get("source_status") == "verified" and not (item.get("checked_at") and item.get("source_locator")):
            raise ValueError("Verified input review requires checked_at and source_locator")
        mentions = item.pop("mentions", [])
        author = item.pop("author", None)
        full_text = item.pop("full_text", None)
        if full_text: item["text_hash"] = text_hash(full_text)
        existing = rows(wb["Отзывы"])
        pid, plat = item.get("platform_review_id"), item.get("platform")
        dupe = next((r for r in existing if source_key(r) == source_key(item) or (item.get("text_hash") and r["venue_id"] == item["venue_id"] and r["text_hash"] == item["text_hash"])), None)
        if dupe:
            receipt["skipped_duplicate_reviews"] += 1
            continue
        rid = next_id(existing, "ID отзыва", "R")
        item.update({"ID отзыва": rid, "provenance": "new", "platform_verification": item.get("platform_verification", "unknown")})
        if author is not None:
            aid = next_id(rows(wb["Авторы"]), "ID автора*", "P")
            item["ID автора*"] = aid
            append(wb["Авторы"], {"ID автора*": aid, "ID отзыва": rid, "Пол: прямое сообщение": "неизвестно", "Возраст: прямое сообщение": "неизвестно", "Местный / приезжий": "неизвестно", "Метод разметки": "Только прямые сообщения; ID в пределах одного отзыва, не уникальный человек.", "URL": item["URL"], "source_status": item["source_status"], "id_scope": "review_participant", **author})
        append(wb["Отзывы"], item)
        receipt["added_reviews"] += 1
        for m in mentions:
            if m.get("specificity") == "concrete" and not m.get("normalized_flavor"):
                m["normalized_flavor"] = normalize_flavor(m.get("raw_flavor"), rows(wb["Справочник вкусов"]))
            m = {"ID": next_id(rows(wb["Упоминания вкусов"]), "ID", "M"), "ID отзыва": rid, "venue_id": item["venue_id"], "Заведение": item["Заведение"], "Дата": item.get("Дата"), "URL": item["URL"], "source_status": item["source_status"], "checked_at": item["checked_at"], **m}
            append(wb["Упоминания вкусов"], m)
            receipt["added_mentions"] += 1
    for m in batch.get("menu", []):
        if m.get("menu_id"):
            patch_row(wb["Меню - предыдущий сбор"], "menu_id", m["menu_id"], m)
        else:
            def menu_key(item):
                return tuple(item.get(k) for k in ("venue_id", "Вкус как в меню", "Цена, ₽", "Объем, мл", "URL", "checked_at"))
            if any(menu_key(old) == menu_key(m) for old in rows(wb["Меню - предыдущий сбор"])):
                receipt["skipped_duplicate_menu"] += 1
                continue
            append(wb["Меню - предыдущий сбор"], {"menu_id": next_id(rows(wb["Меню - предыдущий сбор"]), "menu_id", "N"), **m})
            receipt["added_menu"] += 1
    for s in batch.get("sources", []):
        prior = rows(wb["Источники"])
        if not any(r["batch"] == batch["batch"] and r["url"] == s["url"] and r["venue_id"] == s.get("venue_id") and r["purpose"] == s["purpose"] for r in prior):
            append(wb["Источники"], {"source_id": next_id(prior, "source_id", "S"), "batch": batch["batch"], **s})
    return receipt


def style(wb):
    for ws in wb:
        if ws.title in {"Обзор", "Матрица 20 - заготовка", "Методика"}: continue
        ws.freeze_panes = "B2"
        ws.auto_filter.ref = ws.dimensions
        for c in ws[1]:
            c.font = Font(color="FFFFFF", bold=True)
            c.fill = PatternFill("solid", fgColor="253E50")
            c.alignment = Alignment(wrap_text=True, vertical="top")
        ws.row_dimensions[1].height = 45
        for column in ws.columns:
            header = str(column[0].value or "")
            ws.column_dimensions[column[0].column_letter].width = 42 if any(x in header.casefold() for x in ["url", "ссыл", "поясн", "содер", "audit", "evidence", "note"]) else 23
        for row in ws.iter_rows(min_row=2):
            for c in row: c.alignment = Alignment(wrap_text=True, vertical="top")


def save_atomic(wb, path):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(suffix=".xlsx", dir=path.parent)
    os.close(fd)
    try:
        wb.save(tmp)
        load_workbook(tmp).close()
        os.replace(tmp, path)
    finally:
        if os.path.exists(tmp): os.unlink(tmp)


def export(wb, directory, stats, report):
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    names = {"Заведения": "venues", "Отзывы": "reviews", "Авторы": "authors", "Упоминания вкусов": "mentions", "Меню - предыдущий сбор": "menu", "Справочник вкусов": "flavors", "Источники": "sources", "Сводка вкусов": "flavor_summary"}
    for sheet, fname in names.items():
        with (directory / f"{fname}.csv").open("w", encoding="utf-8-sig", newline="") as f:
            writer = csv.writer(f)
            for row in wb[sheet].values: writer.writerow(["" if v is None else v for v in row])
    (directory / "quality_report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n")
    (directory / "summary.json").write_text(json.dumps(stats, ensure_ascii=False, indent=2) + "\n")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=["migrate", "add", "refresh", "validate"])
    parser.add_argument("--workbook", type=Path, default=DEFAULT)
    parser.add_argument("--batch", type=Path)
    parser.add_argument("--export-dir", type=Path, default=ROOT / "data/processed")
    args = parser.parse_args()
    wb = load_workbook(args.workbook)
    migrate(wb)
    receipt = None
    if args.command == "add":
        if not args.batch: parser.error("add requires --batch")
        receipt = add_batch(wb, json.loads(args.batch.read_text()))
    stats, _ = refresh(wb)
    report = validate(wb)
    print(json.dumps({"receipt": receipt, "stats": stats, "quality": {"errors": report["errors"], "warnings": report["warning_count"]}}, ensure_ascii=False, indent=2))
    if report["errors"]: raise SystemExit(1)
    if args.command != "validate":
        style(wb)
        save_atomic(wb, args.workbook)
        export(wb, args.export_dir, stats, report)


if __name__ == "__main__":
    main()
