#!/usr/bin/env python3
"""Static scan of a frontend source tree for request-amplification patterns.

Finds the places where a web app is likely to make more HTTP requests than
the screen needs: a query hook inside a row component, a poll timer in a
shared hook, zero freshness, blanket invalidation, hand-rolled fetching,
serial awaits, fetch-everything pagination, and so on.

Every hit is a CANDIDATE. The scanner reads text, not types, so it cannot
tell a bounded 3-row list from an unbounded 3,000-row one, or a deliberate
root invalidation from a lazy one. The skill that wraps this script verifies
each candidate by reading the code before anything is reported as a finding.

Usage:
    static_scan.py ROOT [--format json|md] [--out FILE]
                        [--exclude GLOB ...] [--min-confidence low|medium|high]

Only the standard library is used, so the script runs anywhere Python 3.9+
does. Exit code is 0 when the scan ran, 2 on a usage error.
"""
from __future__ import annotations

import argparse
import bisect
import fnmatch
import json
import os
import re
import sys
from dataclasses import asdict, dataclass, field
from typing import Dict, Iterable, List, Optional, Tuple

SOURCE_EXTENSIONS = (".ts", ".tsx", ".js", ".jsx", ".mjs", ".cjs", ".vue", ".svelte")
DEFAULT_EXCLUDES = (
    "node_modules", "dist", "build", "out", ".next", ".nuxt", ".git", "coverage",
    "storybook-static", "__tests__", "__mocks__", "e2e", "fixtures",
)
EXCLUDED_FILE_PATTERNS = (
    "*.test.*", "*.spec.*", "*.stories.*", "*.d.ts", "*.gen.*", "*.generated.*",
    "*routeTree.gen*", "*.msw.*",
)

# Hooks that create a subscription to server state. `use[A-Z]\w*Query` also
# matches RTK Query's generated hooks and Apollo's useQuery/useLazyQuery.
QUERY_HOOK_RE = re.compile(
    r"\b(?:use(?:Suspense)?(?:Infinite)?Quer(?:y|ies)|useSWR\w*|use[A-Z]\w*Query)\s*\("
)
POLL_OPTION_RE = re.compile(r"\b(refetchInterval|refreshInterval|pollInterval)\s*:\s*")
FETCH_CALL_RE = re.compile(r"\b(?:fetch|axios(?:\.\w+)?|ky(?:\.\w+)?|got(?:\.\w+)?)\s*\(")
SDK_CALL_HINT_RE = re.compile(
    r"\b(?:fetch|axios|ky|got|client|api|sdk|transport|http|request|get[A-Z]\w*|list[A-Z]\w*|load[A-Z]\w*|read[A-Z]\w*)\b"
)
COMPONENT_DEF_RE = re.compile(
    r"(?:export\s+(?:default\s+)?)?(?:function\s+([A-Z]\w*)\s*(?=\()|(?:const|let|var)\s+([A-Z]\w*)\s*(?::[^=]+)?=\s*)"
)
HOOK_DEF_RE = re.compile(
    r"(?:export\s+)?(?:function\s+(use[A-Z]\w*)\s*(?=\()|(?:const|let|var)\s+(use[A-Z]\w*)\s*(?::[^=]+)?=\s*)"
)
# Higher-order wrappers that sit between `const X =` and the function body.
WRAPPER_NAMES = {"memo", "forwardRef", "observer", "styled", "withRouter", "connect", "lazy"}
ASYNC_FN_RE = re.compile(
    r"(?:async\s+function\s*(\w*)\s*\([^)]*\)\s*(?::[^{]+)?\{|(?:const|let|var)\s+(\w+)\s*=\s*async\s*(?:\([^)]*\)|\w+)\s*(?::[^=]+)?=>\s*\{|(\w+)\s*:\s*async\s*(?:\([^)]*\)|\w+)\s*=>\s*\{)"
)
AWAIT_ASSIGN_RE = re.compile(r"^\s*(?:const|let|var)\s+(?:\{[^}]*\}|\[[^\]]*\]|(\w+))\s*=\s*await\s+([\w.$]+)\s*\(")
AWAIT_BARE_RE = re.compile(r"^\s*await\s+([\w.$]+)\s*\(")
EXPORT_NAME_RE = re.compile(r"export\s+(?:const|let|var|function|async\s+function)\s+(\w+)")
IMPORT_RE = re.compile(r"""\b(?:import|export)\s+(?:(?:type\s+)?([^'"]*?)\s+from\s+)?['"]([^'"]+)['"]""")
LABEL_FIELDS = ("name", "displayName", "display_name", "title", "label")

ALIAS_PREFIXES = ("@/", "~/", "#/", "src/", "$lib/")


@dataclass
class Finding:
    id: str
    title: str
    severity: str  # P0 | P1 | P2 | P3 | info
    confidence: str  # high | medium | low
    file: str
    line: int
    snippet: str
    why: str
    fix: str
    verify: str
    catalog: str  # anchor in references/catalog.md
    extra: Dict[str, object] = field(default_factory=dict)


@dataclass
class SourceFile:
    path: str
    rel: str
    text: str  # comments stripped
    raw: str
    line_starts: List[int]

    def line_of(self, offset: int) -> int:
        return bisect.bisect_right(self.line_starts, offset)

    def line_text(self, line: int) -> str:
        lines = self.raw.splitlines()
        if 1 <= line <= len(lines):
            return lines[line - 1].strip()
        return ""


BLOCK_COMMENT_RE = re.compile(r"/\*.*?\*/", re.S)
LINE_COMMENT_RE = re.compile(r"(^|[\s;{(,\[])//[^\n]*", re.M)


def strip_comments(text: str) -> str:
    """Blank out comments while keeping offsets stable (newlines preserved)."""

    def blank(match: re.Match) -> str:
        return re.sub(r"[^\n]", " ", match.group(0))

    text = BLOCK_COMMENT_RE.sub(blank, text)

    def blank_line(match: re.Match) -> str:
        return match.group(1) + re.sub(r"[^\n]", " ", match.group(0)[len(match.group(1)):])

    return LINE_COMMENT_RE.sub(blank_line, text)


