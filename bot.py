#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# Bot de Telegram para gestionar series por TMDB
# Listas independientes por chat_id
# Persistencia REAL usando un volumen montado en /data

import os
import json, re, requests
from pathlib import Path
from datetime import datetime, date
from typing import Dict, List, Any
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.constants import ParseMode
from telegram.ext import (
    ApplicationBuilder, CommandHandler, CallbackQueryHandler,
    ContextTypes
)

# =============================
# VARIABLES DE ENTORNO
# =============================
TELEGRAM_BOT_TOKEN = os.getenv("BOT_TOKEN")
TMDB_API_KEY = os.getenv("TMDB_API_KEY")

if not TELEGRAM_BOT_TOKEN:
    raise RuntimeError("❌ Falta BOT_TOKEN")
if not TMDB_API_KEY:
    raise RuntimeError("❌ Falta TMDB_API_KEY")

# =============================
# BASE DE DATOS — PERSISTENTE
# =============================
DB_DIR = Path("/data")
DB_DIR.mkdir(parents=True, exist_ok=True)
DB_PATH = DB_DIR / "series_data.json"

TMDB_BASE = "https://api.themoviedb.org/3"
IMG_BASE = "https://image.tmdb.org/t/p/w500"
PAGE_SIZE = 10

# =============================
# BD
# =============================
def load_db() -> Dict[str, Any]:
    try:
        if DB_PATH.exists():
            raw = DB_PATH.read_text(encoding="utf-8")
            if not raw.strip():
                return {}
            data = json.loads(raw)
            return data if isinstance(data, dict) else {}
        return {}
    except:
        return {}

def save_db(db: Dict[str, Any]):
    DB_PATH.write_text(
        json.dumps(db, ensure_ascii=False, indent=2),
        encoding="utf-8"
    )

def ensure_chat(db, cid):
    if cid not in db:
        db[cid] = {"items": []}

def get_items(db, cid):
    ensure_chat(db, cid)
    return db[cid]["items"]

# =============================
# TMDB
# =============================
def tmdb_tv_details(tmdb_id: int) -> Dict:
    r = requests.get(
        f"{TMDB_BASE}/tv/{tmdb_id}",
        params={"api_key": TMDB_API_KEY, "language": "es-ES"},
        timeout=20,
    )
    r.raise_for_status()
    return r.json()

def tmdb_search_tv(q: str) -> Dict:
    r = requests.get(
        f"{TMDB_BASE}/search/tv",
        params={"api_key": TMDB_API_KEY, "language": "es-ES", "query": q},
        timeout=20,
    )
    r.raise_for_status()
    return r.json()

def parse_seasons_string(s: str) -> List[int]:
    return sorted({int(x) for x in re.findall(r"[sS](\d+)", s)})

def normalize(s: str) -> str:
    return re.sub(r"\s+", " ", s.strip().lower())

# =============================
# ESTADO / PROGRESO
# =============================
def is_future(dstr):
    if not dstr:
        return False
    try:
        return datetime.strptime(dstr, "%Y-%m-%d").date() > date.today()
    except:
        return False

def is_really_airing(details):
    ne = details.get("next_episode_to_air") or {}
    return ne.get("air_date") and is_future(ne["air_date"])

def emitted_season_numbers(details):
    emitted = set()
    today = date.today()

    for s in details.get("seasons", []):
        sn = s.get("season_number")
        ad = s.get("air_date")
        if sn and ad:
            try:
                if datetime.strptime(ad, "%Y-%m-%d").date() <= today:
                    emitted.add(sn)
            except:
                pass

    if is_really_airing(details):
        emitted.add(details["next_episode_to_air"]["season_number"])

    return sorted(emitted)

def mini_progress(emitted, completed, current):
    cset = set(completed)
    out = []
    for s in emitted:
        tick = "✅" if s in cset else "❌"
        if s == current:
            out.append(f"🟢 S{s} {tick}")
        else:
            out.append(f"S{s} {tick}")
    return " ".join(out)

def text_progress(emitted, completed):
    return "✅ Tenemos todo" if emitted and all(s in completed for s in emitted) else "❌ Falta por recopilar"

# =============================
# COMANDOS
# =============================
async def start(update, context):
    await update.message.reply_text(
        "📺 Bienvenido al bot de series.\n\n"
        "/add <ID> S1S2\n"
        "/add <Título> <Año?> S1S2\n"
        "/lista\n"
        "/borrar <ID|título>\n\n"
        "Cada usuario tiene su propia lista."
    )

