# Diagrama de base de datos

```text
users 1---N blogs 1---N blog_versions
blogs 1---N blog_images
blogs 1---N blog_messages
blogs 1---N prompt_history
agent_logs independiente
blogs.current_version_id -> blog_versions.id
```
