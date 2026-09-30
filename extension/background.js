// Local Job Assistant - MV3 service worker.
//
// Owns the import queue: task summaries live in chrome.storage.local (capped,
// TTL-pruned), raw page captures only in chrome.storage.session and are deleted
// as soon as extraction finishes. All task mutations go through mutateTasks()
// so storage writes are strictly serialized; the two workers run on top:
//   - extraction: up to routing.max_parallel_calls concurrent /extract calls,
//     assigning models round-robin when auto-select is enabled; when a model
//     fails with a provider-side error the task automatically fails over to
//     the next untried enabled model and only fails once all are exhausted;
//   - confirmation: strictly serial /confirm writes to avoid SQLite conflicts.

const API_BASE = 'http://127.0.0.1:8765';
const TASKS_KEY = 'jobImportTasksV2';
const CAPTURE_PREFIX = 'jobImportCapture:';
const PENDING_DRAFT_SAVE_KEY = 'pendingImportDraftSaveV1';
const QUEUE_ALARM = 'job-import-queue';
const MAX_TASKS = 20;
const TASK_TTL_MS = 7 * 24 * 60 * 60 * 1000;
const EXTRACT_TIMEOUT_MS = 90000;
const CONFIRM_TIMEOUT_MS = 30000;
const MAX_TELEMETRY_TOKENS = 100000000;
const MAX_TELEMETRY_DURATION_MS = 7 * 24 * 60 * 60 * 1000;
const MAX_TELEMETRY_PROVIDER_CALLS = 1000;
const MAX_ATTEMPTS = 1000;
const MAX_PENDING_DRAFT_SAVES = 20;
const ERROR_MESSAGES = {
  llm_not_configured: '模型尚未配置，请先打开本地设置页保存并测试模型。',
  llm_auth_failed: '模型服务认证失败，请在本地设置页重新保存有效的 API Key。',
  llm_rate_limited: '模型服务触发限流，请稍后重试。',
  llm_connection_failed: '无法连接模型服务，请检查 API Base 与网络。',
  llm_timeout: '模型服务响应超时，请稍后重试或增加超时时间。',
};
// 这些失败换一个模型可能就好：认证/限流/超时/坏输出/连接问题，以及
// 服务端 5xx。本地服务不可达、配置缺失或请求校验错误与模型无关，
// 换模型无意义，不触发自动切换。
const FAILOVER_ERROR_CODES = new Set([
  'llm_auth_failed',
  'llm_model_not_found',
  'llm_rate_limited',
  'llm_request_rejected',
  'llm_connection_failed',
  'llm_timeout',
  'llm_invalid_output',
  'llm_provider_error',
  'llm_no_available_model',
  'request_timeout',
]);

function shouldFailover(code) {
  return FAILOVER_ERROR_CODES.has(code) || /^http_5/.test(code || '');
}

let mutationQueue = Promise.resolve();
let draftSaveMutationQueue = Promise.resolve();
let running = false;
let confirmRunning = false;
let extractionRunning = 0;
let modelCursor = 0;
let routing = { max_parallel_calls: 1, auto_select_enabled: true, default_config_id: null };
const PREFERRED_MODEL_KEY = 'preferredModelId';

function finiteNonNegativeInt(value, max = Number.MAX_SAFE_INTEGER) {
  if (value === null || value === undefined || value === '') return null;
  if (typeof value === 'boolean') return null;
  const normalized = typeof value === 'number'
    ? value
    : (typeof value === 'string' && /^\d+$/.test(value.trim()) ? Number(value) : NaN);
  return Number.isSafeInteger(normalized) && normalized >= 0 && normalized <= max
    ? normalized
    : null;
}

function positiveConfigId(value) {
  const normalized = finiteNonNegativeInt(value, Number.MAX_SAFE_INTEGER);
  return normalized && normalized > 0 ? normalized : null;
}

function safeModelSnapshot(model, fallbackId = null) {
  if (!model || typeof model !== 'object') return null;
  const configId = positiveConfigId(model.config_id ?? model.id ?? fallbackId);
  if (!configId) return null;
  const displayName = typeof model.display_name === 'string' ? model.display_name.slice(0, 200) : '';
  const provider = typeof model.provider === 'string' ? model.provider.slice(0, 100) : '';
  const modelName = typeof model.model === 'string' ? model.model.slice(0, 200) : '';
  return {
    config_id: configId,
    display_name: displayName,
    provider,
    model: modelName,
  };
}

