"""Publication guard tests use only synthetic files in an isolated Git repo."""

import os
from pathlib import Path
import subprocess
import sys


CHECKER = Path(__file__).resolve().parents[1] / "scripts" / "check_publication.py"


def git(repo, *args):
    return subprocess.run(["git", *args], cwd=repo, check=True, capture_output=True,
                          env={**os.environ, "GIT_CONFIG_NOSYSTEM": "1"})


def setup_repo(tmp_path):
    git(tmp_path, "init")
    git(tmp_path, "config", "user.name", "Synthetic Test")
    git(tmp_path, "config", "user.email", "test@example.invalid")
    git(tmp_path, "config", "commit.gpgsign", "false")
    return tmp_path


def check(repo, *args, input=None):
    return subprocess.run([sys.executable, str(CHECKER), *args], cwd=repo,
                          input=input, capture_output=True, text=True)


def test_guard_rejects_private_markdown_and_database(tmp_path):
    repo = setup_repo(tmp_path)
    (repo / "README.md").write_text("Public description", encoding="utf-8")
    git(repo, "add", ".")
    assert check(repo, "--staged").returncode == 0
    (repo / "prompt.md").write_text("Synthetic private prompt", encoding="utf-8")
    (repo / "jobs.db").write_bytes(b"Synthetic database")
    git(repo, "add", ".")
    result = check(repo, "--staged")
    assert result.returncode == 1
    assert "prompt.md" in result.stderr and "jobs.db" in result.stderr


def test_guard_rejects_secret_without_echoing_value(tmp_path):
    repo = setup_repo(tmp_path)
    fake_token = "ghp_" + "a" * 36
    (repo / "README.md").write_text(fake_token, encoding="utf-8")
    git(repo, "add", ".")
    result = check(repo, "--staged")
    assert result.returncode == 1
    assert "GitHub token" in result.stderr
    assert fake_token not in result.stdout + result.stderr


def test_guard_checks_deleted_private_files_in_push_history(tmp_path):
    repo = setup_repo(tmp_path)
    (repo / "README.md").write_text("Public", encoding="utf-8")
    (repo / "prompt.md").write_text("Synthetic private prompt", encoding="utf-8")
    git(repo, "add", ".")
    git(repo, "commit", "-m", "Synthetic private history")
    git(repo, "rm", "prompt.md")
    git(repo, "commit", "-m", "Remove private file")
    assert check(repo, "--staged").returncode == 0
    sha = git(repo, "rev-parse", "HEAD").stdout.decode().strip()
    result = check(repo, "--pre-push", input=f"refs/heads/main {sha} refs/heads/main {'0' * 40}\n")
    assert result.returncode == 1
    assert "prompt.md" in result.stderr


def test_guard_allows_reviewed_map_but_rejects_private_payloads(tmp_path):
    repo = setup_repo(tmp_path)
    graph = repo / ".ua" / "knowledge-graph.json"
    graph.parent.mkdir()
    graph.write_text('{"nodes": []}', encoding="utf-8")
    asset = repo / "docs/map/assets/index-example.js"
    asset.parent.mkdir(parents=True)
    asset.write_text("console.log('synthetic map');", encoding="utf-8")
    git(repo, "add", ".")
    assert check(repo, "--staged").returncode == 0
    for rel in [".ua/tmp/private.json", "docs/map/mail.json", "artifacts/example.py", "scripts/import_paperball_campus.py"]:
        file = repo / rel
        file.parent.mkdir(parents=True, exist_ok=True)
        file.write_text("synthetic", encoding="utf-8")
    git(repo, "add", ".")
    result = check(repo, "--staged")
    assert result.returncode == 1
    assert all(name in result.stderr for name in ["private.json", "mail.json", "example.py", "import_paperball_campus.py"])


def test_guard_still_scans_allowed_map_content_for_secrets(tmp_path):
    repo = setup_repo(tmp_path)
    graph = repo / ".ua/knowledge-graph.json"
    graph.parent.mkdir()
    token = "ghp_" + "b" * 36
    graph.write_text('{"summary": "' + token + '"}', encoding="utf-8")
    git(repo, "add", ".")
    result = check(repo, "--staged")
    assert result.returncode == 1 and "GitHub token" in result.stderr
    assert token not in result.stdout + result.stderr
