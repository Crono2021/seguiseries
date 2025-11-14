#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# Bot de Telegram para gestionar series por TMDB
# Listas independientes por chat_id
# Persistencia REAL usando un volumen montado en /data

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
    raise RuntimeError("❌ Falta la variable BOT_TOKEN")

if not TMDB_API_KEY:
    raise RuntimeError("❌ Falta la variable TMDB_API_KEY")

# =============================
# BASE DE DATOS — PERSISTENTE
# =============================
# IMPORTANTE: en Railway debes montar el volumen en /data
DB_DIR = Path("/data")
DB_DIR.mkdir(parents=True, exist_ok=True)

DB_PATH = DB_DIR / "series_data.json"

TMDB_BASE = "https://api.themoviedb.org/3"
IMG_BASE = "https://image.tmdb.org/t/p/w500"
PAGE_SIZE = 10

WEEKDAYS = ["lunes","martes","miércoles","jueves","viernes","sábado","domingo"]
MONTHS = ["enero","febrero","marzo","abril","mayo","junio","julio","agosto","septiembre","octubre","noviembre","diciembre"]

# =============================
# UTILIDADES BD
# =============================
def load_db() -> Dict[str, Any]:
    """Carga el JSON de /data/series_data.json si existe, si no devuelve {}."""
    try:
        if DB_PATH.exists():
            raw = DB_PATH.read_text(encoding="utf-8")
            if not raw.strip():
                print("[DB] Archivo vacío, usando {}")
                return {}
            data = json.loads(raw)
            if isinstance(data, dict):
                print(f"[DB] Cargada BD desde {DB_PATH} con {len(data)} chats")
                return data
            else:
                print("[DB] Contenido no dict, reseteando a {}")
                return {}
        else:
            print(f"[DB] No existe {DB_PATH}, empezamos con BD vacía")
            return {}
    except Exception as e:
        print(f"[DB] Error al leer BD: {e}")
        return {}

def save_db(db: Dict[str, Any]):
    """Guarda el JSON en /data/series_data.json."""
    try:
        DB_DIR.mkdir(parents=True, exist_ok=True)
        DB_PATH.write_text(
            json.dumps(db, ensure_ascii=False, indent=2),
            encoding="utf-8"
        )
        total_items = sum(len(v.get("items", [])) for v in db.values())
        print(f"[DB] BD guardada en {DB_PATH} ({len(db)} chats, {total_items} series en total)")
    except Exception as e:
        print(f"[DB] ERROR guardando BD: {e}")

def ensure_chat(db: Dict[str, Any], cid: str):
    if cid not in db:
        db[cid] = {"items": []}

def get_items(db: Dict[str, Any], cid: str):
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
# ESTADO / PROGRESO
# =============================
def is_future(dstr: Optional[str]) -> bool:
    if not dstr:
        return False
    try:
        return datetime.strptime(dstr, "%Y-%m-%d").date() > date.today()
    except Exception:
        return False

def is_really_airing(details: Dict) -> bool:
    ne = details.get("next_episode_to_air") or {}
    return bool(ne.get("air_date") and is_future(ne["air_date"]))

def emitted_season_numbers(details: Dict) -> List[int]:
    emitted = set()
    today = date.today()
    for s in details.get("seasons") or []:
        sn = s.get("season_number")
        ad = s.get("air_date")
        if sn and ad:
            try:
                if datetime.strptime(ad, "%Y-%m-%d").date() <= today:
                    emitted.add(sn)
            except Exception:
                pass
    if is_really_airing(details):
        ne = details["next_episode_to_air"]
        emitted.add(ne.get("season_number"))
    return sorted(emitted)

def mini_progress(emitted, completed, current):
    cset = set(completed or [])
    result = []
    for n in emitted:
        tick = "✅" if n in cset else "❌"
        if current == n:
            result.append(f"🟢 S{n} {tick}")
        else:
            result.append(f"S{n} {tick}")
    return " ".join(result)

def text_progress(emitted, completed):
    if emitted and all(s in completed for s in emitted):
        return "✅ Tenemos todo hasta ahora"
    return "❌ Todavía nos queda por recopilar"

# =============================
# COMANDOS
# =============================
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "📺 Bienvenido al bot de series.\n\n"
        "Comandos disponibles:\n"
        "• /add <ID> S1S2\n"
        "• /add <Título> <Año?> S1S2\n"
        "• /lista\n"
        "• /borrar <ID|título>\n\n"
        "Cada usuario/chat tiene su propia lista separada."
    )

