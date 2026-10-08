from __future__ import annotations

from ai.providers import ProviderConfig

PROVIDER_URLS = {
    "DeepSeek": "https://api.deepseek.com",
    "OpenAI": "https://api.openai.com/v1",
    "Gemini": "https://generativelanguage.googleapis.com/v1beta/openai",
    "Claude": "https://api.anthropic.com/v1",
    "OpenRouter": "https://openrouter.ai/api/v1",
    "SiliconFlow": "https://api.siliconflow.cn/v1",
}


def provider_config_from_settings(
    settings,
    *,
    model: str | None = None,
) -> ProviderConfig:
    """Build the single runtime provider configuration from AppSettings."""

    return ProviderConfig(
        provider=settings.ai_provider,
        api_key=settings.deepseek_api_key,
        base_url=settings.deepseek_base_url,
        model=model or settings.deepseek_model,
        temperature=settings.temperature,
        timeout=settings.request_timeout,
        connect_timeout=settings.connect_timeout,
        read_timeout=settings.read_timeout,
        retry_count=settings.retry_count,
        language=settings.language,
    )
