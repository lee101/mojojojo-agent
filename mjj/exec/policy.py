"""Static, conservative placement policy for generated Python."""

from __future__ import annotations

import ast
import os

PATHS = ("inproc", "accelerated", "sandbox", "remote")
_ALIASES = {
    "accel": "accelerated",
    "sandboxed": "sandbox",
    "local": "sandbox",
    "auto": "",
}

# Importing any other module is not proof of malice. It is uncertainty, and
# uncertainty belongs in the sandbox. These modules have no filesystem or
# network authority and are useful in small computations.
_PURE_MODULES = frozenset({
    "array", "bisect", "collections", "decimal", "fractions", "functools",
    "heapq", "itertools", "math", "operator", "random", "statistics",
})
_SAFE_CALLS = frozenset({
    "abs", "all", "any", "bool", "divmod", "enumerate", "filter", "float",
    "format", "int", "len", "list", "map", "max", "min", "pow", "print",
    "range", "reversed", "round", "set", "sorted", "str", "sum", "tuple",
    "zip",
})
_DANGEROUS_NAMES = frozenset({
    "__import__", "breakpoint", "compile", "eval", "exec", "globals", "input",
    "locals", "memoryview", "open", "vars",
})
_DANGEROUS_ROOTS = frozenset({
    "aiohttp", "asyncio", "ctypes", "ftplib", "http", "multiprocessing",
    "os", "pathlib", "requests", "shutil", "signal", "socket", "subprocess",
    "sys", "tempfile", "urllib",
})
_UNTRUSTED_NODES = (
    ast.AsyncFor,
    ast.AsyncFunctionDef,
    ast.AsyncWith,
    ast.Await,
    ast.ClassDef,
    ast.Delete,
    ast.Global,
    ast.Lambda,
    ast.Nonlocal,
    ast.Try,
    ast.With,
    ast.Yield,
    ast.YieldFrom,
)

# A deliberately narrower approximation of mojosub's subset. False negatives
# lose a speedup; false positives start a multi-second compiler for no benefit.
_ACCEL_NODES = (
    ast.Module, ast.FunctionDef, ast.arguments, ast.arg, ast.Return, ast.Assign,
    ast.AnnAssign, ast.AugAssign, ast.Expr, ast.If, ast.For, ast.While,
    ast.Break, ast.Continue, ast.Pass, ast.Name, ast.Load, ast.Store,
    ast.Constant, ast.BinOp, ast.UnaryOp, ast.BoolOp, ast.Compare, ast.IfExp,
    ast.Call, ast.Subscript, ast.List, ast.Tuple, ast.Attribute,
    ast.Add, ast.Sub, ast.Mult, ast.Div, ast.FloorDiv, ast.Mod, ast.Pow,
    ast.USub, ast.UAdd, ast.Not, ast.And, ast.Or, ast.Eq, ast.NotEq, ast.Lt,
    ast.LtE, ast.Gt, ast.GtE, ast.In, ast.NotIn,
)
_ACCEL_CALLS = frozenset({
    "abs", "bool", "float", "int", "len", "max", "min", "range",
})
_MATH_CALLS = frozenset({
    "acos", "asin", "atan", "atan2", "ceil", "cos", "exp", "floor", "log",
    "log10", "pow", "sin", "sqrt", "tan",
})


def normalize_path(value: str) -> str:
    value = _ALIASES.get(value.strip().lower(), value.strip().lower())
    if value not in PATHS and value != "":
        raise ValueError(
            f"where must be one of auto, inproc, accel, sandbox, remote; got {value!r}"
        )
    return value


def forced_path(where: str | None = None) -> str:
    """Return an explicit placement from the argument or ``MJJ_EXEC``."""
    if where is not None and str(where).strip():
        return normalize_path(str(where))
    configured = os.environ.get("MJJ_EXEC", "")
    return normalize_path(configured) if configured.strip() else ""


