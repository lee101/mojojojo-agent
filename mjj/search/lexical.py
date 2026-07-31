"""Small, dependency-free lexical index for code identifiers and paths."""

from __future__ import annotations

import math
import re
from array import array
from collections import Counter, defaultdict
from typing import Iterable, Mapping, Sequence

from mjj.kernels import ACCEL_ENABLED, bm25_accumulate


_WORDS = re.compile(r"[A-Za-z][A-Za-z0-9]*|\d+")
_CAMEL_BOUNDARY = re.compile(
    r"(?<=[a-z0-9])(?=[A-Z])|(?<=[A-Z])(?=[A-Z][a-z])"
)


def tokenize(value: str) -> list[str]:
    """Tokenise prose, paths, snake_case and camelCase identifiers.

    Both the complete identifier and its components are retained.  Searching
    for ``httpserver`` can therefore find ``HTTPServer``, while ``server`` can
    find it too.
    """
    tokens: list[str] = []
    for match in _WORDS.finditer(value):
        word = match.group(0)
        whole = word.lower()
        pieces = [piece.lower() for piece in _CAMEL_BOUNDARY.split(word)]
        tokens.append(whole)
        if len(pieces) > 1:
            tokens.extend(piece for piece in pieces if piece != whole)
    return tokens


def term_frequencies(
    text: str,
    *,
    path: str = "",
    signature: str = "",
) -> dict[str, int]:
    """Return compact frequencies with modest path/signature boosts."""
    terms = Counter(tokenize(text))
    for term in tokenize(signature):
        terms[term] += 1
    for term in tokenize(path):
        terms[term] += 2
    return dict(terms)


class LexicalIndex:
    """BM25 over pre-tokenised chunk term frequencies."""

    def __init__(self, documents: Sequence[Mapping[str, int]]) -> None:
        self.documents = documents
        self.lengths = [sum(document.values()) for document in documents]
        self.average_length = (
            sum(self.lengths) / len(self.lengths) if self.lengths else 1.0
        )
        postings: dict[str, list[tuple[int, int]]] = defaultdict(list)
        for document_id, document in enumerate(documents):
            for term, frequency in document.items():
                if frequency > 0:
                    postings[term].append((document_id, int(frequency)))
        self.postings = dict(postings)
        # Keep the existing public posting shape.  The parallel arrays exist
        # only when mojosub was requested and are zero-copy at its C boundary.
        self._kernel_lengths = (
            array("d", (float(length) for length in self.lengths))
            if ACCEL_ENABLED
            else None
        )
        self._kernel_postings = (
            {
                term: (
                    array("q", (item[0] for item in posting)),
                    array("q", (item[1] for item in posting)),
                )
                for term, posting in self.postings.items()
            }
            if ACCEL_ENABLED
            else None
        )

    def search(
        self,
        query: str | Iterable[str],
        limit: int = 20,
        *,
        k1: float = 1.2,
        b: float = 0.72,
    ) -> list[tuple[int, float]]:
        terms = tokenize(query) if isinstance(query, str) else list(query)
        if not terms or not self.documents or limit <= 0:
            return []
        if self._kernel_postings is not None:
            return self._search_kernel(terms, limit, k1, b)
        query_terms = Counter(terms)
        scores: dict[int, float] = defaultdict(float)
        count = len(self.documents)
        for term, query_frequency in query_terms.items():
            posting = self.postings.get(term)
            if not posting:
                continue
            document_frequency = len(posting)
            inverse_frequency = math.log(
                1.0 + (count - document_frequency + 0.5)
                / (document_frequency + 0.5)
            )
            query_boost = 1.0 + math.log(query_frequency)
            for document_id, frequency in posting:
                length = self.lengths[document_id]
                normalizer = k1 * (
                    1.0 - b + b * length / self.average_length
                )
                scores[document_id] += (
                    inverse_frequency
                    * (frequency * (k1 + 1.0))
                    / (frequency + normalizer)
                    * query_boost
                )
        return sorted(
            scores.items(),
            key=lambda item: (-item[1], item[0]),
        )[:limit]

    def _search_kernel(
        self,
        terms: list[str],
        limit: int,
        k1: float,
        b: float,
    ) -> list[tuple[int, float]]:
        assert self._kernel_lengths is not None
        assert self._kernel_postings is not None
        scores = array("d", [0.0]) * len(self.documents)
        touched: set[int] = set()
        count = len(self.documents)
        for term, query_frequency in Counter(terms).items():
            posting = self._kernel_postings.get(term)
            if posting is None:
                continue
            document_ids, frequencies = posting
            document_frequency = len(document_ids)
            inverse_frequency = math.log(
                1.0 + (count - document_frequency + 0.5)
                / (document_frequency + 0.5)
            )
            query_boost = 1.0 + math.log(query_frequency)
            bm25_accumulate(
                document_ids,
                frequencies,
                self._kernel_lengths,
                scores,
                self.average_length,
                k1,
                b,
                inverse_frequency,
                query_boost,
            )
            touched.update(document_ids)
        return sorted(
            ((document_id, float(scores[document_id]))
             for document_id in touched),
            key=lambda item: (-item[1], item[0]),
        )[:limit]
