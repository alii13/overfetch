#!/usr/bin/env python3
"""Map what a request audit covered, and what it did not.

A static scan reads every module. A capture only sees the code that loaded
while it ran: the routes someone visited, the dialogs and tabs someone opened,
the flags that were on. This script lists the whole surface (routes, lazy
boundaries, gated data sites, endpoints, data modules no route reaches) and,
given one or more captures, marks what was exercised. The difference is the
part of the app the audit can only speak to from code.

Usage:
    coverage_map.py ROOT [--har FILE ...] [--visited FILE ...]
                         [--format md|json] [--out FILE] [--exclude GLOB ...]

--har      HAR or JSON/JSONL request log (repeatable). Dev-server captures
           (Vite, webpack-dev-server) list each module they load, which gives
           exact module coverage; production captures give chunk names.
--visited  Text file of URLs visited during the captures, one per line
           (capture.mjs writes OUT.har.visited.txt). Lines starting with # are
           ignored. HAR page titles and Referer headers are used too.

Standard library only. Exit code 0 when the map was built, 2 on a usage error.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
from collections import deque
from dataclasses import asdict, dataclass, field
from typing import Dict, Iterable, List, Optional, Set, Tuple
from urllib.parse import parse_qs, unquote, urlsplit

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from har_analyze import STATIC_EXT_RE, load_capture, template_path  # noqa: E402
from static_scan import (  # noqa: E402
    FETCH_CALL_RE,
    IMPORT_RE,
    POLL_OPTION_RE,
    QUERY_HOOK_RE,
    SOURCE_EXTENSIONS,
    SourceFile,
    collect_files,
    make_resolver,
    module_id,
)

DYNAMIC_IMPORT_RE = re.compile(r"\bimport\(\s*['\"]([^'\"]+)['\"]\s*\)")
MUTATION_RE = re.compile(r"\buse(?:\w+)?Mutation\s*\(")
TRANSPORT_CALL_RE = re.compile(r"\b(?:api|client|sdk|transport|service|http)\w*\.\w+\s*\(")
INTERVAL_RE = re.compile(r"\bsetInterval\s*\(")
VIEW_EXTENSIONS = (".tsx", ".jsx", ".vue", ".svelte")
PREFETCH_RE = re.compile(r"\b(?:prefetchQuery|prefetchInfiniteQuery|ensureQueryData|preload)\s*\(")
SEG = r"(?:[\w.~-]+|\$\{[^}`]+\}|\{[\w-]+\}|:[\w-]+)"
ENDPOINT_RE = re.compile(
    r"(['\"`])((?:\$\{[^}`]+\})?/" + SEG + r"(?:/" + SEG + r")+)/?(?:\?[^'\"`]*)?\1"
)
API_HINT_RE = re.compile(r"/(?:api|v\d+|graphql|rest|rpc)(?:/|$)", re.I)
CALL_HINT_RE = re.compile(r"(?:fetch|axios|ky|got|url\s*:|\.(?:get|post|put|patch|delete|request))\s*\(?\s*$")
GATE_PATTERNS = {
    "flag": re.compile(
        r"\b(?:useFlag|useFeatureFlag|useFlags|useFeature|isFeatureEnabled|useGate|useExperiment|"
        r"useLDClient|useVariation|featureFlags?\.|flags\.\w+|growthbook|statsig|unleash)\b", re.I),
    "platform": re.compile(
        r"\b(?:isDesktop|isElectron|isTauri|__TAURI\w*|isMobile|isNative|Platform\.OS|navigator\.userAgent)\b"),
    "permission": re.compile(
        r"\b(?:hasPermission|usePermission|usePermissions|useAbility|canAccess|isAdmin|hasRole|"
        r"permissions\.\w+|role\s*===?)\b"),
    "env": re.compile(r"\b(?:import\.meta\.env|process\.env)\.\w+"),
}
ENTRY_NAMES = ("main", "index", "app", "entry-client", "entry")
CODE_ROUTER_RE = re.compile(
    r"\b(?:createBrowserRouter|createHashRouter|createMemoryRouter|createRoutesFromElements|"
    r"useRoutes|new\s+VueRouter|createRouter)\s*\(|<Route\b")
PATH_LITERAL_RE = re.compile(r"""\bpath\s*[:=]\s*\{?\s*['"]([^'"]*)['"]""")
HASH_TAIL_RE = re.compile(r"^[A-Za-z0-9_-]{6,12}$")


@dataclass
class Module:
    rel: str
    mid: str
    query_sites: int = 0
    polls: int = 0
    fetches: int = 0
    mutations: int = 0
    prefetches: int = 0
    transport_calls: int = 0
    view_fetches: int = 0
    endpoints: List[str] = field(default_factory=list)
    gates: Dict[str, int] = field(default_factory=dict)  # kind -> first line

    @property
    def has_data(self) -> bool:
        return bool(self.query_sites or self.polls or self.fetches or self.mutations or self.prefetches
                    or self.transport_calls or self.endpoints)

    @property
    def reads(self) -> int:
        """Places that start a read from UI or orchestration code. A transport module's own fetch
        calls are its implementation, so only fetches in view files count."""
        return self.query_sites + self.prefetches + self.transport_calls + self.view_fetches


@dataclass
class Route:
    pattern: str
    file: str
    kind: str  # file | code | entry
    is_index: bool = False
    is_layout: bool = False
    eager: Set[str] = field(default_factory=set)
    lazy: Set[str] = field(default_factory=set)
    boundaries: List[Tuple[str, int, str]] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Inventory
# ---------------------------------------------------------------------------

def normalize_endpoint(path: str) -> str:
    path = re.sub(r"^\$\{[^}]+\}", "", path)
    path = re.sub(r"\$\{[^}]+\}|\{[\w-]+\}|:[\w-]+", "{}", path)
    return path.split("?", 1)[0].rstrip("/") or "/"


def scan_module(f: SourceFile) -> Module:
    m = Module(rel=f.rel, mid=module_id(f.rel))
    m.query_sites = len(QUERY_HOOK_RE.findall(f.text))
    m.polls = len(POLL_OPTION_RE.findall(f.text))
    m.fetches = len(FETCH_CALL_RE.findall(f.text))
    m.mutations = len(MUTATION_RE.findall(f.text))
    m.prefetches = len(PREFETCH_RE.findall(f.text))
    m.transport_calls = len(TRANSPORT_CALL_RE.findall(f.text))
    if f.rel.endswith(VIEW_EXTENSIONS):
        m.view_fetches = m.fetches
    if INTERVAL_RE.search(f.text) and (m.fetches or m.transport_calls or "refetch" in f.text or "invalidate" in f.text):
        m.polls += len(INTERVAL_RE.findall(f.text))
    seen: Set[str] = set()
    for match in ENDPOINT_RE.finditer(f.text):
        path = match.group(2)
        if STATIC_EXT_RE.search(path.split("?")[0]):
            continue
        before = f.raw[max(0, match.start() - 40):match.start()]
        if not (API_HINT_RE.search(normalize_endpoint(path)) or CALL_HINT_RE.search(before)):
            continue
        norm = normalize_endpoint(path)
        if path.startswith("${") and not API_HINT_RE.search(norm):
            continue  # `${base}/commit/${sha}` is a link to another site, not a read
        if norm.count("/") >= 2 and norm not in seen:
            seen.add(norm)
            m.endpoints.append(norm)
    if m.has_data:
        for kind, regex in GATE_PATTERNS.items():
            hit = regex.search(f.text)
            if hit:
                m.gates[kind] = f.line_of(hit.start())
    return m


def package_deps(root: str) -> Set[str]:
    deps: Set[str] = set()
    probe = os.path.abspath(root)
    for _ in range(4):
        pkg = os.path.join(probe, "package.json")
        if os.path.isfile(pkg):
            try:
                with open(pkg, encoding="utf-8") as handle:
                    data = json.load(handle)
                for key in ("dependencies", "devDependencies", "peerDependencies"):
                    deps.update((data.get(key) or {}).keys())
            except (OSError, ValueError):
                pass
            break
        probe = os.path.dirname(probe)
    return deps


LAYOUT_STEMS = ("__root", "+layout", "layout", "_layout", "root", "_app")


def file_route(rel: str, deps: Set[str]) -> Optional[Tuple[str, bool]]:
    """(pattern, is_index) for a file-based route, or None."""
    parts = rel.replace(os.sep, "/").split("/")
    name, ext = os.path.splitext(parts[-1])
    if ext not in SOURCE_EXTENSIONS:
        return None
    segments: List[str] = []
    if "routes" in parts:
        sub = parts[parts.index("routes") + 1:]
        if name in ("+layout", "+page", "+server", "+error"):
            if name == "+server":
                return None
            sub = sub[:-1]
            tokens = [t for d in sub for t in [d]]
            is_index = name == "+page"
        else:
            if name.startswith("-") or name.endswith((".test", ".spec")) or any(d.startswith("-") for d in sub[:-1]):
                return None
            stem = name[:-5] if name.endswith(".lazy") else name
            tokens = [t for d in sub[:-1] for t in d.split(".")] + stem.split(".")
            is_index = tokens[-1] == "index"
            tokens = [t for t in tokens if t not in ("index", "route")]
        for t in tokens:
            if t == "__root" or (t.startswith("_") and not t.startswith("__")) or (t.startswith("(") and t.endswith(")")):
                continue
            segments.append(t.rstrip("_"))
        return "/" + "/".join(s for s in segments if s), is_index
    if "next" in deps and "app" in parts and name == "page":
        sub = parts[parts.index("app") + 1:-1]
        segments = [d for d in sub if not (d.startswith("(") or d.startswith("@"))]
        return "/" + "/".join(segments), True
    if "next" in deps and "pages" in parts:
        sub = parts[parts.index("pages") + 1:]
        if sub[0] == "api" or name in ("_app", "_document", "_error"):
            return None
        tokens = sub[:-1] + ([] if name == "index" else [name])
        return "/" + "/".join(tokens), name == "index"
    return None


def static_edges(files: List[SourceFile], resolve) -> Dict[str, Set[str]]:
    edges: Dict[str, Set[str]] = {}
    for f in files:
        out = edges.setdefault(module_id(f.rel), set())
        for match in IMPORT_RE.finditer(f.text):
            if re.match(r"(?:import|export)\s+type\b", match.group(0)):
                continue
            target = resolve(f.rel, match.group(2))
            if target:
                out.add(target)
    return edges


def dynamic_edges(files: List[SourceFile], resolve) -> Dict[str, List[Tuple[int, str]]]:
    edges: Dict[str, List[Tuple[int, str]]] = {}
    for f in files:
        for match in DYNAMIC_IMPORT_RE.finditer(f.text):
            target = resolve(f.rel, match.group(1))
            if target:
                edges.setdefault(module_id(f.rel), []).append((f.line_of(match.start()), target))
    return edges


def closure(start: Iterable[str], edges: Dict[str, Set[str]]) -> Set[str]:
    seen = set(start)
    queue = deque(seen)
    while queue:
        for nxt in edges.get(queue.popleft(), ()):
            if nxt not in seen:
                seen.add(nxt)
                queue.append(nxt)
    return seen


def code_routes(files: List[SourceFile], resolve) -> List[Tuple[str, str, Optional[str]]]:
    """(pattern, router file, target module) for code-defined routes; target may be unknown."""
    out = []
    for f in files:
        if not CODE_ROUTER_RE.search(f.text):
            continue
        local_imports = {}
        for match in IMPORT_RE.finditer(f.text):
            target = resolve(f.rel, match.group(2))
            if target:
                for name in re.findall(r"\b([A-Z]\w*)\b", match.group(1) or ""):
                    local_imports[name] = target
        hits = list(PATH_LITERAL_RE.finditer(f.text))
        for i, hit in enumerate(hits):
            window = f.text[hit.end():hits[i + 1].start() if i + 1 < len(hits) else hit.end() + 400][:400]
            target = None
            lazy = DYNAMIC_IMPORT_RE.search(window)
            if lazy:
                target = resolve(f.rel, lazy.group(1))
            else:
                comp = re.search(r"(?:element\s*[:=]\s*\{?\s*<|component\s*[:=]\s*\{?\s*)([A-Z]\w*)", window)
                if comp:
                    target = local_imports.get(comp.group(1))
            pattern = hit.group(1) if hit.group(1).startswith("/") else "/" + hit.group(1)
            out.append((pattern, f.rel, target))
    return out


def build_inventory(root: str, excludes: List[str]) -> dict:
    files = collect_files(root, excludes)
    resolve = make_resolver(files, root)
    by_mid = {module_id(f.rel): f for f in files}
    modules = {module_id(f.rel): scan_module(f) for f in files}
    s_edges = static_edges(files, resolve)
    d_edges = dynamic_edges(files, resolve)
    all_edges = {k: set(v) for k, v in s_edges.items()}
    for src, targets in d_edges.items():
        all_edges.setdefault(src, set()).update(t for _, t in targets)
    deps = package_deps(root)

    routes: List[Route] = []
    for f in files:
        fr = file_route(f.rel, deps)
        if fr:
            stem = os.path.splitext(os.path.basename(f.rel))[0]
            last = stem.split(".")[-1]
            layout = stem in LAYOUT_STEMS or (last.startswith("_") and not last.startswith("__"))
            routes.append(Route(pattern=fr[0], file=f.rel, kind="file", is_index=fr[1], is_layout=layout))
    # A file route with child files (`a.b.tsx` beside `a.b.c.tsx`) also renders for its children.
    stems = [os.path.splitext(os.path.basename(r.file))[0] for r in routes]
    for route, stem in zip(routes, stems):
        if not route.is_index and any(other.startswith(stem + ".") for other in stems if other != stem):
            route.is_layout = True
    for pattern, router_file, target in code_routes(files, resolve):
        # Code-defined paths match exactly: nested relative paths are not joined to their parents.
        routes.append(Route(pattern=pattern, file=by_mid[target].rel if target in by_mid else router_file, kind="code",
                            is_index=not pattern.endswith("*")))
    entries = [f.rel for f in files
               if os.path.splitext(os.path.basename(f.rel))[0] in ENTRY_NAMES
               and f.rel.replace(os.sep, "/").count("/") <= 1 and not any(r.file == f.rel for r in routes)]
    for rel in entries:
        imported_by_other = any(module_id(rel) in closure([module_id(o)], s_edges) for o in entries if o != rel)
        if not imported_by_other:
            routes.append(Route(pattern="(entry)", file=rel, kind="entry"))

    boundary_rows: Dict[Tuple[str, int, str], Set[str]] = {}
    for route in routes:
        start = module_id(route.file)
        route.eager = closure([start], s_edges)
        reach_lazy: Set[str] = set()
        frontier = set(route.eager)
        visited_boundaries: Set[Tuple[str, int, str]] = set()
        while frontier:
            new_targets = []
            for mid in frontier:
                for line, target in d_edges.get(mid, []):
                    key = (by_mid[mid].rel, line, target)
                    if key in visited_boundaries:
                        continue
                    visited_boundaries.add(key)
                    new_targets.append(target)
                    # What the chunk adds beyond what its importer already loads.
                    boundary_rows.setdefault(key, closure([target], s_edges) - closure([mid], s_edges))
            grown = closure(new_targets, s_edges) - route.eager - reach_lazy
            reach_lazy |= grown
            frontier = grown
        route.lazy = reach_lazy
        route.boundaries = sorted(visited_boundaries)

    reached = set()
    for route in routes:
        reached |= route.eager | route.lazy

    return {
        "files": files, "modules": modules, "routes": routes, "boundaries": boundary_rows,
        "reached": reached, "deps": deps, "by_mid": by_mid,
    }


# ---------------------------------------------------------------------------
# Captures
# ---------------------------------------------------------------------------

def url_path(url: str) -> str:
    parts = urlsplit(url.strip())
    if parts.fragment.startswith("/"):
        return parts.fragment.split("?", 1)[0]
    return parts.path or "/"


def load_capture_signals(hars: List[str], visited_files: List[str]) -> dict:
    urls: List[str] = []
    visited: List[str] = []
    for path in hars:
        for req in load_capture(path):
            urls.append(req.url)
            if req.page and re.match(r"https?://", req.page):
                visited.append(req.page)
        try:
            with open(path, encoding="utf-8") as handle:
                text = handle.read().strip()
            data = json.loads(text) if text.startswith("{") else None
        except (OSError, ValueError):
            data = None
        if data and "log" in data:
            for page in data["log"].get("pages", []):
                title = page.get("title", "")
                if re.match(r"https?://", title):
                    visited.append(title)
            for entry in data["log"].get("entries", []):
                for header in entry.get("request", {}).get("headers", []):
                    if header.get("name", "").lower() == "referer":
                        visited.append(header.get("value", ""))
                url = entry.get("request", {}).get("url", "")
                if "/__request-audit__/visit" in url:
                    visited.extend(parse_qs(urlsplit(url).query).get("url", []))
    for path in visited_files:
        with open(path, encoding="utf-8") as handle:
            visited.extend(line.strip() for line in handle if line.strip() and not line.startswith("#"))
    return {"urls": urls, "visited": sorted({url_path(v) for v in visited if v})}


def module_loaded(rel: str, request_paths: List[str], chunk_bases: Set[str]) -> bool:
    """Dev servers request each module by path; production builds name a chunk after its entry module."""
    rel = rel.replace(os.sep, "/")
    if any(p.endswith("/" + rel) or p == rel for p in request_paths):
        return True
    stem = os.path.splitext(os.path.basename(rel))[0].lower()
    if stem in ("index", "page", "route", "+page"):
        stem = os.path.basename(os.path.dirname(rel)).lower() or stem
    for base in chunk_bases:
        if base == stem:
            return True
        for sep in ("-", "."):
            if base.startswith(stem + sep) and HASH_TAIL_RE.match(base[len(stem) + 1:]):
                return True
    return False


def route_matches(pattern: str, path: str, is_index: bool) -> bool:
    p_segs = [s for s in pattern.split("/") if s]
    u_segs = [s for s in path.split("/") if s]
    for i, seg in enumerate(p_segs):
        if seg in ("$", "*") or re.fullmatch(r"\[\[?\.\.\.\w+\]\]?", seg):
            return True
        if i >= len(u_segs):
            return False
        if seg.startswith(("$", ":")) or re.fullmatch(r"\[\w+\]", seg):
            continue
        if seg != u_segs[i]:
            return False
    if is_index or len(p_segs) == len(u_segs):
        return len(p_segs) == len(u_segs)
    return True  # a layout route renders for every descendant path


def endpoint_seen(endpoint: str, templates: Set[str]) -> bool:
    e_segs = [s for s in endpoint.split("/") if s]
    for template in templates:
        t_segs = [s for s in template.split("/") if s]
        if len(t_segs) < len(e_segs):
            continue
        tail = t_segs[len(t_segs) - len(e_segs):]
        if all(e == "{}" or e == t for e, t in zip(e_segs, tail)):
            return True
    return False


# ---------------------------------------------------------------------------
# Report
# ---------------------------------------------------------------------------

def router_label(routes: List[Route], deps: Set[str]) -> str:
    kinds = {r.kind for r in routes}
    if "file" in kinds:
        for dep, label in (("@tanstack/react-router", "TanStack Router file routes"), ("@remix-run/react", "Remix file routes"),
                           ("@sveltejs/kit", "SvelteKit file routes"), ("next", "Next.js file routes"),
                           ("@solidjs/start", "SolidStart file routes")):
            if dep in deps:
                return label
        return "file-based routes"
    if "code" in kinds:
        return "code-defined routes"
    return "no router found; entry files used as the root"


def build_map(root: str, excludes: List[str], hars: List[str], visited_files: List[str]) -> dict:
    inv = build_inventory(root, excludes)
    modules: Dict[str, Module] = inv["modules"]
    routes: List[Route] = inv["routes"]
    by_mid = inv["by_mid"]
    data_mids = {mid for mid, m in modules.items() if m.has_data}

    capture = None
    loaded: Set[str] = set()
    templates: Set[str] = set()
    module_signal = False
    if hars or visited_files:
        signals = load_capture_signals(hars, visited_files)
        request_paths = [urlsplit(u).path for u in signals["urls"]]
        dev_paths = [p for p in request_paths if re.search(r"\.(?:tsx?|jsx|vue|svelte|mjs)$", p)]
        chunk_bases = {os.path.splitext(os.path.basename(p))[0].lower()
                       for p in request_paths if re.search(r"\.m?js$", p)}
        module_signal = bool(dev_paths or chunk_bases)
        loaded = {mid for mid in modules if module_loaded(by_mid[mid].rel, request_paths, chunk_bases)}
        templates = {template_path(p)[0] for p in request_paths
                     if not STATIC_EXT_RE.search(p) and "/@" not in p and "/node_modules/" not in p
                     and "__request-audit__" not in p}
        capture = {
            "files": hars + visited_files,
            "requests": len(signals["urls"]),
            "visited_paths": signals["visited"],
            "module_signal": "dev-server module paths" if dev_paths
                             else ("chunk names" if chunk_bases else "none (no script requests in the capture)"),
        }

    def was_loaded(mid: str) -> Optional[bool]:
        if capture is None or not module_signal:
            return None
        return mid in loaded

    def route_row(r: Route) -> dict:
        data_eager = sorted(by_mid[m].rel for m in r.eager & data_mids)
        data_lazy = sorted(by_mid[m].rel for m in r.lazy & data_mids)
        scope = (r.eager | r.lazy) & data_mids
        gates = sorted({k for m in scope for k in modules[m].gates})
        row = {
            "pattern": r.pattern + (" (layout)" if r.is_layout else ""), "file": r.file, "kind": r.kind,
            "data_modules_eager": data_eager, "data_modules_lazy": data_lazy,
            "reads": sum(modules[m].reads for m in scope),
            "polls": sum(modules[m].polls for m in scope),
            "lazy_boundaries": len(r.boundaries), "gates": gates, "visited": None,
        }
        if capture is not None:
            if r.kind == "entry":
                row["visited"] = capture["requests"] > 0
            else:
                by_url = any(route_matches(r.pattern, path, r.is_index) for path in capture["visited_paths"])
                row["visited"] = by_url or bool(was_loaded(module_id(r.file)))
        return row

    route_rows = sorted((route_row(r) for r in routes), key=lambda row: (-row["reads"], row["pattern"]))

    boundary_rows = []
    for (importer, line, target), reach in sorted(inv["boundaries"].items()):
        scope = reach & data_mids
        row = {
            "loaded_from": f"{importer}:{line}", "target": by_mid[target].rel,
            "data_modules": sorted(by_mid[m].rel for m in scope),
            "reads": sum(modules[m].reads for m in scope),
            "polls": sum(modules[m].polls for m in scope),
            "gates": sorted({k for m in scope for k in modules[m].gates}),
            "loaded": None,
        }
        row["loaded"] = was_loaded(target)
        boundary_rows.append(row)
    boundary_rows.sort(key=lambda row: (-row["reads"], row["target"]))

    gated = sorted(
        ({"module": m.rel, "gates": {k: f"{m.rel}:{v}" for k, v in m.gates.items()}, "reads": m.reads,
          "loaded": was_loaded(m.mid)}
         for m in modules.values() if m.has_data and m.gates and set(m.gates) - {"env"}),
        key=lambda row: (-row["reads"], row["module"]))

    unreached = sorted(
        ({"module": modules[mid].rel, "reads": modules[mid].reads, "polls": modules[mid].polls,
          "fetches": modules[mid].fetches}
         for mid in data_mids - inv["reached"]),
        key=lambda row: (-row["reads"], row["module"]))

    endpoint_refs: Dict[str, List[str]] = {}
    for m in modules.values():
        for e in m.endpoints:
            endpoint_refs.setdefault(e, []).append(m.rel)
    endpoints = sorted(
        ({"endpoint": e, "referenced_in": sorted(refs),
          "seen": endpoint_seen(e, templates) if capture is not None else None}
         for e, refs in endpoint_refs.items()),
        key=lambda row: row["endpoint"])

    data_rows = sorted(
        ({"module": modules[mid].rel, "reads": modules[mid].reads, "polls": modules[mid].polls,
          "loaded": was_loaded(mid)}
         for mid in data_mids),
        key=lambda row: (-row["reads"], row["module"]))

    totals = {
        "modules": len(modules), "data_modules": len(data_mids), "routes": len(routes),
        "lazy_boundaries": len(boundary_rows), "gated_data_modules": len(gated),
        "endpoints": len(endpoints), "unreached_data_modules": len(unreached),
    }
    covered = None
    if capture is not None:
        covered = {
            "routes_visited": sum(1 for r in route_rows if r["visited"]),
            "data_modules_loaded": sum(1 for r in data_rows if r["loaded"]) if module_signal else None,
            "lazy_boundaries_loaded": sum(1 for b in boundary_rows if b["loaded"]) if module_signal else None,
            "gated_modules_loaded": sum(1 for g in gated if g["loaded"]) if module_signal else None,
            "endpoints_seen": sum(1 for e in endpoints if e["seen"]),
        }

    return {
        "root": os.path.abspath(root),
        "router": router_label(routes, inv["deps"]),
        "totals": totals,
        "covered": covered,
        "capture": capture,
        "routes": route_rows,
        "lazy_boundaries": boundary_rows,
        "gated": gated,
        "unreached": unreached,
        "endpoints": endpoints,
        "data_modules": data_rows,
        "plan": capture_plan(route_rows, boundary_rows, gated, capture is not None),
    }


def capture_plan(routes: List[dict], boundaries: List[dict], gated: List[dict], have_capture: bool) -> List[str]:
    todo_routes = [r for r in routes if r["kind"] != "entry" and r["reads"] and not r["visited"]]
    todo_bounds = [b for b in boundaries if b["reads"] and not b["loaded"]]
    todo_gates = [g for g in gated if not g["loaded"]]
    verb = "not yet visited" if have_capture else "to visit"
    plan = []
    if todo_routes:
        plan.append(f"Routes {verb}, most read sites first: " + ", ".join(
            f"{r['pattern']} ({r['reads']})" for r in todo_routes[:12])
            + (f", and {len(todo_routes) - 12} more" if len(todo_routes) > 12 else "") + ".")
    if todo_bounds:
        plan.append("Lazy chunks to trigger (open the dialog, tab or drawer that loads each): " + ", ".join(
            f"{b['target']} from {b['loaded_from']}" for b in todo_bounds[:10])
            + (f", and {len(todo_bounds) - 10} more" if len(todo_bounds) > 10 else "") + ".")
    if todo_gates:
        kinds = sorted({k for g in todo_gates for k in g["gates"]})
        plan.append(f"Gated data sites ({', '.join(kinds)}): capture once with each gate on and off, "
                    "once per platform, and once as a low-permission user. First: "
                    + ", ".join(g["module"] for g in todo_gates[:6]) + ".")
    plan.append("For each journey, cover the states a capture rarely shows: empty and populated (50 to 100 rows), "
                "403, 404 and 500 from a key read, slow network, offline then reconnect, 5 minutes idle and visible, "
                "tab hidden then shown, and persisted cache cold versus warm.")
    return plan


def fraction(n: Optional[int], d: int) -> str:
    if n is None:
        return "not measurable from this capture"
    return f"{n} of {d}" + (f" ({round(100 * n / d)}%)" if d else "")


def md_rows(rows: List[dict], cols: List[Tuple[str, str]], limit: int = 60) -> List[str]:
    if not rows:
        return ["None."]
    out = ["| " + " | ".join(c for _, c in cols) + " |", "| " + " | ".join("---" for _ in cols) + " |"]
    for row in rows[:limit]:
        cells = []
        for key, _ in cols:
            v = row.get(key)
            if isinstance(v, list):
                v = ", ".join(str(x) for x in v[:6]) + (f" (+{len(v) - 6})" if len(v) > 6 else "")
            elif isinstance(v, dict):
                v = ", ".join(f"{k} {x}" for k, x in v.items())
            elif v is None:
                v = "-"
            elif v is True:
                v = "yes"
            elif v is False:
                v = "**no**"
            cells.append(str(v).replace("|", "\\|"))
        out.append("| " + " | ".join(cells) + " |")
    if len(rows) > limit:
        out.append(f"\n{len(rows) - limit} more rows in the JSON output.")
    return out


def to_markdown(result: dict) -> str:
    t = result["totals"]
    lines = [f"# Coverage map: {result['root']}", ""]
    lines.append(
        f"{result['router']}. {t['routes']} routes, {t['data_modules']} of {t['modules']} modules make or define reads, "
        f"{t['lazy_boundaries']} lazy boundaries, {t['gated_data_modules']} gated data modules, "
        f"{t['endpoints']} endpoint paths in source, {t['unreached_data_modules']} data modules no route reaches.")
    lines.append("")
    lines.append("The static scan reads every one of these modules. A capture sees only what loaded while it ran. "
                 "Anything below marked **no** was covered by reading code only.")
    if result["capture"]:
        c, cov = result["capture"], result["covered"]
        lines += ["", "## Capture coverage", "",
                  f"From {len(c['files'])} file(s), {c['requests']} requests. Module signal: {c['module_signal']}. "
                  f"Visited paths: {', '.join(c['visited_paths'][:10]) or 'none recorded'}.", "",
                  "| Surface | Exercised |", "| --- | --- |",
                  f"| Routes | {fraction(cov['routes_visited'], t['routes'])} |",
                  f"| Data modules loaded | {fraction(cov['data_modules_loaded'], t['data_modules'])} |",
                  f"| Lazy boundaries loaded | {fraction(cov['lazy_boundaries_loaded'], t['lazy_boundaries'])} |",
                  f"| Gated data modules loaded | {fraction(cov['gated_modules_loaded'], t['gated_data_modules'])} |",
                  f"| Endpoint paths seen | {fraction(cov['endpoints_seen'], t['endpoints'])} |"]
        if c["module_signal"].startswith("none"):
            lines += ["", "The capture has no script requests, so module and chunk coverage cannot be measured; "
                          "re-export the HAR with static assets, or capture against the dev server."]
    lines += ["", "## Routes", ""]
    lines += md_rows(result["routes"], [("pattern", "Route"), ("file", "File"), ("reads", "Read sites"),
                                         ("polls", "Polls"), ("data_modules_lazy", "Lazy data modules"),
                                         ("lazy_boundaries", "Lazy boundaries"), ("gates", "Gates"), ("visited", "Visited")])
    lines += ["", "## Lazy boundaries", "", "Code behind a dynamic import loads only when something triggers it.", ""]
    lines += md_rows(result["lazy_boundaries"], [("target", "Chunk entry"), ("loaded_from", "Loaded from"),
                                                  ("reads", "Read sites"), ("polls", "Polls"), ("gates", "Gates"),
                                                  ("loaded", "Loaded")])
    lines += ["", "## Gated data modules", "", "Reads that run only under a flag, a platform, or a permission.", ""]
    lines += md_rows(result["gated"], [("module", "Module"), ("gates", "Gate at"), ("reads", "Read sites"),
                                        ("loaded", "Loaded")])
    lines += ["", "## Data modules no route reaches", "",
              "Dead code, or reached through something this map cannot resolve (a registry, a string-built import). "
              "Check before reporting findings in them as live.", ""]
    lines += md_rows(result["unreached"], [("module", "Module"), ("reads", "Read sites"), ("polls", "Polls"),
                                            ("fetches", "Fetch calls")])
    if result["capture"]:
        unseen = [e for e in result["endpoints"] if not e["seen"]]
        lines += ["", "## Endpoint paths never seen in a capture", ""]
        lines += md_rows(unseen, [("endpoint", "Endpoint"), ("referenced_in", "Referenced in")])
    lines += ["", "## Capture plan", ""]
    lines += [f"{i}. {step}" for i, step in enumerate(result["plan"], 1)]
    lines += ["", "Route reachability follows static imports from each route file; layout routes also render for their "
                  "children, and code-defined nested paths are not joined. Endpoint paths are literals found in source; "
                  "paths built at run time or hidden in a generated client may be missing."]
    return "\n".join(lines)


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("root")
    parser.add_argument("--har", action="append", default=[])
    parser.add_argument("--visited", action="append", default=[])
    parser.add_argument("--format", choices=("json", "md"), default="md")
    parser.add_argument("--out")
    parser.add_argument("--exclude", action="append", default=[])
    args = parser.parse_args(argv)
    if not os.path.isdir(args.root):
        print(f"not a directory: {args.root}", file=sys.stderr)
        return 2
    for path in args.har + args.visited:
        if not os.path.isfile(path):
            print(f"not a file: {path}", file=sys.stderr)
            return 2
    result = build_map(args.root, args.exclude, args.har, args.visited)
    output = json.dumps(result, indent=2) if args.format == "json" else to_markdown(result)
    if args.out:
        with open(args.out, "w", encoding="utf-8") as handle:
            handle.write(output + "\n")
    else:
        sys.stdout.write(output + "\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