async def add_series(update: Update, context: ContextTypes.DEFAULT_TYPE):
    db = load_db()
    cid = str(update.effective_chat.id)
    items = get_items(db, cid)

    args = context.args
    if not args:
        await update.message.reply_text(
            "Uso: /add <ID> S1S2 o /add <Título> 2022 S1S2\n"
            "Ejemplo: /add La casa del dragón 2022 S1S2"
        )
        return

    if re.fullmatch(r"\d+", args[0]):  # TMDB ID directo
        tmdb_id = int(args[0])
        seasons = parse_seasons_string("".join(args[1:]))
        try:
            d = tmdb_tv_details(tmdb_id)
        except Exception:
            await update.message.reply_text("ID no válido.")
            return
        title = d.get("name") or d.get("original_name") or f"TMDB {tmdb_id}"
        year = d.get("first_air_date", "").split("-")[0]
    else:
        # Búsqueda por título
        seasons_str = ""

        if re.search(r"[sS]\d+", args[-1]):
            seasons_str = args[-1]
            args = args[:-1]

        year = None
        if args and re.fullmatch(r"\d{4}", args[-1]):
            year = args[-1]
            args = args[:-1]

        title = " ".join(args).strip()
        seasons = parse_seasons_string(seasons_str)

        results = tmdb_search_tv(title).get("results", [])
        if not results:
            await update.message.reply_text("No encontrado.")
            return

        if year:
            match = next(
                (r for r in results if (r.get("first_air_date") or "").startswith(year)),
                None
            )
            result = match or results[0]
        else:
            result = results[0]

        tmdb_id = result["id"]
        title = result.get("name") or title
        year = (result.get("first_air_date") or "").split("-")[0]

    # Guardar o actualizar
    for it in items:
        if int(it["tmdb_id"]) == tmdb_id:
            it["completed"] = sorted(set(it.get("completed", []) + seasons))
            it["title"] = title
            it["year"] = year
            save_db(db)
            await update.message.reply_text(f"Actualizada: {title} ({year})")
            return

    items.append({
        "tmdb_id": tmdb_id,
        "title": title,
        "year": year,
        "completed": seasons
    })
    save_db(db)
    await update.message.reply_text(f"Añadida: {title} ({year})")

async def borrar(update: Update, context: ContextTypes.DEFAULT_TYPE):
    db = load_db()
    cid = str(update.effective_chat.id)
    items = get_items(db, cid)

    q = " ".join(context.args).strip().lower()
    if not q:
        await update.message.reply_text("Uso: /borrar <ID|título>")
        return

    new = [i for i in items if not (q == str(i["tmdb_id"]) or normalize(i["title"]) == q)]
    if len(new) != len(items):
        db[cid]["items"] = new
        save_db(db)
        await update.message.reply_text("🗑️ Serie eliminada.")
    else:
        await update.message.reply_text("No encontrada.")

# =============================
# LISTAR Y FICHA
# =============================
def make_keyboard(total, page):
    start = page * PAGE_SIZE
    end = min(start + PAGE_SIZE, total)

    rows = []
    row = []
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
            d = tmdb_tv_details(int(it["tmdb_id"]))
            emitted = emitted_season_numbers(d)
            completed = it.get("completed", [])
            current = None
            if is_really_airing(d):
                current = d["next_episode_to_air"]["season_number"]
            mini = mini_progress(emitted, completed, current)
            progress = text_progress(emitted, completed)
            lines.append(
                f"{idx}. **{it['title']} ({it['year']})**\n"
                f"{progress}\n🔸 {mini}"
            )
        except Exception:
            lines.append(f"{idx}. {it['title']} ({it['year']})")

    kb = make_keyboard(len(items), page)
    await update.message.reply_text(
        "\n\n".join(lines),
        parse_mode=ParseMode.MARKDOWN,
        reply_markup=kb
    )

async def turn_page(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Maneja solo el cambio de página, sin reutilizar list_series con tipos raros."""
    q = update.callback_query
    await q.answer()
    page = int(q.data.split(":")[1])

    db = load_db()
    cid = str(q.message.chat_id)
    items = get_items(db, cid)

    if not items:
        await q.edit_message_text("Lista vacía.")
        return

    start = page * PAGE_SIZE
    end = min(start + PAGE_SIZE, len(items))

    lines = ["*Tus series:*"]
    for idx, it in enumerate(items[start:end], start=start+1):
        try:
            d = tmdb_tv_details(int(it["tmdb_id"]))
            emitted = emitted_season_numbers(d)
            completed = it.get("completed", [])
            current = None
            if is_really_airing(d):
                current = d["next_episode_to_air"]["season_number"]
            mini = mini_progress(emitted, completed, current)
            progress = text_progress(emitted, completed)
            lines.append(
                f"{idx}. **{it['title']} ({it['year']})**\n"
                f"{progress}\n🔸 {mini}"
            )
        except Exception:
            lines.append(f"{idx}. {it['title']} ({it['year']})")

    kb = make_keyboard(len(items), page)
    await q.edit_message_text(
        "\n\n".join(lines),
        parse_mode=ParseMode.MARKDOWN,
        reply_markup=kb
    )

async def show_series(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    idx = int(q.data.split(":")[1])

    db = load_db()
    cid = str(q.message.chat_id)
    entry = get_items(db, cid)[idx]

    d = tmdb_tv_details(int(entry["tmdb_id"]))

    title = d.get("name") or entry["title"]
    year = (d.get("first_air_date") or "").split("-")[0]
    overview = d.get("overview", "Sinopsis no disponible")
    poster = d.get("poster_path")

    emitted = emitted_season_numbers(d)
    completed = entry.get("completed", [])
    current = None
    if is_really_airing(d):
        current = d["next_episode_to_air"]["season_number"]

    mini = mini_progress(emitted, completed, current)
    progress = text_progress(emitted, completed)

    caption = f"<b>{title} ({year})</b>\n\n{overview}\n\n{mini}\n{progress}"

    await q.answer()

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
    print(f"[INIT] Usando BD en {DB_PATH}")
    print(f"[INIT] Existe BD? {DB_PATH.exists()}")
    app = ApplicationBuilder().token(TELEGRAM_BOT_TOKEN).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("add", add_series))
    app.add_handler(CommandHandler("borrar", borrar))
    app.add_handler(CommandHandler("lista", list_series))
    app.add_handler(CommandHandler("menu", start))

    app.add_handler(CallbackQueryHandler(turn_page, pattern="^page:"))
    app.add_handler(CallbackQueryHandler(show_series, pattern="^show:"))

    print("🚀 Bot en marcha (persistencia real en /data)…")
    app.run_polling()

if __name__ == "__main__":
    main()