def matching_close(text: str, open_idx: int, open_ch: str = "{", close_ch: str = "}") -> int:
    """Index of the bracket closing the one at open_idx, skipping string literals."""
    depth = 0
    i = open_idx
    n = len(text)
    quote: Optional[str] = None
    while i < n:
        ch = text[i]
        if quote:
            if ch == "\\":
                i += 2
                continue
            if ch == quote:
                quote = None
        elif ch in ("'", '"', "`"):
            quote = ch
        elif ch == open_ch:
            depth += 1
        elif ch == close_ch:
            depth -= 1
            if depth == 0:
                return i
        i += 1
    return n - 1


def body_after(text: str, start: int) -> Tuple[int, int]:
    """Span of the first `{...}` block at or after start."""
    open_idx = text.find("{", start)
    if open_idx == -1:
        return start, start
    return open_idx, matching_close(text, open_idx)


def find_function_body(text: str, start: int) -> int:
    """Index of the `{` that opens the function body declared at `start`, or -1.

    Walks past a parameter list (so a destructured `({ id })` is not mistaken
    for the body), through `memo(` / `forwardRef(` wrappers, and up to an arrow.
    An expression-bodied arrow (`=> <Row />`) or a plain value (`= 5`) gives -1.
    """
    i, n, depth, wrapped = start, len(text), 0, 0
    quote: Optional[str] = None
    while i < n:
        ch = text[i]
        if quote:
            if ch == "\\":
                i += 2
                continue
            if ch == quote:
                quote = None
        elif ch in ("'", '"', "`"):
            quote = ch
        elif ch == "(":
            if depth == 0:
                ident = re.search(r"([\w.]+)\s*$", text[max(0, i - 40):i])
                if ident and ident.group(1).split(".")[-1] in WRAPPER_NAMES:
                    wrapped += 1
                    i += 1
                    continue
            depth += 1
        elif ch == ")":
            if depth == 0:
                if wrapped:
                    wrapped -= 1
                    i += 1
                    continue
                return -1
            depth -= 1
        elif depth == 0:
            if ch == "{":
                return i
            if ch == ";":
                return -1
            if ch == "=" and text[i + 1:i + 2] == ">":
                j = i + 2
                while j < n and text[j] in " \t\r\n":
                    j += 1
                return j if text[j:j + 1] == "{" else -1
        i += 1
    return -1


def collect_files(root: str, excludes: Iterable[str]) -> List[SourceFile]:
    files: List[SourceFile] = []
    exclude_dirs = set(DEFAULT_EXCLUDES) | set(excludes)
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = sorted(
            d for d in dirnames
            if d not in exclude_dirs and not any(fnmatch.fnmatch(d, pat) for pat in excludes)
        )
        for name in sorted(filenames):
            if not name.endswith(SOURCE_EXTENSIONS):
                continue
            if any(fnmatch.fnmatch(name, pat) for pat in EXCLUDED_FILE_PATTERNS):
                continue
            path = os.path.join(dirpath, name)
            rel = os.path.relpath(path, root)
            if any(fnmatch.fnmatch(rel, pat) for pat in excludes):
                continue
            try:
                with open(path, encoding="utf-8", errors="replace") as handle:
                    raw = handle.read()
            except OSError:
                continue
            text = strip_comments(raw)
            line_starts = [0] + [m.end() for m in re.finditer(r"\n", text)]
            files.append(SourceFile(path=path, rel=rel, text=text, raw=raw, line_starts=line_starts))
    return files


def detect_libraries(root: str) -> List[str]:
    found: List[str] = []
    markers = {
        "@tanstack/react-query": "TanStack Query (React)",
        "@tanstack/vue-query": "TanStack Query (Vue)",
        "@tanstack/svelte-query": "TanStack Query (Svelte)",
        "@tanstack/solid-query": "TanStack Query (Solid)",
        "react-query": "React Query v3",
        "swr": "SWR",
        "@apollo/client": "Apollo Client",
        "@reduxjs/toolkit": "RTK Query (if createApi is used)",
        "urql": "urql",
        "@tanstack/query-persist-client-core": "TanStack persisted cache",
        "@tanstack/react-query-persist-client": "TanStack persisted cache",
    }
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d not in DEFAULT_EXCLUDES]
        if "package.json" in filenames:
            try:
                with open(os.path.join(dirpath, "package.json"), encoding="utf-8") as handle:
                    pkg = json.load(handle)
            except (OSError, ValueError):
                continue
            deps = {}
            for key in ("dependencies", "devDependencies", "peerDependencies"):
                deps.update(pkg.get(key) or {})
            for dep, label in markers.items():
                if dep in deps:
                    entry = f"{label} ({dep}@{deps[dep]})"
                    if entry not in found:
                        found.append(entry)
    return found


# ---------------------------------------------------------------------------
# Module graph (approximate): who imports whom.
# ---------------------------------------------------------------------------

def module_id(rel: str) -> str:
    base, _ = os.path.splitext(rel)
    if os.path.basename(base) == "index":
        base = os.path.dirname(base)
    return base.replace(os.sep, "/")


@dataclass
class ImportGraph:
    # module_id -> [(importer rel path, import clause text)]
    importers: Dict[str, List[Tuple[str, str]]]
    # importer rel path -> [(target module_id, import clause text)]
    imports: Dict[str, List[Tuple[str, str]]]

    def importers_of(self, mid: str, symbol: Optional[str] = None) -> List[str]:
        """Files importing the module; with a symbol, only those naming it (or importing *)."""
        out = set()
        for rel, clause in self.importers.get(mid, []):
            if symbol is None or re.search(rf"\b{re.escape(symbol)}\b", clause) or "* as" in clause:
                out.add(rel)
        return sorted(out)

    def modules_exporting(self, importer_rel: str, symbol: str) -> List[str]:
        return [mid for mid, clause in self.imports.get(importer_rel, [])
                if re.search(rf"\b{re.escape(symbol)}\b", clause)]


