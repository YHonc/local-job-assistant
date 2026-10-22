"""Build the pinned upstream viewer for the reviewed, public JOB graph only."""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess

ROOT = Path(__file__).resolve().parents[1]
UPSTREAM = "https://github.com/Egonex-AI/Understand-Anything.git"
REVISION = "b8c807c632743ab695ec43cb654a0f1efe216bc8"
CHECKOUT = ROOT / ".tools/understand-anything"
PLUGIN = CHECKOUT / "understand-anything-plugin"
OUTPUT = ROOT / "docs/map"


def run(args, cwd=ROOT, **kwargs):
    return subprocess.run(args, cwd=cwd, check=True, **kwargs)


def assert_upstream_clean():
    paths = run(["git", "diff", "HEAD", "--name-only"], cwd=CHECKOUT,
                capture_output=True, text=True).stdout.splitlines()
    # pnpm 11 adds an empty importer for the dependency-free viewer workspace.
    # Accept only this exact metadata addition, never changed dependency resolutions.
    lock = "understand-anything-plugin/pnpm-lock.yaml"
    if paths == [lock]:
        baseline = run(["git", "show", f"HEAD:{lock}"], cwd=CHECKOUT,
                       capture_output=True, text=True, encoding="utf-8").stdout
        current = (CHECKOUT / lock).read_text(encoding="utf-8")
        if current.replace("\n  packages/viewer: {}\n", "") == baseline:
            return
    if paths:
        raise SystemExit("Upstream tracked files are modified; review before building")


def dependency_notices():
    """Preserve license texts for the dashboard's installed production tree."""
    pending = [PLUGIN / "packages/dashboard"]
    seen = set()
    notices = []
    while pending:
        directory = pending.pop().resolve()
        if directory in seen:
            continue
        seen.add(directory)
        manifest = json.loads((directory / "package.json").read_text(encoding="utf-8"))
        name = manifest["name"]
        texts = []
        for file in sorted(directory.iterdir()):
            if file.is_file() and file.name.lower().startswith(("license", "licence", "copying", "notice")):
                texts.append(file.name + "\n" + file.read_text(encoding="utf-8", errors="replace"))
        if not texts and name.startswith("@understand-anything/"):
            texts = [(CHECKOUT / "LICENSE").read_text(encoding="utf-8")]
        if not texts:
            raise RuntimeError(f"Missing license text for {name}; review before redistribution")
        notices.append((name, manifest.get("version", ""), manifest.get("license", "See license text"), texts))
        deps = {**manifest.get("dependencies", {}), **manifest.get("peerDependencies", {})}
        optional_peers = manifest.get("peerDependenciesMeta", {})
        for dep in deps:
            found = next((parent / "node_modules" / dep for parent in [directory, *directory.parents]
                          if (parent / "node_modules" / dep / "package.json").is_file()), None)
            if found:
                pending.append(found)
            elif not optional_peers.get(dep, {}).get("optional"):
                raise RuntimeError(f"Unresolved dependency {name} -> {dep}")
    chunks = ["Third-party dependency license texts\nGenerated from the pinned dashboard production dependency tree.\n"]
    for name, version, license_name, texts in sorted(notices):
        chunks.append(f"\n{'=' * 72}\n{name}@{version}\nLicense: {license_name}\n" + "\n".join(texts))
    return "\n".join(chunks), len(notices)