function normalizeAttemptCount(value) {
  return finiteNonNegativeInt(value, MAX_ATTEMPTS) ?? 0;
}

function normalizeTelemetry(raw, fallbackModel, fallbackDurationMs = null) {
  if (!raw || typeof raw !== 'object') return null;
  const model = safeModelSnapshot(raw.model, fallbackModel?.config_id || fallbackModel?.id || null)
    || safeModelSnapshot(fallbackModel);
  if (!model) return null;
  const measuredDuration = finiteNonNegativeInt(fallbackDurationMs, MAX_TELEMETRY_DURATION_MS);
  const duration = finiteNonNegativeInt(raw.duration_ms, MAX_TELEMETRY_DURATION_MS) ?? measuredDuration;
  return {
    model,
    prompt_tokens: finiteNonNegativeInt(raw.prompt_tokens, MAX_TELEMETRY_TOKENS),
    completion_tokens: finiteNonNegativeInt(raw.completion_tokens, MAX_TELEMETRY_TOKENS),
    duration_ms: duration,
    provider_calls: finiteNonNegativeInt(raw.provider_calls, MAX_TELEMETRY_PROVIDER_CALLS),
    usage_complete: typeof raw.usage_complete === 'boolean' ? raw.usage_complete : null,
  };
}

async function readPreferredModelId() {
  try {
    const data = await chrome.storage.local.get(PREFERRED_MODEL_KEY);
    return Number(data?.[PREFERRED_MODEL_KEY]) || null;
  } catch {
    return null;
  }
}

class ApiError extends Error {
  constructor(status, data) {
    super(`HTTP ${status}`);
    this.name = 'ApiError';
    this.status = status;
    this.data = data;
  }
}

const captureKey = (id) => CAPTURE_PREFIX + id;
const publicTask = (task) => ({ ...task });

function pruneTasks(tasks) {
  const cutoff = Date.now() - TASK_TTL_MS;
  return tasks
    .filter((task) => Number(task.updatedAt || task.createdAt) >= cutoff)
    .slice(0, MAX_TASKS);
}

async function readTasks() {
  const stored = await chrome.storage.local.get(TASKS_KEY);
  return Array.isArray(stored[TASKS_KEY]) ? stored[TASKS_KEY] : [];
}

async function writeTasks(tasks) {
  await chrome.storage.local.set({ [TASKS_KEY]: pruneTasks(tasks) });
}

// Serialize every mutation of the task list: fn receives the freshly pruned
// list and may mutate/throw; the list is persisted afterwards either way.
function mutateTasks(fn) {
  const operation = mutationQueue.then(async () => {
    const tasks = pruneTasks(await readTasks());
    const result = await fn(tasks);
    await writeTasks(tasks);
    return result;
  });
  mutationQueue = operation.catch(() => {});
  return operation;
}

// Serialize draft queue reads/writes and their network requests. This queue is
// separate from task mutations so the legacy import-task ordering is unchanged.
function mutatePendingDraftSaves(fn) {
  const operation = draftSaveMutationQueue.then(fn);
  draftSaveMutationQueue = operation.catch(() => {});
  return operation;
}

const getCapture = async (id) => (await chrome.storage.session.get(captureKey(id)))[captureKey(id)] || null;
const setCapture = (id, capture) => chrome.storage.session.set({ [captureKey(id)]: capture });
const deleteCapture = (id) => chrome.storage.session.remove(captureKey(id));

// Map any failure to a safe, user-facing code + message; never leaks response bodies.
function safeError(error) {
  if (error?.name === 'AbortError') {
    return { code: 'request_timeout', message: '本地服务响应超时，请稍后重试。' };
  }
  if (error instanceof ApiError) {
    const code = error.data?.detail?.code;
    if (code && ERROR_MESSAGES[code]) return { code, message: ERROR_MESSAGES[code] };
    if (code === 'duplicate_job') {
      return { code, message: '本地库中仍有相似职位，请核对后确认。' };
    }
    return { code: `http_${error.status}`, message: `本地服务返回错误（${error.status}）。` };
  }
  if (error instanceof TypeError) {
    return { code: 'local_api_unavailable', message: '无法连接本地 API，请确认 8765 服务已经启动。' };
  }
  return { code: 'task_failed', message: '任务执行失败，请重试。' };
}

