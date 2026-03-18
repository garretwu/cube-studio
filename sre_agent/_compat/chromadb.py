"""Small ChromaDB-compatible in-process store for local tests."""
from __future__ import annotations

import json
import math
import re
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

_TOKEN_RE = re.compile(r"\w+", re.UNICODE)


def _tokenize(text: str) -> Counter[str]:
    return Counter(token.lower() for token in _TOKEN_RE.findall(text))


def _cosine_distance(left: str, right: str) -> float:
    lvec = _tokenize(left)
    rvec = _tokenize(right)
    if not lvec or not rvec:
        return 1.0
    dot = sum(lvec[token] * rvec.get(token, 0) for token in lvec)
    lnorm = math.sqrt(sum(value * value for value in lvec.values()))
    rnorm = math.sqrt(sum(value * value for value in rvec.values()))
    if lnorm == 0 or rnorm == 0:
        return 1.0
    similarity = dot / (lnorm * rnorm)
    return 1.0 - similarity


def _meta_match(metadata: dict[str, Any], where: dict[str, Any] | None) -> bool:
    if not where:
        return True
    for key, expected in where.items():
        if metadata.get(key) != expected:
            return False
    return True


@dataclass
class _Record:
    document: str
    metadata: dict[str, Any] = field(default_factory=dict)


class Collection:
    def __init__(self, name: str, persist_path: Path | None = None):
        self.name = name
        self.persist_path = persist_path
        self._records: dict[str, _Record] = {}
        self._load()

    def _load(self) -> None:
        if self.persist_path is None or not self.persist_path.exists():
            return
        raw = json.loads(self.persist_path.read_text(encoding="utf-8"))
        records = raw.get("records", {})
        self._records = {
            doc_id: _Record(
                document=str(item.get("document", "")),
                metadata=dict(item.get("metadata", {})),
            )
            for doc_id, item in records.items()
        }

    def _save(self) -> None:
        if self.persist_path is None:
            return
        self.persist_path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "records": {
                doc_id: {"document": record.document, "metadata": record.metadata}
                for doc_id, record in self._records.items()
            }
        }
        self.persist_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    def add(
        self,
        *,
        documents: list[str],
        metadatas: list[dict[str, Any]] | None = None,
        ids: list[str],
    ) -> None:
        metadatas = metadatas or [{} for _ in documents]
        for doc_id, document, metadata in zip(ids, documents, metadatas, strict=True):
            self._records[doc_id] = _Record(document=document, metadata=dict(metadata))
        self._save()

    def upsert(
        self,
        *,
        documents: list[str],
        metadatas: list[dict[str, Any]] | None = None,
        ids: list[str],
    ) -> None:
        self.add(documents=documents, metadatas=metadatas, ids=ids)

    def query(
        self,
        *,
        query_texts: list[str],
        n_results: int,
        where: dict[str, Any] | None = None,
    ) -> dict[str, list[list[Any]]]:
        documents: list[list[str]] = []
        metadatas: list[list[dict[str, Any]]] = []
        distances: list[list[float]] = []
        ids: list[list[str]] = []
        for query in query_texts:
            ranked: list[tuple[float, str, _Record]] = []
            for doc_id, record in self._records.items():
                if not _meta_match(record.metadata, where):
                    continue
                ranked.append((_cosine_distance(query, record.document), doc_id, record))
            ranked.sort(key=lambda item: item[0])
            top = ranked[:n_results]
            documents.append([record.document for _, _, record in top])
            metadatas.append([dict(record.metadata) for _, _, record in top])
            distances.append([distance for distance, _, _ in top])
            ids.append([doc_id for _, doc_id, _ in top])
        return {"documents": documents, "metadatas": metadatas, "distances": distances, "ids": ids}

    def get(self, *, ids: list[str] | None = None, where: dict[str, Any] | None = None) -> dict[str, list[Any]]:
        selected: list[tuple[str, _Record]] = []
        for doc_id, record in self._records.items():
            if ids is not None and doc_id not in ids:
                continue
            if not _meta_match(record.metadata, where):
                continue
            selected.append((doc_id, record))
        return {
            "ids": [doc_id for doc_id, _ in selected],
            "documents": [record.document for _, record in selected],
            "metadatas": [dict(record.metadata) for _, record in selected],
        }

    def delete(self, *, ids: list[str] | None = None, where: dict[str, Any] | None = None) -> None:
        to_delete = self.get(ids=ids, where=where)["ids"]
        for doc_id in to_delete:
            self._records.pop(doc_id, None)
        self._save()

    def count(self) -> int:
        return len(self._records)


class _BaseClient:
    def __init__(self, persist_root: Path | None = None):
        self.persist_root = persist_root
        self._collections: dict[str, Collection] = {}

    def get_or_create_collection(self, name: str, metadata: dict[str, Any] | None = None) -> Collection:
        _ = metadata
        if name not in self._collections:
            persist_path = None
            if self.persist_root is not None:
                persist_path = self.persist_root / f"{name}.json"
            self._collections[name] = Collection(name, persist_path=persist_path)
        return self._collections[name]


class PersistentClient(_BaseClient):
    def __init__(self, path: str):
        root = Path(path)
        root.mkdir(parents=True, exist_ok=True)
        super().__init__(persist_root=root)
        self.path = str(root)


class EphemeralClient(_BaseClient):
    def __init__(self):
        super().__init__()
