"""Thin wrapper around AWS Bedrock (boto3 `bedrock-runtime`).

- `embed()`           -> one embedding vector (Amazon Titan or Cohere embedding models)
- `converse()`        -> one chat call through the model-agnostic Converse API (supports tool use)
- `converse_stream()` -> same, but yields the answer piece by piece (used by the chat)

Credentials: only the access keys from .env (AWS_ACCESS_KEY_ID / AWS_SECRET_ACCESS_KEY,
plus AWS_SESSION_TOKEN for temporary keys). AWS CLI profiles are not used.

Throttling: boto3's "adaptive" retry mode waits and retries automatically when
Bedrock returns ThrottlingException, so one slow-down does not fail a whole run.
"""
from __future__ import annotations

import json
import os
import time
from dataclasses import dataclass, field
from typing import Any, Iterator

import boto3
from botocore.config import Config as BotoConfig

from .config import Config


class BedrockError(Exception):
    pass


@dataclass
class ChatResult:
    text: str
    content: list[dict[str, Any]] = field(default_factory=list)  # raw content blocks (text / toolUse)
    stop_reason: str = ""
    input_tokens: int = 0
    output_tokens: int = 0
    latency_ms: int = 0


class BedrockClient:
    def __init__(self, cfg: Config):
        self.cfg = cfg
        if not cfg.aws_access_key_id or not cfg.aws_secret_access_key:
            raise BedrockError("AWS_ACCESS_KEY_ID / AWS_SECRET_ACCESS_KEY missing in .env")

        # boto3.client() builds a hidden default session that reads AWS_PROFILE from the
        # environment even when keys are passed. An empty or unknown value crashes it
        # (ProfileNotFound), so drop the variable: this project never uses profiles.
        os.environ.pop("AWS_PROFILE", None)

        self.runtime = boto3.client(
            "bedrock-runtime",
            region_name=cfg.aws_region,
            aws_access_key_id=cfg.aws_access_key_id,
            aws_secret_access_key=cfg.aws_secret_access_key,
            aws_session_token=cfg.aws_session_token or None,
            config=BotoConfig(retries={"max_attempts": 8, "mode": "adaptive"}, read_timeout=120),
        )

    # ---------- embeddings ----------
    def embed(self, text: str, purpose: str = "document") -> list[float]:
        """purpose: "document" for stored passages, "query" for a user's question."""
        model = self.cfg.embed_model_id
        if not model:
            raise BedrockError("EMBED_MODEL_ID is not set in .env")

        if "cohere" in model:
            input_type = "search_query" if purpose == "query" else "search_document"
            body: dict[str, Any] = {"texts": [text], "input_type": input_type}
        else:  # Amazon Titan text embeddings
            body = {"inputText": text}
            if "v2" in model:
                body.update({"dimensions": self.cfg.embed_dim, "normalize": True})

        response = self.runtime.invoke_model(
            modelId=model,
            body=json.dumps(body),
            contentType="application/json",
            accept="application/json",
        )
        payload = json.loads(response["body"].read())
        vector = payload["embeddings"][0] if "cohere" in model else payload["embedding"]
        if len(vector) != self.cfg.embed_dim:
            raise BedrockError(
                f"Embedding model returned {len(vector)} numbers but EMBED_DIM={self.cfg.embed_dim}. "
                "Set EMBED_DIM in .env to match the model."
            )
        return vector

    # ---------- chat ----------
    def _converse_kwargs(
        self,
        messages: list[dict[str, Any]],
        system: str | None,
        tools: list[dict[str, Any]] | None,
        max_tokens: int,
    ) -> dict[str, Any]:
        if not self.cfg.chat_model_id:
            raise BedrockError("CHAT_MODEL_ID is not set in .env")

        inference: dict[str, Any] = {"maxTokens": max_tokens}
        if self.cfg.chat_temperature is not None:
            inference["temperature"] = self.cfg.chat_temperature

        kwargs: dict[str, Any] = {
            "modelId": self.cfg.chat_model_id,
            "messages": messages,
            "inferenceConfig": inference,
        }
        if system:
            kwargs["system"] = [{"text": system}]
        if tools:
            kwargs["toolConfig"] = {"tools": tools}
        return kwargs

    def converse_stream(
        self,
        messages: list[dict[str, Any]],
        system: str | None = None,
        max_tokens: int = 1024,
    ) -> Iterator[dict[str, Any]]:
        """Yields {"type": "text", "text": ...} pieces, then one {"type": "usage", ...} at the end."""
        kwargs = self._converse_kwargs(messages, system, None, max_tokens)
        start = time.monotonic()
        response = self.runtime.converse_stream(**kwargs)
        usage: dict[str, Any] = {}
        for event in response["stream"]:
            if "contentBlockDelta" in event:
                text = event["contentBlockDelta"].get("delta", {}).get("text")
                if text:
                    yield {"type": "text", "text": text}
            elif "metadata" in event:
                usage = event["metadata"].get("usage", {}) or {}
            else:
                for key, value in event.items():
                    if key.endswith("Exception"):
                        raise BedrockError(f"{key}: {value}")
        yield {
            "type": "usage",
            "input_tokens": usage.get("inputTokens", 0),
            "output_tokens": usage.get("outputTokens", 0),
            "latency_ms": int((time.monotonic() - start) * 1000),
        }

    def converse(
        self,
        messages: list[dict[str, Any]],
        system: str | None = None,
        tools: list[dict[str, Any]] | None = None,
        max_tokens: int = 1024,
    ) -> ChatResult:
        kwargs = self._converse_kwargs(messages, system, tools, max_tokens)
        start = time.monotonic()
        response = self.runtime.converse(**kwargs)
        latency_ms = int((time.monotonic() - start) * 1000)

        content = response.get("output", {}).get("message", {}).get("content", [])
        text = "".join(block.get("text", "") for block in content)
        usage = response.get("usage", {})
        return ChatResult(
            text=text,
            content=content,
            stop_reason=response.get("stopReason", ""),
            input_tokens=usage.get("inputTokens", 0),
            output_tokens=usage.get("outputTokens", 0),
            latency_ms=latency_ms,
        )

    # ---------- cost ----------
    def cost(self, input_tokens: int, output_tokens: int) -> float:
        return round(
            input_tokens / 1000 * self.cfg.price_input_per_1k + output_tokens / 1000 * self.cfg.price_output_per_1k,
            6,
        )
