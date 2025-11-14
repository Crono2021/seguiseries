#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Bot de Telegram para gestionar una lista de series usando TMDB.
Versión SIN contraseña: todos los comandos son públicos.
Listas separadas por chat_id dentro de un único archivo JSON.
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
# BASE DE DATOS PERSISTENTE
# RUTA CORRECTA DE RAILWAY → /data
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
# UTILIDADES
# =============================
def format_date_natural(dstr: Optional[str]) -> Optional[str]:
    if not dstr:
        return None
    try:
        d = datetime.strptime(dstr, "%Y-%m-%d").date()
        return f"{WEEKDAYS[d.weekday()]}, {d.day} de {MONTHS[d.month-1]} de {d.year}"
    except Exception:
        return None

def is_future(dstr: Optional[str]) -> bool:
    if not dstr:
        return False
    try:
        return datetime.strptime(dstr,"%Y-%m-%d").date() > date.today()
    except Exception:
        return False

def load_db() -> Dict[str, Any]:
    if DB_PATH.exists():
        try:
            db = json.loads(DB_PATH.read_text(encoding="utf-8"))
            if not isinstance(db, dict):
                db = {}
        except Exception:
            db = {}
    else:
        db = {}

    # Normalizamos estructura: {chat_id: {"items": [...] }}
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
        raise ValueError("ID no válido en TMDB")
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
# ESTADO / PROGRESO
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
        except Exception:
            pass

    if is_really_airing(details):
        ne = details.get("next_episode_to_air") or {}
        current = ne.get("season_number")
        if current:
            emitted.add(int(current))

    return sorted(emitted)

def mini_progress(emitted_nums, completed, current):
    cset = set(completed or [])
    parts = []
    for n in emitted_nums:
        mark = "✅" if n in cset else "❌"
        if current and n == current:
            parts.append(f"🟢 S{n} {mark}")
        else:
            parts.append(f"S{n} {mark}")
    return " ".join(parts)

def text_progress(emitted_nums, completed):
    have_all = len(emitted_nums) > 0 and all(
        n in set(completed or []) for n in emitted_nums
    )
    return (
        "✅ Tenemos todo hasta ahora"
        if have_all
        else "❌ Todavía nos queda por recopilar"
    )

# =============================
# START / MENÚ
# =============================
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "📺 Bienvenido al bot de seguimiento de series.\n\n"
        "Comandos disponibles:\n"
        "• /add <TMDBID> <S1S2...>\n"
        "• /add <Título> <Año?> <S1S2>\n"
        "• /lista — ver tus series\n"
        "• /borrar <tmdbid|título>"
    )

async def menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    txt = (
        "📺 Menú\n\n"
        "• /add <TMDBID> <S1S2S3>\n"
        "• /add <Título> <Año?> <S1S2S3>\n"
        "• /lista\n"
        "• /borrar <tmdbid|título>\n"
    )
    await update.message.reply_text(txt)

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
        await update.message.reply_text(
            "Uso: /add <ID> S1S2 o /add <Título> <Año?> S1S2\n"
            "Ejemplo: /add La casa del dragón 2022 S1S2"
        )
        return

    if re.fullmatch(r"\d+", args[0]):
        tmdb_id = int(args[0])
        seasons = parse_seasons_string("".join(args[1:]))
        try:
            d = tmdb_tv_details(tmdb_id)
        except Exception:
            await update.message.reply_text("⚠️ ID inválido o no es una serie de TV.")
            return
        title = d.get("name") or d.get("original_name") or f"TMDB {tmdb_id}"
        year = (d.get("first_air_date") or "").split("-")[0]
    else:
        title, year, seasons = extract_title_year_and_seasons(args)
        if not title:
            await update.message.reply_text(
                "Formato incorrecto. Ejemplo:\n"
                "/add La casa del dragón 2022 S1S2"
            )
            return
        result = find_series_by_title_year(title, year)
        if not result:
            await update.message.reply_text(f"❌ No se encontró «{title}».")
            return
        tmdb_id = int(result["id"])
        title = result.get("name") or title
        year = (result.get("first_air_date") or "").split("-")[0]

    for it in items:
        if int(it["tmdb_id"]) == tmdb_id or normalize(it["title"]) == normalize(title):
            it["completed"] = sorted(set((it.get("completed") or []) + seasons))
            it["title"] = title
            it["year"] = year
            save_db(db)
            await update.message.reply_text(f"Actualizada: {title} ({year})")
            return

    items.append(
        {
            "tmdb_id": tmdb_
