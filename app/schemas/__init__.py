from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field


class BlogCreateRequest(BaseModel):
    prompt: str = Field(min_length=5)
    owner_username: str = "admin"


class BlogEditRequest(BaseModel):
    instruction: str = Field(min_length=3)


class BlogPublishRequest(BaseModel):
    publish: bool = True


class BlogResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    title: str
    slug: str
    niche: str
    target_audience: str
    design_style: str
    status: str
    preview_url: str | None
    published_url: str | None
    current_version_id: int | None
    created_at: datetime
    updated_at: datetime


class BlogVersionResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    version_number: int
    change_summary: str
    html_content: str
    css_content: str
    js_content: str
    seo_metadata: dict
    created_at: datetime



class LoginRequest(BaseModel):
    username: str
    password: str


class TelegramGenerateRequest(BaseModel):
    chat_id: int
    text: str
    username: str | None = None


class VoiceSynthesisRequest(BaseModel):
    text: str = Field(min_length=2, max_length=400)
