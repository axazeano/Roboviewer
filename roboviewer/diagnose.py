"""Provider diagnostics: one minimal request with the response fully broken down.

Needed when a run dies on authentication or on gateway incompatibility: instead of
eight parallel agents exactly one call is made, and the raw status, response body
and headers are surfaced — usually the thing that explains a 401.
"""

from __future__ import annotations

import asyncio
import json
from typing import Any

import httpx
from openai import APIError, APIStatusError, AsyncOpenAI

from .config import ProviderConfig

_SECRET_HEADERS = ("authorization", "api-key", "x-api-key", "token", "cookie", "secret")

_HINTS: dict[int, str] = {
    400: (
        "Шлюз не принял запрос — обычно из-за поля, которого он не знает. "
        "Кандидаты: parallel_tool_calls (выстави false) и tool_choice "
        "(см. terminal_tool_choice ниже)."
    ),
    401: (
        "Авторизация не прошла. Если ключ точно рабочий, дело обычно в форме его "
        "передачи: SDK по умолчанию шлёт Authorization: Bearer <ключ>, а шлюз может "
        "ждать api-key, X-Api-Key или другую схему — см. provider.auth_header и "
        "provider.auth_scheme. Сравни дамп запроса ниже с тем, что проходит вручную."
    ),
    403: "Ключ принят, но доступа к этой модели нет. Проверь имя модели и права ключа.",
    404: (
        "Эндпоинт не найден. Чаще всего base_url указан без /v1 либо, наоборот, "
        "с лишним /chat/completions на конце."
    ),
    422: "Шлюз не принял схему запроса. Проверь имя модели и max_tokens.",
    429: "Лимит запросов. Ключ рабочий — дело в квоте.",
}


def _mask_value(name: str, value: str) -> str:
    """Mask secrets while keeping their shape: the scheme and the key's tail stay
    visible, so "wrong key" can be told apart from "wrong auth scheme"."""
    if not any(marker in name.lower() for marker in _SECRET_HEADERS):
        return value
    scheme, _, secret = value.partition(" ")
    if not secret:  # header without a scheme, the whole value is the key
        return f"{value[:4]}…{value[-4:]}" if len(value) > 12 else "***"
    tail = f"{secret[:4]}…{secret[-4:]}" if len(secret) > 12 else "***"
    return f"{scheme} {tail}"


def _mask_headers(headers: dict[str, str]) -> dict[str, str]:
    return {name: _mask_value(name, value) for name, value in headers.items()}


class _Wire:
    """What actually went over the wire and what came back."""

    def __init__(self) -> None:
        self.method = ""
        self.url = ""
        self.request_headers: dict[str, str] = {}
        self.status: int | None = None
        self.response_headers: dict[str, str] = {}

    def http_client(self, timeout: float) -> httpx.AsyncClient:
        async def on_request(request: httpx.Request) -> None:
            self.method = request.method
            self.url = str(request.url)
            self.request_headers = dict(request.headers)

        async def on_response(response: httpx.Response) -> None:
            self.status = response.status_code
            self.response_headers = dict(response.headers)

        return httpx.AsyncClient(
            timeout=timeout,
            event_hooks={"request": [on_request], "response": [on_response]},
        )

    def dump(self) -> None:
        print("Запрос, ушедший на провод:")
        print(f"  {self.method} {self.url}")
        skip = ("host", "accept-encoding", "connection", "content-length", "accept", "user-agent")
        for name, value in _mask_headers(self.request_headers).items():
            low = name.lower()
            # x-stainless-* is SDK telemetry, unrelated to authentication
            if low in skip or low.startswith("x-stainless-"):
                continue
            print(f"  {name}: {value}")
        if self.status is not None:
            print()
            print(f"Ответ: HTTP {self.status}")
            for name in ("www-authenticate", "x-request-id", "x-error", "server", "content-type"):
                if name in self.response_headers:
                    print(f"  {name}: {self.response_headers[name]}")


def _body_of(exc: Any) -> str:
    for attr in ("body", "message"):
        value = getattr(exc, attr, None)
        if value:
            if isinstance(value, (dict, list)):
                return json.dumps(value, ensure_ascii=False, indent=2)[:2000]
            return str(value)[:2000]
    response = getattr(exc, "response", None)
    if response is not None:
        try:
            return response.text[:2000]
        except Exception:  # noqa: BLE001
            pass
    return "(тело ответа пустое)"


