#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# Bot de Telegram para gestionar una lista de series usando TMDB.
# Persistencia REAL en /data (Railway)

import os
import json, re, requests
from pathlib import Path
from datetime import datetime, date
from typing import Dict, List, Optional, Any
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
    raise RuntimeError("❌ Falta la variable de entorno BOT_TOKEN")

if not TMDB_API_KEY:
    raise RuntimeError("❌ Falta la variable de entorno TMDB_API_KEY")

# =============================
# BASE DE DATOS (PERSISTENTE)
# =============================
DB_DIR = Path("/data")
DB_DIR.mkdir(parents=True, exist_ok=True)

DB_PATH = DB_DIR / "series_data.json"

TMDB_BASE = "https://api.themoviedb.org/3"
IMG_BASE = "https://image.tmdb.org/t/p/"
PAGE_SIZE = 10

WEEKDAYS = ["lunes","martes","miércoles","jueves","viernes","sábado","domingo"]
MONTHS = ["enero","febrero","marzo","abril","mayo","junio","julio","agosto","septiembre","octubre","noviembre","diciembre"]

# =============================
# DB UTILS
# =============================
def load_db() -> Dict[str, Any]:
    if DB_PATH.exists():
        try:
            db = json.loads(DB_PATH.read_text(encoding="utf-8"))
            if not isinstance(db, dict):
                db = {}
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

def save_db(db: Dict[str, Any]) -> None:
    DB_PATH.write_text(json.dumps(db, ensure_ascii=False, indent=2), encoding="utf-8")

def ensure_chat(db: Dict[str, Any], chat_id: str) -> None:
    if chat_id not in db:
        db[chat_id] = {"items": []}

def get_items(db: Dict[str, Any], chat_id: str):
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

# =============================
# MÉTODOS UTILES
# =============================
def parse_seasons_string(s: str) -> List[int]:
    return sorted({int(x) for x in re.findall(r"[sS](\d+)", s or "")})

def normalize(s: str) -> str:
    return re.sub(r"\s+", " ", (s or "").strip().lower())

def emitted_season_numbers(details: Dict) -> List[int]:
    seasons = details.get("seasons") or []
    emitted = set()
    today = date.today()

    for s in seasons:
        sn = s.get("season_number")
        if not sn or sn == 0:
            continue
        ad = s.get("air_date")
        try:
            if ad and datetime.strptime(ad, "%Y-%m-%d").date() <= today:
                emitted.add(sn)
        except:
            pass

    return sorted(emitted)

# =============================
# START / MENU
# =============================
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "📺 *Bienvenido*\n\n"
        "Comandos disponibles:\n"
        "• /add <ID|TÍTULO> S1S2\n"
        "• /lista — Ver tus series\n"
        "• /borrar — Borrado interactivo\n"
        "• /borrartodo — Borra solo tus series\n"
        "➕ *Nuevo:* /caratula <título> — envía la carátula en máxima calidad\n",
        parse_mode=ParseMode.MARKDOWN
    )

# =============================
# ADD
# =============================
async def add_series(update: Update, context: ContextTypes.DEFAULT_TYPE):
    db = load_db()
    cid = str(update.effective_chat.id)
    items = get_items(db, cid)
    args = context.args
    user_id = update.effective_user.id

    if not args:
        await update.message.reply_text("Uso: /add La casa del dragón 2022 S1S2")
        return

    # Si empieza por número → ID
    if re.fullmatch(r"\d+", args[0]):
        tmdb_id = int(args[0])
        seasons = parse_seasons_string("".join(args[1:]))

        try:
            d = tmdb_tv_details(tmdb_id)
        except:
            await update.message.reply_text("❌ ID inválido.")
            return

        title = d.get("name")
        year = (d.get("first_air_date") or "").split("-")[0]

    else:
        # Búsqueda por título
        q = " ".join(args)
        res = tmdb_search_tv(q)
        results = res.get("results", [])
        if not results:
            await update.message.reply_text("No encontrado.")
            return

        data = results[0]
        tmdb_id = int(data["id"])
        title = data["name"]
        year = (data.get("first_air_date") or "").split("-")[0]
        seasons = parse_seasons_string(q)

    # Actualizar si ya existe
    for it in items:
        if int(it["tmdb_id"]) == tmdb_id:
            it["completed"] = sorted(set(it.get("completed", []) + seasons))
            it["title"] = title
            it["year"] = year
            it["user_id"] = user_id
            save_db(db)
            await update.message.reply_text(f"Actualizada: {title}")
            return

    # Añadir
    items.append({
        "tmdb_id": tmdb_id,
        "title": title,
        "year": year,
        "completed": seasons,
        "user_id": user_id
    })

    save_db(db)
    await update.message.reply_text(f"Añadida: {title}")

