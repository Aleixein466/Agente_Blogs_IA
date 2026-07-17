$ErrorActionPreference = "Stop"

if (-not (Test-Path ".env")) {
  Copy-Item ".env.example" ".env"
  Write-Host "Se creo .env desde .env.example. Ajusta credenciales antes de seguir."
}

Write-Host "Iniciando BlogBot IA en modo local..."
Write-Host "1. Activa tu entorno virtual y ejecuta: pip install -r requirements.txt"
Write-Host "2. Asegura PostgreSQL + pgvector y Ollama"
Write-Host "3. Ejecuta: uvicorn app.main:app --reload"
Write-Host "4. En otra terminal: python -m app.telegram_bot"
