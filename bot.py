#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Bot de Telegram para gestionar una lista de series usando TMDB.
Permite añadir series, marcar temporadas completadas y ver su progreso.
/lista y las fichas son públicas, el resto requiere código secreto.
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
    MessageHandler, ContextTypes, filters
)

# ===== CONFIGURA TUS CLAVES =====
# Ahora se leen desde variables de entorno (Railway, .env, etc.)
TELEGRAM_BOT_TOKEN = os.getenv("BOT_TOKEN")
TMDB_API_KEY = os.getenv("TMDB_API_KEY")
# ================================

if not TELEGRAM_BOT_TOKEN:
    raise RuntimeError("❌ No se ha definido la variable BOT_TOKEN en el entorno.")

if not TMDB_API_KEY:
    raise RuntimeError("❌ No se ha definido la variable TMDB_API_KEY en el entorno.")

# Base de datos persistente: en Railway debe usarse /data
DB_PATH = Path("/data/series_data.json")
TMDB_BASE = "https://api.themoviedb.org/3"
IMG_BASE = "https://image.tmdb.org/t/p/w500"
PAGE_SIZE = 10
SECRET_CODE = "Tomodachi"

WEEKDAYS = ["lunes","martes","miércoles","jueves","viernes","sábado","domingo"]
MONTHS = ["enero","febrero","marzo","abril","mayo","junio","julio","agosto","septiembre","octubre","noviembre","diciembre"]

# ======== Utilidades ========
def format_date_natural(dstr: Optional[str]) -> Optional[str]:
    if not dstr: return None
    try:
        d = datetime.strptime(dstr, "%Y-%m-%d").date()
        return f"{WEEKDAYS[d.weekday()]}, {d.day} de {MONTHS[d.month-1]} de {d.year}"
    except Exception:
        return None

def is_future(dstr: Optional[str]) -> bool:
    if not dstr: return False
    try:
        return datetime.strptime(dstr,"%Y-%m-%d").date() > date.today()
    except Exception:
        return False

def load_db() -> Dict[str, Any]:
    # Asegurarse de que el directorio /data exista
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    if DB_PATH.exists():
        try:
            db = json.loads(DB_PATH.read_text(encoding="utf-8"))
            if not isinstance(db, dict): db = {}
        except Exception:
            db = {}
    else:
        db = {}
    if "_auth" not in db: db["_auth"] = {}
    for k,v in list(db.items()):
        if k == "_auth": continue
        if isinstance(v, list): db[k] = {"items": v}
        if isinstance(v, dict) and "items" not in v: v["items"] = v.get("items", [])
    return db

def save_db(db):
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    DB_PATH.write_text(json.dumps(db, ensure_ascii=False, indent=2), encoding="utf-8")

def ensure_chat(db, chat_id): 
    if chat_id not in db: db[chat_id] = {"items": []}

def get_items(db, chat_id):
    ensure_chat(db, chat_id)
    return db[chat_id]["items"]

# ======== TMDB ========
def tmdb_tv_details(tmdb_id: int) -> Dict:
    r = requests.get(f"{TMDB_BASE}/tv/{tmdb_id}", params={"api_key": TMDB_API_KEY, "language":"es-ES"}, timeout=20)
    if r.status_code == 404: raise ValueError("No es una serie válida en TMDB")
    r.raise_for_status()
    return r.json()

def tmdb_search_tv(q: str) -> Dict:
    r = requests.get(f"{TMDB_BASE}/search/tv", params={"api_key": TMDB_API_KEY, "language":"es-ES", "query": q}, timeout=20)
    r.raise_for_status()
    return r.json()

def parse_seasons_string(s: str) -> List[int]:
    return sorted({int(x) for x in re.findall(r"[sS](\d+)", s or "")})

def normalize(s: str) -> str:
    return re.sub(r"\s+", " ", (s or "").strip().lower())

