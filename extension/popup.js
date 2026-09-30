const TASKS_KEY = 'jobImportTasksV2';
const EDITABLE_FIELDS = [
  'company_name',
  'title',
  'location',
  'employment_type',
  'job_category',
  'published_at',
  'description',
  'source_website',
];
const STATUS_LABELS = {
  queued: '排队中',
  extracting: '解析中',
  review: '待核对',
  confirm_queued: '待写入',
  confirming: '添加中',
  added: '已添加',
  failed: '失败',
};
const MAX_TELEMETRY_TOKENS = 100000000;
const MAX_TELEMETRY_DURATION_MS = 7 * 24 * 60 * 60 * 1000;
const MAX_ATTEMPTS = 1000;

const $ = (id) => document.getElementById(id);
const PREVIEW_MODE = new URLSearchParams(location.search).has('preview') || !globalThis.chrome?.runtime?.id;
let tasks = [];
let currentTaskId = null;
let autosaveTimer = null;

// MV3 弹窗里原生 window.confirm 可能因失焦直接关掉整个弹窗；改用 DOM 对话框。
function domConfirm(message) {
  const dialog = $('confirm-dialog');
  const text = $('confirm-dialog-text');
  const accept = $('confirm-accept');
  const cancel = $('confirm-cancel');
  return new Promise((resolve) => {
    const cleanup = () => {
      accept.removeEventListener('click', onAccept);
      cancel.removeEventListener('click', onCancel);
      dialog.removeEventListener('cancel', onCancelEvent);
    };
    const settle = (value) => { cleanup(); dialog.close(); resolve(value); };
    const onAccept = () => settle(true);
    const onCancel = () => settle(false);
    const onCancelEvent = (event) => { event.preventDefault(); settle(false); };
    accept.addEventListener('click', onAccept);
    cancel.addEventListener('click', onCancel);
    dialog.addEventListener('cancel', onCancelEvent);
    text.textContent = message;
    dialog.showModal();
    accept.focus();
  });
}

function showMessage(text, kind = '') {
  const element = $('review-view')?.hidden === false ? $('review-status') : $('status');
  element.textContent = text;
  element.className = `status${kind ? ` ${kind}` : ''}`;
}

function setBusy(button, busy, busyText) {
  if (!button.dataset.label) button.dataset.label = button.textContent;
  button.disabled = busy;
  button.setAttribute('aria-busy', String(busy));
  button.textContent = busy ? busyText : button.dataset.label;
}

function extractPage() {
  const crop = (value, limit) => String(value || '').slice(0, limit);
  return {
    url: location.href,
    title: crop(document.title, 500),
    selected_text: crop(getSelection(), 20000),
    text: crop(document.body?.innerText, 60000),
    metadata: {
      description: crop(document.querySelector('meta[name="description"]')?.content, 1000),
      site_name: crop(document.querySelector('meta[property="og:site_name"]')?.content, 1000),
      canonical: crop(document.querySelector('link[rel="canonical"]')?.href, 1000),
    },
  };
}

async function sendCommand(message) {
  const response = PREVIEW_MODE
    ? await window.PreviewAPI.command(message)
    : await chrome.runtime.sendMessage(message);
  if (!response) throw new Error('扩展后台没有响应，请在扩展管理页重新加载。');
  if (response.error) throw new Error(response.error);
  return response;
}

function subscribeToTasks(listener) {
  if (PREVIEW_MODE) {
    window.PreviewAPI.subscribe(listener);
    return;
  }
  chrome.storage.onChanged.addListener((changes, areaName) => {
    if (areaName === 'local' && changes[TASKS_KEY]) listener();
  });
}