async function request(path, body, timeout) {
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), timeout);
  try {
    const hasBody = body !== undefined;
    const response = await fetch(API_BASE + path, {
      method: hasBody ? 'POST' : 'GET',
      headers: hasBody ? { 'Content-Type': 'application/json' } : {},
      ...(hasBody ? { body: JSON.stringify(body) } : {}),
      signal: controller.signal,
    });
    const data = await response.json().catch(() => ({}));
    if (!response.ok) throw new ApiError(response.status, data);
    return data;
  } finally {
    clearTimeout(timer);
  }
}

// Refresh routing + enabled model list before each scheduling pass. Any failure
// falls back to serial extraction with backend-side model selection.
async function loadRouting() {
  try {
    const [routingResponse, configsResponse] = await Promise.all([
      request('/api/llm/routing', undefined, 10000),
      request('/api/llm/configs', undefined, 10000),
    ]);
    const parallel = Number(routingResponse.max_parallel_calls);
    routing = {
      max_parallel_calls: Math.max(1, Math.min(8, Number.isFinite(parallel) ? parallel : 1)),
      auto_select_enabled: routingResponse.auto_select_enabled !== false,
      default_config_id: routingResponse.default_config_id ?? null,
    };
    return (configsResponse.configs || configsResponse || []).filter((config) => config.enabled);
  } catch {
    routing = { max_parallel_calls: 1, auto_select_enabled: true, default_config_id: null };
    return [];
  }
}

async function createTask(capture) {
  if (!capture || typeof capture.url !== 'string' || typeof capture.title !== 'string') {
    throw Error('页面内容无效。');
  }
  const now = Date.now();
  const task = {
    id: crypto.randomUUID(),
    status: 'queued',
    sourceTitle: capture.title || '未命名职位页',
    sourceUrl: capture.url,
    selected: false,
    applicationStatus: '收藏',
    notes: '',
    allowDuplicate: false,
    triedModelIds: [],
    failoverCount: 0,
    attemptCount: 0,
    extractStartedAt: null,
    extractFinishedAt: null,
    modelSnapshot: null,
    telemetry: null,
    createdAt: now,
    updatedAt: now,
  };
  await mutateTasks((tasks) => {
    const active = tasks.filter((item) => !['added', 'failed'].includes(item.status)).length;
    if (active >= MAX_TASKS) throw Error(`任务队列最多保留 ${MAX_TASKS} 项。`);
    tasks.unshift(task);
  });
  try {
    await setCapture(task.id, capture);
  } catch (error) {
    await removeTask(task.id);
    throw error;
  }
  scheduleQueue();
  return publicTask(task);
}

function captureToDraftBody(capture, idempotencyKey) {
  if (!capture || typeof capture.url !== 'string' || typeof capture.title !== 'string') {
    throw Error('页面内容无效。');
  }
  const url = new URL(capture.url);
  if (!['http:', 'https:'].includes(url.protocol)) throw Error('只支持普通的 http/https 网页。');
  if (url.username || url.password) throw Error('来源地址包含用户名或密码，请先清理地址后重试。');
  const selected = typeof capture.selected_text === 'string' ? capture.selected_text : '';
  const body = typeof capture.text === 'string' ? capture.text : '';
  const capturedText = [
    selected ? `用户选中文本：\n${selected}` : '',
    body ? `页面正文：\n${body}` : '',
  ].filter(Boolean).join('\n\n').slice(0, 100000);
  const siteName = capture.metadata?.site_name || url.hostname;
  return {
    source_url: url.href,
    source_website: String(siteName).slice(0, 200),
    original_title: capture.title.slice(0, 500),
    captured_text: capturedText,
    user_fields: {},
    idempotency_key: idempotencyKey,
  };
}

function normalizePendingDraftSaves(value) {
  if (!value) return [];
  // Keep pending saves written by the first version, which stored one object.
  return Array.isArray(value) ? value : [value];
}

async function readPendingDraftSaves() {
  const stored = await chrome.storage.session.get(PENDING_DRAFT_SAVE_KEY);
  return normalizePendingDraftSaves(stored[PENDING_DRAFT_SAVE_KEY]);
}

async function writePendingDraftSaves(pendingSaves) {
  if (pendingSaves.length) {
    await chrome.storage.session.set({ [PENDING_DRAFT_SAVE_KEY]: pendingSaves });
  } else {
    await chrome.storage.session.remove(PENDING_DRAFT_SAVE_KEY);
  }
}