def build_import_graph(files: List[SourceFile], root: str) -> ImportGraph:
    ids = {module_id(f.rel): f.rel for f in files}
    src_root = "src" if os.path.isdir(os.path.join(root, "src")) else ""
    importers: Dict[str, List[Tuple[str, str]]] = {mid: [] for mid in ids}
    imports: Dict[str, List[Tuple[str, str]]] = {}
    by_basename: Dict[str, List[str]] = {}
    for mid in ids:
        by_basename.setdefault(mid.rsplit("/", 1)[-1], []).append(mid)

    for f in files:
        importer_dir = os.path.dirname(f.rel).replace(os.sep, "/")
        for match in IMPORT_RE.finditer(f.text):
            clause = (match.group(1) or "").strip()
            spec = match.group(2)
            target: Optional[str] = None
            if spec.startswith("."):
                target = os.path.normpath(os.path.join(importer_dir, spec)).replace(os.sep, "/")
            else:
                for prefix in ALIAS_PREFIXES:
                    if spec.startswith(prefix):
                        rest = spec[len(prefix):]
                        target = f"{src_root}/{rest}" if src_root else rest
                        target = target.lstrip("/")
                        break
            if target is None:
                continue
            target = target.replace("\\", "/")
            if target not in ids:
                candidates = by_basename.get(target.rsplit("/", 1)[-1], [])
                if len(candidates) != 1:
                    continue
                target = candidates[0]
            importers[target].append((f.rel, clause))
            imports.setdefault(f.rel, []).append((target, clause))
    return ImportGraph(importers=importers, imports=imports)


# ---------------------------------------------------------------------------
# Definitions: components and hooks with their bodies.
# ---------------------------------------------------------------------------

@dataclass
class Definition:
    name: str
    file: SourceFile
    line: int
    body: str
    start: int


def find_definitions(files: List[SourceFile], pattern: re.Pattern) -> Dict[str, List[Definition]]:
    defs: Dict[str, List[Definition]] = {}
    for f in files:
        for match in pattern.finditer(f.text):
            name = match.group(1) or match.group(2)
            if not name:
                continue
            open_idx = find_function_body(f.text, match.end())
            if open_idx < 0:
                continue
            close_idx = matching_close(f.text, open_idx)
            body = f.text[open_idx:close_idx + 1]
            defs.setdefault(name, []).append(
                Definition(name=name, file=f, line=f.line_of(match.start()), body=body, start=open_idx)
            )
    return defs


def data_hooks(files: List[SourceFile]) -> Dict[str, Definition]:
    """Custom hooks that (transitively, one level) call a query hook."""
    hooks = find_definitions(files, HOOK_DEF_RE)
    direct: Dict[str, Definition] = {}
    for name, defs in hooks.items():
        for d in defs:
            if QUERY_HOOK_RE.search(d.body):
                direct[name] = d
                break
    indirect: Dict[str, Definition] = {}
    for name, defs in hooks.items():
        if name in direct:
            continue
        for d in defs:
            if any(re.search(rf"\b{re.escape(h)}\s*\(", d.body) for h in direct):
                indirect[name] = d
                break
    merged = dict(direct)
    merged.update(indirect)
    return merged


# ---------------------------------------------------------------------------
# Detectors
# ---------------------------------------------------------------------------

def resolve_component(name: str, renderer: SourceFile, components: Dict[str, List[Definition]],
                      graph: ImportGraph) -> List[Definition]:
    """Which definition a JSX tag in `renderer` refers to: local, then imported, then unique."""
    defs = components.get(name, [])
    local = [d for d in defs if d.file.rel == renderer.rel]
    if local:
        return local
    sources = set(graph.modules_exporting(renderer.rel, name))
    imported = [d for d in defs if module_id(d.file.rel) in sources]
    if imported:
        return imported
    return defs if len(defs) == 1 else []


def detect_per_row_queries(files: List[SourceFile], hooks: Dict[str, Definition], graph: ImportGraph) -> List[Finding]:
    components = find_definitions(files, COMPONENT_DEF_RE)
    by_component: Dict[Tuple[str, int], Dict[str, object]] = {}
    for f in files:
        for match in re.finditer(r"\.map\s*\(", f.text):
            close = matching_close(f.text, match.end() - 1, "(", ")")
            callback = f.text[match.end():close]
            for tag in set(t.group(1) for t in re.finditer(r"<([A-Z]\w*)", callback)):
                for comp in resolve_component(tag, f, components, graph):
                    direct = QUERY_HOOK_RE.search(comp.body)
                    via_hook = next(
                        (h for h in hooks if re.search(rf"\b{re.escape(h)}\s*\(", comp.body)), None
                    )
                    if not direct and not via_hook:
                        continue
                    key = (comp.file.rel, comp.line)
                    site = f"{f.rel}:{f.line_of(match.start())}"
                    entry = by_component.setdefault(key, {
                        "name": tag, "comp": comp, "direct": direct, "via_hook": via_hook, "sites": [],
                    })
                    if site not in entry["sites"]:
                        entry["sites"].append(site)

    findings: List[Finding] = []
    for entry in by_component.values():
        comp: Definition = entry["comp"]  # type: ignore[assignment]
        direct = entry["direct"]
        via_hook = entry["via_hook"]
        sites: List[str] = entry["sites"]  # type: ignore[assignment]
        cache_only = bool(re.search(r"enabled\s*:\s*false", comp.body))
        confidence = "high" if direct else "medium"
        if cache_only:
            confidence = "low"
        hook_line = comp.line
        if direct:
            hook_line = comp.file.line_of(comp.start + direct.start())
        findings.append(Finding(
            id="per-row-query",
            title=f"<{entry['name']}> runs a query hook and is rendered per row ({len(sites)} list site{'s' if len(sites) > 1 else ''})",
            severity="P0",
            confidence=confidence,
            file=comp.file.rel,
            line=hook_line,
            snippet=comp.file.line_text(hook_line),
            why=(
                "Each rendered row mounts its own query, so the request count equals the row "
                "count (N+1). Rows that share no cache entry each hit the network, and a list "
                "refetch repeats all of them."
            ),
            fix=(
                "Lift the read to the list: one batch query keyed on the set of ids (an `in` "
                "filter, a names/labels endpoint, or useQueries with combine), chunked to the "
                "backend's limit, and hand each row its slice."
            ),
            verify=(
                f"Open {sites[0]} to see how many rows render; confirm the hook's key includes a per-row id"
                + (f" (reached through {via_hook})" if via_hook else "")
                + (". The hook is `enabled: false`, so this may be a cache read only." if cache_only else ".")
                + " A row count with a small fixed ceiling (an enum, a tab strip) is not a finding."
            ),
            catalog="per-row-query",
            extra={"rendered_in": sites, "via_hook": via_hook},
        ))
    return findings


