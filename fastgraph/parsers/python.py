"""Python adapter (tree-sitter-python)."""

from __future__ import annotations

import tree_sitter_python
from tree_sitter import Language, Parser

from fastgraph.parsers.base import CallRef, ImportRef, ParseResult, SymbolInfo
from fastgraph.parsers.registry import register_adapter
from fastgraph.parsers.util import node_text


def _parse_calls(node, source: bytes) -> list[CallRef]:
    """Collect call_expression targets inside `node`, skipping nested defs."""
    calls: list[CallRef] = []

    def walk(n) -> None:
        if n.type == "function_definition" and n is not node:
            return  # skip nested function bodies (owned by their own symbol)
        if n.type == "call":
            fn = n.child_by_field_name("function")
            if fn is not None:
                target = node_text(fn, source, 160)
                last = target.split(".")[-1]
                calls.append(CallRef(target=last or target, line=n.start_point[0] + 1))
                if "." in target:
                    calls.append(CallRef(target=target, line=n.start_point[0] + 1))
        for c in n.named_children:
            walk(c)

    walk(node)
    return calls


def _docstring(node, source: bytes) -> str:
    body = node.child_by_field_name("body")
    if body is None:
        return ""
    for c in body.named_children:
        if c.type == "expression_statement":
            for s in c.named_children:
                if s.type == "string":
                    return node_text(s, source, 400)
    return ""


def _class_bases(node, source: bytes) -> list[CallRef]:
    bases: list[CallRef] = []
    sc = node.child_by_field_name("superclasses")
    if sc is not None:
        for c in sc.named_children:
            if c.type in ("identifier", "attribute"):
                bases.append(
                    CallRef(
                        target=node_text(c, source, 160),
                        line=c.start_point[0] + 1,
                        rtype="inherits",
                    )
                )
    return bases


class PythonAdapter:
    lang = "python"
    exts = (".py",)

    _parser: Parser | None = None

    def __init__(self):
        if PythonAdapter._parser is None:
            PythonAdapter._parser = Parser(Language(tree_sitter_python.language()))

    def parse(self, source: bytes) -> ParseResult:
        tree = self._parser.parse(source)
        if tree.root_node.has_error:
            raise SyntaxError("python: tree-sitter parse error(s) in root node")
        symbols: list[SymbolInfo] = []
        imports: list[ImportRef] = []
        module_doc = ""

        def walk(node, stack: list[SymbolInfo]) -> None:
            nonlocal module_doc
            t = node.type

            if t == "import_statement":
                imports.append(ImportRef(text=node_text(node, source, 300), line=node.start_point[0] + 1))
            elif t == "import_from_statement":
                imports.append(ImportRef(text=node_text(node, source, 300), line=node.start_point[0] + 1))
            elif t == "class_definition":
                name = node_text(node.child_by_field_name("name"), source, 120)
                parent = stack[-1].qualified_name if stack else None
                sym = SymbolInfo(
                    name=name,
                    kind="class",
                    qualified_name=parent + "." + name if parent else name,
                    signature=f"class {name}",
                    doc=_docstring(node, source),
                    start_line=node.start_point[0] + 1,
                    end_line=node.end_point[0] + 1,
                    start_col=node.start_point[1],
                    end_col=node.end_point[1],
                    parent=parent,
                    bases=_class_bases(node, source),
                )
                symbols.append(sym)
                for c in node.named_children:
                    walk(c, stack + [sym])
            elif t == "function_definition":
                name = node_text(node.child_by_field_name("name"), source, 120)
                if not name:
                    return
                params = node.child_by_field_name("parameters")
                parent = stack[-1].qualified_name if stack else None
                kind = "method" if parent else "function"
                sym = SymbolInfo(
                    name=name,
                    kind=kind,
                    qualified_name=parent + "." + name if parent else name,
                    signature=f"def {name}{node_text(params, source, 200) if params else '()'}",
                    doc=_docstring(node, source),
                    start_line=node.start_point[0] + 1,
                    end_line=node.end_point[0] + 1,
                    start_col=node.start_point[1],
                    end_col=node.end_point[1],
                    parent=parent,
                    calls=_parse_calls(node, source),
                )
                symbols.append(sym)
                for c in node.named_children:
                    walk(c, stack + [sym])
            else:
                if t == "expression_statement" and not module_doc and not stack:
                    for s in node.named_children:
                        if s.type == "string":
                            module_doc = node_text(s, source, 500)
                for c in node.named_children:
                    walk(c, stack)

        walk(tree.root_node, [])
        return ParseResult(language=self.lang, symbols=symbols, imports=imports, module_doc=module_doc)


register_adapter(PythonAdapter())