
"""Optional OpenAI JSON planner transport. No SDK retries or configurable remote URLs."""

import json
import os
import time
from dataclasses import dataclass, field
from decimal import ROUND_UP, Decimal, InvalidOperation
from typing import Protocol

import httpx

OPENAI_ENDPOINT = "https://api.openai.com/v1/chat/completions"
MAX_RESPONSE_BYTES = 262144
MONEY_QUANTUM = Decimal("0.000000000001")


class PlanningError(Exception):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


def money(value: Decimal) -> str:
    return str(value.quantize(MONEY_QUANTUM, rounding=ROUND_UP))


@dataclass(frozen=True)
class PlannerSettings:
    enabled: bool = False
    model: str = ""
    input_price: Decimal = Decimal("0")
    output_price: Decimal = Decimal("0")
    max_output_tokens: int = 2048
    api_key: str = field(default="", repr=False, compare=False)

    def cost(self, input_tokens: int, output_tokens: int) -> Decimal:
        return Decimal(
            money((input_tokens * self.input_price + output_tokens * self.output_price) / 1000000)
        )

    def snapshot(self) -> dict:
        return {
            "provider": "openai",
            "model": self.model,
            "input_price_per_million": str(self.input_price),
            "output_price_per_million": str(self.output_price),
            "max_output_tokens": self.max_output_tokens,
        }


def planner_settings() -> PlannerSettings:
    if os.getenv("LLM_PLANNER_ENABLED", "false").lower() != "true":
        return PlannerSettings()
    try:
        model = os.getenv("LLM_MODEL", "").strip()
        key = os.getenv("OPENAI_API_KEY", "").strip()
        input_price = Decimal(os.getenv("LLM_INPUT_PRICE_PER_MILLION", ""))
        output_price = Decimal(os.getenv("LLM_OUTPUT_PRICE_PER_MILLION", ""))
        output_tokens = int(os.getenv("LLM_MAX_OUTPUT_TOKENS", "2048"))
        if (
            not model
            or len(model) > 100
            or not key
            or not input_price.is_finite()
            or not output_price.is_finite()
            or not 0 <= input_price <= 10000
            or not 0 <= output_price <= 10000
            or not 256 <= output_tokens <= 4096
        ):
            raise ValueError()
        return PlannerSettings(True, model, input_price, output_price, output_tokens, key)
    except (KeyError, ValueError, InvalidOperation):
        raise PlanningError(
            "provider_not_configured", "LLM planning configuration is incomplete or invalid"
        ) from None


@dataclass(frozen=True)
class Completion:
    content: str
    input_tokens: int
    output_tokens: int
    truncated: bool = False


class PlannerProvider(Protocol):
    def complete(
        self,
        messages: list[dict[str, str]],
        max_output_tokens: int,
        timeout_seconds: float,
        input_token_bound: int,
    ) -> Completion: ...


class OpenAIPlannerProvider:
    def __init__(self, settings: PlannerSettings, transport: httpx.BaseTransport | None = None):
        self.settings = settings
        self.transport = transport

    def complete(
        self,
        messages: list[dict[str, str]],
        max_output_tokens: int,
        timeout_seconds: float,
        input_token_bound: int,
    ) -> Completion:
        deadline = time.monotonic() + timeout_seconds
        payload = {
            "model": self.settings.model,
            "messages": messages,
            "response_format": {"type": "json_object"},
            "max_completion_tokens": max_output_tokens,
            "stream": False,
            "store": False,
            "service_tier": "default",
        }
        try:
            with httpx.Client(
                transport=self.transport,
                timeout=httpx.Timeout(min(timeout_seconds, 30.0)),
                follow_redirects=False,
                trust_env=False,
            ) as client:
                with client.stream(
                    "POST",
                    OPENAI_ENDPOINT,
                    headers={"Authorization": f"Bearer {self.settings.api_key}"},
                    json=payload,
                ) as response:
                    if response.status_code != 200:
                        code = {
                            401: "provider_auth_failed",
                            403: "provider_auth_failed",
                            429: "provider_rate_limited",
                        }.get(response.status_code, "provider_unavailable")
                        raise PlanningError(
                            code, "The planning provider rejected or could not serve the request"
                        )
                    data = bytearray()
                    for chunk in response.iter_bytes():
                        data.extend(chunk)
                        if len(data) > MAX_RESPONSE_BYTES:
                            raise PlanningError(
                                "provider_response_too_large",
                                "Planning provider response exceeded its size limit",
                            )
                        if time.monotonic() > deadline:
                            raise PlanningError(
                                "planning_deadline", "Planning provider exceeded the deadline"
                            )
            if time.monotonic() > deadline:
                raise PlanningError("planning_deadline", "Planning provider exceeded the deadline")
            body = json.loads(data)
            choice = body["choices"][0]
            message = choice["message"]
            if message.get("refusal"):
                raise PlanningError(
                    "provider_refusal", "The planning provider declined the request"
                )
            content = message["content"]
            usage = body["usage"]
            input_tokens, output_tokens = usage["prompt_tokens"], usage["completion_tokens"]
            if (
                type(input_tokens) is not int
                or type(output_tokens) is not int
                or not 0 <= input_tokens <= input_token_bound
                or not 0 <= output_tokens <= max_output_tokens
                or type(usage.get("total_tokens")) is not int
                or usage.get("total_tokens") != input_tokens + output_tokens
            ):
                raise PlanningError(
                    "provider_usage_invalid", "Planning provider returned unverifiable token usage"
                )
            if not isinstance(content, str) or len(content.encode()) > 32768:
                raise PlanningError(
                    "provider_response_invalid", "Planning provider returned an invalid response"
                )
            if choice.get("finish_reason") not in ("stop", "length"):
                raise PlanningError(
                    "provider_response_invalid",
                    "Planning provider did not return a complete text response",
                )
            return Completion(
                content, input_tokens, output_tokens, choice.get("finish_reason") == "length"
            )
        except PlanningError:
            raise
        except httpx.TimeoutException:
            raise PlanningError(
                "provider_timeout", "Planning provider timed out; billing status is uncertain"
            ) from None
        except httpx.HTTPError:
            raise PlanningError(
                "provider_unavailable",
                "Planning provider request failed; billing status is uncertain",
            ) from None
        except (ValueError, KeyError, IndexError, TypeError, OverflowError):
            raise PlanningError(
                "provider_response_invalid",
                "Planning provider response or usage could not be validated",
            ) from None