async function saveCapturedDraft(capture) {
  const pending = {
    pendingId: crypto.randomUUID(),
    capture,
    idempotencyKey: crypto.randomUUID(),
    createdAt: Date.now(),
  };
  // Validate before persistence so an unusable capture cannot block the queue.
  captureToDraftBody(pending.capture, pending.idempotencyKey);
  return mutatePendingDraftSaves(async () => {
    const pendingSaves = await readPendingDraftSaves();
    if (pendingSaves.length >= MAX_PENDING_DRAFT_SAVES) {
      throw Error(`待保存捕获最多保留 ${MAX_PENDING_DRAFT_SAVES} 份，请先重试或放弃全部待保存捕获。`);
    }
    pendingSaves.push(pending);
    await writePendingDraftSaves(pendingSaves);
    return submitPendingDraftSaves();
  });
}

async function submitPendingDraftSaves() {
  let pendingSaves = await readPendingDraftSaves();
  if (!pendingSaves.length) throw Error('没有待保存的页面捕获。');
  let lastDraft = null;
  while (pendingSaves.length) {
    const pending = pendingSaves[0];
    const body = captureToDraftBody(pending.capture, pending.idempotencyKey);
    lastDraft = await request('/api/import-drafts', body, 30000);
    // Remove only the completed item. A later capture must survive this write.
    const latest = await readPendingDraftSaves();
    const completedIndex = latest.findIndex((item) => item?.pendingId === pending.pendingId);
    if (completedIndex >= 0) latest.splice(completedIndex, 1);
    await writePendingDraftSaves(latest);
    pendingSaves = latest;
  }
  return lastDraft;
}

async function retryPendingDraftSave() {
  return mutatePendingDraftSaves(async () => {
    if (!(await readPendingDraftSaves()).length) throw Error('没有待重试的草稿保存操作。');
    return submitPendingDraftSaves();
  });
}

async function hasPendingDraftSave() {
  return (await readPendingDraftSaves()).length;
}

async function discardPendingDraftSaves() {
  return mutatePendingDraftSaves(async () => {
    const pendingSaves = await readPendingDraftSaves();
    await writePendingDraftSaves([]);
    return { discardedCount: pendingSaves.length };
  });
}

async function claimConfirm() {
  return mutateTasks((tasks) => {
    const task = tasks
      .filter((item) => item.status === 'confirm_queued')
      .sort((left, right) => left.createdAt - right.createdAt)[0];
    if (!task) return null;
    task.status = 'confirming';
    task.error = null;
    task.errorCode = null;
    task.updatedAt = Date.now();
    return publicTask(task);
  });
}

async function claimExtract(models, pinnedModelId) {
  return mutateTasks((tasks) => {
    const task = tasks
      .filter((item) => item.status === 'queued')
      .sort((left, right) => left.createdAt - right.createdAt)[0];
    if (!task) return null;
    // First claim fixes the model: the user's popup choice wins, then
    // round-robin over enabled models when auto-select is on, otherwise the
    // configured default. Retries (which already carry modelConfigId) must
    // not rotate to a different model. A paused/deleted default stays null so
    // the backend falls back to another enabled configuration. Models this
    // task has already failed over from are never picked again.
    const tried = task.triedModelIds || [];
    const untried = models.filter((model) => !tried.includes(model.id));
    if (models.length && untried.length === 0) {
      // 每个启用模型都失败过：停止自动切换，转人工处理。
      task.status = 'failed';
      task.error = `所有已启用的 ${models.length} 个模型均解析失败，请到设置页检查模型配置，或稍后手动重试。`;
      task.errorCode = 'all_models_failed';
      task.note = null;
      task.selected = false;
      task.telemetry = null;
      task.extractFinishedAt = task.extractFinishedAt || Date.now();
      task.updatedAt = Date.now();
      return publicTask(task);
    }
    if (!task.modelConfigId) {
      const enabledDefault =
        routing.default_config_id
        && models.some((model) => model.id === routing.default_config_id)
        && !tried.includes(routing.default_config_id)
          ? routing.default_config_id
          : null;
      const usablePinned = pinnedModelId && !tried.includes(pinnedModelId) ? pinnedModelId : null;
      if (usablePinned) {
        task.modelConfigId = usablePinned;
      } else if (!routing.auto_select_enabled) {
        task.modelConfigId = enabledDefault
          || (untried.length ? untried[modelCursor++ % untried.length].id : null);
      } else if (untried.length) {
        task.modelConfigId = untried[modelCursor++ % untried.length].id;
      } else {
        task.modelConfigId = enabledDefault;
      }
    }
    const selectedModel = models.find((model) => positiveConfigId(model.id) === positiveConfigId(task.modelConfigId));
    task.modelSnapshot = selectedModel ? safeModelSnapshot(selectedModel) : null;
    task.telemetry = null;
    task.attemptCount = Math.min(MAX_ATTEMPTS, normalizeAttemptCount(task.attemptCount) + 1);
    task.extractStartedAt = Date.now();
    task.extractFinishedAt = null;
    task.status = 'extracting';
    task.updatedAt = Date.now();
    return publicTask(task);
  });
}

