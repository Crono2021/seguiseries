#!/usr/bin/env python33
# -*- coding: utf-8 -*-
# Bot de Telegram para gestionar una lista de series usando TMDB.
# Versión SIN contraseña: todos los comandos son públicos.
# Las listas son por chat: cada grupo/privado tiene su propia base de datos.
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
IMG_BASE = "https://image.tmdb.org/t/p/w500"
PAGE_SIZE = 10

WEEKDAYS = ["lunes","martes","miércoles","jueves","viernes","sábado","domingo"]
MONTHS = ["enero","febrero","marzo","abril","mayo","junio","julio","agosto","septiembre","octubre","noviembre","diciembre"]

# =============================
# DB UTILS
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
    if not dstr:
        return False
    try:
        return datetime.strptime(dstr,"%Y-%m-%d").date() > date.today()
    except:
        return False

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
    DB_DIR.mkdir(parents=True, exist_ok=True)
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
    if r.status_code == 404:
        raise ValueError("ID no válido")
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
    air = ne.get("air_date")
    return bool(air and is_future(air))

def emitted_season_numbers(details: Dict) -> List[int]:
    seasons = details.get("seasons") or []
    emitted = set()
    today = date.today()

    for s in seasons:
        sn = s.get("season_number")
        if sn in (None, 0):
            continue
        ad = s.get("air_date")
        try:
            if ad and datetime.strptime(ad, "%Y-%m-%d").date() <= today:
                emitted.add(int(sn))
        except:
            pass

    if is_really_airing(details):
        ne = details.get("next_episode_to_air") or {}
        current = ne.get("season_number")
        if current:
            emitted.add(int(current))

    return sorted(emitted)

def mini_progress(emitted_nums, completed, current):
    cset = set(completed or [])
    res = []
    for n in emitted_nums:
        mark = "✅" if n in cset else "❌"
        if current and n == current:
            res.append(f"🟢 S{n} {mark}")
        else:
            res.append(f"S{n} {mark}")
    return " ".join(res)

def text_progress(emitted_nums, completed):
    have_all = len(emitted_nums) > 0 and all(n in set(completed or []) for n in emitted_nums)
    return "✅ Tenemos todo hasta ahora" if have_all else "❌ Todavía nos queda por recopilar"

# =============================
# START / MENU
# =============================
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "📺 Bienvenido.\n\n"
        "• /add <ID> S1S2\n"
        "• /add <Título> <Año?> S1S2\n"
        "• /lista\n"
        "• /borrar <id|título>"
    )

async def menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "📺 Menú\n"
        "/add • /lista • /borrar"
    )

# =============================
# ADD
# =============================
def extract_title_year_and_seasons(args: List[str]):
    year = None
    seasons_str = ""

    if re.search(r"[sS]\d+", args[-1]):
        seasons_str = args[-1]
        args = args[:-1]

    if args and re.fullmatch(r"\d{4}", args[-1]):
        year = args[-1]
        args = args[:-1]

    title = " ".join(args).strip() if args else None
    seasons = parse_seasons_string(seasons_str)
    return title, year, seasons

def find_series_by_title_year(title, year):
    res = tmdb_search_tv(title)
    results = res.get("results", [])
    if not results:
        return None
    if year:
        for r in results:
            y = (r.get("first_air_date") or "").split("-")[0]
            if y == year:
                return r
    return results[0]

async def add_series(update: Update, context: ContextTypes.DEFAULT_TYPE):
    db = load_db()
    cid = str(update.effective_chat.id)
    items = get_items(db, cid)
    args = context.args

    if not args:
        await update.message.reply_text("Uso: /add La casa del dragón 2022 S1S2")
        return

    if re.fullmatch(r"\d+", args[0]):
        tmdb_id = int(args[0])
        seasons = parse_seasons_string("".join(args[1:]))
        try:
            d = tmdb_tv_details(tmdb_id)
        except:
            await update.message.reply_text("⚠️ ID no válido.")
            return
        title = d.get("name") or d.get("original_name")
        year = (d.get("first_air_date") or "").split("-")[0]
    else:
        title, year, seasons = extract_title_year_and_seasons(args)
        if not title:
            await update.message.reply_text("Formato incorrecto.")
            return
        result = find_series_by_title_year(title, year)
        if not result:
            await update.message.reply_text(f"❌ No encontrado: {title}")
            return
        tmdb_id = int(result["id"])
        title = result.get("name") or title
        year = (result.get("first_air_date") or "").split("-")[0]

    for it in items:
        if int(it["tmdb_id"]) == tmdb_id:
            it["completed"] = sorted(set((it.get("completed") or []) + seasons))
            it["title"] = title
            it["year"] = year
            save_db(db)
            await update.message.reply_text(f"Actualizada: {title} ({year})")
            return

    items.append({
        "tmdb_id": tmdb_id,
        "title": title,
        "year": year,
        "completed": seasons,
    })
    save_db(db)
    await update.message.reply_text(f"Añadida: {title} ({year})")

