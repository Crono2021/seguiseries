# Usa una imagen oficial de Python
FROM python:3.10-slim

# Ajustes básicos de Python
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

# Directorio de trabajo dentro del contenedor
WORKDIR /app

# Instalar dependencias de sistema mínimas (por si requests o TLS las necesita)
RUN apt-get update && apt-get install -y --no-install-recommends \
    ca-certificates \
 && rm -rf /var/lib/apt/lists/*

# Copiamos solo requirements primero, para aprovechar la cache de Docker
COPY requirements.txt .

# Instalamos dependencias de Python
RUN pip install --no-cache-dir -r requirements.txt

# Copiamos el resto del código
COPY . .

# Nos aseguramos de que el directorio del volumen exista
# (Railway montará el volumen encima de esta ruta)
RUN mkdir -p /mnt/series_db

# Comando de arranque
CMD ["python", "bot.py"]