async function captureCurrentPage() {
  if (PREVIEW_MODE) {
    return window.PreviewAPI.capture();
  }
  const [tab] = await chrome.tabs.query({ active: true, currentWindow: true });
  if (!tab?.id || !tab.url || !['http:', 'https:'].includes(new URL(tab.url).protocol)) {
    throw new Error('只支持普通的 http/https 网页。');
  }
  const [injected] = await chrome.scripting.executeScript({
    target: { tabId: tab.id },
    func: extractPage,
  });
  if (!injected?.result) throw new Error('未能读取当前页面。');
  return injected.result;
}

function taskIsEligible(task) {
  return task.status === 'review' && (!task.duplicates?.length || task.allowDuplicate);
}

function taskTitle(task) {
  return task.summary?.title || task.draft?.title || task.sourceTitle || '未命名职位页';
}

function taskCompany(task) {
  return task.summary?.companyName || task.draft?.company_name || '等待提取公司信息';
}

function taskHost(task) {
  try {
    return new URL(task.sourceUrl).hostname;
  } catch (error) {
    return '未知来源';
  }
}

function taskTime(task) {
  return new Intl.DateTimeFormat('zh-CN', {
    hour: '2-digit',
    minute: '2-digit',
    hour12: false,
  }).format(new Date(task.updatedAt || task.createdAt));
}

function parseNonNegativeInt(value, max = Number.MAX_SAFE_INTEGER) {
  if (value === null || value === undefined || value === '') return null;
  if (typeof value === 'boolean') return null;
  const normalized = typeof value === 'number'
    ? value
    : (typeof value === 'string' && /^\d+$/.test(value.trim()) ? Number(value) : NaN);
  return Number.isSafeInteger(normalized) && normalized >= 0 && normalized <= max
    ? normalized
    : null;
}

function taskTimestamp(value) {
  if (value === null || value === undefined || value === '' || typeof value === 'boolean') return null;
  const normalized = typeof value === 'number' ? value : Number(value);
  return Number.isFinite(normalized) && normalized >= 0 ? normalized : null;
}

function taskAttemptCount(task) {
  const explicit = parseNonNegativeInt(task.attemptCount, MAX_ATTEMPTS);
  return explicit;
}

function taskDurationMs(task, now = Date.now()) {
  const started = taskTimestamp(task.extractStartedAt);
  if (started !== null) {
    const finished = taskTimestamp(task.extractFinishedAt);
    if (finished !== null && finished >= started) return Math.max(0, finished - started);
    if (task.status === 'extracting') return Math.max(0, now - started);
    return null;
  }
  return parseNonNegativeInt(task.telemetry?.duration_ms, MAX_TELEMETRY_DURATION_MS);
}

function formatTaskDuration(task, now = Date.now()) {
  const duration = taskDurationMs(task, now);
  if (duration !== null) return formatDuration(duration);
  if (!Object.hasOwn(task, 'extractStartedAt')) return '未记录';
  return task.extractStartedAt === null || task.extractStartedAt === undefined ? '未开始' : '未记录';
}

function formatAttempts(task) {
  const attempts = taskAttemptCount(task);
  return attempts === null ? '未记录' : `${attempts} 次`;
}

function telemetryNote(task) {
  const calls = parseNonNegativeInt(task.telemetry?.provider_calls, 1000);
  const details = [];
  const attempts = taskAttemptCount(task);
  if (attempts !== null && attempts > 1) details.push('本次请求，不含前序尝试');
  if (task.telemetry?.usage_complete === false) details.push('用量不完整');
  if (calls !== null && calls > 1) details.push(`内部调用 ${calls} 次`);
  return details.length ? `（${details.join('，')}）` : '';
}

function formatDuration(milliseconds) {
  if (milliseconds === null || milliseconds === undefined) return '未开始';
  const totalSeconds = Math.floor(milliseconds / 1000);
  if (totalSeconds < 1) return `${Math.max(0, Math.round(milliseconds))}毫秒`;
  if (totalSeconds < 60) return `${totalSeconds}秒`;
  const minutes = Math.floor(totalSeconds / 60);
  const seconds = totalSeconds % 60;
  if (minutes < 60) return `${minutes}分${seconds}秒`;
  const hours = Math.floor(minutes / 60);
  return `${hours}小时${minutes % 60}分`;
}

