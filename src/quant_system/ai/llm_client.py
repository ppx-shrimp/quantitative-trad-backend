from __future__ import annotations

import json
import time
from typing import Any

import httpx

from quant_system.ai.prompts import SYSTEM_PROMPT, build_system_prompt
from quant_system.core.config import settings


class LLMClientError(RuntimeError):
    pass


class OpenAICompatibleClient:
    def __init__(self) -> None:
        self.provider = settings.llm_provider
        self.base_url = (settings.llm_base_url or "").rstrip("/")
        self.api_key = settings.llm_api_key
        self.model = settings.llm_model
        self.timeout = settings.llm_timeout_seconds
        self.temperature = settings.llm_temperature
        self.retry_count = max(0, int(settings.llm_retry_count or 0))
        self.retry_delay_seconds = max(0.0, float(settings.llm_retry_delay_seconds or 0.0))

    def enabled(self) -> bool:
        return bool(settings.ai_enabled and not settings.ai_mock_enabled and self.base_url and self.api_key)

    def status(self) -> dict[str, Any]:
        missing: list[str] = []
        if not settings.ai_enabled:
            missing.append("QUANT_AI_ENABLED=true")
        if settings.ai_mock_enabled:
            missing.append("QUANT_AI_MOCK_ENABLED=false")
        if not self.base_url:
            missing.append("QUANT_LLM_BASE_URL")
        if not self.api_key:
            missing.append("QUANT_LLM_API_KEY")
        if not self.model:
            missing.append("QUANT_LLM_MODEL")
        mode = "real" if self.enabled() else "mock"
        return {
            "ai_enabled": settings.ai_enabled,
            "mock_enabled": settings.ai_mock_enabled,
            "mode": mode,
            "llm_ready": self.enabled(),
            "provider": self.provider,
            "base_url": self.base_url or None,
            "api_key_configured": bool(self.api_key),
            "model": self.model,
            "temperature": self.temperature,
            "timeout_seconds": self.timeout,
            "retry_count": self.retry_count,
            "missing": missing,
            "message": "真实模型已配置，可调用 LLM。" if self.enabled() else "当前使用 mock 分析；如需真实模型，请补齐 missing 中的配置。",
        }

    def diagnose(self) -> dict[str, Any]:
        status = self.status()
        if not self.enabled():
            return {
                **status,
                "connectivity": "skipped",
                "ok": False,
                "latency_ms": None,
                "error": "LLM 未启用或配置不完整，跳过连通性测试。",
            }
        prompt = "请只返回 JSON：{\"action\":\"watch\",\"confidence\":0.5,\"risk_level\":\"medium\",\"summary\":\"连通性测试\",\"reasons\":[\"模型可响应\"],\"risk_warnings\":[],\"suggested_plan\":{},\"data_quality\":{\"mode\":\"diagnose\"}}"
        started = time.perf_counter()
        try:
            output = self.complete_json(prompt)
            latency_ms = round((time.perf_counter() - started) * 1000, 2)
            return {
                **status,
                "connectivity": "ok",
                "ok": True,
                "latency_ms": latency_ms,
                "sample_output": output,
                "error": None,
            }
        except Exception as exc:
            latency_ms = round((time.perf_counter() - started) * 1000, 2)
            return {
                **status,
                "connectivity": "error",
                "ok": False,
                "latency_ms": latency_ms,
                "sample_output": None,
                "error": str(exc),
            }

    def complete_json(self, prompt: str, *, analysis_type: str = "buy_decision") -> dict[str, Any]:
        """调用 LLM 并返回解析后的 JSON dict。

        Args:
            prompt: user prompt 内容
            analysis_type: 分析类型，用于选择对应的 system prompt (v3)
        """
        if not self.enabled():
            raise LLMClientError("LLM 未启用或未配置 base_url/api_key，当前应使用 mock 分析。")
        system_prompt = build_system_prompt(analysis_type)
        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": prompt},
        ]
        return self._call_llm(messages, response_format={"type": "json_object"})

    def complete_chat(
        self,
        messages: list[dict[str, str]],
        *,
        analysis_type: str = "buy_decision",
        system_prompt: str | None = None,
    ) -> str:
        """多轮对话调用 LLM，返回纯文本回复（不要求 JSON）。

        Args:
            messages: 完整的消息列表 [{"role": "...", "content": "..."}]
            analysis_type: 分析类型，用于生成默认 system prompt
            system_prompt: 对话专用 system prompt；不传时使用分析 JSON prompt
        """
        if not self.enabled():
            raise LLMClientError("LLM 未启用或未配置 base_url/api_key，当前应使用 mock 分析。")
        system_prompt = system_prompt or build_system_prompt(analysis_type)
        full_messages = [{"role": "system", "content": system_prompt}] + messages
        return self._call_llm_text(full_messages)

    def _call_llm(self, messages: list[dict[str, str]], *, response_format: dict | None = None) -> dict[str, Any]:
        """底层 LLM HTTP 调用，返回 JSON dict。"""
        content = self._call_llm_raw(messages, response_format=response_format)
        if isinstance(content, dict):
            return content
        return self._parse_json_content(str(content))

    def _call_llm_text(self, messages: list[dict[str, str]]) -> str:
        """底层 LLM HTTP 调用，返回纯文本。"""
        content = self._call_llm_raw(messages)
        return content if isinstance(content, str) else json.dumps(content, ensure_ascii=False)

    def _call_llm_raw(self, messages: list[dict[str, str]], *, response_format: dict | None = None) -> Any:
        """统一的 HTTP 请求逻辑，返回 choices[0].message.content 原始值。"""
        url = f"{self.base_url}/chat/completions"
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }
        payload: dict[str, Any] = {
            "model": self.model,
            "temperature": self.temperature,
            "messages": messages,
        }
        if response_format:
            payload["response_format"] = response_format
        data: dict[str, Any] | None = None
        last_error: Exception | None = None
        for attempt in range(self.retry_count + 1):
            try:
                with httpx.Client(timeout=self.timeout, trust_env=False) as client:
                    response = client.post(url, headers=headers, json=payload)
                    response.raise_for_status()
                    data = response.json()
                break
            except httpx.HTTPStatusError as exc:
                last_error = exc
                status_code = exc.response.status_code if exc.response else 0
                if not self._should_retry_status(status_code) or attempt >= self.retry_count:
                    body = exc.response.text[:500] if exc.response is not None else ""
                    raise LLMClientError(f"LLM HTTP 状态异常：{status_code or 'unknown'} {body}") from exc
                self._sleep_before_retry(attempt)
            except (httpx.ConnectError, httpx.TimeoutException, httpx.RequestError) as exc:
                last_error = exc
                if attempt >= self.retry_count:
                    raise LLMClientError(f"LLM 调用失败：{exc}") from exc
                self._sleep_before_retry(attempt)
            except Exception as exc:
                last_error = exc
                raise LLMClientError(f"LLM 调用失败：{exc}") from exc
        if data is None:
            raise LLMClientError(f"LLM 调用失败：{last_error}")
        try:
            return data["choices"][0]["message"]["content"]
        except (KeyError, IndexError) as exc:
            raise LLMClientError(f"LLM 返回结构异常：{exc}") from exc

    def _parse_json_content(self, content: str) -> dict[str, Any]:
        clean = content.strip()
        candidates = [clean]
        extracted = self._extract_json_object(clean)
        if extracted and extracted != clean:
            candidates.append(extracted)
        for candidate in candidates:
            try:
                parsed = json.loads(candidate)
                if isinstance(parsed, dict):
                    return parsed
            except Exception:
                continue
        raise LLMClientError("LLM 返回无法解析为 JSON：未找到合法 JSON object。")

    def _extract_json_object(self, text: str) -> str | None:
        start = text.find("{")
        if start < 0:
            return None
        depth = 0
        in_string = False
        escape = False
        for index in range(start, len(text)):
            char = text[index]
            if escape:
                escape = False
                continue
            if char == "\\":
                escape = True
                continue
            if char == '"':
                in_string = not in_string
                continue
            if in_string:
                continue
            if char == "{":
                depth += 1
            elif char == "}":
                depth -= 1
                if depth == 0:
                    return text[start:index + 1]
        return None

    def _should_retry_status(self, status_code: int) -> bool:
        return status_code in {408, 409, 425, 429, 500, 502, 503, 504}

    def _sleep_before_retry(self, attempt: int) -> None:
        if self.retry_delay_seconds <= 0:
            return
        time.sleep(self.retry_delay_seconds * (2 ** attempt))
