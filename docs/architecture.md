# Arquitectura

```text
Telegram User
   |
python-telegram-bot
   |
BlogGeneratorService ---- OpenClawService
   |                         |
   |                         +--> OpenClaw (opcional)
   |
   +--> OllamaService --> Ollama qwen4:2b
   |
   +--> SQLAlchemy --> PostgreSQL + pgvector
   |
   +--> generated_blogs/ + uploads/
   |
FastAPI API + Admin UI
```

## Notas

- El sistema es local-first y funciona aun si OpenClaw no esta disponible.
- Ollama tiene fallback estructurado para no bloquear la generacion inicial.
