from __future__ import annotations

import json
import logging
import time
from dataclasses import dataclass
from urllib import error
from urllib import request

CONNECT_TIMEOUT = 10
READ_TIMEOUT = 60
RETRY_COUNT = 2
logger = logging.getLogger(__name__)


class APIRequestError(RuntimeError):
    """Provider failure with actionable HTTP and response details."""

    def __init__(
        self,
        provider: str,
        message: str,
        *,
        status_code: int | None = None,
        response_body: str = "",
    ) -> None:
        self.provider = provider
        self.status_code = status_code
        self.response_body = _safe_response_body(response_body)
        status = f"HTTP {status_code}: " if status_code is not None else ""
        detail = f"；API 返回：{self.response_body}" if self.response_body else ""
        super().__init__(f"{provider} 请求失败：{status}{message}{detail}")


@dataclass
class ProviderConfig:
    provider: str
    api_key: str
    base_url: str
    model: str
    temperature: float
    timeout: int
    connect_timeout: int = CONNECT_TIMEOUT
    read_timeout: int = READ_TIMEOUT
    retry_count: int = RETRY_COUNT
    language: str = "zh_CN"


@dataclass
class ConnectionTestResult:
    provider: str
    api_key_valid: bool
    endpoint_ok: bool
    model_available: bool
    model: str
    elapsed_ms: int
    available_models: list[str]
    models_api_supported: bool


def complete(prompt: str, config: ProviderConfig, cancel_token=None) -> str:
    """Call an OpenAI-compatible chat completion provider."""

    return str(
        complete_with_metadata(prompt, config, cancel_token=cancel_token)["content"]
    )


def complete_with_metadata(
    prompt: str,
    config: ProviderConfig,
    cancel_token=None,
) -> dict[str, object]:
    """Call a provider and return content plus request metadata."""

    _validate_config(config)
    _check_cancel(cancel_token)
    payload = {
        "model": config.model,
        "messages": [
            {"role": "system", "content": _system_prompt(config.language)},
            {"role": "user", "content": prompt},
        ],
        "temperature": config.temperature,
        "response_format": {"type": "json_object"},
    }
    started_at = time.perf_counter()
    response_data = _post_with_retry(payload, config, cancel_token=cancel_token)
    _check_cancel(cancel_token)
    elapsed = time.perf_counter() - started_at

    return {
        "content": _extract_content(response_data),
        "usage": response_data.get("usage", {}),
        "elapsed_seconds": round(elapsed, 2),
        "model": config.model,
        "provider": config.provider,
    }


def list_models(config: ProviderConfig, cancel_token=None) -> list[str] | None:
    """Return model IDs, or None when the provider has no models endpoint."""

    _validate_credentials(config)
    _check_cancel(cancel_token)
    api_request = request.Request(
        config.base_url.rstrip("/") + "/models",
        headers={"Authorization": f"Bearer {config.api_key}"},
        method="GET",
    )

    try:
        with request.urlopen(
            api_request,
            timeout=config.connect_timeout or config.timeout,
        ) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except error.HTTPError as exc:
        body = _read_error_body(exc)

        if exc.code in {404, 405, 501}:
            return None

        raise APIRequestError(
            config.provider,
            str(exc.reason or exc),
            status_code=exc.code,
            response_body=body,
        ) from exc
    except error.URLError as exc:
        raise APIRequestError(config.provider, str(exc.reason or exc)) from exc
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise APIRequestError(
            config.provider,
            f"models 接口返回了无效 JSON：{exc}",
        ) from exc

    _check_cancel(cancel_token)
    items = payload.get("data", []) if isinstance(payload, dict) else []

    return [
        str(item.get("id"))
        for item in items
        if isinstance(item, dict) and item.get("id")
    ]


def choose_model(
    config: ProviderConfig,
    candidates: list[str],
    cancel_token=None,
) -> tuple[str, list[str], bool]:
    """Select a configured/candidate model using the provider models endpoint."""

    models = list_models(config, cancel_token=cancel_token)
    ordered = _unique([config.model, *candidates])

    if models is None:
        return (ordered[0] if ordered else ""), [], False

    for candidate in ordered:
        if candidate in models:
            return candidate, models, True

    discovered = _chat_model_candidates(models)

    if discovered:
        return discovered[0], models, True

    return "", models, True


def test_connection(
    config: ProviderConfig,
    candidates: list[str],
    cancel_token=None,
) -> ConnectionTestResult:
    """Discover a model and issue a real minimal completion request."""

    _validate_credentials(config)
    started_at = time.perf_counter()
    model, available_models, supported = choose_model(
        config,
        candidates,
        cancel_token=cancel_token,
    )
    attempts = _unique(
        [model, config.model, *candidates]
        if not supported
        else [model]
    )
    last_error: Exception | None = None

    for candidate in attempts:
        if not candidate:
            continue

        test_config = ProviderConfig(**{**config.__dict__, "model": candidate})

        try:
            complete(
                (
                    "Return exactly this JSON object: {\"status\":\"ok\"}. "
                    "Do not add any other fields."
                ),
                test_config,
                cancel_token=cancel_token,
            )
        except APIRequestError as exc:
            last_error = exc

            if exc.status_code in {400, 404, 422} and not supported:
                continue

            raise
        else:
            return ConnectionTestResult(
                provider=config.provider,
                api_key_valid=True,
                endpoint_ok=True,
                model_available=True,
                model=candidate,
                elapsed_ms=round((time.perf_counter() - started_at) * 1000),
                available_models=available_models,
                models_api_supported=supported,
            )

    if last_error is not None:
        raise last_error

    raise APIRequestError(config.provider, "没有可用于测试的模型")


