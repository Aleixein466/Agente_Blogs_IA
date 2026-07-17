CREATE EXTENSION IF NOT EXISTS vector;

CREATE TABLE IF NOT EXISTS users (
    id SERIAL PRIMARY KEY,
    telegram_chat_id VARCHAR(64) UNIQUE,
    username VARCHAR(120) UNIQUE NOT NULL,
    full_name VARCHAR(255),
    hashed_password VARCHAR(255),
    is_active BOOLEAN NOT NULL DEFAULT TRUE,
    role VARCHAR(50) NOT NULL DEFAULT 'admin',
    preferences JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS blogs (
    id SERIAL PRIMARY KEY,
    owner_id INTEGER NOT NULL REFERENCES users(id),
    title VARCHAR(255) NOT NULL,
    slug VARCHAR(255) UNIQUE NOT NULL,
    niche VARCHAR(120) NOT NULL,
    target_audience VARCHAR(255) NOT NULL,
    palette JSONB NOT NULL DEFAULT '{}'::jsonb,
    design_style VARCHAR(120) NOT NULL,
    brief TEXT NOT NULL,
    status VARCHAR(50) NOT NULL DEFAULT 'draft',
    current_version_id INTEGER,
    preview_url VARCHAR(500),
    published_url VARCHAR(500),
    metadata_json JSONB NOT NULL DEFAULT '{}'::jsonb,
    embedding vector(768),
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS blog_versions (
    id SERIAL PRIMARY KEY,
    blog_id INTEGER NOT NULL REFERENCES blogs(id) ON DELETE CASCADE,
    version_number INTEGER NOT NULL,
    change_summary TEXT NOT NULL,
    html_content TEXT NOT NULL,
    css_content TEXT NOT NULL,
    js_content TEXT NOT NULL,
    seo_metadata JSONB NOT NULL DEFAULT '{}'::jsonb,
    generation_prompt TEXT NOT NULL,
    embedding vector(768),
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

ALTER TABLE blogs
    ADD CONSTRAINT IF NOT EXISTS fk_blogs_current_version
    FOREIGN KEY (current_version_id) REFERENCES blog_versions(id);

CREATE TABLE IF NOT EXISTS blog_images (
    id SERIAL PRIMARY KEY,
    blog_id INTEGER NOT NULL REFERENCES blogs(id) ON DELETE CASCADE,
    original_name VARCHAR(255) NOT NULL,
    stored_path VARCHAR(500) NOT NULL,
    mime_type VARCHAR(120) NOT NULL,
    size_bytes INTEGER NOT NULL,
    analysis_json JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS blog_messages (
    id SERIAL PRIMARY KEY,
    blog_id INTEGER NOT NULL REFERENCES blogs(id) ON DELETE CASCADE,
    user_id INTEGER REFERENCES users(id),
    channel VARCHAR(50) NOT NULL DEFAULT 'telegram',
    role VARCHAR(50) NOT NULL,
    message TEXT NOT NULL,
    embedding vector(768),
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS agent_logs (
    id SERIAL PRIMARY KEY,
    task_type VARCHAR(120) NOT NULL,
    status VARCHAR(50) NOT NULL,
    request_payload JSONB NOT NULL DEFAULT '{}'::jsonb,
    response_payload JSONB NOT NULL DEFAULT '{}'::jsonb,
    error_message TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS prompt_history (
    id SERIAL PRIMARY KEY,
    blog_id INTEGER REFERENCES blogs(id) ON DELETE SET NULL,
    prompt_type VARCHAR(120) NOT NULL,
    prompt_text TEXT NOT NULL,
    response_text TEXT,
    embedding vector(768),
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_blogs_embedding ON blogs USING ivfflat (embedding vector_cosine_ops) WITH (lists = 100);
CREATE INDEX IF NOT EXISTS idx_blog_versions_embedding ON blog_versions USING ivfflat (embedding vector_cosine_ops) WITH (lists = 100);
CREATE INDEX IF NOT EXISTS idx_blog_messages_embedding ON blog_messages USING ivfflat (embedding vector_cosine_ops) WITH (lists = 100);
CREATE INDEX IF NOT EXISTS idx_prompt_history_embedding ON prompt_history USING ivfflat (embedding vector_cosine_ops) WITH (lists = 100);

INSERT INTO users (username, full_name, role, preferences)
VALUES ('admin', 'Administrador', 'admin', '{"seed": true}')
ON CONFLICT (username) DO NOTHING;
