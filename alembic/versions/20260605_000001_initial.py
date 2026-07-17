"""initial blogbot schema"""

from alembic import op
import sqlalchemy as sa
from pgvector.sqlalchemy import Vector


revision = "20260605_000001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("CREATE EXTENSION IF NOT EXISTS vector")
    op.create_table(
        "users",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("telegram_chat_id", sa.String(length=64), nullable=True, unique=True),
        sa.Column("username", sa.String(length=120), nullable=False, unique=True),
        sa.Column("full_name", sa.String(length=255), nullable=True),
        sa.Column("hashed_password", sa.String(length=255), nullable=True),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        sa.Column("role", sa.String(length=50), nullable=False, server_default="admin"),
        sa.Column("preferences", sa.JSON(), nullable=False, server_default=sa.text("'{}'")),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("NOW()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("NOW()")),
    )
    op.create_table(
        "blogs",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("owner_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("title", sa.String(length=255), nullable=False),
        sa.Column("slug", sa.String(length=255), nullable=False, unique=True),
        sa.Column("niche", sa.String(length=120), nullable=False),
        sa.Column("target_audience", sa.String(length=255), nullable=False),
        sa.Column("palette", sa.JSON(), nullable=False, server_default=sa.text("'{}'")),
        sa.Column("design_style", sa.String(length=120), nullable=False),
        sa.Column("brief", sa.Text(), nullable=False),
        sa.Column("status", sa.String(length=50), nullable=False, server_default="draft"),
        sa.Column("current_version_id", sa.Integer(), nullable=True),
        sa.Column("preview_url", sa.String(length=500), nullable=True),
        sa.Column("published_url", sa.String(length=500), nullable=True),
        sa.Column("metadata_json", sa.JSON(), nullable=False, server_default=sa.text("'{}'")),
        sa.Column("embedding", Vector(768), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("NOW()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("NOW()")),
    )
    op.create_table(
        "blog_versions",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("blog_id", sa.Integer(), sa.ForeignKey("blogs.id", ondelete="CASCADE"), nullable=False),
        sa.Column("version_number", sa.Integer(), nullable=False),
        sa.Column("change_summary", sa.Text(), nullable=False),
        sa.Column("html_content", sa.Text(), nullable=False),
        sa.Column("css_content", sa.Text(), nullable=False),
        sa.Column("js_content", sa.Text(), nullable=False),
        sa.Column("seo_metadata", sa.JSON(), nullable=False, server_default=sa.text("'{}'")),
        sa.Column("generation_prompt", sa.Text(), nullable=False),
        sa.Column("embedding", Vector(768), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("NOW()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("NOW()")),
    )
    op.create_foreign_key("fk_blogs_current_version", "blogs", "blog_versions", ["current_version_id"], ["id"])
    op.create_table(
        "blog_images",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("blog_id", sa.Integer(), sa.ForeignKey("blogs.id", ondelete="CASCADE"), nullable=False),
        sa.Column("original_name", sa.String(length=255), nullable=False),
        sa.Column("stored_path", sa.String(length=500), nullable=False),
        sa.Column("mime_type", sa.String(length=120), nullable=False),
        sa.Column("size_bytes", sa.Integer(), nullable=False),
        sa.Column("analysis_json", sa.JSON(), nullable=False, server_default=sa.text("'{}'")),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("NOW()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("NOW()")),
    )
    op.create_table(
        "blog_messages",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("blog_id", sa.Integer(), sa.ForeignKey("blogs.id", ondelete="CASCADE"), nullable=False),
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=True),
        sa.Column("channel", sa.String(length=50), nullable=False, server_default="telegram"),
        sa.Column("role", sa.String(length=50), nullable=False),
        sa.Column("message", sa.Text(), nullable=False),
        sa.Column("embedding", Vector(768), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("NOW()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("NOW()")),
    )
    op.create_table(
        "agent_logs",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("task_type", sa.String(length=120), nullable=False),
        sa.Column("status", sa.String(length=50), nullable=False),
        sa.Column("request_payload", sa.JSON(), nullable=False, server_default=sa.text("'{}'")),
        sa.Column("response_payload", sa.JSON(), nullable=False, server_default=sa.text("'{}'")),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("NOW()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("NOW()")),
    )
    op.create_table(
        "prompt_history",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("blog_id", sa.Integer(), sa.ForeignKey("blogs.id", ondelete="SET NULL"), nullable=True),
        sa.Column("prompt_type", sa.String(length=120), nullable=False),
        sa.Column("prompt_text", sa.Text(), nullable=False),
        sa.Column("response_text", sa.Text(), nullable=True),
        sa.Column("embedding", Vector(768), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("NOW()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("NOW()")),
    )


def downgrade() -> None:
    op.drop_table("prompt_history")
    op.drop_table("agent_logs")
    op.drop_table("blog_messages")
    op.drop_table("blog_images")
    op.drop_constraint("fk_blogs_current_version", "blogs", type_="foreignkey")
    op.drop_table("blog_versions")
    op.drop_table("blogs")
    op.drop_table("users")