function formatToken(value) {
  const normalized = parseNonNegativeInt(value, MAX_TELEMETRY_TOKENS);
  return normalized === null ? '未返回' : String(normalized);
}

function taskModel(task) {
  const raw = task.telemetry?.model || task.modelSnapshot;
  const configId = parseNonNegativeInt(raw?.config_id ?? task.modelConfigId);
  const displayName = typeof raw?.display_name === 'string' ? raw.display_name.trim() : '';
  const provider = typeof raw?.provider === 'string' ? raw.provider.trim() : '';
  const modelName = typeof raw?.model === 'string' ? raw.model.trim() : '';
  const providerModel = provider && modelName ? `${provider} / ${modelName}` : (provider || modelName);
  if (displayName && providerModel) return `${displayName}（${providerModel}）`;
  if (displayName) return displayName;
  if (providerModel) return providerModel;
  if (configId) return `配置 #${configId}`;
  return '后端自动选择';
}

function renderReviewTelemetry(task) {
  if (!task) return;
  const status = STATUS_LABELS[task.status] || task.status || '未知';
  $('review-stage').textContent = status;
  $('review-attempts').textContent = formatAttempts(task);
  $('review-model').textContent = taskModel(task);
  $('review-duration').textContent = formatTaskDuration(task);
  $('review-duration').dataset.reviewDuration = task.id;
  $('review-prompt-tokens').textContent = formatToken(task.telemetry?.prompt_tokens);
  $('review-completion-tokens').textContent = formatToken(task.telemetry?.completion_tokens);
  $('review-usage-note').textContent = telemetryNote(task);
}

function appendTextElement(parent, tag, className, text) {
  const element = document.createElement(tag);
  element.className = className;
  element.textContent = text;
  parent.append(element);
  return element;
}

function makeAction(label, className, handler) {
  const button = document.createElement('button');
  button.type = 'button';
  button.className = className;
  button.textContent = label;
  button.addEventListener('click', handler);
  return button;
}

function createTaskCard(task, index) {
  const card = document.createElement('article');
  card.className = 'task-card';
  card.dataset.status = task.status;

  const selector = document.createElement('input');
  selector.type = 'checkbox';
  selector.className = 'task-select';
  selector.checked = Boolean(task.selected);
  selector.disabled = !taskIsEligible(task);
  selector.setAttribute('aria-label', `选择 ${taskTitle(task)}`);
  selector.addEventListener('change', async () => {
    try {
      await sendCommand({ type: 'task:patch', taskId: task.id, patch: { selected: selector.checked } });
      await refreshTasks();
    } catch (error) {
      showMessage(error.message, 'error');
    }
  });
  card.append(selector);

  const main = document.createElement('div');
  main.className = 'task-main';
  const open = document.createElement('button');
  open.type = 'button';
  open.className = 'task-open';
  open.disabled = task.status !== 'review';
  open.addEventListener('click', () => openReview(task.id));
  appendTextElement(open, 'span', 'task-kicker', `TASK ${String(index + 1).padStart(2, '0')}`);
  appendTextElement(open, 'span', 'task-title', taskTitle(task));
  appendTextElement(open, 'span', 'task-company', taskCompany(task));
  main.append(open);
  card.append(main);

  appendTextElement(card, 'span', 'task-state', STATUS_LABELS[task.status] || task.status);

  const footer = document.createElement('div');
  footer.className = 'task-footer';
  appendTextElement(footer, 'span', 'task-meta', `${taskHost(task)} · ${taskTime(task)}`);
  const actions = document.createElement('div');
  actions.className = 'task-actions';
  if (task.status === 'review') {
    actions.append(makeAction('核对', 'text-button', () => openReview(task.id)));
  }
  if (task.status === 'failed' && task.errorCode !== 'capture_expired') {
    actions.append(makeAction('重试', 'text-button', () => retryTask(task.id)));
  }
  actions.append(makeAction('移除', 'text-button danger-text', () => removeTask(task.id)));
  footer.append(actions);
  card.append(footer);

  const duplicateMessage = task.duplicates?.length && !task.allowDuplicate
    ? `发现 ${task.duplicates.length} 个重复候选，核对后才能选择。`
    : null;
  if (task.error || duplicateMessage) {
    appendTextElement(card, 'p', 'task-error', task.error || duplicateMessage);
  }
  if (task.note) {
    appendTextElement(card, 'p', 'task-note', task.note);
  }
  const telemetry = document.createElement('div');
  telemetry.className = 'task-telemetry';
  appendTextElement(telemetry, 'span', 'task-telemetry-item', `阶段：${STATUS_LABELS[task.status] || task.status || '未知'}`);
  const duration = appendTextElement(
    telemetry,
    'span',
    'task-telemetry-item task-duration',
    `耗时：${formatTaskDuration(task)}`,
  );
  duration.dataset.taskDuration = task.id;
  appendTextElement(telemetry, 'span', 'task-telemetry-item', `尝试：${formatAttempts(task)}`);
  const model = appendTextElement(telemetry, 'span', 'task-telemetry-item task-model', `模型：${taskModel(task)}`);
  model.title = taskModel(task);
  appendTextElement(
    telemetry,
    'span',
    'task-telemetry-item task-tokens',
    `本次解析请求用量：输入 ${formatToken(task.telemetry?.prompt_tokens)} · 输出 ${formatToken(task.telemetry?.completion_tokens)}${telemetryNote(task)}`,
  );
  card.append(telemetry);
  return card;
}

