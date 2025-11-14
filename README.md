# Bot de series TMDB para Telegram (Railway + GitHub ready)

Este bot permite gestionar una lista de series usando TMDB. Soporta:

- Añadir series por ID de TMDB o por título/año
- Ver lista paginada (/lista)
- Ver ficha con póster y sinopsis (botones)
- Marcar temporadas completadas
- Sistema de autenticación por código secreto para administrar

## Variables de entorno

Debes definir:

- `BOT_TOKEN` → token del bot de Telegram
- `TMDB_API_KEY` → API key de TMDB

En desarrollo puedes usar un archivo `.env` o exportarlas en tu shell.
En Railway se configuran desde la sección **Variables** del proyecto.

## Persistencia de datos

La base de datos se guarda en:

- `/data/series_data.json`

En Railway, la ruta `/data` es persistente entre despliegues y reinicios.

## Despliegue en Railway

1. Crea un repositorio en GitHub con estos archivos.
2. En Railway: **New Project → Deploy from GitHub**.
3. Elige el repositorio del bot.
4. En **Variables**, añade:

   - `BOT_TOKEN`
   - `TMDB_API_KEY`

5. Railway detectará el `Procfile` y levantará el worker con:

   ```bash
   worker: python bot.py
   ```

¡Listo! El bot estará ejecutándose 24/7 en Railway.
