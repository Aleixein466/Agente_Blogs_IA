# BlogBot IA

BlogBot IA es una plataforma local-first para crear, editar, previsualizar, exportar y administrar blogs generados con IA desde una interfaz web y, de forma opcional, desde Telegram.

## Que incluye

- API en FastAPI
- Panel web con Jinja2 + Bootstrap 5
- Integracion local con Ollama
- PostgreSQL 16 + `pgvector`
- Bot de Telegram opcional
- Generacion y publicacion local de blogs
- Soporte para busqueda de imagenes externas con atribucion
- Soporte para notas de voz y respuesta de audio

## Arquitectura rapida

- `app/`: API, panel web, servicios y bot de Telegram
- `database/init.sql`: estructura inicial de la base de datos
- `templates/` y `static/`: frontend del panel administrativo
- `generated_blogs/`: blogs generados localmente
- `uploads/`: archivos subidos por el usuario
- `logs/`: logs locales
- `docs/`: documentacion complementaria

## Requisitos

Antes de empezar, asegurate de tener instalado:

- Python 3.12 o superior
- PostgreSQL 16 o superior
- Extension `pgvector`
- Ollama
- Git

Opcional:

- Token de bot de Telegram si quieres usar el bot
- Claves de Pexels o Unsplash si quieres enriquecer la galeria de imagenes

## Variables y servicios necesarios

Para que la aplicacion web funcione correctamente, estos componentes son los importantes:

- Base de datos PostgreSQL disponible y accesible desde `DATABASE_URL`
- Extension `pgvector` habilitada
- Ollama corriendo en `OLLAMA_BASE_URL`
- Archivo `.env` configurado

Estos componentes son opcionales:

- `TELEGRAM_BOT_TOKEN`: solo si vas a iniciar el bot de Telegram
- `OPENCLAW_ENABLED=true`: solo si ya tienes OpenClaw corriendo y quieres integrarlo
- `PEXELS_API_KEY` o `UNSPLASH_ACCESS_KEY`: solo para mejorar la busqueda de imagenes

## Instalacion recomendada

### 1. Clonar el repositorio

```powershell
git clone https://github.com/Aleixein466/Agente_Blogs_IA.git
cd Agente_Blogs_IA
```

### 2. Crear y activar el entorno virtual

```powershell
py -3.12 -m venv .venv
.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
pip install -r requirements.txt
```

### 3. Crear el archivo `.env`

```powershell
Copy-Item .env.example .env
```

Abre `.env` y revisa como minimo estas variables:

```env
APP_HOST=127.0.0.1
APP_PORT=8000
SECRET_KEY=cambia-esta-clave
ADMIN_USERNAME=admin
ADMIN_PASSWORD=Admin123
DATABASE_URL=postgresql+psycopg://postgres:Admin@localhost:5432/blogbot_ia
OLLAMA_BASE_URL=http://127.0.0.1:11434
OLLAMA_MODEL=qwen4:2b
PUBLIC_BASE_URL=http://127.0.0.1:8000
```

Si vas a usar Telegram, completa tambien:

```env
TELEGRAM_BOT_TOKEN=tu_token_real
TELEGRAM_ALLOWED_CHAT_IDS=123456789
```

## Opcion A: levantar dependencias con Docker

Esta es la forma mas rapida de arrancar PostgreSQL y Ollama.

### 1. Iniciar contenedores

```powershell
docker compose up -d
```

Esto levanta:

- PostgreSQL en `localhost:5432`
- Ollama en `localhost:11434`

### 2. Descargar el modelo de Ollama

```powershell
docker exec -it blogbot-ollama ollama pull qwen4:2b
```

Si prefieres usar Ollama instalado en tu sistema, no necesitas este paso dentro del contenedor.

## Opcion B: instalar dependencias manualmente

Usa esta opcion si no quieres Docker.

### PostgreSQL

1. Instala PostgreSQL 16.
2. Crea una base de datos llamada `blogbot_ia`.
3. Habilita `pgvector`.
4. Ejecuta el script `database/init.sql`.

Ejemplo desde `psql`:

```sql
CREATE DATABASE blogbot_ia;
\c blogbot_ia
CREATE EXTENSION IF NOT EXISTS vector;
```

Luego ejecuta:

```powershell
psql -U postgres -d blogbot_ia -f database/init.sql
```

### Ollama

