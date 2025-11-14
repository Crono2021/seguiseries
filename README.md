# Bot de seguimiento de series (Telegram + TMDB)

Bot en Python para gestionar una lista de series por chat usando TMDB.

## Variables de entorno

- `BOT_TOKEN`: token del bot de Telegram
- `TMDB_API_KEY`: API key de TMDB

## Railway + Volumen persistente

1. Crea un volumen en Railway y móntalo en:

   `/mnt/series_db`

2. Sube este proyecto a GitHub y conéctalo a Railway.
3. Configura las variables de entorno `BOT_TOKEN` y `TMDB_API_KEY`.
4. Deploy y listo.

Cada chat (grupo o privado) tendrá su propia lista de series almacenada en
`/mnt/series_db/series_data.json` (persistente entre deploys).