def resolve_ms(value: str, file_text: str) -> Optional[int]:
    """Milliseconds for a literal (`60_000`, `5 * 1000`) or a constant defined in the file."""
    expr = value.strip()
    if re.fullmatch(r"[A-Za-z_]\w*", expr):
        const = re.search(rf"\b{re.escape(expr)}\s*=\s*([^\n;]+)", file_text)
        if not const:
            return None
        expr = const.group(1).strip().rstrip(",")
    expr = expr.replace("_", "")
    if re.fullmatch(r"[\d\s*+]+", expr):
        try:
            return int(eval(expr, {"__builtins__": {}}, {}))  # digits, spaces, * and + only
        except (SyntaxError, ValueError):
            return None
    return None


def classify_poll_value(text_after: str) -> str:
    head = text_after[:120].strip()
    if head.startswith(("(", "async", "function")) or "=>" in head.split("\n")[0]:
        return "function"
    if "?" in head.split("\n")[0]:
        return "conditional"
    return "always-on"


def enclosing_export(f: SourceFile, offset: int) -> Optional[str]:
    """Name of the exported symbol whose definition contains `offset`, if any."""
    best: Optional[str] = None
    for match in EXPORT_NAME_RE.finditer(f.text, 0, offset):
        best = match.group(1)
    return best


def detect_shared_polls(files: List[SourceFile], graph: ImportGraph) -> List[Finding]:
    findings: List[Finding] = []
    for f in files:
        for match in POLL_OPTION_RE.finditer(f.text):
            line = f.line_of(match.start())
            kind = classify_poll_value(f.text[match.end():])
            value = f.text[match.end():match.end() + 80].split("\n")[0].strip().rstrip(",")
            ms = resolve_ms(value, f.text)
            symbol = enclosing_export(f, match.start())
            shared_hook = bool(symbol) and (symbol.startswith("use") or "Query" in symbol or "Options" in symbol)
            callers = graph.importers_of(module_id(f.rel), symbol) if symbol else []
            importer_count = len(callers)
            if shared_hook and importer_count >= 2:
                severity, confidence = ("P0", "high") if kind == "always-on" else ("P1", "medium")
                findings.append(Finding(
                    id="shared-poll-timer",
                    title=f"Poll interval inside `{symbol}`, imported by {importer_count} modules",
                    severity=severity,
                    confidence=confidence,
                    file=f.rel,
                    line=line,
                    snippet=f.line_text(line),
                    why=(
                        "Query libraries run one interval timer per observer, not per key. A poll option in "
                        "a shared hook adds a timer for every component that calls it, each on its own clock, "
                        "so the endpoint is hit several times per period."
                    ),
                    fix=(
                        "Keep the poll in exactly one owner (the shell or a single subscriber component) and "
                        "let everyone else read the cache; or gate the option so only one caller passes it."
                    ),
                    verify=(
                        "Count mounted callers on a typical screen: " + ", ".join(callers[:6])
                        + ". If more than one can be mounted at once, the timers stack."
                    ),
                    catalog="shared-poll-timer",
                    extra={"importers": callers, "interval": value},
                ))
            if kind == "always-on":
                window = f.text[max(0, match.start() - 600):match.end() + 600]
                gated = bool(re.search(r"\benabled\s*:|visibilityState|document\.hidden|useIsIdle|idle", window))
                findings.append(Finding(
                    id="always-on-poll",
                    title=f"Unconditional poll every {ms} ms" if ms else f"Unconditional poll ({value})",
                    severity="P1" if (ms is not None and ms < 10_000) else "P2",
                    confidence="high" if not gated else "low",
                    file=f.rel,
                    line=line,
                    snippet=f.line_text(line),
                    why=(
                        "A literal interval keeps polling while the user is idle on a visible tab and while "
                        "nothing on screen changes. Hidden tabs pause by default, visible idle ones do not."
                    ),
                    fix=(
                        "Make the interval a function of the data (poll only while something is running), "
                        "or gate on idle/visibility, or replace with a push channel if one exists."
                    ),
                    verify=(
                        "Check what state the poll is waiting for; if the row has a terminal status, return "
                        "false from the interval function once it is reached."
                        + (" A gate (`enabled`/visibility) exists nearby; confirm it covers this call." if gated else "")
                    ),
                    catalog="always-on-poll",
                    extra={"interval": value},
                ))
    return findings


def detect_freshness(files: List[SourceFile]) -> List[Finding]:
    findings: List[Finding] = []
    patterns = [
        (r"\bstaleTime\s*:\s*0\b", "staleTime: 0", "Every mount and every focus refetches; the cache only dedupes concurrent reads."),
        (r"\b(?:gcTime|cacheTime)\s*:\s*0\b", "gcTime: 0", "Data is dropped the moment the last observer unmounts, so each return to the screen is a cold read."),
        (r"\brefetchOnMount\s*:\s*['\"]always['\"]", 'refetchOnMount: "always"', "Ignores freshness and refetches on every mount."),
        (r"\brefetchOnWindowFocus\s*:\s*['\"]always['\"]", 'refetchOnWindowFocus: "always"', "Ignores staleTime; every alt-tab back refetches."),
        (r"\brefetchOnReconnect\s*:\s*['\"]always['\"]", 'refetchOnReconnect: "always"', "Ignores staleTime on reconnect."),
        (r"\bdedupingInterval\s*:\s*0\b", "dedupingInterval: 0 (SWR)", "Concurrent callers each hit the network."),
        (r"\bfetchPolicy\s*:\s*['\"](?:no-cache|network-only)['\"]", "fetchPolicy: no-cache/network-only (Apollo)", "Bypasses the normalized cache on every render."),
    ]
    for f in files:
        for regex, label, why in patterns:
            for match in re.finditer(regex, f.text):
                line = f.line_of(match.start())
                findings.append(Finding(
                    id="zero-freshness",
                    title=f"{label} at a call site",
                    severity="P1",
                    confidence="medium",
                    file=f.rel,
                    line=line,
                    snippet=f.line_text(line),
                    why=why,
                    fix="Give the read a freshness window that matches how fast the data really changes, and invalidate on the writes that change it.",
                    verify="Confirm the data cannot tolerate even a few seconds of staleness; most reads can.",
                    catalog="zero-freshness",
                ))
    # QueryClient defaults.
    for f in files:
        for match in re.finditer(r"new\s+QueryClient\s*\(", f.text):
            close = matching_close(f.text, match.end() - 1, "(", ")")
            args = f.text[match.end():close]
            line = f.line_of(match.start())
            if not re.search(r"\bstaleTime\s*:", args):
                via_variable = bool(args.strip()) and "{" not in args
                findings.append(Finding(
                    id="default-stale-zero",
                    title="QueryClient has no default staleTime (defaults to 0)"
                          + (" - options passed as a variable, check it" if via_variable else ""),
                    severity="P1",
                    confidence="low" if via_variable else "high",
                    file=f.rel,
                    line=line,
                    snippet=f.line_text(line),
                    why=(
                        "With staleTime 0 every new observer refetches on mount, every window focus refetches, "
                        "and two components that mount a second apart each hit the network. This is the single "
                        "most common source of duplicate reads."
                    ),
                    fix="Set `defaultOptions.queries.staleTime` to a sensible floor (30-60 s) and override per query where data really changes faster.",
                    verify="Check whether a wrapper sets defaults elsewhere (setQueryDefaults) before counting this.",
                    catalog="default-stale-zero",
                ))
            if not re.search(r"\bretry\s*:", args):
                findings.append(Finding(
                    id="default-retry",
                    title="QueryClient keeps the default retry policy (3 retries with backoff)",
                    severity="info",
                    confidence="high",
                    file=f.rel,
                    line=line,
                    snippet=f.line_text(line),
                    why="A 4xx that will never succeed is retried three times; a 100-row N+1 with a 403 chunk becomes 400 requests.",
                    fix="Set `retry` to a function that returns false for 4xx (except 408/429) and keeps a small count for 5xx/network errors.",
                    verify="Look for a retry override on hot queries before calling this a gap.",
                    catalog="retry-policy",
                ))
    return findings


