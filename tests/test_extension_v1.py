import json
import re
import subprocess
from pathlib import Path

ROOT = Path(__file__).parents[1]
EXT = ROOT / "extension"

# background.js is kept in readable formatting; assertions below use
# whitespace-tolerant regexes so reformatting cannot silently weaken them.
def source(name):
    return (EXT / name).read_text(encoding="utf-8")

def test_manifest_permissions_are_minimal():
    manifest = json.loads(source("manifest.json"))
    assert manifest["manifest_version"] == 3
    assert set(manifest["permissions"]) == {"activeTab", "scripting", "storage", "alarms"}
    assert set(manifest["host_permissions"]) == {"http://127.0.0.1:8765/*", "http://localhost:8765/*"}
    assert manifest["background"] == {"service_worker": "background.js"}
    assert "content_scripts" not in manifest and "<all_urls>" not in json.dumps(manifest)


def test_manifest_declares_icons_and_minimum_chrome_version():
    manifest = json.loads(source("manifest.json"))
    # structuredClone (popup preview) requires Chrome 98.
    assert int(manifest["minimum_chrome_version"]) >= 98
    icons = manifest["icons"]
    assert set(icons.values()) == set(manifest["action"]["default_icon"].values())
    for path in set(icons.values()):
        file = EXT / path
        assert file.is_file() and file.stat().st_size > 0


def test_popup_uses_dom_confirm_dialog_instead_of_native_confirm():
    popup, html, css = source("popup.js"), source("popup.html"), source("popup.css")
    # 原生 confirm 在 MV3 弹窗中可能因失焦关闭整个弹窗。
    assert "confirm(" not in popup
    assert "function domConfirm" in popup
    assert "await domConfirm(" in popup
    assert 'id="confirm-dialog"' in html and 'id="confirm-accept"' in html
    assert "#confirm-dialog" in css


def test_preview_simulation_is_isolated_in_preview_js():
    popup, html, preview = source("popup.js"), source("popup.html"), source("preview.js")
    assert '<script src="preview.js"></script>' in html
    assert html.index("preview.js") < html.index("popup.js")
    assert "window.PreviewAPI" in preview and "window.PreviewAPI" in popup
    for fixture in ("previewDraft", "previewTasks", "previewCommand", "previewFinish"):
        assert fixture not in popup and fixture in preview
    # 预览代码不得触碰扩展 API 或持久敏感存储。
    for forbidden in ("chrome.", "document.cookie", "localStorage"):
        assert forbidden not in preview

def test_capture_is_click_gated_and_session_scoped():
    popup, bg = source("popup.js"), source("background.js")
    assert re.search(r"\$\(['\"]extract['\"]\)\.addEventListener\(['\"]click['\"]", popup)
    assert "captureCurrentPage" in popup and "chrome.scripting.executeScript" in popup
    assert re.search(r"type:\s*['\"]task:create['\"],\s*capture", popup)
    assert "chrome.runtime.onMessage.addListener" in bg
    assert "chrome.storage.local" in bg and "chrome.storage.session" in bg
    assert re.search(r"setCapture\(\s*task\.id\s*,\s*capture\s*\)", bg) and "deleteCapture" in bg

def test_extract_contract_preserves_model_config_and_retry_does_not_rotate():
    bg = source("background.js")
    assert re.search(r"model_config_id\s*:\s*task\.modelConfigId", bg)
    assert "data.model_config_id" in bg and "current.modelConfigId" in bg
    claim = bg[bg.index("async function claimExtract"):bg.index("async function finishExtract")]
    assert "if (!task.modelConfigId)" in claim and "modelCursor++" in claim

def test_parallelism_is_clamped_and_claims_are_atomic():
    bg = source("background.js")
    assert re.search(r"Math\.max\(\s*1\s*,\s*Math\.min\(\s*8\s*,", bg)
    assert "mutationQueue" in bg
    assert re.search(r"status\s*===\s*'queued'", bg)
    assert re.search(r"extractionRunning\s*<\s*parallelLimit", bg)
    assert "confirmRunning" in bg
    assert re.search(r"request\(\s*'/api/imports/job/confirm'", bg)

def test_confirm_success_guard_removes_task_and_capture_failures_keep_review():
    bg = source("background.js")
    assert re.search(r"status\s*!==\s*'confirming'", bg) and "tasks.splice" in bg
    assert re.search(r"if\s*\(removed\)\s*await\s+deleteCapture", bg)
    assert re.search(r"status\s*=\s*'review'", bg)
    assert re.search(r"error\.status\s*===\s*409", bg) and "candidates" in bg

