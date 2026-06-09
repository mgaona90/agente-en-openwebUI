"""Settings loaded from env / .env."""
from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", case_sensitive=False, extra="ignore")

    # === Auth ===
    anthropic_api_key: str = Field(min_length=1)
    anthropic_base_url: str = "https://api.anthropic.com"

    agent_model: str = "claude-haiku-4-5-20251001"

    # Langfuse — optional; tracing only runs when all three are set.
    langfuse_public_key: str = ""
    langfuse_secret_key: str = ""
    langfuse_host: str = "https://cloud.langfuse.com"

    # Agent behavior
    default_system_prompt: str = (
        "You are a helpful assistant. "
        "Use the tools available to you to answer questions or complete tasks."
    )
    max_turns: int = 40
    expose_tool_results: bool = False
    log_level: str = "INFO"

    @property
    def tracing_enabled(self) -> bool:
        return bool(self.langfuse_public_key and self.langfuse_secret_key and self.langfuse_host)

    @property
    def mcp_enabled(self) -> bool:
        return False  # Configure MCP servers per-deployment as needed


def get_settings() -> Settings:
    return Settings()