def detect_broad_invalidation(files: List[SourceFile]) -> List[Finding]:
    findings: List[Finding] = []
    for f in files:
        for match in re.finditer(r"\b(invalidateQueries|refetchQueries|resetQueries|removeQueries)\s*\(", f.text):
            close = matching_close(f.text, match.end() - 1, "(", ")")
            args = f.text[match.end():close].strip()
            line = f.line_of(match.start())
            root_like = (
                args == ""
                or re.search(r"\.(root|all)\s*\(\s*\)", args)
                or re.search(r"queryKey\s*:\s*\[\s*['\"][^'\"]+['\"]\s*\]", args)
            )
            if not root_like:
                continue
            window_before = f.text[max(0, match.start() - 400):match.start()]
            in_input_handler = bool(re.search(r"on(?:Change|Input|KeyDown|KeyUp)\s*[=:(]", window_before))
            findings.append(Finding(
                id="broad-invalidation",
                title=f"{match.group(1)}({args[:40]}{'...' if len(args) > 40 else ''}) invalidates a whole family",
                severity="P0" if in_input_handler else "P2",
                confidence="high" if in_input_handler else "medium",
                file=f.rel,
                line=line,
                snippet=f.line_text(line),
                why=(
                    "Invalidating a root key marks every query under it stale and refetches each one that is "
                    "mounted. One write turns into a page-wide reload"
                    + (", and here it runs from an input handler, so every keystroke does it." if in_input_handler else ".")
                ),
                fix="Invalidate the narrowest key the write actually changed (the list and the item), or update the cache from the write response with setQueryData.",
                verify="Count the mounted queries under this key on the screen where the write happens; if it is one or two, this is fine.",
                catalog="broad-invalidation",
            ))
    return findings


def detect_manual_fetching(files: List[SourceFile]) -> List[Finding]:
    findings: List[Finding] = []
    for f in files:
        in_view_layer = any(seg in f.rel.split("/") for seg in ("components", "routes", "pages", "app", "views", "screens", "features"))
        for match in re.finditer(r"\buseEffect\s*\(", f.text):
            open_idx, close_idx = body_after(f.text, match.end())
            body = f.text[open_idx:close_idx + 1]
            if FETCH_CALL_RE.search(body) or re.search(r"\b(?:client|api|sdk|transport|http|service)\w*\.(?:get|post|list|read)\w*\s*\(", body):
                line = f.line_of(match.start())
                findings.append(Finding(
                    id="effect-fetch",
                    title="useEffect fetches by hand",
                    severity="P1",
                    confidence="high" if FETCH_CALL_RE.search(body) else "medium",
                    file=f.rel,
                    line=line,
                    snippet=f.line_text(line),
                    why=(
                        "An effect-driven fetch has no cache, no dedup across components, no cancellation, "
                        "and runs again on every dependency change and StrictMode double-mount."
                    ),
                    fix="Move the read into a query hook with a key; let the cache dedupe and the library cancel.",
                    verify="Check whether the effect writes to local state that a query could own instead.",
                    catalog="effect-fetch",
                ))
        if in_view_layer:
            for match in FETCH_CALL_RE.finditer(f.text):
                line = f.line_of(match.start())
                findings.append(Finding(
                    id="fetch-in-view",
                    title="Direct HTTP call from the view layer",
                    severity="P3",
                    confidence="medium",
                    file=f.rel,
                    line=line,
                    snippet=f.line_text(line),
                    why="Reads that bypass the data layer are invisible to the cache, so they cannot be deduped or invalidated with the rest.",
                    fix="Route it through the transport/service that owns the endpoint and expose it as a query.",
                    verify="Ignore if this is a one-off download or an upload, which are not cacheable reads.",
                    catalog="fetch-in-view",
                ))
    return findings


def detect_serial_awaits(files: List[SourceFile]) -> List[Finding]:
    findings: List[Finding] = []
    for f in files:
        for match in ASYNC_FN_RE.finditer(f.text):
            open_idx = match.end() - 1
            if f.text[open_idx] != "{":
                continue
            close_idx = matching_close(f.text, open_idx)
            body = f.text[open_idx:close_idx + 1]
            body_start_line = f.line_of(open_idx)
            awaits: List[Tuple[int, Optional[str], str, str]] = []
            for i, raw_line in enumerate(body.splitlines()):
                m1 = AWAIT_ASSIGN_RE.match(raw_line)
                m2 = AWAIT_BARE_RE.match(raw_line) if not m1 else None
                if m1:
                    awaits.append((body_start_line + i, m1.group(1), m1.group(2), raw_line.strip()))
                elif m2:
                    awaits.append((body_start_line + i, None, m2.group(1), raw_line.strip()))
            if len(awaits) < 2:
                continue
            for (line_a, var_a, _call_a, text_a), (line_b, _var_b, _call_b, text_b) in zip(awaits, awaits[1:]):
                if line_b - line_a > 6:
                    continue
                if var_a and re.search(rf"\b{re.escape(var_a)}\b", text_b):
                    continue
                if not (SDK_CALL_HINT_RE.search(text_a) and SDK_CALL_HINT_RE.search(text_b)):
                    continue
                findings.append(Finding(
                    id="serial-awaits",
                    title="Two awaits in sequence that do not depend on each other",
                    severity="P2",
                    confidence="medium",
                    file=f.rel,
                    line=line_b,
                    snippet=f"{text_a}  /  {text_b}",
                    why="The second request waits for the first to finish although it needs nothing from it; the user pays both latencies in series.",
                    fix="Start both and await together (Promise.all), or make them two queries so the library runs them in parallel.",
                    verify="Confirm the second call really takes no input from the first; a shared side effect (auth refresh) can make the order matter.",
                    catalog="serial-awaits",
                ))
                break  # one per function is enough for a candidate list
    return findings