async function finishExtract(task, data) {
  const committed = await mutateTasks((tasks) => {
    const current = tasks.find((item) => item.id === task.id);
    if (!current || current.status !== 'extracting') return false;
    const finishedAt = Date.now();
    const startedAt = finiteNonNegativeInt(current.extractStartedAt);
    const elapsed = startedAt !== null ? Math.max(0, finishedAt - startedAt) : null;
    current.status = 'review';
    current.draft = data.draft;
    current.duplicates = data.duplicates || [];
    const responseConfigId = positiveConfigId(data.model_config_id);
    if (responseConfigId) current.modelConfigId = responseConfigId;
    current.telemetry = normalizeTelemetry(data.telemetry, current.modelSnapshot, elapsed);
    if (current.telemetry?.model) {
      current.modelSnapshot = current.telemetry.model;
      current.modelConfigId = current.telemetry.model.config_id;
    } else if (responseConfigId && current.modelSnapshot?.config_id !== responseConfigId) {
      // 老后端没有 telemetry 时，不能把此前选择的模型误标成响应模型。
      current.modelSnapshot = null;
    }
    current.extractFinishedAt = finishedAt;
    current.selected = !current.duplicates.length;
    current.note = null;
    current.updatedAt = Date.now();
    return true;
  });
  if (committed) await deleteCapture(task.id);
}

async function failExtract(task, error) {
  const failure = safeError(error);
  const canFailover = shouldFailover(failure.code);
  let requeued = false;
  await mutateTasks((tasks) => {
    const current = tasks.find((item) => item.id === task.id);
    if (!current || current.status !== 'extracting') return;
    const finishedAt = Date.now();
    current.triedModelIds = [...new Set([
      ...(current.triedModelIds || []),
      current.modelConfigId,
    ].filter(Boolean))];
    current.telemetry = null;
    current.extractFinishedAt = finishedAt;
    if (canFailover) {
      // 模型侧失败：清空 modelConfigId 让下一次认领从未试过的启用模型中挑选。
      current.failoverCount = current.triedModelIds.length;
      current.status = 'queued';
      current.modelConfigId = null;
      current.modelSnapshot = null;
      current.note = `第 ${current.failoverCount} 个模型解析失败，自动切换下一个模型重试`;
      current.error = null;
      current.errorCode = null;
      current.selected = false;
      requeued = true;
    } else {
      current.status = 'failed';
      current.note = null;
      current.error = current.failoverCount
        ? `已自动尝试 ${current.failoverCount + 1} 个模型均失败：${failure.message}`
        : failure.message;
      current.errorCode = failure.code;
      current.selected = false;
    }
    current.updatedAt = Date.now();
  });
  if (requeued) scheduleQueue();
}

async function finishConfirm(task) {
  const removed = await mutateTasks((tasks) => {
    const index = tasks.findIndex((item) => item.id === task.id);
    const current = index >= 0 ? tasks[index] : null;
    if (!current || current.status !== 'confirming') return false;
    tasks.splice(index, 1);
    return true;
  });
  if (removed) await deleteCapture(task.id);
}

async function failConfirm(task, error) {
  const failure = safeError(error);
  await mutateTasks((tasks) => {
    const current = tasks.find((item) => item.id === task.id);
    if (!current || current.status !== 'confirming') return;
    current.status = 'review';
    current.error = failure.message;
    current.errorCode = failure.code;
    current.selected = false;
    if (error instanceof ApiError && error.status === 409 && error.data?.detail?.code === 'duplicate_job') {
      current.duplicates = error.data.detail.candidates || [];
      current.allowDuplicate = false;
    }
    current.updatedAt = Date.now();
  });
}

