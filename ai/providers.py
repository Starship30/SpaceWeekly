import json
import time
from dataclasses import dataclass
from urllib import error
from urllib import request

CONNECT_TIMEOUT = 10
READ_TIMEOUT = 60
RETRY_COUNT = 2
JSON_SYSTEM_PROMPT = (
    "你是专业航天新闻编辑。必须只输出严格 JSON，"
    "不要输出 Markdown，不要输出解释文字。"
)


@dataclass
class ProviderConfig:
    provider: str
    api_key: str
    base_url: str
    model: str
    temperature: float
    max_tokens: int
    timeout: int
    connect_timeout: int = CONNECT_TIMEOUT
    read_timeout: int = READ_TIMEOUT
    retry_count: int = RETRY_COUNT


def complete(prompt: str, config: ProviderConfig) -> str:
    """Call an OpenAI-compatible chat completion provider."""
    return str(complete_with_metadata(prompt, config)["content"])


def complete_with_metadata(
    prompt: str,
    config: ProviderConfig,
) -> dict[str, object]:
    """Call a provider and return content plus request metadata."""
    _validate_config(config)
    payload = {
        "model": config.model,
        "messages": [
            {"role": "system", "content": JSON_SYSTEM_PROMPT},
            {"role": "user", "content": prompt},
        ],
        "temperature": config.temperature,
        "max_tokens": config.max_tokens,
        "response_format": {"type": "json_object"},
    }
    started_at = time.perf_counter()
    response_data = _post_with_retry(payload, config)
    elapsed = time.perf_counter() - started_at

    return {
        "content": _extract_content(response_data),
        "usage": response_data.get("usage", {}),
        "elapsed_seconds": round(elapsed, 2),
        "model": config.model,
        "provider": config.provider,
    }


def _validate_config(config: ProviderConfig) -> None:
    if not config.api_key:
        raise ValueError(f"{config.provider} API Key is not configured.")

    if not config.base_url:
        raise ValueError(f"{config.provider} Base URL is not configured.")

    if not config.model:
        raise ValueError(f"{config.provider} Model is not configured.")


def _post_with_retry(
    payload: dict[str, object],
    config: ProviderConfig,
) -> dict[str, object]:
    last_error: Exception | None = None

    for attempt in range(config.retry_count + 1):
        try:
            return _post(payload, config)
        except error.HTTPError as exc:
            last_error = exc

            if exc.code == 429:
                time.sleep(min(30, 2 ** attempt + 1))
                continue

            if attempt >= config.retry_count:
                break
        except error.URLError as exc:
            last_error = exc

            if attempt >= config.retry_count:
                break

            time.sleep(min(10, 2 ** attempt))

    raise RuntimeError(f"{config.provider} request failed.") from last_error


def _post(payload: dict[str, object], config: ProviderConfig) -> dict[str, object]:
    api_request = request.Request(
        config.base_url.rstrip("/") + "/chat/completions",
        data=json.dumps(payload).encode("utf-8"),
        headers={
            "Authorization": f"Bearer {config.api_key}",
            "Content-Type": "application/json",
        },
        method="POST",
    )
    timeout = config.read_timeout or config.timeout

    with request.urlopen(api_request, timeout=timeout) as response:
        return json.loads(response.read().decode("utf-8"))


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