def choose_path(
    code: str,
    *,
    packages: list[str] | None = None,
    where: str | None = None,
) -> str:
    """Place code on the cheapest path that is safe by static inspection."""
    explicit = forced_path(where)
    if explicit:
        return explicit
    if packages:
        return "sandbox"
    try:
        tree = ast.parse(code)
    except SyntaxError:
        # Syntax errors are harmless, and the in-process path reports them with
        # the lowest latency and a useful traceback.
        return "inproc"
    if not is_pure(tree):
        return "sandbox"
    if acceleratable_functions(tree):
        return "accelerated"
    return "inproc"


def is_pure(tree: ast.AST) -> bool:
    """Whether the tree has no observable host/network authority."""
    definitions = {
        node.name for node in getattr(tree, "body", ())
        if isinstance(node, ast.FunctionDef)
    }
    imported_roots: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, _UNTRUSTED_NODES):
            return False
        if isinstance(node, ast.Import):
            for alias in node.names:
                root = alias.name.split(".", 1)[0]
                if root not in _PURE_MODULES:
                    return False
                imported_roots.add(alias.asname or root)
        elif isinstance(node, ast.ImportFrom):
            root = (node.module or "").split(".", 1)[0]
            if node.level or root not in _PURE_MODULES:
                return False
        elif isinstance(node, ast.Name) and node.id in _DANGEROUS_NAMES:
            return False
        elif isinstance(node, ast.Attribute):
            root = _attribute_root(node)
            if root in _DANGEROUS_ROOTS:
                return False
        elif isinstance(node, ast.Call):
            if isinstance(node.func, ast.Name):
                name = node.func.id
                if name in _DANGEROUS_NAMES:
                    return False
                if (
                    name not in _SAFE_CALLS
                    and name not in definitions
                    and name not in imported_roots
                ):
                    return False
            elif isinstance(node.func, ast.Attribute):
                root = _attribute_root(node.func)
                if root not in imported_roots:
                    return False
            else:
                return False
    return True


def acceleratable_functions(tree: ast.AST | str) -> list[str]:
    """Names worth decorating with mojosub's non-blocking JIT."""
    if isinstance(tree, str):
        try:
            tree = ast.parse(tree)
        except SyntaxError:
            return []
    definitions = {
        node.name for node in getattr(tree, "body", ())
        if isinstance(node, ast.FunctionDef)
    }
    return [
        node.name for node in getattr(tree, "body", ())
        if isinstance(node, ast.FunctionDef)
        and _acceleratable(node, definitions)
    ]


def _acceleratable(node: ast.FunctionDef, definitions: set[str]) -> bool:
    if node.decorator_list or not any(
        isinstance(child, (ast.For, ast.While)) for child in ast.walk(node)
    ):
        return False
    for child in ast.walk(node):
        if not isinstance(child, _ACCEL_NODES):
            return False
        if isinstance(child, ast.Constant) and not isinstance(
            child.value, (int, float, bool, type(None))
        ):
            return False
        if isinstance(child, ast.Call):
            if isinstance(child.func, ast.Name):
                if child.func.id not in _ACCEL_CALLS and child.func.id not in definitions:
                    return False
            elif isinstance(child.func, ast.Attribute):
                if (
                    _attribute_root(child.func) != "math"
                    or child.func.attr not in _MATH_CALLS
                ):
                    return False
            else:
                return False
        if isinstance(child, ast.Attribute) and not (
            isinstance(child.ctx, ast.Load)
            and (
                (_attribute_root(child) == "math" and child.attr in _MATH_CALLS)
                or child.attr in {"shape"}
            )
        ):
            return False
    return True


def _attribute_root(node: ast.Attribute) -> str:
    value: ast.AST = node
    while isinstance(value, ast.Attribute):
        value = value.value
    return value.id if isinstance(value, ast.Name) else ""


__all__ = [
    "PATHS", "acceleratable_functions", "choose_path", "forced_path",
    "is_pure", "normalize_path",
]
