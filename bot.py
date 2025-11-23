#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# Bot de Telegram para gestionar una lista de series usando TMDB.
# Persistencia REAL en /data (Railway)

import os
import json, re, requests
from pathlib import Path
from datetime import date
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
    raise RuntimeError("❌ Falta la variable de entorno BOT_TOKEN")

if not TMDB_API_KEY:
    raise RuntimeError("❌ Falta la variable de entorno TMDB_API_KEY")

# =============================
# BASE DE DATOS
# =============================
DB_DIR = Path("/data")
DB_DIR.mkdir(parents=True, exist_ok=True)
DB_PATH = DB_DIR / "series_data.json"

TMDB_BASE = "https://api.themoviedb.org/3"
IMG_BASE = "https://image.tmdb.org/t/p/"
PAGE_SIZE = 10

# =============================
# DB UTILS
# =============================
def load_db() -> Dict[str, Any]:
    if DB_PATH.exists():
        try:
            db = json.loads(DB_PATH.read_text("utf-8"))
            if not isinstance(db, dict):
                db = {}
        except:
            db = {}
    else:
        db = {}
    for k,v in list(db.items()):
        if isinstance(v, list):
            db[k] = {"items": v}
        elif isinstance(v, dict) and "items" not in v:
            v["items"] = v.get("items", [])
    return db

