// Preview-mode simulation for popup.html opened outside the extension
// (file:// or ?preview). popup.js talks to it only through window.PreviewAPI,
// keeping every preview fixture isolated from production logic.
// loaded unconditionally; it touches no Chrome APIs and adds no permissions.

function previewDraft(overrides = {}) {
  return {
    company_name: '远岚数据',
    title: '数据产品实习生',
    location: '厦门 · 可远程',
    employment_type: '实习',
    job_category: '产品',
    published_at: '2026-08-29',
    description: '参与指标体系梳理、数据产品原型设计与业务分析。',
    source_website: '示例招聘网',
    confidence: 0.91,
    warnings: [],
    source_url: 'https://jobs.example.test/roles/data-product-intern',
    original_title: '数据产品实习生｜远岚数据',
    ...overrides,
  };
}

function previewModel(configId = 1, overrides = {}) {
  return {
    config_id: configId,
    display_name: `预览模型 ${configId}`,
    provider: 'preview',
    model: `demo-${configId}`,
    ...overrides,
  };
}

function previewTelemetry(model = previewModel(), overrides = {}) {
  return {
    model,
    prompt_tokens: 120,
    completion_tokens: 0,
    duration_ms: 1350,
    provider_calls: 1,
    usage_complete: true,
    ...overrides,
  };
}

const previewListeners = new Set();
let previewTasks = [
  {
    id: 'preview-review',
    status: 'review',
    sourceTitle: '数据产品实习生｜远岚数据',
    sourceUrl: 'https://jobs.example.test/roles/data-product-intern',
    draft: previewDraft(),
    duplicates: [],
    selected: true,
    applicationStatus: '收藏',
    notes: '',
    allowDuplicate: false,
    attemptCount: 1,
    extractStartedAt: Date.now() - 180000,
    extractFinishedAt: Date.now() - 45000,
    modelSnapshot: previewModel(1),
    telemetry: previewTelemetry(previewModel(1)),
    createdAt: Date.now() - 180000,
    updatedAt: Date.now() - 45000,
  },
  {
    id: 'preview-extracting',
    status: 'extracting',
    sourceTitle: 'AI 应用工程实习生 - 职位详情',
    sourceUrl: 'https://careers.example.test/ai-agent-intern',
    selected: false,
    applicationStatus: '收藏',
    notes: '',
    allowDuplicate: false,
    attemptCount: 1,
    extractStartedAt: Date.now() - 12000,
    extractFinishedAt: null,
    modelSnapshot: previewModel(2),
    telemetry: null,
    createdAt: Date.now() - 120000,
    updatedAt: Date.now() - 12000,
  },
  {
    id: 'preview-duplicate',
    status: 'review',
    sourceTitle: '商业分析实习生｜海屿科技',
    sourceUrl: 'https://campus.example.test/jobs/ba-2048',
    draft: previewDraft({
      company_name: '海屿科技',
      title: '商业分析实习生',
      location: '泉州',
      source_url: 'https://campus.example.test/jobs/ba-2048',
      original_title: '商业分析实习生｜海屿科技',
      confidence: 0.86,
      warnings: ['岗位类型未明确标注，已按页面上下文判断为实习。'],
    }),
    duplicates: [{
      job_id: 12,
      company_name: '海屿科技',
      title: '商业分析实习生',
      source_url: 'https://campus.example.test/jobs/ba-2048',
      reasons: ['source_url'],
    }],
    selected: false,
    applicationStatus: '收藏',
    notes: '',
    allowDuplicate: false,
    attemptCount: 1,
    extractStartedAt: Date.now() - 90000,
    extractFinishedAt: Date.now() - 30000,
    modelSnapshot: previewModel(1),
    telemetry: previewTelemetry(previewModel(1), { prompt_tokens: 80, completion_tokens: 20, duration_ms: 60000 }),
    createdAt: Date.now() - 90000,
    updatedAt: Date.now() - 30000,
  },
  {
    id: 'preview-failed',
    status: 'failed',
    sourceTitle: '算法运营实习生',
    sourceUrl: 'https://work.example.test/job/510',
    selected: false,
    error: '无法连接模型服务，请检查 API Base 与网络。',
    errorCode: 'llm_connection_failed',
    attemptCount: 2,
    extractStartedAt: Date.now() - 60000,
    extractFinishedAt: Date.now() - 18000,
    modelSnapshot: previewModel(3),
    telemetry: null,
    createdAt: Date.now() - 60000,
    updatedAt: Date.now() - 18000,
  },
];

function notifyPreview() {
  for (const listener of previewListeners) listener();
}

function previewFind(taskId) {
  return previewTasks.find((task) => task.id === taskId);
}

