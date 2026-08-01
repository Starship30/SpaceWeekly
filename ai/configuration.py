from __future__ import annotations

from ai.providers import ProviderConfig


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
