from sqlalchemy.orm import Session

from app.models import AgentLog
from app.services.blog_generator import BlogGeneratorService


class BlogAgentCoordinator:
    def __init__(self) -> None:
        self.generator = BlogGeneratorService()

    async def create_blog(self, db: Session, owner_username: str, prompt: str):
        db.add(
            AgentLog(
                task_type="agent.create_blog",
                status="started",
                request_payload={"owner_username": owner_username, "prompt": prompt},
                response_payload={},
            )
        )
        db.commit()
        return await self.generator.create_blog(db, owner_username, prompt)
