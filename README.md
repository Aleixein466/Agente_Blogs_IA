# BlogBot IA

BlogBot IA es una plataforma local-first para crear, editar, previsualizar, exportar y administrar blogs generados con IA desde Telegram y una interfaz web administrativa.

## Incluye

- API en FastAPI
- Panel administrativo con Jinja2 + Bootstrap 5
- Integracion local con Ollama (`qwen4:2b`)
- Coordinador de tareas compatible con OpenClaw
- PostgreSQL + `pgvector`
- Bot de Telegram con `python-telegram-bot`
- Galeria con imagenes externas y atribucion visible por autor/fuente/licencia
- Historial de versiones, imagenes, prompts y logs de agente

## Inicio rapido

1. Copia `.env.example` a `.env` y ajusta credenciales.
2. Crea la base de datos y ejecuta `database/init.sql`.
3. Instala dependencias:

```powershell
py -3.12 -m venv .venv
.venv\Scripts\Activate.ps1
pip install -r requirements.txt
```

4. Ejecuta el servidor:

```powershell
uvicorn app.main:app --reload
```

5. En otra terminal, inicia el bot:

```powershell
python -m app.telegram_bot
```

La documentacion ampliada esta en [`docs/install.md`](/c:/Users/alexi/Downloads/openclaw/BLOGS/docs/install.md), [`docs/technical.md`](/c:/Users/alexi/Downloads/openclaw/BLOGS/docs/technical.md) y [`docs/user.md`](/c:/Users/alexi/Downloads/openclaw/BLOGS/docs/user.md).