def detect_fetch_all(files: List[SourceFile]) -> List[Finding]:
    findings: List[Finding] = []
    for f in files:
        for match in re.finditer(r"\b(limit|pageSize|page_size|per_page|perPage|first|take)\s*[:=]\s*(\d[\d_]*)", f.text):
            try:
                n = int(match.group(2).replace("_", ""))
            except ValueError:
                continue
            if n < 500:
                continue
            line = f.line_of(match.start())
            findings.append(Finding(
                id="fetch-all",
                title=f"Page size {n} ({match.group(1)})",
                severity="P2",
                confidence="medium",
                file=f.rel,
                line=line,
                snippet=f.line_text(line),
                why="A page size this large usually means 'fetch everything' for a client-side filter or count, which grows with the tenant and ships bytes the screen never shows.",
                fix="Ask the server for the aggregate (count, facet) or filter server-side; page what the user can scroll.",
                verify="Check what the caller does with the rows; a count or a name lookup should be its own endpoint.",
                catalog="fetch-all",
            ))
        for match in re.finditer(r"\b(?:while|do)\b[^{]*\{", f.text):
            close = matching_close(f.text, match.end() - 1)
            body = f.text[match.end():close]
            if re.search(r"\b(cursor|nextPage|next_page|hasMore|has_more|offset|pageToken)\b", body) and re.search(r"\bawait\b", body):
                line = f.line_of(match.start())
                findings.append(Finding(
                    id="fetch-all",
                    title="Loop that walks every page",
                    severity="P2",
                    confidence="medium",
                    file=f.rel,
                    line=line,
                    snippet=f.line_text(line),
                    why="Walks the whole collection before rendering; request count and latency grow with the data.",
                    fix="Stop at the first page that satisfies the screen, or move the aggregate server-side.",
                    verify="Check whether the loop has an upper bound and whether a partial result would do.",
                    catalog="fetch-all",
                ))
    return findings


def detect_hover_prefetch(files: List[SourceFile]) -> List[Finding]:
    findings: List[Finding] = []
    for f in files:
        for match in re.finditer(r"on(?:Mouse|Pointer)Enter\s*=\s*\{", f.text):
            close = matching_close(f.text, match.end() - 1)
            handler = f.text[match.end():close]
            # Handlers often delegate (`onPointerEnter={() => onPrefetch(id)}`): follow one level.
            prefetchy = re.compile(r"prefetch|ensureQueryData|preload|warm", re.I)
            hit = bool(prefetchy.search(handler))
            if not hit:
                callee = re.search(r"(?:=>\s*|^\s*)(\w+)\s*(?:\(|$)", handler.strip())
                if callee:
                    name = callee.group(1)
                    definition = re.search(rf"(?:const|let|function)\s+{re.escape(name)}\b", f.text)
                    if definition:
                        body_idx = find_function_body(f.text, definition.end())
                        if body_idx >= 0:
                            hit = bool(prefetchy.search(f.text[body_idx:matching_close(f.text, body_idx) + 1]))
            if not hit:
                continue
            line = f.line_of(match.start())
            findings.append(Finding(
                id="hover-prefetch",
                title="Prefetch on hover",
                severity="info",
                confidence="high",
                file=f.rel,
                line=line,
                snippet=f.line_text(line),
                why="Often fine: one read per hovered row, deduped by the cache. Worth a look only if it fetches a full profile for a label, or if a sweep across a list fires dozens of reads.",
                fix="If it over-fetches, prefetch the projection the target screen needs first; add a short staleTime so repeated hovers do not refetch.",
                verify="Hover across ten rows in a capture and count the reads; one per row with no repeats is the expected result.",
                catalog="hover-prefetch",
            ))
    return findings


def detect_detail_for_label(files: List[SourceFile]) -> List[Finding]:
    findings: List[Finding] = []
    label_re = "|".join(LABEL_FIELDS)
    for f in files:
        for match in re.finditer(r"(?:const|let)\s+(?:\{\s*data\s*:\s*(\w+)[^}]*\}|(\w+))\s*=\s*" + QUERY_HOOK_RE.pattern, f.text):
            var = match.group(1) or match.group(2)
            if not var:
                continue
            close = matching_close(f.text, match.end() - 1, "(", ")")
            # Uses of the variable after the hook call, within the enclosing function.
            fn_close = matching_close(f.text, f.text.rfind("{", 0, match.start()))
            scope = f.text[close + 1:fn_close]
            uses = [m for m in re.finditer(rf"\b{re.escape(var)}\b(?!\s*=[^=>])", scope)]
            if not uses:
                continue
            label_uses = 0
            other_uses = 0
            for u in uses:
                window = scope[u.start():u.start() + 60]
                tail = scope[u.end():u.end() + 3].lstrip()
                if re.match(rf"{re.escape(var)}(?:\?\.|\.)(?:data(?:\?\.|\.))?(?:{label_re})\b", window):
                    label_uses += 1
                elif re.match(rf"{re.escape(var)}(?:\?\.|\.)(?:isLoading|isPending|isError|isFetching|error|status)\b", window):
                    continue
                elif scope[max(0, u.start() - 1):u.start()] == "!" or tail[:2] in (")", "&&", "?", "==", "!=", ",", "}", "||", "??", "? "):
                    continue  # a null check or a boolean test, not a read of the payload
                else:
                    other_uses += 1
            if label_uses == 0 or other_uses > 0:
                continue
            line = f.line_of(match.start())
            findings.append(Finding(
                id="detail-for-label",
                title=f"Full read of `{var}` used only for a label",
                severity="P2",
                confidence="medium",
                file=f.rel,
                line=line,
                snippet=f.line_text(line),
                why="The component fetches the whole object to print its name; the payload and the per-id read are both larger than the screen needs.",
                fix="Read names through a batch names/labels projection shared by the page, or use `select` so the cache entry is still shared but the component subscribes to the field only.",
                verify="Confirm nothing else on the screen already holds this object; if a parent list fetched it, pass the name down instead.",
                catalog="detail-for-label",
            ))
    return findings