def main():
    node, pnpm = shutil.which("node"), shutil.which("pnpm")
    if not node or not pnpm:
        raise SystemExit("Node.js 22+ and pnpm 10+ must be on PATH")
    version = run([node, "--version"], capture_output=True, text=True).stdout.strip()
    if int(version.lstrip("v").split(".")[0]) < 22:
        raise SystemExit("Use Node.js 22+ for the map build")
    if not (ROOT / ".ua/knowledge-graph.json").is_file():
        raise SystemExit("Generate and review .ua/knowledge-graph.json with Understand-Anything first")
    if not CHECKOUT.exists():
        run(["git", "clone", UPSTREAM, str(CHECKOUT)])
        run(["git", "checkout", "--detach", REVISION], cwd=CHECKOUT)
    actual = run(["git", "rev-parse", "HEAD"], cwd=CHECKOUT, capture_output=True, text=True).stdout.strip()
    if actual != REVISION:
        raise SystemExit("Existing tool checkout is a different revision; preserve it and review first")
    assert_upstream_clean()
    run([pnpm, "install", "--frozen-lockfile"], cwd=PLUGIN)
    assert_upstream_clean()
    run([pnpm, "--filter", "@understand-anything/core", "build"], cwd=PLUGIN)
    dashboard = PLUGIN / "packages/dashboard"
    run([pnpm, "exec", "tsc", "-b"], cwd=dashboard)
    run([pnpm, "exec", "vite", "build", "--config", "vite.config.demo.ts", "--base", "./"], cwd=dashboard)
    OUTPUT.mkdir(parents=True, exist_ok=True)
    notices, notice_count = dependency_notices()
    shutil.copytree(dashboard / "dist", OUTPUT, dirs_exist_ok=True)
    for name in ("knowledge-graph.json", "meta.json", "config.json"):
        shutil.copy2(ROOT / ".ua" / name, OUTPUT / name)
    # Optional overlays are deliberately empty: avoid 404 noise without inventing domain analysis.
    for name in ("domain-graph.json", "diff-overlay.json"):
        (OUTPUT / name).write_text("null\n", encoding="utf-8")
    shutil.copy2(CHECKOUT / "LICENSE", OUTPUT / "UNDERSTAND-ANYTHING-LICENSE.txt")
    (OUTPUT / "DEPENDENCY-LICENSES.txt").write_text(notices, encoding="utf-8", newline="\n")
    (OUTPUT / "THIRD_PARTY_NOTICES.md").write_text(
        "# 第三方许可与来源\n\n"
        "JOB 平台保留根目录 MIT License。交互地图复用 Understand-Anything 官方 dashboard，"
        "采用上游静态展示配置，未修改其业务代码。\n\n"
        f"- 上游：{UPSTREAM.removesuffix('.git')}\n- 版本：2.9.7\n- 固定修订：`{REVISION}`\n"
        "- [Understand-Anything 完整 MIT 声明](UNDERSTAND-ANYTHING-LICENSE.txt)\n"
        f"- [浏览器端依赖版权与许可全文](DEPENDENCY-LICENSES.txt)：{notice_count} 个已安装生产依赖条目，"
        "包括查看器工作区包；为保留完整声明可能涵盖未被最终 bundle 使用的间接依赖。\n\n"
        "重新分发本页面时，请一并保留这些声明。依赖各自适用其许可，项目 MIT 不覆盖其原始许可。\n",
        encoding="utf-8", newline="\n")
    (ROOT / "docs/.nojekyll").write_text("", encoding="utf-8")
    (ROOT / "docs/index.html").write_text(
        '<!doctype html><html lang="zh-CN"><meta charset="utf-8"><title>JOB 平台项目地图</title>'
        '<meta http-equiv="refresh" content="0;url=./map/"><a href="./map/">打开项目地图</a></html>\n',
        encoding="utf-8", newline="\n")
    # Hash exactly the portable bytes Git will publish (also on Windows).
    for file in OUTPUT.rglob("*"):
        if file.is_file() and file.suffix in {".js", ".css", ".html", ".json", ".txt", ".md", ".svg"}:
            content = file.read_text(encoding="utf-8-sig")
            file.write_text(content, encoding="utf-8", newline="\n")
    manifest = {p.relative_to(OUTPUT).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
                for p in sorted(OUTPUT.rglob("*")) if p.is_file() and p.name != "build-manifest.json"}
    (OUTPUT / "build-manifest.json").write_text(json.dumps({
        "upstreamRevision": REVISION, "upstreamVersion": "2.9.7", "mode": "official-static-demo",
        "sourcePreview": "local-dashboard-only", "files": manifest,
    }, ensure_ascii=False, indent=2) + "\n", encoding="utf-8", newline="\n")
    print(f"Built map: {len(manifest)} files; {notice_count} dependency license entries")


if __name__ == "__main__":
    main()