# =============================
# BORRAR
# =============================
async def borrar(update: Update, context: ContextTypes.DEFAULT_TYPE):
    db = load_db()
    cid = str(update.effective_chat.id)
    items = get_items(db, cid)
    q = " ".join(context.args).strip().lower()

    if not q:
        await update.message.reply_text("Uso: /borrar <título|id>")
        return

    new = [i for i in items if not (q == str(i["tmdb_id"]) or normalize(i["title"]) == q)]

    if len(new) < len(items):
        db[cid]["items"] = new
        save_db(db)
        await update.message.reply_text("🗑️ Eliminada.")
    else:
        await update.message.reply_text("No encontrada.")

# =============================
# LISTAR
# =============================
def make_list_keyboard(total: int, page: int) -> InlineKeyboardMarkup:
    start = page * PAGE_SIZE
    end = min(start + PAGE_SIZE, total)
    rows = []

    buttons = [
        InlineKeyboardButton(str(i + 1), callback_data=f"show:{i}")
        for i in range(start, end)
    ]

    for i in range(0, len(buttons), 5):
        rows.append(buttons[i:i+5])

    nav = []
    if page > 0:
        nav.append(InlineKeyboardButton("⬅️", callback_data=f"page:{page-1}"))
    if end < total:
        nav.append(InlineKeyboardButton("➡️", callback_data=f"page:{page+1}"))

    if nav:
        rows.append(nav)

    return InlineKeyboardMarkup(rows)

def build_list_entry(it: Dict) -> str:
    d = tmdb_tv_details(int(it["tmdb_id"]))
    title = it["title"]
    year = it["year"]

    emitted = emitted_season_numbers(d)
    completed = it.get("completed", [])
    current = emitted[-1] if emitted else None

    mini = mini_progress(emitted, completed, current)
    progress = text_progress(emitted, completed)

    return f"**{title} ({year})**\n{progress}\n🔸 {mini}"

async def list_series(update: Update, context: ContextTypes.DEFAULT_TYPE, page: int = 0):
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
            entry = build_list_entry(it)
        except:
            entry = f"{it['title']} ({it['year']})"
        lines.append(f"{idx}. {entry}")

    kb = make_list_keyboard(len(items), page)
    await update.message.reply_text(
        "\n\n".join(lines), parse_mode=ParseMode.MARKDOWN, reply_markup=kb
    )

async def turn_page(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    page = int(q.data.split(":")[1])

    return await list_series(q, context, page)

# =============================
# FICHA
# =============================
async def show_series(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()

    db = load_db()
    cid = str(q.message.chat_id)
    items = get_items(db, cid)
    idx = int(q.data.split(":")[1])
    entry = items[idx]

    tmdb_id = int(entry["tmdb_id"])
    d = tmdb_tv_details(tmdb_id)

    title = d.get("name") or entry["title"]
    year = (d.get("first_air_date") or "").split("-")[0]
    title_with_year = f"{title} ({year})"

    overview = d.get("overview") or "Sinopsis no disponible."
    poster = d.get("poster_path")

    emitted = emitted_season_numbers(d)
    completed = entry.get("completed", [])

    current = emitted[-1] if emitted else None

    mini = mini_progress(emitted, completed, current)
    progress = text_progress(emitted, completed)

    caption = (
        f"<b>{title_with_year}</b>\n\n"
        f"{overview}\n\n"
        f"{mini}\n{progress}"
    )

    if poster:
        await q.message.reply_photo(
            photo=IMG_BASE + poster,
            caption=caption,
            parse_mode=ParseMode.HTML
        )
    else:
        await q.message.reply_text(
            caption,
            parse_mode=ParseMode.HTML
        )

# =============================
# MAIN
# =============================
def main():
    print("🚀 Bot en marcha — persistencia REAL en /data")
    print("Ruta de BD:", DB_PATH)

    app = ApplicationBuilder().token(TELEGRAM_BOT_TOKEN).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("menu", menu))
    app.add_handler(CommandHandler("add", add_series))
    app.add_handler(CommandHandler("borrar", borrar))
    app.add_handler(CommandHandler("lista", list_series))

    app.add_handler(CallbackQueryHandler(turn_page, pattern="^page:"))
    app.add_handler(CallbackQueryHandler(show_series, pattern="^show:"))

    app.run_polling()

if __name__ == "__main__":
    main()
