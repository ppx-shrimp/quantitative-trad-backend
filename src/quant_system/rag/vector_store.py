from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol


@dataclass
class VectorDocument:
    id: str
    text: str
    vector: list[float]
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class VectorSearchResult:
    id: str
    score: float
    text: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)


class VectorStore(Protocol):
    def status(self) -> dict[str, Any]:
        ...

    def ensure_collection(self) -> dict[str, Any]:
        ...

    def upsert_documents(self, documents: list[VectorDocument]) -> dict[str, Any]:
        ...

    def search(self, vector: list[float], *, limit: int = 8, filters: dict[str, Any] | None = None) -> list[VectorSearchResult]:
        ...