function renderTasks() {
  const list = $('task-list');
  list.replaceChildren();
  const ordered = [...tasks].sort((left, right) => right.createdAt - left.createdAt);
  ordered.forEach((task, index) => list.append(createTaskCard(task, index)));

  $('queue-count').textContent = String(tasks.length);
  $('empty-queue').hidden = tasks.length > 0;
  const reviewTasks = tasks.filter((task) => task.status === 'review');
  const eligible = reviewTasks.filter(taskIsEligible);
  const selected = eligible.filter((task) => task.selected);
  $('batch-toolbar').hidden = reviewTasks.length === 0;
  $('batch-confirm').disabled = selected.length === 0;
  $('batch-confirm').textContent = selected.length ? `批量添加 ${selected.length} 项` : '批量添加';
  $('select-all').checked = eligible.length > 0 && selected.length === eligible.length;
  $('select-all').indeterminate = selected.length > 0 && selected.length < eligible.length;
  $('select-all').disabled = eligible.length === 0;
}

async function refreshTasks() {
  const response = await sendCommand({ type: 'tasks:list' });
  tasks = response.tasks || [];
  renderTasks();
  if (currentTaskId) {
    const current = tasks.find((task) => task.id === currentTaskId);
    if (!current || current.status !== 'review') showQueue();
    else renderReviewTelemetry(current);
  }
}

function tickDurations() {
  const now = Date.now();
  for (const element of document.querySelectorAll('[data-task-duration]')) {
    const task = tasks.find((item) => item.id === element.dataset.taskDuration);
    if (task) element.textContent = `耗时：${formatTaskDuration(task, now)}`;
  }
  const reviewDuration = $('review-duration');
  if (currentTaskId && reviewDuration?.dataset.reviewDuration === currentTaskId) {
    const task = tasks.find((item) => item.id === currentTaskId);
    if (task) reviewDuration.textContent = formatTaskDuration(task, now);
  }
}

function renderWarnings(warnings) {
  const list = $('warning-list');
  list.replaceChildren();
  for (const warning of warnings || []) appendTextElement(list, 'li', '', warning);
  $('warnings').hidden = list.childElementCount === 0;
}