```powershell
ollama serve
ollama pull qwen4:2b
```

## Preparar la base de datos

Si usaste Docker, el contenedor de PostgreSQL monta `database/init.sql` automaticamente en el primer arranque.

Si no estas seguro de si ya se aplico, puedes ejecutarlo manualmente:

```powershell
psql -U postgres -d blogbot_ia -f database/init.sql
```

## Ejecutar la aplicacion

Con el entorno virtual activado:

```powershell
uvicorn app.main:app --reload
```

La aplicacion quedara disponible en:

- Panel web: `http://127.0.0.1:8000`
- Healthcheck: `http://127.0.0.1:8000/health`

## Ejecutar el bot de Telegram

Este paso es opcional. Solo hazlo si ya configuraste `TELEGRAM_BOT_TOKEN`.

En otra terminal, con el entorno virtual activado:

```powershell
python -m app.telegram_bot
```

## Verificar que todo funcione

### 1. Verificar el endpoint de salud

Abre en el navegador:

`http://127.0.0.1:8000/health`

Debes recibir algo similar a:

```json
{"status":"ok","app":"BlogBot IA","model":"qwen4:2b"}
```

### 2. Ejecutar la prueba rapida

```powershell
pytest tests/test_smoke.py
```

## Flujo minimo recomendado

Si quieres probar el proyecto con lo esencial:

1. Levanta PostgreSQL y Ollama.
2. Copia `.env.example` a `.env`.
3. Ajusta `DATABASE_URL`, `SECRET_KEY` y `OLLAMA_BASE_URL` si hace falta.
4. Instala dependencias de Python.
5. Ejecuta `uvicorn app.main:app --reload`.
6. Abre `http://127.0.0.1:8000`.

## Integracion con OpenClaw

Si ya tienes OpenClaw corriendo, configura:

```env
OPENCLAW_ENABLED=true
OPENCLAW_BASE_URL=http://127.0.0.1:4100
```

Si no lo configuras, el proyecto sigue funcionando con su coordinador local.

## Imagenes externas

Puedes mejorar la busqueda de imagenes configurando una o varias de estas variables:

- `PEXELS_API_KEY`
- `UNSPLASH_ACCESS_KEY`
- `OPENVERSE_BASE_URL`

Si no agregas claves de Pexels ni Unsplash, el sistema intentara usar Openverse como fallback.

## Audio y voz

El proyecto incluye:

- Transcripcion de audio con `faster-whisper`
- Sintesis de voz con `kokoro`

Configuracion relevante en `.env`:

```env
WHISPER_MODEL=small
WHISPER_DEVICE=cpu
WHISPER_COMPUTE_TYPE=int8
WHISPER_CPU_THREADS=4
STT_LANGUAGE=es
KOKORO_LANG_CODE=e
KOKORO_VOICE=ef_dora
```

La primera ejecucion puede descargar modelos y tardar mas de lo normal.

## Problemas comunes

### La app no conecta a PostgreSQL

Revisa:

- Que PostgreSQL este corriendo
- Que `DATABASE_URL` coincida con tu usuario, password, puerto y base de datos
- Que la base `blogbot_ia` exista
- Que `pgvector` este habilitado

### La generacion con IA no responde

Revisa:

- Que Ollama este corriendo
- Que `OLLAMA_BASE_URL` sea correcto
- Que el modelo `qwen4:2b` este descargado

Si Ollama no responde, algunas partes del sistema pueden usar respuestas de respaldo, pero no tendras la experiencia completa.

### El bot de Telegram falla al iniciar

Revisa:

- Que `TELEGRAM_BOT_TOKEN` tenga un valor real
- Que el entorno virtual este activado
- Que no haya otro proceso usando el mismo bot

### Falla PowerShell al activar `.venv`

Si PowerShell bloquea scripts, prueba:

```powershell
Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass
```

Y luego:

```powershell
.venv\Scripts\Activate.ps1
```

## Script local rapido

Puedes ejecutar:

```powershell
.\run_local.ps1
```

Ese script:

- Crea `.env` si todavia no existe
- Te recuerda los pasos necesarios

No reemplaza la instalacion ni levanta automaticamente todos los servicios.

## Documentacion adicional

- [Guia de instalacion](docs/install.md)
- [Documentacion tecnica](docs/technical.md)
- [Manual de usuario](docs/user.md)
- [Arquitectura](docs/architecture.md)