function previewFinish(taskId, status, delay = 850) {
  setTimeout(() => {
    const task = previewFind(taskId);
    if (!task) return;
    if (status === 'review') {
      task.status = 'review';
      task.draft = previewDraft({
        title: '新增队列中的职位',
        source_url: task.sourceUrl,
        original_title: task.sourceTitle,
      });
      task.duplicates = [];
      task.selected = true;
      task.extractFinishedAt = Date.now();
      task.modelSnapshot = task.modelSnapshot || previewModel(2);
      task.telemetry = previewTelemetry(task.modelSnapshot, {
        duration_ms: Math.max(0, task.extractFinishedAt - (task.extractStartedAt || task.extractFinishedAt)),
      });
    } else {
      const index = previewTasks.findIndex((item) => item.id === task.id);
      if (index >= 0) previewTasks.splice(index, 1);
      notifyPreview();
      return;
    }
    task.updatedAt = Date.now();
    notifyPreview();
  }, delay);
}

async function previewCommand(message) {
  if (message.type === 'tasks:list') return { ok: true, tasks: structuredClone(previewTasks) };
  if (message.type === 'draft:pending') return { ok: true, pending: false, count: 0 };
  if (message.type === 'draft:save') {
    return {
      ok: true,
      draft: { id: 'preview-draft', status: 'draft', version: 1 },
    };
  }
  if (message.type === 'draft:retry') return { ok: false, error: '预览模式没有待重试的草稿。' };
  if (message.type === 'draft:discard') return { ok: true, discardedCount: 0 };
  if (message.type === 'task:create') {
    const task = {
      id: crypto.randomUUID(),
      status: 'extracting',
      sourceTitle: message.capture.title || '预览职位页',
      sourceUrl: message.capture.url,
      selected: false,
      applicationStatus: '收藏',
      notes: '',
      allowDuplicate: false,
      attemptCount: 1,
      extractStartedAt: Date.now(),
      extractFinishedAt: null,
      modelSnapshot: previewModel(2),
      telemetry: null,
      createdAt: Date.now(),
      updatedAt: Date.now(),
    };
    previewTasks.unshift(task);
    notifyPreview();
    previewFinish(task.id, 'review', 1100);
    return { ok: true, task: structuredClone(task) };
  }
  if (message.type === 'task:patch') {
    const task = previewFind(message.taskId);
    if (task) Object.assign(task, message.patch, { updatedAt: Date.now() });
    notifyPreview();
    return { ok: Boolean(task), task: task ? structuredClone(task) : null };
  }
  if (message.type === 'task:retry') {
    const task = previewFind(message.taskId);
    if (task) {
      task.status = 'queued';
      task.error = null;
      task.errorCode = null;
      task.telemetry = null;
      task.extractStartedAt = null;
      task.extractFinishedAt = null;
      task.modelSnapshot = null;
      task.updatedAt = Date.now();
      notifyPreview();
      setTimeout(() => {
        const current = previewFind(task.id);
        if (!current || current.status !== 'queued') return;
        current.status = 'extracting';
        current.attemptCount = (Number.isSafeInteger(current.attemptCount) ? current.attemptCount : 0) + 1;
        current.extractStartedAt = Date.now();
        current.extractFinishedAt = null;
        current.modelSnapshot = previewModel(2);
        current.updatedAt = Date.now();
        notifyPreview();
        previewFinish(current.id, 'review');
      }, 80);
    }
    return { ok: Boolean(task), task: task ? structuredClone(task) : null };
  }
  if (message.type === 'task:confirm') {
    const task = previewFind(message.taskId);
    if (!task) return { ok: false, error: '任务不存在。' };
    Object.assign(task, message.changes || {});
    if (task.duplicates?.length && !task.allowDuplicate) {
      return { ok: false, code: 'duplicate_requires_review', task: structuredClone(task) };
    }
    task.status = 'confirming';
    task.selected = false;
    task.updatedAt = Date.now();
    notifyPreview();
    previewFinish(task.id, 'added');
    return { ok: true, task: structuredClone(task) };
  }
  if (message.type === 'tasks:confirm_batch') {
    const acceptedIds = [];
    const blockedIds = [];
    for (const task of previewTasks) {
      if (!message.taskIds.includes(task.id) || task.status !== 'review') continue;
      if (task.duplicates?.length && !task.allowDuplicate) {
        blockedIds.push(task.id);
      } else {
        task.status = 'confirming';
        task.selected = false;
        task.updatedAt = Date.now();
        acceptedIds.push(task.id);
        previewFinish(task.id, 'added', 700 + acceptedIds.length * 180);
      }
    }
    notifyPreview();
    return { ok: true, acceptedIds, blockedIds };
  }
  if (message.type === 'task:remove') {
    const index = previewTasks.findIndex((task) => task.id === message.taskId);
    if (index >= 0) previewTasks.splice(index, 1);
    notifyPreview();
    return { ok: true, removed: index >= 0 };
  }
  return { ok: false, error: '未知预览命令。' };
}

window.PreviewAPI = {
  command: previewCommand,
  subscribe(listener) {
    previewListeners.add(listener);
  },
  capture() {
    return {
      url: 'https://preview.example.test/jobs/new-role',
      title: '新增预览职位页',
      selected_text: '数据分析、AI 应用、实习',
      text: '这是用于界面预览的虚构职位正文，不会发送到任何外部服务。',
      metadata: { site_name: '预览招聘网' },
    };
  },
};