// Confirmation writes are strictly serial: one in-flight /confirm at a time.
async function confirmWorker() {
  if (confirmRunning) return;
  confirmRunning = true;
  try {
    while (true) {
      const task = await claimConfirm();
      if (!task) break;
      try {
        await request(
          '/api/imports/job/confirm',
          {
            draft: task.draft,
            application_status: task.applicationStatus,
            notes: task.notes || null,
            allow_duplicate: Boolean(task.allowDuplicate),
          },
          CONFIRM_TIMEOUT_MS,
        );
        await finishConfirm(task);
      } catch (error) {
        await failConfirm(task, error);
      }
    }
  } finally {
    confirmRunning = false;
  }
}

async function extractOne(task) {
  try {
    const capture = await getCapture(task.id);
    if (!capture) throw Error('capture missing');
    const data = await request(
      '/api/imports/job/extract',
      { ...capture, model_config_id: task.modelConfigId || undefined },
      EXTRACT_TIMEOUT_MS,
    );
    await finishExtract(task, data);
  } catch (error) {
    await failExtract(task, error);
  } finally {
    extractionRunning--;
    setTimeout(() => void processQueues(), 0);
  }
}

async function processQueues() {
  if (running) return;
  running = true;
  try {
    const models = await loadRouting();
    await confirmWorker();
    const preferredId = await readPreferredModelId();
    const pinnedModelId =
      preferredId && models.some((model) => model.id === preferredId) ? preferredId : null;
    const parallelLimit = Math.max(
      1,
      Math.min(8, Number.isFinite(Number(routing.max_parallel_calls)) ? Number(routing.max_parallel_calls) : 1),
    );
    while (extractionRunning < parallelLimit) {
      const task = await claimExtract(models, pinnedModelId);
      if (!task) break;
      if (task.status !== 'extracting') continue; // 模型全部失败，已在认领时标记 failed
      extractionRunning++;
      void extractOne(task);
    }
  } finally {
    running = false;
  }
}

function scheduleQueue() {
  chrome.alarms.create(QUEUE_ALARM, { when: Date.now() + 250 });
  void processQueues();
}

// Service workers can be killed at any moment: unfinished extractions can be
// retried safely, but an interrupted confirm must go back to human review.
async function recover() {
  await mutateTasks((tasks) => {
    for (const task of tasks) {
      if (task.status === 'extracting') {
        task.status = 'failed';
        task.error = '上次解析在扩展后台重启时中断，请确认后手动重试。';
        task.errorCode = 'extraction_interrupted';
        task.note = null;
        task.selected = false;
        task.telemetry = null;
        task.extractFinishedAt = task.extractFinishedAt || Date.now();
      } else if (task.status === 'confirming') {
        task.status = 'review';
        task.error = '上次添加在扩展重启时中断，请先在 Tracker 核对后再重试。';
        task.errorCode = 'confirmation_interrupted';
        task.note = null;
        task.selected = false;
      }
      if (['failed', 'review'].includes(task.status)) task.updatedAt = Date.now();
    }
  });
  scheduleQueue();
}

async function patchTask(taskId, patch) {
  return mutateTasks((tasks) => {
    const task = tasks.find((item) => item.id === taskId);
    if (!task || task.status !== 'review') throw Error('只有待核对任务可以修改。');
    for (const key of ['selected', 'draft', 'applicationStatus', 'notes', 'allowDuplicate']) {
      if (Object.hasOwn(patch || {}, key)) task[key] = patch[key];
    }
    task.updatedAt = Date.now();
    return publicTask(task);
  });
}

async function retryTask(taskId) {
  const hasCapture = Boolean(await getCapture(taskId));
  const task = await mutateTasks((tasks) => {
    const current = tasks.find((item) => item.id === taskId);
    if (!current || current.status !== 'failed') throw Error('当前任务不需要重试。');
    if (!hasCapture) {
      current.error = '页面内容已过期，请回到原页面重新创建任务。';
      current.errorCode = 'capture_expired';
      return publicTask(current);
    }
    current.status = 'queued';
    current.error = null;
    current.errorCode = null;
    current.note = null;
    current.telemetry = null;
    current.extractStartedAt = null;
    current.extractFinishedAt = null;
    current.modelSnapshot = null;
    // 手动重试沿用原模型（不轮换），但清空切换历史：再次失败仍会自动换模型。
    current.triedModelIds = [];
    current.failoverCount = 0;
    current.updatedAt = Date.now();
    return publicTask(current);
  });
  if (task.status === 'queued') scheduleQueue();
  return task;
}