def test_unavailable_routing_falls_back_to_one_and_backend_default():
    bg = source("background.js")
    assert re.search(r"routing\s*=\s*\{\s*max_parallel_calls:\s*1", bg)
    assert re.search(r"default_config_id:\s*null", bg)
    # A paused/deleted default must not be pinned onto new tasks; the backend
    # then falls back to another enabled configuration.
    assert re.search(
        r"enabledDefault\s*=\s*routing\.default_config_id\s*&&\s*models\.some", bg
    )
    assert "task.modelConfigId = enabledDefault" in bg

def test_extraction_failover_rotates_models_and_exhausts_gracefully():
    """解析失败自动切换其他启用模型，全部失败才标记终态。"""
    bg, popup, css = source("background.js"), source("popup.js"), source("popup.css")
    assert "FAILOVER_ERROR_CODES" in bg and "function shouldFailover" in bg
    for code in ("llm_auth_failed", "llm_rate_limited", "llm_timeout", "llm_invalid_output"):
        assert f"'{code}'" in bg
    # 与模型无关的失败不触发切换。
    assert "'llm_not_configured'" not in bg.split("FAILOVER_ERROR_CODES")[1].split("]")[0]

    claim = bg[bg.index("async function claimExtract"):bg.index("async function finishExtract")]
    assert "triedModelIds" in claim and "untried" in claim
    assert "all_models_failed" in claim

    fail = bg[bg.index("async function failExtract"):bg.index("async function finishConfirm")]
    assert "shouldFailover(failure.code)" in fail
    assert "current.status = 'queued'" in fail
    assert "已自动尝试" in fail

    retry = bg[bg.index("async function retryTask"):bg.index("async function queueConfirm")]
    assert "current.triedModelIds = []" in retry

    # popup 以中性提示展示切换过程，与红色错误区分。
    assert "'task-note'" in popup and ".task-note" in css
    # note 必须落在卡片的第 2-3 网格列，否则会被挤进 20px 复选框列变成竖排。
    grid_rule = re.search(r"\.task-footer,[^{]+\{[^}]*grid-column[^}]*\}", css)
    assert grid_rule and "task-note" in grid_rule.group(0)


def test_restart_recovery_and_popup_natural_disappearance():
    bg, popup = source("background.js"), source("popup.js")
    assert "extraction_interrupted" in bg and "confirmation_interrupted" in bg
    assert re.search(r"status\s*===\s*'extracting'", bg) and re.search(r"status\s*===\s*'confirming'", bg)
    assert "showQueue()" in popup and "chrome.storage.onChanged" in popup

def test_static_privacy_and_node_syntax_checks():
    combined = "\n".join(source(name) for name in ("popup.html", "popup.css", "popup.js", "preview.js", "background.js"))
    for forbidden in ("document.cookie", "localStorage", "sessionStorage", "chrome.history", "<all_urls>"):
        assert forbidden not in combined
    assert '<script src="popup.js"></script>' in source("popup.html")
    for name in ("background.js", "popup.js", "preview.js"):
        result = subprocess.run(["node", "--check", str(EXT / name)], capture_output=True, text=True)
        assert result.returncode == 0, result.stderr

def test_popup_model_selection_pins_extraction_model():
    popup, bg, html, css = (source(n) for n in ("popup.js", "background.js", "popup.html", "popup.css"))
    assert 'id="model-select"' in html and "model-picker" in css
    assert re.search(r"type:\s*['\"]models:list['\"]", popup)
    assert "preferredModelId" in popup and "chrome.storage.local.set" in popup
    assert re.search(r"case\s+'models:list'", bg) and "display_name" in bg
    assert "readPreferredModelId" in bg and "PREFERRED_MODEL_KEY" in bg
    claim = bg[bg.index("async function claimExtract"):bg.index("async function finishExtract")]
    assert "pinnedModelId" in claim and "if (usablePinned)" in claim
    assert "usablePinned = pinnedModelId && !tried.includes(pinnedModelId)" in claim
    assert re.search(r"models\.some\(\s*\(model\)\s*=>\s*model\.id\s*===\s*preferredId", bg)
    assert "changes[PREFERRED_MODEL_KEY]" in bg


def test_popup_exposes_and_persists_job_category_and_publication_date_fields():
    popup, html, preview = (source(name) for name in ("popup.js", "popup.html", "preview.js"))
    assert 'id="job_category"' in html
    assert "'job_category'" in popup
    assert "job_category: '产品'" in preview
    assert 'id="published_at"' in html
    assert "'published_at'" in popup
    assert "published_at: '2026-08-29'" in preview