def _validate_credentials(config: ProviderConfig) -> None:
    if not config.api_key:
        raise ValueError(f"{config.provider} API Key 未配置。")

    if not config.base_url:
        raise ValueError(f"{config.provider} Endpoint 未配置。")


def _validate_config(config: ProviderConfig) -> None:
    _validate_credentials(config)

    if not config.model:
        raise ValueError(f"{config.provider} Model 未配置。")


def _post_with_retry(
    payload: dict[str, object],
    config: ProviderConfig,
    cancel_token=None,
) -> dict[str, object]:
    last_error: Exception | None = None

    for attempt in range(config.retry_count + 1):
        _check_cancel(cancel_token)

        try:
            return _post(payload, config)
        except APIRequestError as exc:
            last_error = exc
            retryable = exc.status_code == 429 or (
                exc.status_code is not None and exc.status_code >= 500
            )

            if not retryable or attempt >= config.retry_count:
                raise

            logger.warning(
                "[AI] llm=FAIL attempt=%s error=API_ERROR detail=%s",
                attempt + 1,
                exc,
            )
            _cancelable_wait(cancel_token, min(30, 2 ** attempt + 1))
        except error.URLError as exc:
            last_error = exc

            if attempt >= config.retry_count:
                break

            logger.warning(
                "[AI] llm=FAIL attempt=%s error=API_ERROR detail=%s",
                attempt + 1,
                exc,
            )
            _cancelable_wait(cancel_token, min(10, 2 ** attempt))

    if isinstance(last_error, APIRequestError):
        raise last_error

    reason = getattr(last_error, "reason", last_error)
    raise APIRequestError(config.provider, str(reason or "未知网络错误")) from last_error


def _post(payload: dict[str, object], config: ProviderConfig) -> dict[str, object]:
    api_request = request.Request(
        config.base_url.rstrip("/") + "/chat/completions",
        data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        headers={
            "Authorization": f"Bearer {config.api_key}",
            "Content-Type": "application/json",
        },
        method="POST",
    )
    timeout = config.read_timeout or config.timeout

    try:
        with request.urlopen(api_request, timeout=timeout) as response:
            response_body = response.read().decode("utf-8")
    except error.HTTPError as exc:
        raise APIRequestError(
            config.provider,
            str(exc.reason or exc),
            status_code=exc.code,
            response_body=_read_error_body(exc),
        ) from exc

    try:
        data = json.loads(response_body)
    except json.JSONDecodeError as exc:
        raise APIRequestError(
            config.provider,
            f"API 返回了无效 JSON：{exc}",
            response_body=response_body,
        ) from exc

    if not isinstance(data, dict):
        raise APIRequestError(config.provider, "API 返回结构不是 JSON 对象")

    return data


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


def _system_prompt(language: str) -> str:
    if language == "en_US":
        return (
            "You are a professional space-news editor. Return strict JSON only, "
            "with no Markdown fences or explanatory text. All generated prose "
            "must be in English."
        )

    return (
        "你是专业航天新闻编辑。只返回严格 JSON，不要输出 Markdown 代码块或解释文字。"
        "所有生成性文本必须使用简体中文。"
    )


def _read_error_body(exc: error.HTTPError) -> str:
    try:
        return exc.read().decode("utf-8", errors="replace")
    except Exception:
        return ""


def _safe_response_body(body: str) -> str:
    text = str(body or "").replace("\r", " ").replace("\n", " ").strip()
    return text[:2000]


def _unique(values: list[str]) -> list[str]:
    return list(dict.fromkeys(str(value).strip() for value in values if str(value).strip()))


def _chat_model_candidates(models: list[str]) -> list[str]:
    excluded = ("embedding", "rerank", "audio", "image", "tts", "whisper")
    return [
        model
        for model in models
        if not any(marker in model.lower() for marker in excluded)
        and any(
            marker in model.lower()
            for marker in (
                "chat",
                "reason",
                "instruct",
                "deepseek",
                "gpt",
                "claude",
                "gemini",
            )
        )
    ]


def _check_cancel(cancel_token) -> None:
    if cancel_token is not None:
        cancel_token.check()


def _cancelable_wait(cancel_token, seconds: float) -> None:
    if cancel_token is not None:
        if cancel_token.wait(seconds):
            cancel_token.check()
        return

    time.sleep(seconds)
