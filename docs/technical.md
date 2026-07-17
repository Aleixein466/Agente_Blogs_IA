# Manual tecnico

## Capas

- `app/main.py`: punto de entrada FastAPI.
- `app/api/`: rutas API y vistas web.
- `app/services/`: integraciones y generacion.
- `app/models/`: entidades SQLAlchemy.
- `app/telegram_bot.py`: bot de Telegram por polling.
- `database/init.sql`: bootstrap SQL con `pgvector`.
- `generated_blogs/`: salida HTML/CSS/JS.

## Flujo de generacion

1. Telegram o panel web envia un prompt.
2. `BlogGeneratorService` registra prompt, llama a OpenClaw y a Ollama.
3. Se normaliza el brief.
4. Se crea `Blog`.
5. Se crea `BlogVersion`.
6. Se escriben los archivos en `generated_blogs/<slug>/`.
7. Se expone la vista previa por HTTP.

## Seguridad

- Login simple de administrador por sesion.
- Token CSRF en el panel.
- Sanitizacion recomendada en futuras iteraciones para HTML editable por usuario.
- ORM SQLAlchemy para reducir riesgo de SQL injection.