async def add_series(update, context):
    db = load_db()
    cid = str(update.effective_chat.id)
    items = get_items(db, cid)
    args = context.args

    if not args:
        await update.message.reply_text("Uso: /add <ID> S1S2 o /add Título 2022 S1S2")
        return

    # Si es ID numérico
    if re.fullmatch(r"\d+", args[0]):
        tmdb_id = int(args[0])
        seasons = parse_seasons_string("".join(args[1:]))
        d = tmdb_tv_details(tmdb_id)
        title = d.get("name")
        year = d.get("first_air_date", "").split("-")[0]
    else:
        seasons_str = args[-1] if re.search(r"[sS]\d+", args[-1]) else ""
        if seasons_str:
            args = args[:-1]

        year = args[-1] if re.fullmatch(r"\d{4}", args[-1]) else None
        if year:
            args = args[:-1]

        title = " ".join(args)
        seasons = parse_seasons_string(seasons_str)

        res = tmdb_search_tv(title).get("results", [])
        if not res:
            await update.message.reply_text("No encontrado.")
            return

        if year:
            match = next((r for r in res if r.get("first_air_date", "").startswith(year)), res[0])
        else:
            match = res[0]

        tmdb_id = match["id"]
        title = match["name"]
        year = match.get("first_air_date", "").split("-")[0]

    # Actualizar si ya existe
    for it in items:
        if int(it["tmdb_id"]) == tmdb_id:
            it["completed"] = sorted(set(it["completed"] + seasons))
            save_db(db)
            await update.message.reply_text(f"Actualizada: {title}")
            return

    # Añadir nueva
    items.append({"tmdb_id": tmdb_id, "title": title, "year": year, "completed": seasons})
    save_db(db)
    await update.message.reply_text(f"Añadida: {title}")

async def borrar(update, context):
    db = load_db()
    cid = str(update.effective_chat.id)
    items = get_items(db, cid)
    query = normalize(" ".join(context.args))

    new = [i for i in items if not (query == str(i["tmdb_id"]) or normalize(i["title"]) == query)]

    if len(new) != len(items):
        db[cid]["items"] = new
        save_db(db)
        await update.message.reply_text("🗑️ Eliminada.")
    else:
        await update.message.reply_text("No encontrada.")

def make_keyboard(total, page):
    rows = []
    row = []
    start = page * PAGE_SIZE
    end = min(start + PAGE_SIZE, total)

    for i in range(start, end):
        row.append(InlineKeyboardButton(str(i+1), callback_data=f"show:{i}"))
        if len(row) == 5:
            rows.append(row)
            row = []
    if row:
        rows.append(row)

    nav = []
    if page > 0:
        nav.append(InlineKeyboardButton("⬅️", callback_data=f"page:{page-1}"))
    if end < total:
        nav.append(InlineKeyboardButton("➡️", callback_data=f"page:{page+1}"))
    if nav:
        rows.append(nav)

    return InlineKeyboardMarkup(rows)

async def list_series(update, context, page=0):
    db = load_db()
    cid = str(update.effective_chat.id)
    items = get_items(db, cid)
    if not items:
        await update.message.reply_text("Lista vacía.")
        return

    start = page * PAGE_SIZE
    end = min(start + PAGE_SIZE, len(items))

    lines = ["*Tus series:*"]
    for idx, it in enumerate(items[start:end], start=start+1):
        try:
            d = tmdb_tv_details(int(it["tmdb_id"]))
            emitted = emitted_season_numbers(d)
            completed = it["completed"]
            current = d.get("next_episode_to_air", {}).get("season_number")
            mini = mini_progress(emitted, completed, current)
            progress = text_progress(emitted, completed)
            lines.append(f"{idx}. **{it['title']} ({it['year']})**\n{progress}\n🔸 {mini}")
        except:
            lines.append(f"{idx}. {it['title']} ({it['year']})")

    kb = make_keyboard(len(items), page)
    await update.message.reply_text("\n\n".join(lines), parse_mode=ParseMode.MARKDOWN, reply_markup=kb)

async def turn_page(update, context):
    q = update.callback_query
    await q.answer()
    page = int(q.data.split(":")[1])
    await list_series(q, context, page)

async def show_series(update, context):
    q = update.callback_query
    idx = int(q.data.split(":")[1])

    db = load_db()
    cid = str(q.message.chat.id)
    entry = get_items(db, cid)[idx]
    d = tmdb_tv_details(int(entry["tmdb_id"]))

    title = d.get("name")
    year = d.get("first_air_date", "").split("-")[0]
    overview = d.get("overview", "")
    poster = d.get("poster_path")
    emitted = emitted_season_numbers(d)
    current = d.get("next_episode_to_air", {}).get("season_number")
    mini = mini_progress(emitted, entry["completed"], current)
    progress = text_progress(emitted, entry["completed"])

    caption = f"<b>{title} ({year})</b>\n\n{overview}\n\n{mini}\n{progress}"
    await q.answer()

    if poster:
        await q.message.reply_photo(IMG_BASE + poster, caption=caption, parse_mode=ParseMode.HTML)
    else:
        await q.message.reply_text(caption, parse_mode=ParseMode.HTML)

# =============================
# MAIN
# =============================
def main():
    print("🚀 Bot arrancando con polling…")
    app = ApplicationBuilder().token(TELEGRAM_BOT_TOKEN).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("add", add_series))
    app.add_handler(CommandHandler("borrar", borrar))
    app.add_handler(CommandHandler("lista", list_series))

    app.add_handler(CallbackQueryHandler(turn_page, pattern="^page:"))
    app.add_handler(CallbackQueryHandler(show_series, pattern="^show:"))

    app.run_polling(drop_pending_updates=True)

if __name__ == "__main__":
    main()