# ======== Estado ========
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
        if sn in (None,0): continue
        ad = s.get("air_date")
        try:
            if ad and datetime.strptime(ad,"%Y-%m-%d").date() <= today:
                emitted.add(int(sn))
        except Exception:
            pass
    if is_really_airing(details):
        ne = details.get("next_episode_to_air") or {}
        current = ne.get("season_number")
        if current: emitted.add(int(current))
    return sorted(emitted)

def mini_progress(emitted_nums, completed, current):
    cset=set(completed or [])
    parts=[]
    for n in emitted_nums:
        mark="✅" if n in cset else "❌"
        if current and n==current:
            parts.append(f"🟢 S{n} {mark}")
        else:
            parts.append(f"S{n} {mark}")
    return " ".join(parts)

def text_progress(emitted_nums, completed):
    have_all = len(emitted_nums)>0 and all(n in set(completed or []) for n in emitted_nums)
    return "✅ Tenemos todo hasta ahora" if have_all else "❌ Todavía nos queda por recopilar"

# ======== Autenticación ========
async def ensure_auth(update: Update, context: ContextTypes.DEFAULT_TYPE) -> Optional[Dict[str, Any]]:
    db=load_db()
    cid=str(update.effective_chat.id)
    if db.get("_auth",{}).get(cid):
        return db
    if update.message:
        await update.message.reply_text(
            "🔒 Este bot está bloqueado para comandos administrativos.\n\n"
            "📋 Puedes usar /lista para ver el listado actual de series y su progreso.\n"
            "🔑 Si eres administrador del bot, introduce la contraseña secreta."
        )
    return None

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    db=load_db()
    cid=str(update.effective_chat.id)
    if not db.get("_auth",{}).get(cid):
        await update.message.reply_text(
            "🔒 Bienvenido.\n\n"
            "📋 Puedes usar /lista para ver el listado de series actuales.\n"
            "🔑 Si eres administrador, introduce la contraseña secreta para gestionar la base de datos."
        )
        return
    await show_menu(update, context)

