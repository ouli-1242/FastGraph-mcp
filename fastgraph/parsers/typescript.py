"""TypeScript / JavaScript / JSX adapter (tree-sitter-typescript + javascript)."""

from __future__ import annotations

import tree_sitter_javascript
import tree_sitter_typescript
from tree_sitter import Language, Parser

from fastgraph.parsers.base import CallRef, ImportRef, ParseResult, SymbolInfo
from fastgraph.parsers.registry import register_adapter
from fastgraph.parsers.util import node_text

_TS_LANGS: dict[str, Language] = {
    "typescript": Language(tree_sitter_typescript.language_typescript()),
    "tsx": Language(tree_sitter_typescript.language_tsx()),
    "javascript": Language(tree_sitter_javascript.language()),
}


def _calls_in(node, source: bytes) -> list[CallRef]:
    calls: list[CallRef] = []

    def walk(n, top: bool) -> None:
        if not top and n.type in (
            "function_declaration", "function_expression", "arrow_function",
            "method_definition", "class_declaration", "lexical_declaration",
        ):
            return
        if n.type == "call_expression":
            fn = n.child_by_field_name("function")
            if fn is not None:
                target = node_text(fn, source, 160)
                last = target.split(".")[-1]
                calls.append(CallRef(target=last or target, line=n.start_point[0] + 1))
                if "." in target:
                    calls.append(CallRef(target=target, line=n.start_point[0] + 1))
        for c in n.named_children:
            walk(c, False)

    walk(node, True)
    return calls


def _js_doc(node, source: bytes) -> str:
    for c in node.named_children:
        if c.type == "comment":
            return node_text(c, source, 300)
        if c.type in ("statement_block", "class_body"):
            for inner in c.named_children:
                if inner.type == "comment":
                    return node_text(inner, source, 300)
    return ""


def _ts_bases(node, source: bytes) -> list[CallRef]:
    bases: list[CallRef] = []
    for c in node.named_children:
        if c.type == "class_heritage":
            for h in c.named_children:
                if h.type in ("identifier", "nested_identifier"):
                    bases.append(
                        CallRef(target=node_text(h, source, 160), line=h.start_point[0] + 1, rtype="inherits")
                    )
    return bases


class TSAdapter:
    lang = "typescript"
    exts = (".ts", ".mts", ".cts")

    def __init__(self, jsx: bool = False, lang: str = "typescript", exts=(".ts", ".mts", ".cts")):
        self.lang = lang
        self.exts = exts
        self._parser = Parser(_TS_LANGS[lang])

    def parse(self, source: bytes) -> ParseResult:
        tree = self._parser.parse(source)
        symbols: list[SymbolInfo] = []
        imports: list[ImportRef] = []

        def walk(node, stack: list[SymbolInfo]) -> None:
            t = node.type

            if t == "import_statement":
                imports.append(ImportRef(text=node_text(node, source, 300), line=node.start_point[0] + 1))
            elif t == "class_declaration":
                name_node = node.child_by_field_name("name")
                name = node_text(name_node, source, 120)
                parent = stack[-1].qualified_name if stack else None
                sym = SymbolInfo(
                    name=name, kind="class",
                    qualified_name=parent + "." + name if parent else name,
                    signature=f"class {name}", doc=_js_doc(node, source),
                    start_line=node.start_point[0] + 1, end_line=node.end_point[0] + 1,
                    start_col=node.start_point[1], end_col=node.end_point[1],
                    parent=parent, bases=_ts_bases(node, source),
                )
                symbols.append(sym)
                for c in node.named_children:
                    walk(c, stack + [sym])
            elif t == "method_definition":
                name = node_text(node.child_by_field_name("name"), source, 120)
                parent = stack[-1].qualified_name if stack else None
                params = node.child_by_field_name("parameters")
                sig = f"{name}({node_text(params, source, 200) if params else ''})"
                sym = SymbolInfo(
                    name=name, kind="method",
                    qualified_name=parent + "." + name if parent else name,
                    signature=sig, doc=_js_doc(node, source),
                    start_line=node.start_point[0] + 1, end_line=node.end_point[0] + 1,
                    start_col=node.start_point[1], end_col=node.end_point[1],
                    parent=parent, calls=_calls_in(node, source),
                )
                symbols.append(sym)
                for c in node.named_children:
                    walk(c, stack + [sym])
            elif t == "function_declaration":
                name = node_text(node.child_by_field_name("name"), source, 120)
                parent = stack[-1].qualified_name if stack else None
                params = node.child_by_field_name("parameters")
                sym = SymbolInfo(
                    name=name, kind="function",
                    qualified_name=parent + "." + name if parent else name,
                    signature=f"function {name}({node_text(params, source, 200) if params else ''})",
                    doc=_js_doc(node, source),
                    start_line=node.start_point[0] + 1, end_line=node.end_point[0] + 1,
                    start_col=node.start_point[1], end_col=node.end_point[1],
                    parent=parent, calls=_calls_in(node, source),
                )
                symbols.append(sym)
                for c in node.named_children:
                    walk(c, stack + [sym])
            elif t in ("lexical_declaration", "variable_declaration"):
                for v in node.named_children:
                    if v.type == "variable_declarator":
                        vname = node_text(v.child_by_field_name("name"), source, 120)
                        value = v.child_by_field_name("value")
                        if value is not None and value.type in ("arrow_function", "function_expression"):
                            parent = stack[-1].qualified_name if stack else None
                            sym = SymbolInfo(
                                name=vname, kind="function",
                                qualified_name=parent + "." + vname if parent else vname,
                                signature=f"const {vname} = (…)",
                                doc="",
                                start_line=v.start_point[0] + 1, end_line=v.end_point[0] + 1,
                                start_col=v.start_point[1], end_col=v.end_point[1],
                                parent=parent, calls=_calls_in(value, source),
                            )
                            symbols.append(sym)
            else:
                for c in node.named_children:
                    walk(c, stack)

        walk(tree.root_node, [])
        return ParseResult(language=self.lang, symbols=symbols, imports=imports)


register_adapter(TSAdapter(lang="typescript", exts=(".ts", ".mts", ".cts")))
register_adapter(TSAdapter(lang="tsx", exts=(".tsx",)))
register_adapter(TSAdapter(lang="javascript", exts=(".js", ".mjs", ".cjs", ".jsx")))