def save_db(db: Dict[str, Any]):
    DB_PATH.write_text(
        json.dumps(db, ensure_ascii=False, indent=2),
        "utf-8"
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
def tmdb_search_tv(q: str) -> Dict:
    r = requests.get(
        f"{TMDB_BASE}/search/tv",
        params={"api_key": TMDB_API_KEY, "language": "es-ES", "query": q},
        timeout=20
    )
    r.raise_for_status()
    return r.json()

def tmdb_tv_details(tmdb_id: int) -> Dict:
    r = requests.get(
        f"{TMDB_BASE}/tv/{tmdb_id}",
        params={"api_key": TMDB_API_KEY, "language": "es-ES"},
        timeout=20
    )
    r.raise_for_status()
    return r.json()

def tmdb_watch_providers(tmdb_id: int) -> Dict:
    r = requests.get(
        f"{TMDB_BASE}/tv/{tmdb_id}/watch/providers",
        params={"api_key": TMDB_API_KEY},
        timeout=20
    )
    r.raise_for_status()
    return r.json()

# =============================
# UTILS
# =============================
def parse_seasons_string(s: str) -> List[int]:
    return sorted({int(x) for x in re.findall(r"[sS](\d+)", s or "")})

# =============================
# START
# =============================
async def start(update, context):
    await update.message.reply_text(
        "📺 *Bienvenido*\n\n"
        "Comandos disponibles:\n"
        "• /add <ID|TÍTULO> S1S2\n"
        "• /lista — Ver tus series\n"
        "• /borrar — Borrado interactivo\n"
        "• /borrartodo — Borra solo tus series\n"
        "• /caratula <título> — Carátula en máxima calidad\n"
        "• /ficha <título> — Ficha completa de una *serie*\n",
        parse_mode=ParseMode.MARKDOWN
    )

# =============================
# ADD
# =============================
async def add_series(update, context):
    db = load_db()
    cid = str(update.effective_chat.id)
    items = get_items(db, cid)

    args = context.args
    uid = update.effective_user.id

    if not args:
        await update.message.reply_text("Uso: /add La casa del dragón 2022 S1S2")
        return

    # Si empieza por ID
    if re.fullmatch(r"\d+", args[0]):
        tmdb_id = int(args[0])
        seasons = parse_seasons_string(" ".join(args[1:]))
        try:
            d = tmdb_tv_details(tmdb_id)
        except:
            await update.message.reply_text("ID inválido.")
            return
        title = d.get("name")
        year = (d.get("first_air_date") or "").split("-")[0]
    else:
        q = " ".join(args)
        res = tmdb_search_tv(q)
        results = res.get("results", [])
        if not results:
            await update.message.reply_text("No encontrado.")
            return
        s = results[0]
        tmdb_id = s["id"]
        title = s["name"]
        year = (s.get("first_air_date") or "").split("-")[0]
        seasons = parse_seasons_string(q)

    # Actualizar si existe
    for it in items:
        if int(it["tmdb_id"]) == tmdb_id:
            it["completed"] = sorted(set(it.get("completed", []) + seasons))
            it["title"] = title
            it["year"] = year
            it["user_id"] = uid
            save_db(db)
            await update.message.reply_text(f"Actualizada: {title}")
            return

    items.append({
        "tmdb_id": tmdb_id,
        "title": title,
        "year": year,
        "completed": seasons,
        "user_id": uid
    })

    save_db(db)
    await update.message.reply_text(f"Añadida: {title}")

# =============================
# CARÁTULA
# =============================
async def caratula(update, context):
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
    title = s["name"]
    poster = s.get("poster_path")

    if not poster:
        await update.message.reply_text("No hay carátula disponible.")
        return

    url = f"{IMG_BASE}original{poster}"

    await update.message.reply_photo(
        url, caption=f"<b>{title}</b>", parse_mode=ParseMode.HTML
    )

# =============================
# FICHA (SOLO SERIES)
# =============================
async def ficha(update, context):
    if not context.args:
        await update.message.reply_text("Uso: /ficha <título>")
        return

    q = " ".join(context.args)
    res = tmdb_search_tv(q)
    results = res.get("results", [])

    if not results:
        await update.message.reply_text("No encontrado.")
        return

    s = results[0]
    tmdb_id = s["id"]
    details = tmdb_tv_details(tmdb_id)

    title = details.get("name")
    year = (details.get("first_air_date") or "").split("-")[0]
    score = details.get("vote_average") or 0
    genres = ", ".join(g["name"] for g in details.get("genres", [])) or "Desconocido"
    overview = details.get("overview") or "Sin sinopsis."

    # PROVIDERS (ESPAÑA)
    prov = tmdb_watch_providers(tmdb_id)
    pl = prov.get("results", {}).get("ES", {})
    plataforma = "Desconocida"

    if "flatrate" in pl and pl["flatrate"]:
        plataforma = ", ".join(p["provider_name"] for p in pl["flatrate"])
    elif "rent" in pl and pl["rent"]:
        plataforma = ", ".join(p["provider_name"] for p in pl["rent"])
    elif "buy" in pl and pl["buy"]:
        plataforma = ", ".join(p["provider_name"] for p in pl["buy"])

    poster = details.get("poster_path")
    url = f"{IMG_BASE}original{poster}" if poster else None

    caption = (
        f"<b>{title} ({year})</b>\n\n"
        f"⭐ <b>Puntuación TMDB:</b> {score}/10\n"
        f"🎭 <b>Géneros:</b> {genres}\n"
        f"📺 <b>Plataforma:</b> {plataforma}\n\n"
        f"{overview}"
    )

    if url:
        await update.message.reply_photo(
            url, caption=caption, parse_mode=ParseMode.HTML
        )
    else:
        await update.message.reply_text(
            caption, parse_mode=ParseMode.HTML
        )

# =============================
# BORRARTODO
# =============================
async def borrartodo(update, context):
    db = load_db()
    cid = str(update.effective_chat.id)
    items = get_items(db, cid)
    uid = update.effective_user.id

    new = [s for s in items if s.get("user_id") != uid]
    deleted = len(items) - len(new)

    db[cid]["items"] = new
    save_db(db)

    await update.message.reply_text(f"🗑️ Se han borrado {deleted} de tus series.")

# =============================
# BORRADO INTERACTIVO
# =============================
def make_delete_keyboard(items, page):
    total = len(items)
    max_page = max((total-1)//PAGE_SIZE,0)
    page = max(0,min(page,max_page))

    start = page*PAGE_SIZE
    end = min(start+PAGE_SIZE,total)

    rows = []
    for i in range(start,end):
        rows.append([
            InlineKeyboardButton(
                f"{i+1}. {items[i]['title']}",
                callback_data=f"delitem:{i}:{page}"
            )
        ])

    nav=[]
    if page>0:
        nav.append(InlineKeyboardButton("⬅️",callback_data=f"delpage:{page-1}"))
    if end<total:
        nav.append(InlineKeyboardButton("➡️",callback_data=f"delpage:{page+1}"))
    if nav:
        rows.append(nav)

    rows.append([InlineKeyboardButton("TERMINAR",callback_data="delend")])
    return InlineKeyboardMarkup(rows)

async def borrar(update, context):
    db=load_db()
    cid=str(update.effective_chat.id)
    items=get_items(db,cid)

    if not items:
        await update.message.reply_text("No hay series para borrar.")
        return

    kb=make_delete_keyboard(items,0)
    await update.message.reply_text(
        "Pulsa una serie para borrarla. Pulsa TERMINAR cuando acabes.",
        reply_markup=kb
    )

async def delete_turn_page(update, context):
    q=update.callback_query
    await q.answer()

    db=load_db()
    cid=str(q.message.chat.id)
    items=get_items(db,cid)

    page=int(q.data.split(":")[1])
    kb=make_delete_keyboard(items,page)
    await q.edit_message_reply_markup(reply_markup=kb)

async def delete_item(update, context):
    q=update.callback_query
    await q.answer()

    db=load_db()
    cid=str(q.message.chat.id)
    items=get_items(db,cid)

    _,idx,page = q.data.split(":")
    idx=int(idx); page=int(page)

    if idx < len(items):
        del items[idx]
        save_db(db)
        await q.message.reply_text("🗑️ Serie borrada.")

    if not items:
        await q.edit_message_text("No quedan series.")
        return

    kb=make_delete_keyboard(items,page)
    await q.edit_message_reply_markup(reply_markup=kb)

async def delete_end(update, context):
    q=update.callback_query
    await q.answer()
    await q.edit_message_text("✔️ Borrado terminado.")

# =============================
# LISTAR
# =============================
def make_list_keyboard(total,page):
    max_page=max((total-1)//PAGE_SIZE,0)
    page=max(0,min(page,max_page))

    start=page*PAGE_SIZE
    end=min(start+PAGE_SIZE,total)

    rows=[]
    rows.append([
        InlineKeyboardButton(str(i+1),callback_data=f"show:{i}")
        for i in range(start,end)
    ])

    nav=[]
    if page>0:
        nav.append(InlineKeyboardButton("⬅️",callback_data=f"page:{page-1}"))
    if end<total:
        nav.append(InlineKeyboardButton("➡️",callback_data=f"page:{page+1}"))
    if nav:
        rows.append(nav)

    return InlineKeyboardMarkup(rows)

async def list_series(update, context, page=0):
    db=load_db()
    cid=str(update.effective_chat.id)
    items=get_items(db,cid)

    if not items:
        await update.message.reply_text("Lista vacía.")
        return

    total=len(items)
    max_page=max((total-1)//PAGE_SIZE,0)
    page=max(0,min(page,max_page))

    start=page*PAGE_SIZE
    end=min(start+PAGE_SIZE,total)

    lines=["*Tus series:*"]
    for idx,it in enumerate(items[start:end],start+1):
        lines.append(f"{idx}. {it['title']} ({it['year']})")

    kb=make_list_keyboard(total,page)

    await update.message.reply_text(
        "\n".join(lines),
        parse_mode=ParseMode.MARKDOWN,
        reply_markup=kb
    )

async def turn_page(update, context):
    q=update.callback_query
    await q.answer()

    page=int(q.data.split(":")[1])

    db=load_db()
    cid=str(q.message.chat.id)
    items=get_items(db,cid)

    total=len(items)
    max_page=max((total-1)//PAGE_SIZE,0)
    page=max(0,min(page,max_page))

    start=page*PAGE_SIZE
    end=min(start+PAGE_SIZE,total)

    lines=["*Tus series:*"]
    for idx,it in enumerate(items[start:end],start+1):
        lines.append(f"{idx}. {it['title']} ({it['year']})")

    kb=make_list_keyboard(total,page)

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
    app.add_handler(CommandHandler("ficha", ficha))

    app.add_handler(CallbackQueryHandler(turn_page, pattern="^page:"))
    app.add_handler(CallbackQueryHandler(delete_turn_page, pattern="^delpage:"))
    app.add_handler(CallbackQueryHandler(delete_item, pattern="^delitem:"))
    app.add_handler(CallbackQueryHandler(delete_end, pattern="^delend$"))

    app.run_polling()

if __name__ == "__main__":
    main()