async function queueConfirm(taskId, changes = {}) {
  const result = await mutateTasks((tasks) => {
    const task = tasks.find((item) => item.id === taskId);
    if (!task || task.status !== 'review') throw Error('任务尚未进入可添加状态。');
    for (const key of ['draft', 'applicationStatus', 'notes', 'allowDuplicate']) {
      if (Object.hasOwn(changes, key)) task[key] = changes[key];
    }
    if (task.duplicates?.length && !task.allowDuplicate) {
      return { blocked: true, task: publicTask(task) };
    }
    task.status = 'confirm_queued';
    task.selected = false;
    task.error = null;
    task.errorCode = null;
    task.updatedAt = Date.now();
    return { blocked: false, task: publicTask(task) };
  });
  if (!result.blocked) scheduleQueue();
  return result;
}

async function queueBatch(ids) {
  const result = await mutateTasks((tasks) => {
    const acceptedIds = [];
    const blockedIds = [];
    for (const task of tasks) {
      if (!ids.includes(task.id) || task.status !== 'review') continue;
      if (task.duplicates?.length && !task.allowDuplicate) {
        blockedIds.push(task.id);
        continue;
      }
      task.status = 'confirm_queued';
      task.selected = false;
      task.error = null;
      task.errorCode = null;
      task.updatedAt = Date.now();
      acceptedIds.push(task.id);
    }
    return { acceptedIds, blockedIds };
  });
  if (result.acceptedIds.length) scheduleQueue();
  return result;
}

async function removeTask(taskId) {
  await deleteCapture(taskId);
  return mutateTasks((tasks) => {
    const index = tasks.findIndex((item) => item.id === taskId);
    if (index >= 0) tasks.splice(index, 1);
    return { removed: index >= 0 };
  });
}

async function handleMessage(message) {
  switch (message?.type) {
    case 'tasks:list':
      return { ok: true, tasks: pruneTasks(await readTasks()).map(publicTask) };
    case 'task:create':
      return { ok: true, task: await createTask(message.capture) };
    case 'draft:save':
      return { ok: true, draft: await saveCapturedDraft(message.capture) };
    case 'draft:retry':
      return { ok: true, draft: await retryPendingDraftSave() };
    case 'draft:pending':
      {
        const count = await hasPendingDraftSave();
        return { ok: true, pending: count > 0, count };
      }
    case 'draft:discard':
      return { ok: true, ...await discardPendingDraftSaves() };
    case 'task:patch':
      return { ok: true, task: await patchTask(message.taskId, message.patch) };
    case 'task:retry':
      return { ok: true, task: await retryTask(message.taskId) };
    case 'task:confirm': {
      const result = await queueConfirm(message.taskId, message.changes);
      return {
        ok: !result.blocked,
        code: result.blocked ? 'duplicate_requires_review' : null,
        task: result.task,
      };
    }
    case 'tasks:confirm_batch':
      return { ok: true, ...await queueBatch(Array.isArray(message.taskIds) ? message.taskIds : []) };
    case 'task:remove':
      return { ok: true, ...await removeTask(message.taskId) };
    case 'models:list': {
      const models = await loadRouting();
      return {
        ok: true,
        models: models.map((model) => ({
          id: model.id,
          label: `${model.display_name}（${model.provider} / ${model.model}）`,
        })),
      };
    }
    default:
      return { ok: false, error: '未知扩展命令。' };
  }
}

chrome.runtime.onMessage.addListener((message, _sender, sendResponse) => {
  handleMessage(message)
    .then(sendResponse)
    .catch((error) => sendResponse({ ok: false, error: error.message || '扩展后台任务失败。' }));
  return true;
});

chrome.alarms.onAlarm.addListener((alarm) => {
  if (alarm.name === QUEUE_ALARM) void processQueues();
});

// Switching the popup model takes effect for tasks that have not started yet.
chrome.storage.onChanged.addListener((changes, areaName) => {
  if (areaName === 'local' && changes[PREFERRED_MODEL_KEY]) scheduleQueue();
});

chrome.runtime.onStartup.addListener(recover);
chrome.runtime.onInstalled.addListener(recover);
recover();
