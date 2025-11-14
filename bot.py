#!/usr/bin/env python33
# -*- coding: utf-8 -*-
"""
Bot de Telegram para gestionar una lista de series usando TMDB.
Versión SIN contraseña: todos los comandos son públicos.
"""

import os
import json, re, requests
from pathlib import Path
from datetime import datetime, date
from typing import Dict, List, Optional, Any
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.constants import ParseMode
from telegram.ext import (
    ApplicationBuilder, CommandHandler, CallbackQueryHandler,
    ContextTypes, filters, MessageHandler
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
# BASE DE DATOS (PERSISTENTE)
# =============================

# 👉 Crear carpeta del volumen si no existe (solución definitiva Railway)
DB_DIR = Path("/mnt/series_db")
DB_DIR.mkdir(parents=True, exist_ok=True)

DB_PATH = DB_DIR / "series_data.json"

TMDB_BASE = "https://api.themoviedb.org/3"
IMG_BASE = "https://image.tmdb.org/t/p/w500"
PAGE_SIZE = 10

WEEKDAYS = ["lunes", "martes", "miércoles", "jueves",
            "viernes", "sábado", "domingo"]
MONTHS = ["enero", "febrero", "marzo", "abril", "mayo", "junio",
          "julio", "agosto", "septiembre", "octubre", "noviembre", "diciembre"]

# =============================
# UTILIDADES
# =============================

def format_date_natural(dstr: Optional[str]) -> Optional[str]:
    if not dstr:
        return None
    try:
        d = datetime.strptime(dstr, "%Y-%m-%d").date()
        return f"{WEEKDAYS[d.weekday()]}, {d.day} de {MONTHS[d.month-1]} de {d.year}"
    except:
        return None

def is_future(dstr: Optional[str]) -> bool:
    try:
        return datetime.strptime(dstr, "%Y-%m-%d").date() > date.today()
    except:
        return False

def load_db() -> Dict[str, Any]:
    if DB_PATH.exists():
        try:
            db = json.loads(DB_PATH.read_text(encoding="utf-8"))
        except:
            db = {}
    else:
        db = {}

    for k, v in list(db.items()):
        if isinstance(v, list):
            db[k] = {"items": v}
        elif isinstance(v, dict) and "items" not in v:
            v["items"] = v.get("items", [])

    return db

def save_db(db):
    DB_PATH.write_text(
        json.dumps(db, ensure_ascii=False, indent=2),
        encoding="utf-8"
    )

def ensure_chat(db, chat_id):
    if chat_id not in db:
        db[chat_id] = {"items": []}

def get_items(db, chat_id):
    ensure_chat(db, chat_id)
    return db[chat_id]["items"]

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
    return sorted({int(x) for x in re.findall(r"[sS](\d+)", s or "")})

def normalize(s: str) -> str:
    return re.sub(r"\s+", " ", (s or "").strip().lower())

# =============================
# ESTADO
# =============================
def is_really_airing(details: Dict) -> bool:
    ne = details.get("next_episode_to_air") or {}
    return bool(ne.get("air_date") and is_future(ne.get("air_date")))

def emitted_season_numbers(details: Dict) -> List[int]:
    seasons = details.get("seasons") or []
    today = date.today()
    emitted = set()

    for s in seasons:
        ad = s.get("air_date")
        try:
            if ad and datetime.strptime(ad, "%Y-%m-%d").date() <= today:
                emitted.add(int(s["season_number"]))
        except:
            pass

    if is_really_airing(details):
        emitted.add(int(details["next_episode_to_air"]["season_number"]))

    return sorted(emitted)

def mini_progress(emitted, completed, current):
    cset = set(completed)
    parts = []
    for n in emitted:
        mark = "✅" if n in cset else "❌"
        parts.append(f"{'🟢 ' if n == current else ''}S{n} {mark}")
    return " ".join(parts)

def text_progress(emitted, completed):
    return "✅ Tenemos todo hasta ahora" if all(n in completed for n in emitted) else "❌ Todavía nos queda por recopilar"

# =============================
# START
# =============================
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "📺 Bienvenido al bot de seguimiento de series.\n\n"
        "Comandos disponibles:\n"
        "• /add <TMDBID> <S1S2...>\n"
        "• /add <Título> <Año?> <S1S2>\n"
        "• /lista\n"
        "• /borrar <tmdbid|título>"
    )

# =============================
# /add
# =============================
def extract_title_year_and_seasons(args: List[str]):
    if not args:
        return None, None, []
    year = None
    seasons_str = ""

    if re.search(r"[sS]\d+", args[-1]):
        seasons_str = args[-1]
        args = args[:-1]

    if args and re.fullmatch(r"\d{4}", args[-1]):
        year = args[-1]
        args = args[:-1]

    title = " ".join(args).strip()
    seasons = parse_seasons_string(seasons_str)
    return title, year, seasons

async def add_series(update: Update, context: ContextTypes.DEFAULT_TYPE):
    db = load_db()
    cid = str(update.effective_chat.id)
    items = get_items(db, cid)

    args = context.args
    if not args:
        await update.message.reply_text("Uso: /add <ID> S1S2 o /add <Título> <Año?> S1S2")
        return

    # Modo ID
    if re.fullmatch(r"\d+", args[0]):
        tmdb_id = int(args[0])
        seasons = parse_seasons_string(" ".join(args[1:]))
        d = tmdb_tv_details(tmdb_id)
        title = d["name"]
        year = (d.get("first_air_date") or "").split("-")[0]
    else:
        title, year, seasons = extract_title_year_and_seasons(args)
        result = tmdb_search_tv(title)["results"]
        if not result:
            await update.message.reply_text("No encontrado.")
            return
        r = result[0] if not year else next((x for x in result if x.get("first_air_date", "").startswith(year)), result[0])
        tmdb_id = int(r["id"])
        title = r["name"]
        year = (r.get("first_air_date") or "").split("-")[0]

    # Actualizar o añadir
    for it in items:
        if int(it["tmdb_id"]) == tmdb_id:
            it["completed"] = sorted(set(it["completed"] + seasons))
            save_db(db)
            await update.message.reply_text(f"Actualizada: {title}")
            return

    items.append({"tmdb_id": tmdb_id, "title": title, "year": year, "completed": seasons})
    save_db(db)
    await update.message.reply_text(f"Añadida: {title} ({year})")