async def handle_secret(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message: return
    db=load_db()
    cid=str(update.effective_chat.id)
    if db.get("_auth",{}).get(cid):
        return
    if (update.message.text or "").strip()==SECRET_CODE:
        db["_auth"][cid]=True
        save_db(db)
        await update.message.reply_text("✅ Acceso concedido")
        await show_menu(update,context)
    else:
        await update.message.reply_text("❌ Código incorrecto.")

# ======== Menú ========
async def show_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    txt=(
        "📺 Menú\n\n"
        "• /add <TMDBID> <S1S2S3>\n"
        "  o /add <Título> <Año?> <S1S2S3>\n"
        "• /lista – ver tus series (10 por página, usa ⬅️➡️)\n"
        "• /borrar <tmdbid|título>\n"
        "• /bloquear – volver a pedir el código secreto\n"
    )
    if update.message:
        await update.message.reply_text(txt)
    else:
        await update.callback_query.edit_message_text(txt)

async def bloquear(update: Update, context: ContextTypes.DEFAULT_TYPE):
    db=load_db()
    cid=str(update.effective_chat.id)
    if cid in db.get("_auth",{}):
        del db["_auth"][cid]
        save_db(db)
    await update.message.reply_text(
        "🔒 Bot bloqueado.\n\n"
        "📋 Puedes seguir usando /lista para ver las series.\n"
        "🔑 Introduce la contraseña secreta si eres administrador."
    )

# ======== /add ========
def extract_title_year_and_seasons(args):
    if not args: return None, None, []
    year=None
    seasons_str=""
    if re.search(r"[sS]\d+", args[-1]):
        seasons_str=args[-1]
        args=args[:-1]
    if args and re.fullmatch(r"\d{4}", args[-1]):
        year=args[-1]
        args=args[:-1]
    title=" ".join(args).strip() if args else None
    seasons=sorted({int(x) for x in re.findall(r"[sS](\d+)", seasons_str)})
    return title, year, seasons

def find_series_by_title_year(title, year):
    res=tmdb_search_tv(title)
    results=res.get("results",[])
    if not results: return None
    if year:
        for r in results:
            y=(r.get("first_air_date") or "").split("-")[0]
            if y==year: return r
    return results[0]

async def add_series(update: Update, context: ContextTypes.DEFAULT_TYPE):
    db=await ensure_auth(update,context)
    if not db:return
    cid=str(update.effective_chat.id)
    items=get_items(db,cid)
    args=context.args
    if not args:
        await update.message.reply_text("Uso: /add <TMDBID> <S1S2S3> o /add <Título> <Año?> <S1S2S3>")
        return
    if re.fullmatch(r"\d+", args[0]):
        tmdb_id=int(args[0])
        seasons=sorted({int(x) for x in re.findall(r"[sS](\d+)", "".join(args[1:]))})
        try:
            d=tmdb_tv_details(tmdb_id)
        except Exception:
            await update.message.reply_text("⚠️ ID no válido o no corresponde a una serie de TV.")
            return
        title=d.get("name") or d.get("original_name") or f"TMDB {tmdb_id}"
        year=(d.get("first_air_date") or "").split("-")[0]
    else:
        title,year,seasons=extract_title_year_and_seasons(args)
        if not title:
            await update.message.reply_text("⚠️ Formato no válido. Ejemplo: /add The Witcher 2019 S1S2")
            return
        result=find_series_by_title_year(title,year)
        if not result:
            await update.message.reply_text(f"❌ No se encontró «{title}».")
            return
        tmdb_id=int(result["id"])
        title=result.get("name") or title
        year=(result.get("first_air_date") or "").split("-")[0]
    for it in items:
        if int(it.get("tmdb_id"))==tmdb_id or normalize(it.get("title"))==normalize(title):
            it["completed"]=sorted(set((it.get("completed") or [])+seasons))
            it["title"]=title
            it["year"]=year
            save_db(db)
            await update.message.reply_text(f"✅ Serie «{title} ({year})» actualizada con éxito.")
            return
    items.append({"tmdb_id":tmdb_id,"title":title,"year":year,"completed":seasons})
    save_db(db)
    await update.message.reply_text(f"✅ Serie «{title} ({year})» añadida con éxito.")

# ======== /borrar ========
async def borrar(update: Update, context: ContextTypes.DEFAULT_TYPE):
    db=await ensure_auth(update,context)
    if not db:return
    cid=str(update.effective_chat.id)
    items=get_items(db,cid)
    if not context.args:
        await update.message.reply_text("Uso: /borrar <tmdbid|título>")
        return
    q=" ".join(context.args).strip().lower()
    new=[it for it in items if not (q==str(it.get("tmdb_id")) or normalize(it.get("title"))==q)]
    if len(new)<len(items):
        db[cid]["items"]=new
        save_db(db)
        await update.message.reply_text("🗑️ Serie eliminada.")
    else:
        await update.message.reply_text("No se encontró ninguna serie con ese nombre o ID.")

# ======== /lista ========
def make_list_keyboard(total: int, page: int) -> InlineKeyboardMarkup:
    start = page * PAGE_SIZE
    end = min(start + PAGE_SIZE, total)
    rows = []
    buttons = [InlineKeyboardButton(str(i+1), callback_data=f"show:{i}") for i in range(start, end)]
    for i in range(0, len(buttons), 5):
        rows.append(buttons[i:i+5])
    nav = []
    if page > 0: nav.append(InlineKeyboardButton("⬅️", callback_data=f"page:{page-1}"))
    if end < total: nav.append(InlineKeyboardButton("➡️", callback_data=f"page:{page+1}"))
    if nav: rows.append(nav)
    return InlineKeyboardMarkup(rows)

def build_list_entry(it: Dict) -> str:
    d = tmdb_tv_details(int(it["tmdb_id"]))
    title = it.get("title") or d.get("name") or "—"
    year = it.get("year") or (d.get("first_air_date") or "").split("-")[0]
    emitted = emitted_season_numbers(d)
    completed = it.get("completed", [])
    current = None
    if is_really_airing(d):
        ne = d.get("next_episode_to_air") or {}
        current = ne.get("season_number")
    progress = text_progress(emitted, completed)
    mini = mini_progress(emitted, completed, current)
    extra = ""
    if is_really_airing(d):
        ne = d.get("next_episode_to_air") or {}
        plat = ((d.get("networks") or [{}])[0].get("name")) if (d.get("networks") or []) else None
        when = format_date_natural(ne.get("air_date"))
        extra = f"📡 En emisión — Temporada {current}"
        if when and plat: extra += f"\n🕓 Próximo episodio: {when} en {plat}"
    else:
        st = (d.get("status") or "").lower()
        extra = "📺 Serie finalizada" if st=="ended" else "⏳ Pendiente"
    return f"**{title} ({year})** — {extra}\n{progress}\n🔸 Progreso: {mini}"

async def list_series(update: Update, context: ContextTypes.DEFAULT_TYPE, page: int = 0):
    db=load_db()
    cid=str(update.effective_chat.id)
    items=get_items(db,cid)
    if not items:
        await update.message.reply_text("Tu lista está vacía. Usa /add si eres admin para añadir series.")
        return
    start=page*PAGE_SIZE
    end=min(start+PAGE_SIZE,len(items))
    lines=["*Tus series:*"]
    for idx,it in enumerate(items[start:end],start=start+1):
        try:
            entry=build_list_entry(it)
        except Exception:
            entry=f"**{it.get('title','—')} ({it.get('year','')})**"
        lines.append(f"{idx}. {entry}")
    text="\n\n".join(lines)
    kb=make_list_keyboard(len(items),page)
    await update.message.reply_text(text,parse_mode=ParseMode.MARKDOWN,reply_markup=kb)

async def turn_page(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q=update.callback_query
    await q.answer()
    page=int(q.data.split(":")[1])
    db=load_db()
    cid=str(q.message.chat_id)
    items=get_items(db,cid)
    start=page*PAGE_SIZE
    end=min(start+PAGE_SIZE,len(items))
    lines=["*Tus series:*"]
    for idx,it in enumerate(items[start:end],start=start+1):
        try:
            entry=build_list_entry(it)
        except Exception:
            entry=f"**{it.get('title','—')} ({it.get('year','')})**"
        lines.append(f"{idx}. {entry}")
    text="\n\n".join(lines)
    kb=make_list_keyboard(len(items),page)
    await q.edit_message_text(text,parse_mode=ParseMode.MARKDOWN,reply_markup=kb)

# ======== Ficha ========
async def show_series(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q=update.callback_query
    await q.answer()
    db=load_db()
    cid=str(q.message.chat_id)
    items=get_items(db,cid)
    idx=int(q.data.split(":")[1])
    entry=items[idx]
    tmdb_id=int(entry["tmdb_id"])
    d=tmdb_tv_details(tmdb_id)
    title=d.get("name") or entry.get("title","—")
    overview=(d.get("overview") or "Sin sinopsis disponible.").strip()
    poster=d.get("poster_path")
    emitted=emitted_season_numbers(d)
    completed=entry.get("completed",[])
    current=None
    if is_really_airing(d):
        ne=d.get("next_episode_to_air") or {}
        current=ne.get("season_number")
    mini=mini_progress(emitted,completed,current)
    progress=text_progress(emitted,completed)
    caption=f"<b>{title}</b>\n\n{overview}\n\n{mini}\n{progress}"
    if poster:
        await q.message.reply_photo(photo=IMG_BASE+poster,caption=caption,parse_mode=ParseMode.HTML)
    else:
        await q.message.reply_text(caption,parse_mode=ParseMode.HTML)

# ======== main ========
def main():
    app = ApplicationBuilder().token(TELEGRAM_BOT_TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_secret))
    app.add_handler(CommandHandler("add", add_series))
    app.add_handler(CommandHandler("borrar", borrar))
    app.add_handler(CommandHandler("bloquear", bloquear))
    app.add_handler(CommandHandler("lista", list_series))
    app.add_handler(CallbackQueryHandler(turn_page, pattern="^page:"))
    app.add_handler(CallbackQueryHandler(show_series, pattern="^show:"))
    print("🚀 Bot listo. Ejecutando...")
    app.run_polling()

if __name__ == "__main__":
    main()
