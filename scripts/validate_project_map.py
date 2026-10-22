"""Validate the public graph and static artifacts without running the application."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
import re

ROOT = Path(__file__).resolve().parents[1]
PRIVATE = {"local", "private", "data", "backups", "artifacts", "outputs", "output", ".tools", ".agents", ".venv", ".tmp"}


def load(path):
    return json.loads(path.read_text(encoding="utf-8"))


def main():
    graph = load(ROOT / ".ua/knowledge-graph.json")
    errors = []
    ids = {n["id"] for n in graph["nodes"]}
    if len(ids) != len(graph["nodes"]):
        errors.append("duplicate node IDs")
    for node in graph["nodes"]:
        relative = node.get("filePath")
        if relative:
            p = Path(relative)
            target = (ROOT / p).resolve()
            if p.is_absolute() or not target.is_relative_to(ROOT) or PRIVATE.intersection(p.parts) or "paperball" in relative.lower():
                errors.append(f"out-of-scope path: {relative}")
            elif not target.is_file():
                errors.append(f"missing source: {relative}")
            elif node.get("lineRange"):
                start, end = node["lineRange"]
                count = len(target.read_text(encoding="utf-8-sig").splitlines())
                if not 1 <= start <= end <= count:
                    errors.append(f"invalid source range: {node['id']}")
    for edge in graph["edges"]:
        if edge["source"] not in ids or edge["target"] not in ids:
            errors.append("dangling edge")
    assigned = []
    for layer in graph["layers"]:
        assigned.extend(layer["nodeIds"])
    if len(set(assigned)) != len(assigned) or set(assigned) - ids:
        errors.append("duplicate or invalid layer assignment")
    file_ids = {n["id"] for n in graph["nodes"] if n["type"] in {"file", "document", "config", "pipeline"}}
    if file_ids - set(assigned):
        errors.append("unassigned file nodes")
    for step in graph["tour"]:
        if set(step["nodeIds"]) - ids:
            errors.append("invalid tour reference")
    sources = load(ROOT / ".ua/source-manifest.json")
    for rel, digest in sources["files"].items():
        path = ROOT / rel
        if not path.is_file() or hashlib.sha256(path.read_text(encoding="utf-8-sig").encode("utf-8")).hexdigest() != digest:
            errors.append(f"source changed since analysis: {rel}")
    site = ROOT / "docs/map"
    if (site / "knowledge-graph.json").read_text(encoding="utf-8") != (ROOT / ".ua/knowledge-graph.json").read_text(encoding="utf-8"):
        errors.append("published graph differs from canonical graph")
    manifest = load(site / "build-manifest.json")
    for rel, digest in manifest["files"].items():
        path = (site / rel).resolve()
        if not path.is_relative_to(site) or not path.is_file() or hashlib.sha256(path.read_bytes()).hexdigest() != digest:
            errors.append(f"static artifact mismatch: {rel}")
    links = 0
    for rel in ["README.md", "ARCHITECTURE.md", "docs/development.md", "docs/map/README.md", "docs/map/THIRD_PARTY_NOTICES.md"]:
        document = ROOT / rel
        for target in re.findall(r"\[[^\]]+\]\(([^)]+)\)", document.read_text(encoding="utf-8")):
            if "://" in target or target.startswith("#"):
                continue
            links += 1
            if not (document.parent / target.split("#")[0]).exists():
                errors.append(f"broken link in {rel}: {target}")
    if errors:
        raise SystemExit("\n".join(errors))
    print(f"Map validation passed: {len(graph['nodes'])} nodes, {len(graph['edges'])} edges, "
          f"{len(sources['files'])} source fingerprints, {links} local links, {len(manifest['files'])} static files")


if __name__ == "__main__":
    main()