# =============================
# /borrar
# =============================
async def borrar(update: Update, context: ContextTypes.DEFAULT_TYPE):
    db = load_db()
    cid = str(update.effective_chat.id)
    items = get_items(db, cid)

    q = " ".join(context.args).lower()
    new = [it for it in items if not (q == str(it["tmdb_id"]) or normalize(it["title"]) == q)]

    if len(new) < len(items):
        db[cid]["items"] = new
        save_db(db)
        await update.message.reply_text("🗑️ Serie eliminada.")
    else:
        await update.message.reply_text("No encontrada.")

# =============================
# LISTA
# =============================
def make_list_keyboard(total: int, page: int):
    start = page * PAGE_SIZE
    end = min(start + PAGE_SIZE, total)
    rows = []

    nums = [
        InlineKeyboardButton(str(i+1), callback_data=f"show:{i}")
        for i in range(start, end)
    ]

    for i in range(0, len(nums), 5):
        rows.append(nums[i:i+5])

    nav = []
    if page > 0:
        nav.append(InlineKeyboardButton("⬅️", callback_data=f"page:{page-1}"))
    if end < total:
        nav.append(InlineKeyboardButton("➡️", callback_data=f"page:{page+1}"))

    if nav:
        rows.append(nav)
    return InlineKeyboardMarkup(rows)

def build_list_entry(it):
    d = tmdb_tv_details(it["tmdb_id"])
    emitted = emitted_season_numbers(d)
    completed = it["completed"]
    current = None

    if is_really_airing(d):
        current = d["next_episode_to_air"]["season_number"]

    return (
        f"**{it['title']} ({it['year']})**\n"
        f"{text_progress(emitted, completed)}\n"
        f"🔸 {mini_progress(emitted, completed, current)}"
    )

async def list_series(update, context, page=0):
    db = load_db()
    cid = str(update.effective_chat.id)
    items = get_items(db, cid)

    if not items:
        await update.message.reply_text("Lista vacía.")
        return

    start = page * PAGE_SIZE
    end = min(start+PAGE_SIZE, len(items))

    lines = ["*Tus series:*"]
    for i, it in enumerate(items[start:end], start=start+1):
        try:
            lines.append(f"{i}. {build_list_entry(it)}")
        except:
            lines.append(f"{i}. {it['title']} ({it['year']})")

    await update.message.reply_text(
        "\n\n".join(lines),
        parse_mode=ParseMode.MARKDOWN,
        reply_markup=make_list_keyboard(len(items), page)
    )

async def turn_page(update, context):
    q = update.callback_query
    page = int(q.data.split(":")[1])
    db = load_db()
    cid = str(q.message.chat_id)
    items = get_items(db, cid)

    start = page * PAGE_SIZE
    end = min(start+PAGE_SIZE, len(items))

    lines = ["*Tus series:*"]
    for i, it in enumerate(items[start:end], start=start+1):
        try:
            lines.append(f"{i}. {build_list_entry(it)}")
        except:
            lines.append(f"{i}. {it['title']} ({it['year']})")

    await q.edit_message_text(
        "\n\n".join(lines),
        parse_mode=ParseMode.MARKDOWN,
        reply_markup=make_list_keyboard(len(items), page)
    )

# =============================
# FICHA
# =============================
async def show_series(update, context):
    q = update.callback_query
    idx = int(q.data.split(":")[1])

    db = load_db()
    cid = str(q.message.chat_id)
    entry = get_items(db, cid)[idx]

    d = tmdb_tv_details(entry["tmdb_id"])

    title = d["name"]
    year = (d.get("first_air_date") or "").split("-")[0]
    overview = d.get("overview", "Sinopsis no disponible.")
    poster = d.get("poster_path")

    emitted = emitted_season_numbers(d)
    completed = entry["completed"]

    current = None
    if is_really_airing(d):
        current = d["next_episode_to_air"]["season_number"]

    caption = (
        f"<b>{title} ({year})</b>\n\n"
        f"{overview}\n\n"
        f"{mini_progress(emitted, completed, current)}\n"
        f"{text_progress(emitted, completed)}"
    )

    if poster:
        await q.message.reply_photo(
            IMG_BASE + poster,
            caption=caption,
            parse_mode=ParseMode.HTML
        )
    else:
        await q.message.reply_text(caption, parse_mode=ParseMode.HTML)

# =============================
# MAIN
# =============================

def main():
    app = ApplicationBuilder().token(TELEGRAM_BOT_TOKEN).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("lista", list_series))
    app.add_handler(CommandHandler("add", add_series))
    app.add_handler(CommandHandler("borrar", borrar))

    app.add_handler(CallbackQueryHandler(turn_page, pattern="^page:"))
    app.add_handler(CallbackQueryHandler(show_series, pattern="^show:"))

    print("🚀 Bot en marcha (sin contraseña, base de datos persistente)…")
    app.run_polling()

if __name__ == "__main__":
    main()
