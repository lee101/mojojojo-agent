"""Optional tree-sitter symbol extraction for typed, token-efficient maps."""

from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

MAX_SYMBOL_BYTES = 1 * 1024 * 1024

_LANGUAGE_BY_SUFFIX = {
    ".py": "python",
    ".pyi": "python",
    ".js": "javascript",
    ".jsx": "javascript",
    ".ts": "typescript",
    ".tsx": "tsx",
    ".go": "go",
    ".rs": "rust",
    ".java": "java",
    ".c": "c",
    ".h": "c",
    ".cc": "cpp",
    ".cpp": "cpp",
    ".hpp": "cpp",
    ".rb": "ruby",
    ".php": "php",
}

_QUERIES = {
    "python": """
        (class_definition name: (identifier) @name) @def
        (function_definition name: (identifier) @name) @def
    """,
    "javascript": """
        (class_declaration name: (identifier) @name) @def
        (function_declaration name: (identifier) @name) @def
        (method_definition name: (property_identifier) @name) @def
        (export_statement declaration: (function_declaration name: (identifier) @name) @def)
        (export_statement declaration: (class_declaration name: (identifier) @name) @def)
    """,
    "typescript": """
        (class_declaration name: (type_identifier) @name) @def
        (class_declaration name: (identifier) @name) @def
        (function_declaration name: (identifier) @name) @def
        (method_definition name: (property_identifier) @name) @def
        (interface_declaration name: (type_identifier) @name) @def
        (type_alias_declaration name: (type_identifier) @name) @def
    """,
    "tsx": """
        (class_declaration name: (type_identifier) @name) @def
        (class_declaration name: (identifier) @name) @def
        (function_declaration name: (identifier) @name) @def
        (method_definition name: (property_identifier) @name) @def
        (interface_declaration name: (type_identifier) @name) @def
        (type_alias_declaration name: (type_identifier) @name) @def
    """,
    "go": """
        (function_declaration name: (identifier) @name) @def
        (method_declaration name: (field_identifier) @name) @def
        (type_spec name: (type_identifier) @name) @def
    """,
    "rust": """
        (function_item name: (identifier) @name) @def
        (struct_item name: (type_identifier) @name) @def
        (enum_item name: (type_identifier) @name) @def
        (trait_item name: (type_identifier) @name) @def
        (impl_item type: (type_identifier) @name) @def
    """,
    "java": """
        (class_declaration name: (identifier) @name) @def
        (interface_declaration name: (identifier) @name) @def
        (method_declaration name: (identifier) @name) @def
        (enum_declaration name: (identifier) @name) @def
    """,
    "c": """
        (function_definition declarator: (function_declarator declarator: (identifier) @name)) @def
        (type_definition type: (_) declarator: (type_identifier) @name) @def
    """,
    "cpp": """
        (function_definition declarator: (function_declarator declarator: (identifier) @name)) @def
        (class_specifier name: (type_identifier) @name) @def
        (struct_specifier name: (type_identifier) @name) @def
    """,
    "ruby": """
        (class name: (constant) @name) @def
        (module name: (constant) @name) @def
        (method name: (identifier) @name) @def
    """,
    "php": """
        (class_declaration name: (name) @name) @def
        (function_definition name: (name) @name) @def
        (method_declaration name: (name) @name) @def
    """,
}


@dataclass(frozen=True)
class Symbol:
    name: str
    line: int
    signature: str


def extract_symbols(path: str | Path, data: bytes | None = None) -> list[Symbol] | None:
    """Return tree-sitter symbols, or ``None`` when the optional pack is absent."""
    label = Path(path)
    language = _LANGUAGE_BY_SUFFIX.get(label.suffix.lower())
    if language is None:
        return None
    if data is None:
        try:
            data = label.read_bytes()
        except OSError:
            return None
    if len(data) > MAX_SYMBOL_BYTES or b"\0" in data[:8192]:
        return None
    packed = _language_tools(language)
    if packed is None:
        return None
    parser, query = packed
    try:
        root = parser.parse(data).root_node
    except Exception:
        return None
    from tree_sitter import QueryCursor

    cursor = QueryCursor(query)
    symbols: list[Symbol] = []
    seen: set[tuple[str, int]] = set()
    try:
        matches = cursor.matches(root)
    except Exception:
        return None
    for _pattern, captures in matches:
        name_nodes = captures.get("name") or ()
        def_nodes = captures.get("def") or ()
        if not name_nodes:
            continue
        name_node = name_nodes[0]
        def_node = def_nodes[0] if def_nodes else name_node
        name = data[name_node.start_byte:name_node.end_byte].decode(
            "utf-8", "replace"
        )
        line = int(name_node.start_point[0]) + 1
        key = (name, line)
        if key in seen:
            continue
        seen.add(key)
        signature = _signature(data, def_node)
        symbols.append(Symbol(name=name, line=line, signature=signature))
    symbols.sort(key=lambda item: (item.line, item.name))
    return symbols


@lru_cache(maxsize=None)
def _language_tools(language: str):
    source = _QUERIES.get(language)
    if source is None:
        return None
    try:
        from tree_sitter import Query
        from tree_sitter_language_pack import get_language, get_parser
    except ImportError:
        return None
    try:
        lang = get_language(language)
        parser = get_parser(language)
        return parser, Query(lang, source)
    except (LookupError, OSError, RuntimeError, TypeError, ValueError):
        return None


def _signature(data: bytes, node, limit: int = 200) -> str:
    text = data[node.start_byte:node.end_byte].decode("utf-8", "replace")
    first = text.splitlines()[0] if text else ""
    compact = " ".join(first.replace("\t", "    ").split())
    if compact.endswith("{"):
        compact = compact.rstrip("{").rstrip()
    if len(compact) <= limit:
        return compact
    return compact[: limit - 1] + "…"
