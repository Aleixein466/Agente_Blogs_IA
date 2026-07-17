from datetime import datetime

from pgvector.sqlalchemy import Vector
from sqlalchemy import JSON, Boolean, DateTime, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base


class TimestampMixin:
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=datetime.utcnow, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False
    )


class User(TimestampMixin, Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    telegram_chat_id: Mapped[str | None] = mapped_column(String(64), unique=True)
    username: Mapped[str] = mapped_column(String(120), unique=True, index=True)
    full_name: Mapped[str | None] = mapped_column(String(255))
    hashed_password: Mapped[str | None] = mapped_column(String(255))
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    role: Mapped[str] = mapped_column(String(50), default="admin", nullable=False)
    preferences: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)

    blogs: Mapped[list["Blog"]] = relationship(back_populates="owner")
    messages: Mapped[list["BlogMessage"]] = relationship(back_populates="user")


class Blog(TimestampMixin, Base):
    __tablename__ = "blogs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    owner_id: Mapped[int] = mapped_column(ForeignKey("users.id"), nullable=False)
    title: Mapped[str] = mapped_column(String(255), nullable=False)
    slug: Mapped[str] = mapped_column(String(255), unique=True, index=True, nullable=False)
    niche: Mapped[str] = mapped_column(String(120), nullable=False)
    target_audience: Mapped[str] = mapped_column(String(255), nullable=False)
    palette: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    design_style: Mapped[str] = mapped_column(String(120), nullable=False)
    brief: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(String(50), default="draft", nullable=False)
    current_version_id: Mapped[int | None] = mapped_column(ForeignKey("blog_versions.id"))
    preview_url: Mapped[str | None] = mapped_column(String(500))
    published_url: Mapped[str | None] = mapped_column(String(500))
    metadata_json: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    embedding: Mapped[list[float] | None] = mapped_column(Vector(768))

    owner: Mapped["User"] = relationship(back_populates="blogs")
    versions: Mapped[list["BlogVersion"]] = relationship(
        back_populates="blog", foreign_keys="BlogVersion.blog_id", cascade="all, delete-orphan"
    )
    current_version: Mapped["BlogVersion | None"] = relationship(foreign_keys=[current_version_id], post_update=True)
    images: Mapped[list["BlogImage"]] = relationship(back_populates="blog", cascade="all, delete-orphan")
    messages: Mapped[list["BlogMessage"]] = relationship(back_populates="blog", cascade="all, delete-orphan")
    prompts: Mapped[list["PromptHistory"]] = relationship(back_populates="blog", cascade="all, delete-orphan")


class BlogVersion(TimestampMixin, Base):
    __tablename__ = "blog_versions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    blog_id: Mapped[int] = mapped_column(ForeignKey("blogs.id"), nullable=False)
    version_number: Mapped[int] = mapped_column(Integer, nullable=False)
    change_summary: Mapped[str] = mapped_column(Text, nullable=False)
    html_content: Mapped[str] = mapped_column(Text, nullable=False)
    css_content: Mapped[str] = mapped_column(Text, nullable=False)
    js_content: Mapped[str] = mapped_column(Text, nullable=False)
    seo_metadata: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    generation_prompt: Mapped[str] = mapped_column(Text, nullable=False)
    embedding: Mapped[list[float] | None] = mapped_column(Vector(768))

    blog: Mapped["Blog"] = relationship(back_populates="versions", foreign_keys=[blog_id])


class BlogImage(TimestampMixin, Base):
    __tablename__ = "blog_images"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    blog_id: Mapped[int] = mapped_column(ForeignKey("blogs.id"), nullable=False)
    original_name: Mapped[str] = mapped_column(String(255), nullable=False)
    stored_path: Mapped[str] = mapped_column(String(500), nullable=False)
    mime_type: Mapped[str] = mapped_column(String(120), nullable=False)
    size_bytes: Mapped[int] = mapped_column(Integer, nullable=False)
    analysis_json: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)

    blog: Mapped["Blog"] = relationship(back_populates="images")


class BlogMessage(TimestampMixin, Base):
    __tablename__ = "blog_messages"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    blog_id: Mapped[int] = mapped_column(ForeignKey("blogs.id"), nullable=False)
    user_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"))
    channel: Mapped[str] = mapped_column(String(50), default="telegram", nullable=False)
    role: Mapped[str] = mapped_column(String(50), nullable=False)
    message: Mapped[str] = mapped_column(Text, nullable=False)
    embedding: Mapped[list[float] | None] = mapped_column(Vector(768))

    blog: Mapped["Blog"] = relationship(back_populates="messages")
    user: Mapped["User | None"] = relationship(back_populates="messages")


class AgentLog(TimestampMixin, Base):
    __tablename__ = "agent_logs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    task_type: Mapped[str] = mapped_column(String(120), nullable=False)
    status: Mapped[str] = mapped_column(String(50), nullable=False)
    request_payload: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    response_payload: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    error_message: Mapped[str | None] = mapped_column(Text)


class PromptHistory(TimestampMixin, Base):
    __tablename__ = "prompt_history"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    blog_id: Mapped[int | None] = mapped_column(ForeignKey("blogs.id"))
    prompt_type: Mapped[str] = mapped_column(String(120), nullable=False)
    prompt_text: Mapped[str] = mapped_column(Text, nullable=False)
    response_text: Mapped[str | None] = mapped_column(Text)
    embedding: Mapped[list[float] | None] = mapped_column(Vector(768))

    blog: Mapped["Blog | None"] = relationship(back_populates="prompts")
