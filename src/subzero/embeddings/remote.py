"""OpenAI- and Gemini-backed embedding providers."""

from __future__ import annotations

import httpx

from subzero.config import EMBEDDING_DIM
from subzero.errors import EmbeddingInputError, SubzeroError
from subzero.logging_setup import get_logger

logger = get_logger(__name__)

#: A transport cap, not a guarantee: providers limit input in tokens and chars
#: are not tokens, so dense text can still be refused. The service shrinks any
#: input a provider refuses; this only keeps one absurd row off the wire.
_MAX_INPUT_CHARS = 24_000


class OpenAIEmbeddingProvider:
    dimension = EMBEDDING_DIM
    default_model = "text-embedding-3-small"

    def __init__(self, api_key: str, model: str = default_model) -> None:
        self._model = model
        self._client = httpx.AsyncClient(
            base_url="https://api.openai.com/v1",
            timeout=httpx.Timeout(30.0, connect=10.0),
            headers={"Authorization": f"Bearer {api_key}"},
        )

    async def embed(self, texts: list[str]) -> list[list[float]]:
        if not texts:
            return []
        response = await self._client.post(
            "/embeddings",
            json={
                "model": self._model,
                "input": [t[:_MAX_INPUT_CHARS] for t in texts],
                "dimensions": self.dimension,
            },
        )
        if response.status_code == 400:
            # The request itself was refused, usually one input over the
            # token limit. The body names the offending index, which is the
            # whole diagnosis, so keep a piece of it in the log.
            raise EmbeddingInputError(f"Embedding request refused: HTTP 400: {response.text[:300]}")
        if response.status_code != 200:
            raise SubzeroError(f"Embedding request failed: HTTP {response.status_code}")
        data = sorted(response.json()["data"], key=lambda d: d["index"])
        return [item["embedding"] for item in data]

    async def aclose(self) -> None:
        await self._client.aclose()


class GeminiEmbeddingProvider:
    dimension = EMBEDDING_DIM
    default_model = "gemini-embedding-001"

    def __init__(self, api_key: str, model: str = default_model) -> None:
        self._model = model
        self._client = httpx.AsyncClient(
            base_url="https://generativelanguage.googleapis.com/v1beta",
            timeout=httpx.Timeout(30.0, connect=10.0),
            headers={"x-goog-api-key": api_key},
        )

    async def embed(self, texts: list[str]) -> list[list[float]]:
        if not texts:
            return []
        response = await self._client.post(
            f"/models/{self._model}:batchEmbedContents",
            json={
                "requests": [
                    {
                        "model": f"models/{self._model}",
                        "content": {"parts": [{"text": t[:_MAX_INPUT_CHARS]}]},
                        "outputDimensionality": self.dimension,
                    }
                    for t in texts
                ]
            },
        )
        if response.status_code == 400:
            raise EmbeddingInputError(f"Embedding request refused: HTTP 400: {response.text[:300]}")
        if response.status_code != 200:
            raise SubzeroError(f"Embedding request failed: HTTP {response.status_code}")
        return [item["values"] for item in response.json()["embeddings"]]

    async def aclose(self) -> None:
        await self._client.aclose()