def _with_cause(exc: Exception) -> str:
    """The SDK wraps low-level failures into APIConnectionError('Connection error.'),
    which explains nothing. The real reason sits in __cause__."""
    text = f"{type(exc).__name__}: {exc}"
    cause = exc.__cause__ or exc.__context__
    if cause is not None and str(cause) and str(cause) not in str(exc):
        text += f"\n    Первопричина: {type(cause).__name__}: {str(cause)[:300]}"
    return text


PONG_TOOL = {
    "type": "function",
    "function": {
        "name": "pong",
        "description": "Вернуть ответ вызовом этого тула",
        "parameters": {
            "type": "object",
            "properties": {"text": {"type": "string", "description": "Любое слово"}},
            "required": ["text"],
        },
    },
}

# tool_choice variants that gateways support to varying degrees
TOOL_MODES: list[tuple[str, str, Any]] = [
    ("auto", 'tool_choice = "auto"', "auto"),
    ("required", 'tool_choice = "required"', "required"),
    ("forced", "tool_choice = {функция}", {"type": "function", "function": {"name": "pong"}}),
]


class ProbeResult:
    def __init__(self) -> None:
        self.error: str | None = None
        self.tool_calls: list[str] = []
        self.legacy_function_call: str | None = None
        self.finish_reason: str | None = None
        self.content: str = ""

    @property
    def ok(self) -> bool:
        return self.error is None

    @property
    def content_looks_like_call(self) -> bool:
        """The model "called" the tool as text — what gateways without tool_call parsing do."""
        probe = self.content.strip()
        return bool(probe) and ("pong" in probe and ("{" in probe or "<" in probe))

    def summary(self) -> str:
        if self.error:
            return f"ошибка — {self.error}"
        if self.tool_calls:
            return f"tool_calls: {', '.join(self.tool_calls)}"
        if self.legacy_function_call:
            return f"устаревшее поле function_call: {self.legacy_function_call}"
        text = self.content.strip().replace("\n", " ")[:90] or "(пусто)"
        marker = "текст, похожий на вызов" if self.content_looks_like_call else "обычный текст"
        return f"{marker} · finish_reason={self.finish_reason} · {text}"


async def _request(provider: ProviderConfig, *, tools: bool, tool_choice: Any,
                   wire: "_Wire | None" = None) -> ProbeResult:
    timeout = min(provider.timeout_s, 60.0)
    client = AsyncOpenAI(
        api_key=provider.resolve_api_key(),
        base_url=provider.base_url,
        timeout=timeout,
        max_retries=0,
        http_client=wire.http_client(timeout) if wire else None,
    )
    kwargs: dict[str, Any] = {
        "model": provider.model,
        "messages": [{"role": "user", "content": "Вызови тул pong со словом ping."
                      if tools else "ping"}],
        "max_tokens": 64,
        "extra_headers": provider.request_headers(),
    }
    if tools:
        kwargs["tools"] = [PONG_TOOL]
        if tool_choice is not None:
            kwargs["tool_choice"] = tool_choice
        if not provider.parallel_tool_calls:
            kwargs["parallel_tool_calls"] = False

    result = ProbeResult()
    try:
        completion = await client.chat.completions.create(**kwargs)
    except APIStatusError as exc:
        lines = [f"HTTP {exc.status_code}", f"Тело ответа: {_body_of(exc)}"]
        if exc.status_code in _HINTS:
            lines.append(f"Вероятная причина: {_HINTS[exc.status_code]}")
        result.error = "\n    ".join(lines)
        return result
    except (APIError, Exception) as exc:  # noqa: BLE001 — network, DNS, TLS
        result.error = _with_cause(exc)
        return result
    finally:
        await client.close()

    choice = completion.choices[0]
    message = choice.message
    result.finish_reason = choice.finish_reason
    result.content = message.content or ""
    result.tool_calls = [tc.function.name for tc in (message.tool_calls or [])]
    legacy = getattr(message, "function_call", None)
    if legacy is not None:
        result.legacy_function_call = getattr(legacy, "name", str(legacy))
    return result


def _print_auth_hints() -> None:
    print("Сравни это с запросом, который у тебя проходит вручную. Если отличается")
    print("форма авторизации — настрой provider.auth_header / provider.auth_scheme:")
    print('  auth_header = "api-key",   auth_scheme = ""       → api-key: <ключ>')
    print('  auth_header = "X-Api-Key", auth_scheme = ""       → X-Api-Key: <ключ>')
    print('  auth_scheme = "Token"                             → Authorization: Token <ключ>')


