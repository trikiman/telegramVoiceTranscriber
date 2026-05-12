"""Channel digest subsystem (v1.1).

Listens for new posts in user-selected Telegram channels, batches them,
scores via Groq LLM every N minutes, and delivers a filtered digest.
"""