function renderDuplicates(duplicates) {
  const list = $('duplicate-list');
  list.replaceChildren();
  for (const candidate of duplicates || []) {
    const reasons = (candidate.reasons || []).map((reason) => (
      reason === 'source_url' ? '同一链接' : '公司与职位名相同'
    )).join('、');
    appendTextElement(
      list,
      'li',
      '',
      `${candidate.title} · ${candidate.company_name}${reasons ? `（${reasons}）` : ''}`,
    );
  }
  $('duplicates').hidden = list.childElementCount === 0;
}

function openReview(taskId) {
  const task = tasks.find((item) => item.id === taskId);
  if (!task?.draft || task.status !== 'review') return;
  currentTaskId = taskId;
  for (const field of EDITABLE_FIELDS) $(field).value = task.draft[field] ?? '';
  $('source_url').value = task.draft.source_url;
  $('application_status').value = task.applicationStatus || '收藏';
  $('notes').value = task.notes || '';
  $('allow_duplicate').checked = Boolean(task.allowDuplicate);
  $('review-source').textContent = task.sourceTitle;
  const confidence = $('confidence');
  confidence.hidden = typeof task.draft.confidence !== 'number';
  if (!confidence.hidden) confidence.textContent = `置信度 ${Math.round(task.draft.confidence * 100)}%`;
  renderWarnings(task.draft.warnings);
  renderDuplicates(task.duplicates);
  renderReviewTelemetry(task);
  $('review-status').textContent = task.error || '';
  $('review-status').className = `status${task.error ? ' error' : ''}`;
  $('queue-view').hidden = true;
  $('review-view').hidden = false;
  $('company_name').focus();
}

function showQueue() {
  clearTimeout(autosaveTimer);
  currentTaskId = null;
  $('review-view').hidden = true;
  $('queue-view').hidden = false;
  renderTasks();
}

function currentChanges() {
  const task = tasks.find((item) => item.id === currentTaskId);
  if (!task?.draft) return null;
  const draft = { ...task.draft };
  for (const field of EDITABLE_FIELDS) {
    const value = $(field).value.trim();
    draft[field] = ['company_name', 'title'].includes(field) ? value : (value || null);
  }
  return {
    draft,
    applicationStatus: $('application_status').value,
    notes: $('notes').value.trim(),
    allowDuplicate: $('allow_duplicate').checked,
  };
}

async function persistCurrentTask(silent = false) {
  const changes = currentChanges();
  if (!currentTaskId || !changes) return;
  try {
    await sendCommand({ type: 'task:patch', taskId: currentTaskId, patch: changes });
  } catch (error) {
    if (!silent) showMessage(`自动保存失败：${error.message}`, 'error');
  }
}

function scheduleAutosave() {
  clearTimeout(autosaveTimer);
  autosaveTimer = setTimeout(() => void persistCurrentTask(), 350);
}

async function retryTask(taskId) {
  try {
    const response = await sendCommand({ type: 'task:retry', taskId });
    if (!response.ok || response.task?.status !== 'queued') {
      throw new Error(response.task?.error || '任务无法重试。');
    }
    showMessage('任务已重新加入解析队列。', 'success');
    await refreshTasks();
  } catch (error) {
    showMessage(error.message, 'error');
  }
}

async function removeTask(taskId) {
  if (!(await domConfirm('从任务夹移除这一项？'))) return;
  try {
    await sendCommand({ type: 'task:remove', taskId });
    if (currentTaskId === taskId) showQueue();
    await refreshTasks();
    showMessage('任务已移除。', 'success');
  } catch (error) {
    showMessage(error.message, 'error');
  }
}