def detect_unbounded_parallel(files: List[SourceFile]) -> List[Finding]:
    findings: List[Finding] = []
    for f in files:
        for match in re.finditer(r"Promise\.(?:all|allSettled)\s*\(\s*([\w.]+)\.map\s*\(", f.text):
            close = matching_close(f.text, match.end() - 1, "(", ")")
            callback = f.text[match.end():close]
            if not (FETCH_CALL_RE.search(callback) or re.search(r"\b(?:get|read|load|fetch|list)[A-Z]\w*\s*\(|\.(?:get|post|list)\s*\(", callback)):
                continue
            window = f.text[max(0, match.start() - 800):match.start()]
            limited = bool(re.search(r"limit|pLimit|p-limit|concurrency|semaphore|queue|chunk", window, re.I))
            line = f.line_of(match.start())
            findings.append(Finding(
                id="unbounded-parallel",
                title=f"Promise.all over {match.group(1)}.map with a request per item",
                severity="P1",
                confidence="low" if limited else "high",
                file=f.rel,
                line=line,
                snippet=f.line_text(line),
                why="Fires one request per element at once with no ceiling; a long list becomes a burst that trips rate limits and starves other reads.",
                fix="Batch the ids into one request where the API allows it; otherwise run them through a concurrency limiter (5-6 in flight) and make the work cancellable.",
                verify="Find the upper bound of the array; if it is a fixed enum it is fine." + (" A limiter is referenced nearby; confirm it wraps this call." if limited else ""),
                catalog="unbounded-parallel",
            ))
    return findings


def detect_hand_rolled_polls(files: List[SourceFile]) -> List[Finding]:
    findings: List[Finding] = []
    for f in files:
        for match in re.finditer(r"\bsetInterval\s*\(", f.text):
            close = matching_close(f.text, match.end() - 1, "(", ")")
            callback = f.text[match.end():close]
            if not re.search(r"fetch|refetch|invalidate|queryClient|mutate|load|poll|sync", callback, re.I):
                continue
            fn_open = f.text.rfind("{", 0, match.start())
            fn_close = matching_close(f.text, fn_open) if fn_open >= 0 else len(f.text)
            cleaned = "clearInterval" in f.text[fn_open:fn_close + 1]
            line = f.line_of(match.start())
            findings.append(Finding(
                id="hand-rolled-poll",
                title="setInterval drives a network read" + ("" if cleaned else " and is never cleared"),
                severity="P0" if not cleaned else "P2",
                confidence="high",
                file=f.rel,
                line=line,
                snippet=f.line_text(line),
                why=(
                    "A manual interval ignores visibility, idle state and the query cache"
                    + (", and without clearInterval each mount adds another timer that outlives the component." if not cleaned else ".")
                ),
                fix="Use the library's interval option on one owner, with a function that stops when the data settles; or clear the timer on unmount.",
                verify="Mount and unmount the component twice in a capture and count the reads per period.",
                catalog="hand-rolled-poll",
            ))
    return findings


def detect_key_hygiene(files: List[SourceFile]) -> List[Finding]:
    findings: List[Finding] = []
    roots: Dict[str, List[str]] = {}
    for f in files:
        if re.search(r"(query-keys|queryKeys|keys)\.[tj]sx?$", f.rel):
            continue
        for match in re.finditer(r"queryKey\s*:\s*\[\s*['\"]([^'\"]+)['\"]", f.text):
            roots.setdefault(match.group(1), []).append(f"{f.rel}:{f.line_of(match.start())}")
    if not roots:
        return findings
    normalized: Dict[str, List[str]] = {}
    for root in roots:
        norm = re.sub(r"[-_\s]", "", root.lower())  # plural vs singular is a legitimate list/item split
        normalized.setdefault(norm, []).append(root)
    collisions = {k: v for k, v in normalized.items() if len(v) > 1}
    for norm, variants in collisions.items():
        first_site = roots[variants[0]][0]
        file, _, line = first_site.rpartition(":")
        findings.append(Finding(
            id="key-drift",
            title="Query keys that differ only by spelling: " + ", ".join(f"'{v}'" for v in variants),
            severity="P2",
            confidence="medium",
            file=file,
            line=int(line),
            snippet=first_site,
            why="Two spellings of the same key are two cache entries; the same data is fetched once per spelling and invalidation misses one of them.",
            fix="Define keys once in a factory module and import it everywhere.",
            verify="Open each site and check whether they really read the same endpoint with the same scope.",
            catalog="key-drift",
            extra={"sites": {v: roots[v] for v in variants}},
        ))
    total_sites = sum(len(v) for v in roots.values())
    if total_sites >= 10:
        findings.append(Finding(
            id="inline-keys",
            title=f"{total_sites} inline string query keys across {len(roots)} roots",
            severity="P3",
            confidence="high",
            file=next(iter(roots.values()))[0].rpartition(":")[0],
            line=int(next(iter(roots.values()))[0].rpartition(":")[2]),
            snippet="; ".join(sorted(roots)[:8]),
            why="Inline keys drift; drift splits the cache and breaks invalidation.",
            fix="Move keys to a factory module (one function per scope) so the spelling has one owner.",
            verify="Hygiene, not a request bug by itself; pair with the key-drift entries above.",
            catalog="key-drift",
        ))
    return findings


