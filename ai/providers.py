import json
from dataclasses import dataclass
from urllib import error
from urllib import request


@dataclass
class ProviderConfig:
    provider: str
    api_key: str
    base_url: str
    model: str
    temperature: float
    max_tokens: int
    timeout: int


def complete(prompt: str, config: ProviderConfig) -> str:
    """Call an OpenAI-compatible chat completion provider."""
    if not config.api_key:
        raise ValueError(f"{config.provider} API Key is not configured.")

    if not config.base_url:
        raise ValueError(f"{config.provider} Base URL is not configured.")

    if not config.model:
        raise ValueError(f"{config.provider} Model is not configured.")

    payload = {
        "model": config.model,
        "messages": [
            {
                "role": "system",
                "content": "你是专业航天新闻编辑，输出严格 JSON。",
            },
            {
                "role": "user",
                "content": prompt,
            },
        ],
        "temperature": config.temperature,
        "max_tokens": config.max_tokens,
    }
    api_request = request.Request(
        config.base_url.rstrip("/") + "/chat/completions",
        data=json.dumps(payload).encode("utf-8"),
        headers={
            "Authorization": f"Bearer {config.api_key}",
            "Content-Type": "application/json",
        },
        method="POST",
    )

    try:
        with request.urlopen(api_request, timeout=config.timeout) as response:
            data = json.loads(response.read().decode("utf-8"))
    except error.URLError as exc:
        raise RuntimeError(f"{config.provider} request failed.") from exc

    return _extract_content(data)


def _extract_content(data: dict[str, object]) -> str:
    choices = data.get("choices")

    if not isinstance(choices, list) or not choices:
        raise ValueError("AI response has no choices.")

    message = choices[0].get("message")

    if not isinstance(message, dict):
        raise ValueError("AI response has no message.")

    content = message.get("content")

    if not isinstance(content, str):
        raise ValueError("AI response content is invalid.")

    return content