$('extract').addEventListener('click', async () => {
  const button = $('extract');
  setBusy(button, true, '正在读取当前页…');
  showMessage('正在读取当前页面并创建后台任务…');
  try {
    const capture = await captureCurrentPage();
    const response = await sendCommand({ type: 'task:create', capture });
    if (!response.ok) throw new Error(response.error || '未能创建解析任务。');
    await refreshTasks();
    showMessage('已加入队列。现在可以关闭弹窗或继续收集其他职位。', 'success');
  } catch (error) {
    showMessage(error.message || '未能创建解析任务。', 'error');
  } finally {
    setBusy(button, false, '');
    renderTasks();
  }
});

async function updateDraftSaveRetry() {
  try {
    const response = await sendCommand({ type: 'draft:pending' });
    const count = Math.max(0, Number(response.count) || 0);
    $('retry-draft-save').hidden = count === 0;
    $('retry-draft-save').textContent = `重试保存待处理捕获（${count}）`;
    $('discard-draft-save').hidden = count === 0;
    $('discard-draft-save').textContent = `放弃全部待保存捕获（${count}）`;
  } catch {
    $('retry-draft-save').hidden = true;
    $('discard-draft-save').hidden = true;
  }
}

$('save-draft').addEventListener('click', async () => {
  const button = $('save-draft');
  setBusy(button, true, '正在保存…');
  showMessage('正在保存页面来源和正文，不会调用 AI…');
  try {
    const capture = await captureCurrentPage();
    const response = await sendCommand({ type: 'draft:save', capture });
    if (!response.ok || !response.draft?.id) throw new Error('草稿未能保存。');
    showMessage(`已保存到待整理（草稿 #${response.draft.id}）。`, 'success');
  } catch (error) {
    showMessage(`草稿未保存：${error.message}。若有待保存捕获，可在下方重试或全部放弃。`, 'error');
  } finally {
    setBusy(button, false, '');
    await updateDraftSaveRetry();
  }
});

$('retry-draft-save').addEventListener('click', async () => {
  const button = $('retry-draft-save');
  setBusy(button, true, '正在重试…');
  showMessage('正在按原幂等键重试待保存捕获…');
  try {
    const response = await sendCommand({ type: 'draft:retry' });
    if (!response.ok || !response.draft?.id) throw new Error('草稿未能保存。');
    showMessage(`已保存到待整理（草稿 #${response.draft.id}）。`, 'success');
  } catch (error) {
    showMessage(`仍未保存：${error.message}`, 'error');
  } finally {
    setBusy(button, false, '');
    await updateDraftSaveRetry();
  }
});

$('discard-draft-save').addEventListener('click', async () => {
  const button = $('discard-draft-save');
  try {
    const pending = await sendCommand({ type: 'draft:pending' });
    const count = Math.max(0, Number(pending.count) || 0);
    if (!count) {
      await updateDraftSaveRetry();
      return;
    }
    if (!(await domConfirm(`放弃全部 ${count} 份尚未成功保存的页面捕获？放弃后无法重试。`))) return;
    setBusy(button, true, '正在放弃…');
    const response = await sendCommand({ type: 'draft:discard' });
    showMessage(`已放弃 ${response.discardedCount} 份待保存捕获。`, 'success');
  } catch (error) {
    showMessage(`无法放弃待保存捕获：${error.message}`, 'error');
  } finally {
    setBusy(button, false, '');
    await updateDraftSaveRetry();
  }
});

$('select-all').addEventListener('change', async (event) => {
  const eligible = tasks.filter(taskIsEligible);
  try {
    await Promise.all(eligible.map((task) => sendCommand({
      type: 'task:patch',
      taskId: task.id,
      patch: { selected: event.target.checked },
    })));
    await refreshTasks();
  } catch (error) {
    showMessage(error.message, 'error');
  }
});