async def _probe_all(provider: ProviderConfig, wire: _Wire) -> tuple[ProbeResult, dict[str, ProbeResult]]:
    """Plain request first; tool modes are only worth probing if it succeeded."""
    plain = await _request(provider, tools=False, tool_choice=None, wire=wire)
    if not plain.ok:
        return plain, {}

    modes: dict[str, ProbeResult] = {}
    for key, _, choice in TOOL_MODES:
        modes[key] = await _request(provider, tools=True, tool_choice=choice)
    return plain, modes


def _report_tool_modes(modes: dict[str, ProbeResult]) -> int:
    width = max(len(label) for _, label, _ in TOOL_MODES)
    for key, label, _ in TOOL_MODES:
        result = modes[key]
        mark = "✓" if result.tool_calls else "✗"
        print(f"   {mark} {label:<{width}}  {result.summary()}")

    working = [key for key, _, _ in TOOL_MODES if modes[key].tool_calls]
    print()

    if not working:
        print("Итог: шлюз не вернул ни одного настоящего tool_call.")
        if any(modes[k].legacy_function_call for k in modes):
            print("  Пришло устаревшее поле function_call вместо tool_calls — шлюз говорит на")
            print("  протоколе до июня 2023 года. Ревьюер такого не поймёт.")
        elif any(modes[k].content_looks_like_call for k in modes):
            print("  Вместо вызова пришёл текст, похожий на вызов: шлюз не разбирает tool_call,")
            print("  а просто пересказывает его словами. Ревьюер попробует достать JSON из")
            print("  текста, но надёжной работы не будет — замечания будут теряться.")
        else:
            print("  Модель просто ответила текстом. Либо она не умеет tool calling, либо")
            print("  шлюз выкидывает поле tools из запроса.")
        print("  Что делать: взять модель с поддержкой tool calling или другой шлюз.")
        return 1

    # The reviewer needs "auto" during the run and the terminal mode on the last turn.
    if "auto" not in working:
        print("Итог: tool calling есть, но не при tool_choice = \"auto\".")
        print("  Ревьюер работает именно на auto: он сам решает, читать ли файлы, и")
        print("  форсирует вызов только на последнем ходу. Такой шлюз не подойдёт.")
        return 1

    best = next(key for key in ("forced", "required", "auto") if key in working)
    print("Итог: шлюз поддерживает tool calling.")
    if best == "forced":
        print('  Настройка по умолчанию подходит: terminal_tool_choice = "forced".')
        return 0

    print(f'  Но режим "forced" не поддерживается. Пропиши в конфиг:')
    print(f'    [provider]')
    print(f'    terminal_tool_choice = "{best}"')
    if best == "auto":
        print("  На auto ревьюер не может заставить агента сдать результат на последнем")
        print("  ходу — часть пунктов будет падать с «модель не вызвала submit_findings».")
        print("  Помогает поднять max_turns.")
    return 0


def check_provider(provider: ProviderConfig) -> int:
    key, source = provider.api_key_source()

    print("Провайдер")
    print(f"  base_url       {provider.base_url}")
    print(f"  model          {provider.model}")
    print(f"  ключ           {provider.masked_key()}")
    print(f"  источник ключа {source}")
    print(f"  авторизация    {provider.auth_header}: "
          f"{(provider.auth_scheme + ' ') if provider.auth_scheme else ''}<ключ>")
    print(f"  сдача результата  terminal_tool_choice = \"{provider.terminal_tool_choice}\"")
    if provider.extra_headers:
        print(f"  доп. заголовки {_mask_headers(provider.extra_headers)}")
    print()

    if key is None:
        print("✗ Ключ не найден — запрос делать нечем.")
        print(f"  Задай provider.api_key в конфиге или переменную {provider.api_key_env}.")
        return 2
    if key != key.strip():
        print("⚠ В ключе есть пробелы или перевод строки по краям — частая причина 401.")

    wire = _Wire()
    plain, modes = asyncio.run(_probe_all(provider, wire))

    print("1. Обычный запрос")
    if plain.ok:
        # For a plain request text is exactly what we want, so no call-shape verdict here
        print(f"   ✓ ответ получен: {plain.content.strip()[:80] or '(пусто)'}")
    else:
        print(f"   ✗ {plain.summary()}")
    if not plain.ok:
        print()
        wire.dump()
        print()
        _print_auth_hints()
        return 1

    print()
    print("2. Вызов тула — так ревьюер сдаёт результат")
    return _report_tool_modes(modes)
