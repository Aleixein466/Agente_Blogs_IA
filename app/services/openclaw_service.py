from __future__ import annotations

import httpx

from app.config import get_settings


class OpenClawService:
    def __init__(self) -> None:
        self.settings = get_settings()

    async def dispatch(self, task_type: str, payload: dict) -> dict:
        if not self.settings.openclaw_enabled:
            return {"source": "internal", "task_type": task_type, "accepted": True, "payload": payload}

        try:
            async with httpx.AsyncClient(timeout=30.0) as client:
                response = await client.post(
                    f"{self.settings.openclaw_base_url}/tasks",
                    json={"task_type": task_type, "payload": payload},
                )
                response.raise_for_status()
                return response.json()
        except Exception as exc:
            return {"source": "fallback", "task_type": task_type, "accepted": False, "error": str(exc)}