$('batch-confirm').addEventListener('click', async () => {
  const selectedIds = tasks
    .filter((task) => task.status === 'review' && task.selected)
    .map((task) => task.id);
  if (!selectedIds.length) return;
  if (!(await domConfirm(`确认把选中的 ${selectedIds.length} 个职位添加到本地 Tracker？`))) return;
  const button = $('batch-confirm');
  setBusy(button, true, '正在排队…');
  try {
    const response = await sendCommand({ type: 'tasks:confirm_batch', taskIds: selectedIds });
    await refreshTasks();
    const blocked = response.blockedIds?.length || 0;
    showMessage(
      blocked
        ? `已开始添加 ${response.acceptedIds.length} 项；${blocked} 项因重复候选被保留。`
        : `已开始批量添加 ${response.acceptedIds.length} 项。`,
      blocked ? '' : 'success',
    );
  } catch (error) {
    showMessage(error.message, 'error');
  } finally {
    setBusy(button, false, '');
    renderTasks();
  }
});

$('back-to-queue').addEventListener('click', async () => {
  clearTimeout(autosaveTimer);
  await persistCurrentTask();
  showQueue();
  await refreshTasks();
});

$('job-form').addEventListener('input', scheduleAutosave);
$('job-form').addEventListener('change', scheduleAutosave);

$('job-form').addEventListener('submit', async (event) => {
  event.preventDefault();
  const task = tasks.find((item) => item.id === currentTaskId);
  const changes = currentChanges();
  if (!task || !changes) return;
  if (task.duplicates?.length && !changes.allowDuplicate) {
    showMessage('检测到重复职位；请核对并勾选“仍然添加”。', 'error');
    $('allow_duplicate').focus();
    return;
  }
  clearTimeout(autosaveTimer);
  const button = $('confirm');
  setBusy(button, true, '正在排队…');
  try {
    const response = await sendCommand({
      type: 'task:confirm',
      taskId: currentTaskId,
      changes,
    });
    if (!response.ok && response.code === 'duplicate_requires_review') {
      showMessage('本地库发现新的重复候选，请再次核对。', 'error');
      await refreshTasks();
      return;
    }
    showQueue();
    await refreshTasks();
    showMessage('已加入添加队列，关闭弹窗也会继续。', 'success');
  } catch (error) {
    showMessage(error.message, 'error');
  } finally {
    setBusy(button, false, '');
  }
});

$('remove-current').addEventListener('click', () => {
  if (currentTaskId) void removeTask(currentTaskId);
});

subscribeToTasks(() => void refreshTasks().catch((error) => showMessage(error.message, 'error')));

const PREFERRED_MODEL_KEY = 'preferredModelId';

async function loadModelPicker() {
  const select = $('model-select');
  if (!select) return;
  if (PREVIEW_MODE) {
    select.disabled = true;
    return;
  }
  try {
    const [response, stored] = await Promise.all([
      sendCommand({ type: 'models:list' }),
      chrome.storage.local.get(PREFERRED_MODEL_KEY),
    ]);
    const models = Array.isArray(response.models) ? response.models : [];
    const esc = (value) => String(value ?? '').replace(/[&<>"']/g, (char) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[char]));
    select.innerHTML =
      '<option value="">自动 · 跟随设置路由</option>' +
      models.map((model) => `<option value="${model.id}">${esc(model.label)}</option>`).join('');
    const preferred = Number(stored?.[PREFERRED_MODEL_KEY]) || null;
    if (preferred && models.some((model) => model.id === preferred)) {
      select.value = String(preferred);
    }
    select.addEventListener('change', () => {
      const value = select.value ? Number(select.value) : null;
      void chrome.storage.local.set({ [PREFERRED_MODEL_KEY]: value });
      showMessage(value ? '后续新任务将优先使用所选模型。' : '已恢复自动选择模型。', 'success');
    });
  } catch {
    select.disabled = true;
    select.innerHTML = '<option value="">模型列表暂不可用</option>';
  }
}

loadModelPicker();

if (PREVIEW_MODE) document.body.classList.add('preview-mode');

setInterval(tickDurations, 1000);

refreshTasks().catch((error) => {
  showMessage(error.message || '无法读取任务队列，请重新加载扩展。', 'error');
});
updateDraftSaveRetry();