def detect_platform_notes(files: List[SourceFile]) -> List[Finding]:
    findings: List[Finding] = []
    for f in files:
        for regex, title, why in (
            (r"\b(?:persistQueryClient|PersistQueryClientProvider|createSyncStoragePersister|createAsyncStoragePersister)\b",
             "Persisted query cache",
             "The cache outlives the page. Check `maxAge`, the `buster` on deploys, and that each platform (web, desktop shell) persists into its own store; a shared store leaks stale shapes across builds."),
            (r"\bfocusManager\.setEventListener\b",
             "Custom focus listener",
             "Focus refetching follows this listener, not the browser default. On a desktop shell it usually fires on OS window focus, which is more often than tab visibility; staleTime is what throttles it."),
            (r"\bonlineManager\.setEventListener\b",
             "Custom online listener",
             "Reconnect refetches follow this listener; a flapping signal refetches every stale query on each flip."),
        ):
            for match in re.finditer(regex, f.text):
                line = f.line_of(match.start())
                findings.append(Finding(
                    id="platform-note",
                    title=title,
                    severity="info",
                    confidence="high",
                    file=f.rel,
                    line=line,
                    snippet=f.line_text(line),
                    why=why,
                    fix="No change implied; record the behaviour in the report so freshness claims are platform-specific.",
                    verify="Read the listener and the persister options.",
                    catalog="platform-notes",
                ))
    return findings


DETECTORS = [
    ("per-row-query", lambda ctx: detect_per_row_queries(ctx["files"], ctx["hooks"], ctx["graph"])),
    ("shared-poll-timer", lambda ctx: detect_shared_polls(ctx["files"], ctx["graph"])),
    ("freshness", lambda ctx: detect_freshness(ctx["files"])),
    ("broad-invalidation", lambda ctx: detect_broad_invalidation(ctx["files"])),
    ("manual-fetching", lambda ctx: detect_manual_fetching(ctx["files"])),
    ("serial-awaits", lambda ctx: detect_serial_awaits(ctx["files"])),
    ("fetch-all", lambda ctx: detect_fetch_all(ctx["files"])),
    ("hover-prefetch", lambda ctx: detect_hover_prefetch(ctx["files"])),
    ("detail-for-label", lambda ctx: detect_detail_for_label(ctx["files"])),
    ("unbounded-parallel", lambda ctx: detect_unbounded_parallel(ctx["files"])),
    ("hand-rolled-poll", lambda ctx: detect_hand_rolled_polls(ctx["files"])),
    ("key-hygiene", lambda ctx: detect_key_hygiene(ctx["files"])),
    ("platform-notes", lambda ctx: detect_platform_notes(ctx["files"])),
]

SEVERITY_ORDER = {"P0": 0, "P1": 1, "P2": 2, "P3": 3, "info": 4}
CONFIDENCE_ORDER = {"high": 0, "medium": 1, "low": 2}


def run(root: str, excludes: List[str], min_confidence: str) -> Dict[str, object]:
    files = collect_files(root, excludes)
    graph = build_import_graph(files, root)
    hooks = data_hooks(files)
    ctx = {"files": files, "graph": graph, "hooks": hooks}
    findings: List[Finding] = []
    for _name, detector in DETECTORS:
        findings.extend(detector(ctx))
    threshold = CONFIDENCE_ORDER[min_confidence]
    findings = [f for f in findings if CONFIDENCE_ORDER[f.confidence] <= threshold]
    findings.sort(key=lambda f: (SEVERITY_ORDER[f.severity], CONFIDENCE_ORDER[f.confidence], f.file, f.line))
    query_sites = sum(len(QUERY_HOOK_RE.findall(f.text)) for f in files)
    return {
        "root": os.path.abspath(root),
        "files_scanned": len(files),
        "query_hook_call_sites": query_sites,
        "data_hooks_found": sorted(hooks),
        "libraries": detect_libraries(root),
        "counts": {
            sev: sum(1 for f in findings if f.severity == sev) for sev in SEVERITY_ORDER
        },
        "findings": [asdict(f) for f in findings],
    }


def to_markdown(result: Dict[str, object]) -> str:
    lines: List[str] = []
    lines.append(f"# Static scan: {result['root']}")
    lines.append("")
    lines.append(f"Files scanned: {result['files_scanned']}. Query hook call sites: {result['query_hook_call_sites']}.")
    libs = result.get("libraries") or []
    lines.append("Libraries: " + (", ".join(libs) if libs else "none detected from package.json") + ".")
    counts = result["counts"]
    lines.append("Candidates: " + ", ".join(f"{k} {v}" for k, v in counts.items()) + ".")
    lines.append("")
    lines.append("Every row is a candidate until the code behind it is read. Confidence says how often this detector is right, not how bad the issue is.")
    lines.append("")
    lines.append("| # | Severity | Confidence | Detector | Where | Summary |")
    lines.append("| --- | --- | --- | --- | --- | --- |")
    for i, f in enumerate(result["findings"], 1):
        lines.append(f"| {i} | {f['severity']} | {f['confidence']} | {f['id']} | `{f['file']}:{f['line']}` | {f['title']} |")
    lines.append("")
    lines.append("## Details")
    lines.append("")
    for i, f in enumerate(result["findings"], 1):
        lines.append(f"### {i}. {f['title']}")
        lines.append("")
        lines.append(f"`{f['file']}:{f['line']}` · {f['severity']} · confidence {f['confidence']} · catalog `{f['catalog']}`")
        lines.append("")
        if f["snippet"]:
            lines.append("```")
            lines.append(f["snippet"])
            lines.append("```")
        lines.append(f"Why it matters: {f['why']}")
        lines.append("")
        lines.append(f"Fix: {f['fix']}")
        lines.append("")
        lines.append(f"Verify first: {f['verify']}")
        extra = {k: v for k, v in (f.get("extra") or {}).items() if v}
        if extra:
            lines.append("")
            lines.append("Extra: " + json.dumps(extra, ensure_ascii=False))
        lines.append("")
    return "\n".join(lines)


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("root", help="source tree to scan (an app folder, or a repo root)")
    parser.add_argument("--format", choices=("json", "md"), default="md")
    parser.add_argument("--out", help="write here instead of stdout")
    parser.add_argument("--exclude", action="append", default=[], help="extra dir name or glob to skip (repeatable)")
    parser.add_argument("--min-confidence", choices=("low", "medium", "high"), default="low",
                        help="drop candidates below this confidence")
    args = parser.parse_args(argv)
    if not os.path.isdir(args.root):
        print(f"not a directory: {args.root}", file=sys.stderr)
        return 2
    result = run(args.root, args.exclude, args.min_confidence)
    output = json.dumps(result, indent=2) if args.format == "json" else to_markdown(result)
    if args.out:
        with open(args.out, "w", encoding="utf-8") as handle:
            handle.write(output + "\n")
    else:
        sys.stdout.write(output + "\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
