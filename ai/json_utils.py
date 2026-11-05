from __future__ import annotations

import json
import logging
import re
import time
from datetime import datetime
from pathlib import Path
from uuid import uuid4

from resources import resource_path

logger = logging.getLogger(__name__)


class AIResponseParseError(ValueError):
    """Raised after all safe JSON recovery strategies have failed."""


class AIEmptyResponseError(AIResponseParseError):
    """Raised when a provider returned no content at all."""


class AIResponseValidationError(AIResponseParseError):
    """A valid JSON object failed schema or content checks."""


class AITruncatedResponseError(AIResponseParseError):
    """The provider reported an output length limit."""


def request_json(
    prompt: str,
    provider_config,
    *,
    module_name: str,
    cancel_token=None,
    validator=None,
    max_attempts: int = 2,
    retry_base_delay: float = 0.25,
) -> dict[str, object]:
    """Request strict JSON with bounded parse/empty-response retries."""

    from ai.providers import complete_with_metadata

    raw_outputs: list[str] = []
    errors: list[str] = []

    attempts = max(1, min(int(max_attempts), 4))
    last_error: AIResponseParseError | None = None
    secret = getattr(provider_config,"api_key","")
    redact = lambda value: value.replace(secret,"[已隐藏]") if isinstance(secret,str) and secret else value

    for attempt in range(attempts):
        request_prompt = prompt

        if attempt:
            previous_reason = errors[-1] if errors else "invalid JSON"
            request_prompt = (
                prompt
                + "\n\nYour previous response was rejected for this reason: "
                + previous_reason
                + ". Correct that specific problem. Return only the exact JSON "
                "object required by the schema, with valid escaping and no "
                "Markdown code fence."
            )

        response = complete_with_metadata(
            request_prompt,
            provider_config,
            cancel_token=cancel_token,
        )
        content = response["content"]
        finish = response.get("finish_reason")
        raw_outputs.append(content)

        stage = "解析"
        try:
            if finish in ("length","max_tokens"):
                raise AITruncatedResponseError("模型返回被截断")
            if finish in ("content_filter","aborted","insufficient_system_resource"):
                raise AIResponseParseError("模型未完成回复")
            data = parse_json_object(content)

            if validator is not None:
                stage = "校验"
                validator(data)

            return data
        except AIResponseParseError as exc:
            if isinstance(exc,AIResponseValidationError):
                stage = "校验"
            last_error = AIResponseValidationError(str(exc)) if stage=="校验" else exc
            errors.append(redact(f"{stage}失败（结束原因 {finish or '未提供'}）：{exc}"))
            label = "Translator" if module_name.startswith("translator") else "AI"
            logger.warning(
                "[%s] llm=FAIL attempt=%s error=%s detail=%s",
                label,
                attempt + 1,
                stage,
                redact(str(exc)),
            )

            if attempt + 1 < attempts:
                _cancelable_wait(
                    cancel_token,
                    min(2.0, max(0.0, retry_base_delay) * (2 ** attempt)),
                )

    raw_path = save_raw_response(
        [redact(value) for value in raw_outputs],
        module_name=module_name,
        model=provider_config.model,
        prompt=redact(prompt),
        errors=[redact(value) for value in errors],
    )
    message = ("模型内容未通过校验：" if isinstance(last_error,AIResponseValidationError) else "")+redact(str(last_error))
    failure = type(last_error)(message)
    failure.diagnostic_path = raw_path
    raise failure from None


def parse_json_object(content: str) -> dict[str, object]:
    """Strip wrappers, extract an object, and repair common JSON mistakes."""

    cleaned = _strip_code_fence(content)

    if not cleaned:
        raise AIEmptyResponseError("AI 返回内容为空")

    # Quoted strings are indivisible tokens, including escaped quotes and commas.
    def remove_trailing(match):
        if match[0].startswith('"') or cleaned[:match.start()].rstrip().endswith(('{','[',',',':')):
            return match[0]
        return ''
    def invalid_constant(value):
        raise AIResponseParseError(f"模型返回了无效的 JSON 数值 {value}")
    repaired = re.sub(r'"(?:[^"\\]|\\.)*"|,\s*(?=[}\]])',remove_trailing,cleaned,flags=re.S)
    last_error: json.JSONDecodeError | None = None

    for candidate in (cleaned,repaired):
        try:
            data = json.loads(candidate,parse_constant=invalid_constant)
        except json.JSONDecodeError as exc:
            last_error = exc
            continue

        if not isinstance(data,dict):
            raise AIResponseValidationError("模型返回必须是 JSON 对象")
        return data

    detail = str(last_error) if last_error is not None else "response is not an object"
    raise AIResponseParseError(f"AI 返回内容无法解析为 JSON：{detail}")


def save_raw_response(
    raw_outputs: list[str] | str,
    *,
    module_name: str,
    model: str,
    prompt: str = "",
    errors: list[str] | None = None,
) -> Path:
    """Persist safe AI diagnostics after the retry path is exhausted."""

    from logging_config import _redact
    outputs = raw_outputs if isinstance(raw_outputs, list) else [raw_outputs]
    output_dir = resource_path("logs", "ai_raw")
    output_dir.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
    path = output_dir / f"{module_name}_{timestamp}_{uuid4().hex[:6]}.log"
    sections = [
        f"time: {datetime.now().astimezone().isoformat()}",
        f"module: {module_name}",
        f"model: {model or '-'}",
        "",
        "prompt:",
        prompt or "-",
        "",
        "parse_errors:",
        "\n".join(errors or []) or "-",
    ]

    for index, output in enumerate(outputs, start=1):
        sections.extend(
            [
                "",
                f"raw_output_{index}:",
                str(output),
            ]
        )

    path.write_text(_redact("\n".join(sections)), encoding="utf-8")
    logger.error(
        "AI 响应未通过检查，已保存请求和原始输出：%s（模型：%s）",
        path,
        model or "-",
    )
    return path


def _strip_code_fence(content: str) -> str:
    text = str(content or "").strip().lstrip("\ufeff")
    fenced = re.match(
        r"^```(?:json)?\s*(.*?)\s*```$",
        text,
        flags=re.IGNORECASE | re.DOTALL,
    )

    if fenced:
        text = fenced.group(1).strip()

    return text


def _cancelable_wait(cancel_token, seconds: float) -> None:
    if seconds <= 0:
        return

    deadline = time.monotonic() + seconds

    while time.monotonic() < deadline:
        if cancel_token is not None:
            cancel_token.check()

        time.sleep(min(0.1, max(0.0, deadline - time.monotonic())))