# =============================
# CARÁTULA
# =============================
async def caratula(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args:
        await update.message.reply_text("Uso: /caratula <título>")
        return

    q = " ".join(context.args)
    res = tmdb_search_tv(q)
    results = res.get("results", [])

    if not results:
        await update.message.reply_text("No encontrado.")
        return

    s = results[0]
    title = s.get("name")
    poster = s.get("poster_path")

    if not poster:
        await update.message.reply_text("No hay carátula disponible.")
        return

    url = f"{IMG_BASE}original{poster}"

    await update.message.reply_photo(
        photo=url,
        caption=f"<b>{title}</b>",
        parse_mode=ParseMode.HTML
    )

# =============================
# BORRARTODO
# =============================
async def borrartodo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    db = load_db()
    cid = str(update.effective_chat.id)
    items = get_items(db, cid)

    uid = update.effective_user.id
    new_items = [s for s in items if s.get("user_id") != uid]
    deleted = len(items) - len(new_items)

    db[cid]["items"] = new_items
    save_db(db)

    await update.message.reply_text(f"🗑️ Se han borrado {deleted} de tus series.")

# =============================
# BORRADO INTERACTIVO
# =============================
def make_delete_keyboard(items, page):
    total = len(items)
    max_page = max((total - 1) // PAGE_SIZE, 0)
    page = max(0, min(page, max_page))

    start = page * PAGE_SIZE
    end = min(start + PAGE_SIZE, total)

    rows = []

    for i in range(start, end):
        rows.append([
            InlineKeyboardButton(
                f"{i+1}. {items[i]['title']}",
                callback_data=f"delitem:{i}:{page}"
            )
        ])

    nav = []
    if page > 0:
        nav.append(InlineKeyboardButton("⬅️", callback_data=f"delpage:{page-1}"))
    if end < total:
        nav.append(InlineKeyboardButton("➡️", callback_data=f"delpage:{page+1}"))
    if nav:
        rows.append(nav)

    rows.append([InlineKeyboardButton("TERMINAR", callback_data="delend")])
    return InlineKeyboardMarkup(rows)

async def borrar(update, context):
    db = load_db()
    cid = str(update.effective_chat.id)
    items = get_items(db, cid)

    if not items:
        await update.message.reply_text("No hay series para borrar.")
        return

    kb = make_delete_keyboard(items, 0)
    await update.message.reply_text(
        "Pulsa una serie para borrarla.\nPulsa TERMINAR cuando acabes.",
        reply_markup=kb
    )

async def delete_turn_page(update, context):
    q = update.callback_query
    await q.answer()

    db = load_db()
    cid = str(q.message.chat.id)
    items = get_items(db, cid)

    page = int(q.data.split(":")[1])
    kb = make_delete_keyboard(items, page)
    await q.edit_message_reply_markup(reply_markup=kb)

async def delete_item(update, context):
    q = update.callback_query
    await q.answer()

    db = load_db()
    cid = str(q.message.chat.id)
    items = get_items(db, cid)

    _, idx, page = q.data.split(":")
    idx = int(idx)
    page = int(page)

    if idx < len(items):
        name = items[idx]["title"]
        del items[idx]
        save_db(db)
        await q.message.reply_text(f"🗑️ Borrada: {name}")

    # Redibujar menú
    if not items:
        await q.edit_message_text("No quedan series.")
        return

    kb = make_delete_keyboard(items, page)
    await q.edit_message_reply_markup(reply_markup=kb)

async def delete_end(update, context):
    q = update.callback_query
    await q.answer()
    await q.edit_message_text("✔ Borrado terminado.")

# =============================
# LISTAR
# =============================
def make_list_keyboard(total, page):
    max_page = max((total - 1) // PAGE_SIZE, 0)
    page = max(0, min(page, max_page))

    start = page * PAGE_SIZE
    end = min(start + PAGE_SIZE, total)

    rows = []

    rows.append([
        InlineKeyboardButton(
            str(i + 1),
            callback_data=f"show:{i}"
        ) for i in range(start, end)
    ])

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

    total = len(items)
    max_page = max((total - 1) // PAGE_SIZE, 0)
    page = max(0, min(page, max_page))

    start = page * PAGE_SIZE
    end = min(start + PAGE_SIZE, total)

    lines = ["*Tus series:*"]
    for idx, it in enumerate(items[start:end], start + 1):
        lines.append(f"{idx}. {it['title']} ({it['year']})")

    kb = make_list_keyboard(total, page)

    await update.message.reply_text(
        "\n".join(lines),
        parse_mode=ParseMode.MARKDOWN,
        reply_markup=kb
    )

async def turn_page(update, context):
    q = update.callback_query
    await q.answer()

    page = int(q.data.split(":")[1])

    db = load_db()
    cid = str(q.message.chat.id)
    items = get_items(db, cid)

    total = len(items)
    max_page = max((total - 1) // PAGE_SIZE, 0)
    page = max(0, min(page, max_page))

    start = page * PAGE_SIZE
    end = min(start + PAGE_SIZE, total)

    lines = ["*Tus series:*"]
    for idx, it in enumerate(items[start:end], start + 1):
        lines.append(f"{idx}. {it['title']} ({it['year']})")

    kb = make_list_keyboard(total, page)

    await q.edit_message_text(
        "\n".join(lines),
        parse_mode=ParseMode.MARKDOWN,
        reply_markup=kb
    )

# =============================
# MAIN
# =============================
def main():
    app = ApplicationBuilder().token(TELEGRAM_BOT_TOKEN).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("add", add_series))
    app.add_handler(CommandHandler("lista", list_series))
    app.add_handler(CommandHandler("borrar", borrar))
    app.add_handler(CommandHandler("borrartodo", borrartodo))
    app.add_handler(CommandHandler("caratula", caratula))

    app.add_handler(CallbackQueryHandler(turn_page, pattern="^page:"))
    app.add_handler(CallbackQueryHandler(delete_turn_page, pattern="^delpage:"))
    app.add_handler(CallbackQueryHandler(delete_item, pattern="^delitem:"))
    app.add_handler(CallbackQueryHandler(delete_end, pattern="^delend$"))
    app.add_handler(CallbackQueryHandler(turn_page, pattern="^page:"))
    app.add_handler(CallbackQueryHandler(lambda u,c:None, pattern="^show:"))  # Ficha opcional

    app.run_polling()

if __name__ == "__main__":
    main()
