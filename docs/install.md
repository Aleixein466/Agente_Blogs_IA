# Instalacion

## Requisitos

- Python 3.12+
- PostgreSQL 16+
- Extension `pgvector`
- Ollama con `qwen4:2b`
- Token de Telegram

## Pasos

1. Crea `.env` a partir de `.env.example`.
2. Instala dependencias con `pip install -r requirements.txt`.
3. Crea la base `blogbot_ia`.
4. Ejecuta `database/init.sql`.
5. Inicia FastAPI con `uvicorn app.main:app --reload`.
6. Inicia el bot con `python -m app.telegram_bot`.
7. Abre `http://127.0.0.1:8000`.

## Ollama

```powershell
ollama pull qwen4:2b
ollama serve
```

## OpenClaw

Si ya tienes OpenClaw corriendo, configura `OPENCLAW_BASE_URL` y pon `OPENCLAW_ENABLED=true`. Si no, el coordinador interno sigue funcionando y registra la tarea localmente.

## Imagenes externas

El proyecto ahora puede buscar imagenes en varios proveedores:

- `PEXELS_API_KEY`: recomendado si quieres resultados rapidos con atribucion a Pexels y fotografo.
- `UNSPLASH_ACCESS_KEY`: opcional para ampliar resultados con atribucion a Unsplash y autor.
- `OPENVERSE_BASE_URL`: por defecto usa `https://api.openverse.org` y sirve como fallback con licencia visible.

Si no configuras `PEXELS_API_KEY` ni `UNSPLASH_ACCESS_KEY`, el sistema intentara usar Openverse.
