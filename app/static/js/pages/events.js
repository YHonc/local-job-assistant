(() => {
  if (typeof document === 'undefined') return;

  const app = window.JobAssistant || {};
  const sharedDom = app.dom || {};
  const $ = sharedDom.$ || ((selector, root = document) => root.querySelector(selector));
  const esc = sharedDom.esc || ((value) => String(value ?? '').replace(/[&<>"']/g, (char) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[char])));
  const sharedApi = app.api;
  const list = $('#emailList');
  if (!list) return;

  const statusLabels = {
    processing: '处理中',
    linked: '已关联',
    created: '已新建',
    pending: '待处理',
    failed: '失败',
    filtered: '未命中筛选',
  };
  const eventTypeLabels = {
    APPLICATION_RECEIVED: '收到回复',
    ASSESSMENT: '测评',
    WRITTEN_TEST: '笔试',
    INTERVIEW: '面试',
    INTERVIEW_CHANGED: '面试变更',
    OFFER: 'Offer',
    REJECTED: '拒绝',
    RECRUITMENT_MARKETING: '招聘营销',
    OTHER: '其他',
  };
  const associationLabels = {
    unmatched: '待匹配',
    suggested: '待确认',
    confirmed: '已确认',
    dismissed: '保持未关联',
    auto_bound: '自动关联',
    manual: '已人工关联',
    linked: '已关联',
  };
  const state = {
    emails: [],
    events: [],
    currentEmail: null,
    currentEmailId: null,
    loadingEmails: false,
    loadingDetail: false,
    loadingEvents: false,
    eventsLoaded: false,
    pollTimer: null,
    candidateMap: new Map(),
    detailRequestToken: 0,
  };

  const byId = (id) => document.getElementById(id);
  const eventLabel = (value) => eventTypeLabels[value] || value || '其他';
  const associationLabel = (value) => associationLabels[token(value)] || text(value) || '待处理';
  const asArray = (value) => Array.isArray(value) ? value : (value ? [value] : []);
  const trim = (value) => typeof value === 'string' ? value.trim() : value;
  const text = (value) => {
    if (value === null || value === undefined) return '';
    if (typeof value === 'string' || typeof value === 'number' || typeof value === 'boolean') return String(value);
    return '';
  };
  const formatTime = (value) => {
    if (!value) return '未提取';
    const date = new Date(value);
    return Number.isNaN(date.getTime()) ? text(value) : date.toLocaleString('zh-CN');
  };
  const toDateValue = (value) => value ? formatTime(value) : '未提取';
  const token = (value) => text(value).toLowerCase().replace(/[\s-]+/g, '_');
  const first = (...values) => values.find((value) => value !== undefined && value !== null && value !== '');
  const numberOrNull = (value) => {
    const number = Number(value);
    return Number.isFinite(number) && number > 0 ? number : null;
  };
  const titleOf = (value) => {
    if (typeof value === 'string') return value;
    if (!value || typeof value !== 'object') return '';
    return text(first(value.title, value.name, value.job_title, value.label));
  };
  const companyOf = (value) => {
    if (typeof value === 'string') return value;
    if (!value || typeof value !== 'object') return '';
    return text(first(value.name, value.company_name, value.title, value.label));
  };
  const safeHref = (value) => {
    const candidate = trim(value);
    if (!candidate) return '';
    try {
      const parsed = new URL(candidate, window.location.origin);
      return ['http:', 'https:'].includes(parsed.protocol) ? parsed.href : '';
    } catch {
      return '';
    }
  };
  const localJobHref = (value) => {
    const id = numberOrNull(value);
    return id ? `/jobs/${encodeURIComponent(id)}` : '';
  };
  const details = (value) => value && typeof value === 'object' ? value : {};

  async function request(path, options = {}) {
    if (typeof sharedApi === 'function') return sharedApi(path, options);
    const response = await fetch(`/api${path}`, {
      headers: { 'Content-Type': 'application/json' },
      ...options,
    });
    const payload = response.status === 204 ? null : await response.json().catch(() => ({}));
    if (!response.ok) {
      const detail = payload?.detail;
      const message = typeof detail === 'string' ? detail : (detail?.message || `请求失败（${response.status}）`);
      const error = new Error(message);
      error.status = response.status;
      throw error;
    }
    return payload;
  }

  function setFeedback(id, message, error = false) {
    const output = byId(id);
    if (!output) return;
    output.textContent = message || '';
    output.className = `feedback${error ? ' error' : ''}`;
  }

  function unwrapCollection(payload, keys = []) {
    if (Array.isArray(payload)) return payload;
    if (!payload || typeof payload !== 'object') return [];
    for (const key of keys) if (Array.isArray(payload[key])) return payload[key];
    return [];
  }

  function statusKey(raw) {
    const value = token(raw);
    if (['running', 'queued', 'processing', 'cleaning', 'pending_cleaning', 'archiving', 'not_processed', 'waiting'].includes(value)) return 'processing';
    if (['linked', 'associated', 'confirmed', 'matched', 'match_confirmed', 'auto_bound', 'manual', 'manually_associated', 'archived_linked'].includes(value)) return 'linked';
    if (['created', 'new', 'newly_created', 'auto_created', 'job_created', 'archived'].includes(value)) return 'created';
    if (['pending', 'unmatched', 'suggested', 'needs_review', 'conflict', 'manual', 'awaiting_match', 'awaiting_archive', 'review'].includes(value)) return 'pending';
    if (['failed', 'error', 'cleaning_failed', 'archive_failed', 'match_failed'].includes(value)) return 'failed';
    if (['filtered', 'skipped', 'regex_skipped', 'not_relevant'].includes(value)) return 'filtered';
    return '';
  }

  function deriveEmailStatus(raw, cleaning, entries = []) {
    const direct = statusKey(first(raw.status, raw.processing_status, raw.email_status));
    if (direct) return direct;
    if (raw.processing_error || raw.error || cleaning?.error) return 'failed';
    const cleaningStatus = statusKey(first(raw.cleaning_status, cleaning?.status));
    if (cleaningStatus === 'failed' || cleaningStatus === 'processing') return cleaningStatus;
    const archiveStatus = statusKey(first(raw.archive_status, raw.archiving_status));
    if (archiveStatus === 'failed') return 'failed';
    if (entries.some((entry) => entry.status === 'pending')) return 'pending';
    if (entries.some((entry) => entry.status === 'created')) return 'created';
    if (entries.some((entry) => entry.status === 'linked')) return 'linked';
    if (archiveStatus && archiveStatus !== 'created') return archiveStatus;
    if (raw.pre_filtered === false && raw.llm_processed !== true) return 'filtered';
    if (raw.llm_processed === false || raw.llm_processed === undefined && !cleaning) return 'processing';
    if (cleaningStatus === 'linked' || cleaningStatus === 'created') return cleaningStatus;
    return 'pending';
  }

  function normalizeLinks(value) {
    return asArray(value).map((item) => {
      if (typeof item === 'string') return { label: '相关链接', url: item };
      const object = details(item);
      return {
        label: text(first(object.label, object.name, object.type, '相关链接')),
        url: text(first(object.url, object.href, object.link)),
      };
    }).filter((item) => safeHref(item.url));
  }

  function normalizeCleaning(raw, message) {
    const source = details(first(raw.cleaning, raw.cleaning_result, raw.cleaningResult));
    const company = first(source.company, raw.company, source.company_name, raw.company_name);
    const job = first(source.job, raw.job, source.job_title, raw.job_title);
    return {
      id: first(source.id, source.cleaning_result_id, raw.cleaning_result_id),
      status: text(first(source.status, raw.cleaning_status)),
      version: first(source.version, raw.cleaning_version),
      body: text(first(source.body, source.cleaned_body, raw.cleaned_body, raw.cleaned_body_text, raw.cleaning_body)),
      summary: text(first(source.summary, source.ai_summary, raw.cleaning_summary, raw.cleaned_summary, raw.summary, raw.ai_summary)),
      company,
      job,
      companyName: companyOf(company),
      jobTitle: titleOf(job),
      workLocation: text(first(source.work_location, source.location, raw.work_location, raw.job_location)),
      links: normalizeLinks(first(source.links, source.job_links, raw.links, raw.job_links)),
      fieldEvidence: details(first(source.field_evidence, source.evidence, raw.field_evidence)),
      eventTime: first(source.event_time, raw.event_time),
      deadline: first(source.deadline, raw.deadline),
      actionRequired: Boolean(first(source.action_required, raw.action_required, false)),
      actions: first(source.actions, raw.cleaning_actions),
      error: text(first(source.error, source.processing_error, raw.processing_error)),
      originalBody: text(first(raw.original_body_text, raw.body_text, message?.body_text, message?.body_preview)),
    };
  }

  function normalizeCandidate(candidate) {
    const item = details(candidate);
    const job = details(item.job);
    const company = details(item.company);
    return {
      ...item,
      jobId: numberOrNull(first(item.job_id, item.jobId, job.id)),
      companyId: numberOrNull(first(item.company_id, item.companyId, company.id)),
      applicationId: numberOrNull(first(item.application_id, item.applicationId)),
      companyName: text(first(item.company_name, company.name, item.name)),
      jobTitle: text(first(item.job_title, job.title, job.name, item.title)),
      score: first(item.score, item.match_score, item.confidence),
      reasons: asArray(first(item.reasons, item.match_reasons, item.evidence)).map(text).filter(Boolean),
    };
  }

  function normalizeEntry(entry) {
    const raw = details(entry);
    const job = details(raw.job);
    const company = details(raw.company);
    const candidates = asArray(first(raw.candidates, raw.candidate_jobs, raw.match_candidates, raw.job_candidates)).map(normalizeCandidate);
    const rawStatus = first(raw.status, raw.archive_status, raw.association_status, raw.match_status);
    let reason = raw.match_reason;
    try { reason = JSON.parse(reason).reason || reason; } catch { /* plain reason */ }
    return {
      ...raw,
      id: first(raw.id, raw.entry_id, raw.job_entry_id),
      eventIds: asArray(first(raw.event_ids, raw.events, raw.recruitment_event_id)).map((item) => numberOrNull(typeof item === 'object' ? item.id : item)).filter(Boolean),
      companyId: numberOrNull(first(raw.company_id, raw.companyId, company.id)),
      jobId: numberOrNull(first(raw.job_id, raw.jobId, job.id)),
      applicationId: numberOrNull(first(raw.application_id, raw.applicationId)),
      companyName: text(first(raw.company_name, company.name)),
      jobTitle: text(first(raw.job_title, job.title, job.name)),
      location: text(first(raw.work_location, raw.location, job.location)),
      links: normalizeLinks(first(raw.links, raw.job_links, raw.job_url, raw.url, job.url, raw.source_url)),
      link: text(first(raw.job_url, raw.url, job.url, raw.source_url, asArray(raw.links)[0])),
      status: statusKey(rawStatus) || (raw.job_id ? 'linked' : 'pending'),
      rawStatus: text(rawStatus),
      reasons: asArray(first(raw.match_reasons, reason, raw.reasons, raw.evidence)).map(text).filter(Boolean),
      candidates,
      events: asArray(raw.events).filter((item) => item && typeof item === 'object'),
      created: rawStatus === 'auto_created' || Boolean(first(raw.created, raw.auto_created, raw.job_created, false)),
      manualLocked: Boolean(first(raw.manual_locked, raw.association_locked, raw.manually_associated, false)),
    };
  }

  function normalizeEmail(raw) {
    const source = details(raw);
    const cleaning = normalizeCleaning(source, source);
    const entries = asArray(first(source.job_entries, source.jobEntries, source.entries, source.positions, source.jobs, source.cleaning?.job_entries, source.cleaning?.jobEntries)).map(normalizeEntry);
    const events = asArray(first(source.events, source.recruitment_events, source.event, source.recruitment_event)).filter((item) => item && typeof item === 'object');
    const email = {
      ...source,
      id: numberOrNull(source.id) || source.id,
      sender: text(source.sender),
      subject: text(source.subject),
      receivedAt: first(source.received_at, source.receivedAt),
      syncedAt: first(source.synced_at, source.syncedAt),
      bodyPreview: text(first(source.body_preview, source.preview, source.snippet)),
      bodyText: text(source.body_text),
      cleaning,
      jobEntries: entries,
      events,
      availableActions: first(source.available_actions, source.workflow_actions, source.actions_allowed),
    };
    email.status = deriveEmailStatus(source, cleaning, entries);
    email.statusLabel = statusLabels[email.status] || '待处理';
    return email;
  }

  function statusTag(status, extraClass = '') {
    const key = statusKey(status) || 'pending';
    return `<span class="tag email-status-tag status-${esc(key)} ${extraClass}" data-status="${esc(key)}">${esc(statusLabels[key] || text(status) || '待处理')}</span>`;
  }

  function associationTag(status) {
    const raw = token(status) || 'unmatched';
    return `<span class="tag association-status" data-association-status="${esc(raw)}">${esc(associationLabels[raw] || text(status) || '待匹配')}</span>`;
  }

  function searchText(email) {
    const fields = [
      email.sender, email.subject, email.bodyPreview, email.cleaning.summary,
      email.cleaning.companyName, email.cleaning.jobTitle,
      ...email.jobEntries.flatMap((entry) => [entry.companyName, entry.jobTitle]),
    ];
    return fields.join(' ').toLocaleLowerCase('zh-CN');
  }

  function renderAccountFilter() {
    const select = byId('emailAccountFilter');
    if (!select) return;
    const current = select.value;
    const accounts = new Map();
    state.emails.forEach((email) => {
      const id = first(email.email_account_id, email.emailAccountId);
      if (id !== undefined && id !== null) accounts.set(String(id), text(first(email.account_email, email.account_address, email.email_account)) || `账户 #${id}`);
    });
    select.innerHTML = '<option value="">全部账户</option>' + [...accounts.entries()].map(([id, label]) => `<option value="${esc(id)}">${esc(label)}</option>`).join('');
    if ([...accounts.keys()].includes(current)) select.value = current;
  }

  function renderEmailCard(email) {
    const summary = email.cleaning.summary
      || (email.status === 'processing' ? '正在等待 AI 清洗结果…'
        : email.status === 'failed' ? 'AI 清洗失败，打开详情重试。'
          : email.status === 'filtered' ? '未命中筛选，仅保留必要摘要。'
            : 'AI 清洗摘要尚未生成。');
    const headline = [email.cleaning.companyName, email.cleaning.jobTitle].filter(Boolean).join(' · ') || '岗位信息待提取';
    const entryCount = email.jobEntries.length ? `${email.jobEntries.length} 个岗位条目` : '尚未形成岗位条目';
    const eventCount = email.events.length ? ` · ${email.events.length} 个事件` : '';
    const error = email.cleaning.error || text(email.processing_error);
    return `<article class="email-row status-${esc(email.status)}" data-email-id="${esc(email.id)}" data-email-status="${esc(email.status)}">
      <button class="email-row-main" type="button" data-open-email aria-label="打开邮件：${esc(email.subject || headline)}">
        <span class="email-row-top"><span class="email-sender">${esc(email.sender || '发件人待提取')}</span><span class="email-received">${esc(formatTime(email.receivedAt))}</span></span>
        <span class="email-row-title">${esc(email.subject || '无主题')}</span>
        <span class="email-row-headline">${esc(headline)}</span>
        <span class="email-row-summary">${esc(summary)}</span>
        <span class="email-row-meta">${statusTag(email.status)}<span class="muted-copy">${esc(entryCount + eventCount)}</span>${error ? `<span class="email-error-copy">${esc(error)}</span>` : ''}</span>
      </button>
      <div class="email-row-actions"><button class="text-button" type="button" data-open-email>查看详情</button></div>
    </article>`;
  }

  function filteredEmails() {
    const query = text(byId('emailSearch')?.value).trim().toLocaleLowerCase('zh-CN');
    const status = byId('emailStatusFilter')?.value || '';
    const account = byId('emailAccountFilter')?.value || '';
    return state.emails.filter((email) => {
      const accountId = String(first(email.email_account_id, email.emailAccountId, ''));
      return (!status || email.status === status) && (!account || accountId === account) && (!query || searchText(email).includes(query));
    });
  }

  function renderEmails() {
    const emails = filteredEmails();
    const count = byId('emailCount');
    if (count) count.textContent = state.emails.length === emails.length ? `共 ${emails.length} 封` : `显示 ${emails.length} / ${state.emails.length} 封`;
    if (!emails.length) {
      list.innerHTML = '<div class="empty email-empty"><strong>还没有符合条件的邮件</strong><p>在设置中添加邮箱并完成同步后，命中的邮件会先展示清洗进度，再显示岗位归档结果。</p><a class="button quiet" href="/settings">去配置邮箱</a></div>';
      return;
    }
    list.innerHTML = emails.map(renderEmailCard).join('');
  }

  function fieldRows(cleaning) {
    const fields = [
      ['公司', cleaning.companyName],
      ['岗位', cleaning.jobTitle],
      ['工作地点', cleaning.workLocation],
      ['事件时间', toDateValue(cleaning.eventTime)],
      ['行动截止', toDateValue(cleaning.deadline)],
      ['需要行动', cleaning.actionRequired ? '是' : '否'],
      ['建议行动', asArray(cleaning.actions).map(text).filter(Boolean).join('；')],
    ].filter(([, value]) => value !== undefined && value !== null && value !== '');
    return fields.map(([label, value]) => `<div><dt>${esc(label)}</dt><dd>${esc(value)}</dd></div>`).join('');
  }

  function renderLinks(cleaning) {
    if (!cleaning.links.length) return '';
    return `<div class="cleaning-links"><dt>相关链接</dt><dd>${cleaning.links.map((link) => `<a href="${esc(safeHref(link.url))}" target="_blank" rel="noopener noreferrer">${esc(link.label)}</a>`).join(' · ')}</dd></div>`;
  }

  function renderEvidence(cleaning) {
    const entries = Object.entries(cleaning.fieldEvidence || {}).filter(([, value]) => value !== undefined && value !== null && value !== '');
    if (!entries.length) return '';
    return `<details class="field-evidence"><summary>查看字段依据</summary><dl>${entries.map(([key, value]) => `<div><dt>${esc(key)}</dt><dd>${esc(typeof value === 'string' ? value : JSON.stringify(value))}</dd></div>`).join('')}</dl></details>`;
  }

  function renderCleaning(email) {
    const cleaning = email.cleaning;
    const body = cleaning.body;
    const title = cleaning.status ? `AI 清洗 · ${{ completed: '已完成', submitted: '已完成', pending: '待清洗' }[cleaning.status] || statusLabels[statusKey(cleaning.status)] || text(cleaning.status)}` : 'AI 清洗结果';
    return `<section class="detail-section cleaning-result" aria-labelledby="cleaningResultTitle">
      <div class="detail-section-head"><h3 id="cleaningResultTitle">${esc(title)}</h3>${statusTag(email.status)}</div>
      ${cleaning.summary ? `<p class="cleaning-summary">${esc(cleaning.summary)}</p>` : '<p class="muted-copy">清洗摘要尚未生成，邮件原文仍可查看。</p>'}
      ${body ? `<pre class="cleaned-body">${esc(body)}</pre>` : '<p class="empty-inline">暂无清洗正文。</p>'}
      ${renderEvidence(cleaning)}
    </section>`;
  }

  function renderStructured(email) {
    const cleaning = email.cleaning;
    const rows = fieldRows(cleaning);
    const links = renderLinks(cleaning);
    if (!rows && !links) return '<section class="detail-section"><h3>结构化信息</h3><p class="muted-copy">尚未提取结构化岗位信息。</p></section>';
    return `<section class="detail-section structured-result"><h3>结构化信息</h3><dl class="event-facts detail-facts">${rows}${links}</dl><p class="hint">这些字段来自邮件清洗结果，仅作为岗位归档依据；申请状态仍由你在 Tracker 手动管理。</p></section>`;
  }

  function candidateToken(entry, candidate, index) {
    const key = `${entry.id || 'entry'}:${index}`;
    state.candidateMap.set(key, candidate);
    return key;
  }

  function candidateLabel(candidate) {
    const company = candidate.companyName || '公司待确认';
    const job = candidate.jobTitle || '岗位待确认';
    const score = candidate.score !== undefined && candidate.score !== null && candidate.score !== '' ? ` · 匹配度 ${candidate.score}` : '';
    return `${company} · ${job}${score}`;
  }

  function entryAssociation(entry) {
    const candidates = entry.candidates;
    if (!candidates.length) return '<p class="muted-copy">暂无候选岗位；可先重新匹配，或在 Tracker 补充岗位后再次处理。</p>';
    const options = candidates.map((candidate, index) => `<option value="${esc(candidateToken(entry, candidate, index))}">${esc(candidateLabel(candidate))}</option>`).join('');
    return `<div class="entry-association"><label>修改关联<select data-entry-candidate aria-label="选择岗位"> <option value="">请选择岗位</option>${options}</select></label><button class="button primary" type="button" data-entry-associate data-entry-id="${esc(entry.id)}">确认关联</button></div>`;
  }

  function renderEntry(entry, index) {
    const company = entry.companyName || '公司待确认';
    const job = entry.jobTitle || '岗位待确认';
    const href = localJobHref(entry.jobId);
    const link = href ? `<a class="entry-job-link" href="${esc(href)}">查看岗位</a>` : '';
    const reasons = entry.reasons.length ? `<ul class="match-reasons">${entry.reasons.map((reason) => `<li>${esc(reason)}</li>`).join('')}</ul>` : '';
    const eventCount = entry.events.length || entry.eventIds.length;
    return `<article class="job-entry-card status-${esc(entry.status)}" data-entry-id="${esc(entry.id || index)}">
      <div class="job-entry-head"><div><span class="email-entry-index">岗位条目 ${index + 1}</span><h4>${esc(company)} · ${esc(job)}</h4></div>${statusTag(entry.status)} </div>
      <dl class="entry-facts"><div><dt>工作地点</dt><dd>${esc(entry.location || '未提取')}</dd></div><div><dt>岗位链接</dt><dd>${entry.link && safeHref(entry.link) ? `<a href="${esc(safeHref(entry.link))}" target="_blank" rel="noopener noreferrer">打开链接</a>` : '未提取'}</dd></div><div><dt>招聘事件</dt><dd>${esc(eventCount ? `${eventCount} 个` : '暂无')}</dd></div></dl>
      ${href ? `<p class="association-ok">已关联岗位 <a href="${esc(href)}">#${esc(entry.jobId)}</a>${entry.created ? ' · 本次自动新建' : ''}</p>` : ''}
      ${reasons}
      ${entryAssociation(entry)}
      <div class="entry-actions"><button class="text-button" type="button" data-entry-rematch data-entry-id="${esc(entry.id)}">重新匹配</button>${entry.manualLocked ? '<span class="muted-copy">人工关联已锁定</span>' : ''}</div>
    </article>`;
  }

  function renderEntries(email) {
    state.candidateMap.clear();
    if (!email.jobEntries.length) return '<section class="detail-section"><div class="detail-section-head"><h3>岗位条目</h3><span class="muted-copy">0 个</span></div><p class="muted-copy">后台尚未形成岗位条目；清洗结果会先展示，匹配完成后这里会更新。</p></section>';
    return `<section class="detail-section email-jobs-section"><div class="detail-section-head"><h3>岗位条目</h3><span class="muted-copy">${email.jobEntries.length} 个</span></div><div class="job-entry-list">${email.jobEntries.map(renderEntry).join('')}</div></section>`;
  }

  function renderEmailEvents(email) {
    if (!email.events.length) return '<section class="detail-section"><h3>招聘事件</h3><p class="muted-copy">这封邮件尚未生成招聘事件，或事件仍在处理中。</p></section>';
    return `<section class="detail-section email-event-section"><div class="detail-section-head"><h3>招聘事件</h3><span class="muted-copy">${email.events.length} 个</span></div><div class="email-event-list">${email.events.map((event) => `<article class="email-event"><span class="event-type">${esc(eventLabel(event.event_type || event.type))}</span><strong>${esc([event.company_name, event.job_title].filter(Boolean).join(' · ') || '事件信息待确认')}</strong><p>${esc(event.ai_summary || event.summary || '暂无摘要')}</p><span class="muted-copy">${esc(formatTime(event.event_time))} · ${esc(associationLabel(event.association_status))}</span></article>`).join('')}</div></section>`;
  }

  function renderOriginal(email) {
    const original = email.cleaning.originalBody || email.bodyText || email.bodyPreview;
    return `<details class="email-original"><summary>查看原文（纯文本）</summary><dl class="event-facts email-meta"><div><dt>主题</dt><dd>${esc(email.subject || '无主题')}</dd></div><div><dt>发件人</dt><dd>${esc(email.sender || '未提取')}</dd></div><div><dt>接收时间</dt><dd>${esc(formatTime(email.receivedAt))}</dd></div><div><dt>同步时间</dt><dd>${esc(formatTime(email.syncedAt))}</dd></div></dl><pre class="email-body">${esc(original || '（无正文）')}</pre><p class="hint">邮件内容以纯文本显示，系统不会执行邮件 HTML。</p></details>`;
  }

  function canAction(actions, name, fallback = true) {
    if (Array.isArray(actions)) return actions.includes(name);
    if (actions && typeof actions === 'object' && Object.prototype.hasOwnProperty.call(actions, name)) return Boolean(actions[name]);
    return fallback;
  }

  function setDialogButton(button, visible, disabled = false) {
    if (!button) return;
    button.hidden = !visible;
    button.disabled = disabled;
  }

  function updateDialogActions(email, loading = false) {
    const cleaningId = first(email?.cleaning?.id, email?.cleaning_result_id);
    const actions = email?.availableActions;
    // Legacy rows can exist before a cleaning result is created.  They still
    // need the message-level process entry point so the user can start the
    // workflow from the detail dialog.
    const canProcess = Boolean(email?.id);
    setDialogButton(byId('retryCleaning'), canProcess && canAction(actions, 'retry_cleaning', true), loading || !canAction(actions, 'retry_cleaning', true));
    setDialogButton(byId('reprocessEmail'), Boolean(cleaningId) && canAction(actions, 'reprocess', true), loading || !canAction(actions, 'reprocess', true));
    setDialogButton(byId('rematchEmail'), Boolean(email?.jobEntries.length), loading || !canAction(actions, 'rematch', true));
    setDialogButton(byId('retryArchive'), Boolean(cleaningId), loading || !canAction(actions, 'archive', true));
    setDialogButton(byId('deleteEmail'), Boolean(email), loading);
  }

  function showDialog(dialog) {
    if (!dialog) return;
    if (typeof dialog.showModal === 'function') {
      if (!dialog.open) dialog.showModal();
    } else dialog.setAttribute('open', '');
  }

  function closeDialog(dialog) {
    if (!dialog) return;
    if (typeof dialog.close === 'function') dialog.close();
    else dialog.removeAttribute('open');
  }

  function renderEmailDetail(email) {
    const dialogTitle = byId('emailDialogTitle');
    const subtitle = byId('emailDialogSubtitle');
    const status = byId('emailDialogStatus');
    const content = byId('emailContent');
    if (!content) return;
    if (dialogTitle) dialogTitle.textContent = email.subject || '邮件详情';
    if (subtitle) subtitle.textContent = `${email.sender || '发件人待提取'} · ${formatTime(email.receivedAt)}`;
    if (status) status.outerHTML = `<span id="emailDialogStatus" class="tag dialog-status" data-status="${esc(email.status)}">${esc(email.statusLabel)}</span>`;
    content.innerHTML = renderCleaning(email) + renderStructured(email) + renderEntries(email) + renderEmailEvents(email) + renderOriginal(email);
    updateDialogActions(email);
  }

  function renderDetailLoading(email) {
    const dialogTitle = byId('emailDialogTitle');
    const subtitle = byId('emailDialogSubtitle');
    const status = byId('emailDialogStatus');
    const content = byId('emailContent');
    if (dialogTitle) dialogTitle.textContent = email?.subject || '邮件详情';
    if (subtitle) subtitle.textContent = email ? `${email.sender || '发件人待提取'} · ${formatTime(email.receivedAt)}` : '';
    if (status) status.textContent = email ? (statusLabels[email.status] || '读取中') : '读取中';
    if (content) content.innerHTML = '<p class="muted-copy">正在读取清洗结果、岗位条目和招聘事件…</p>';
    updateDialogActions(email, true);
  }

  async function refreshCurrentDetail(id, requestToken = state.detailRequestToken) {
    const emailId = encodeURIComponent(id);
    const detail = normalizeEmail(await request(`/email-messages/${emailId}`));
    if (requestToken !== state.detailRequestToken || String(state.currentEmailId) !== String(id)) return null;
    // Corrections must be possible even when the extracted company is wrong
    // and the desired job does not appear among fuzzy candidates.
    const [jobsResult, companiesResult, applicationsResult] = await Promise.allSettled([
      request('/jobs'), request('/companies'), request('/applications'),
    ]);
    if (requestToken !== state.detailRequestToken || String(state.currentEmailId) !== String(id)) return null;
    // Candidate data is already returned by the email API.  These optional
    // reads only widen the selector to every local Job; a failed helper API
    // must never hide an otherwise readable email detail.
    const jobs = jobsResult.status === 'fulfilled' ? unwrapCollection(jobsResult.value, ['items', 'jobs']) : [];
    const companies = companiesResult.status === 'fulfilled' ? unwrapCollection(companiesResult.value, ['items', 'companies']) : [];
    const applications = applicationsResult.status === 'fulfilled' ? unwrapCollection(applicationsResult.value, ['items', 'applications']) : [];
    const companyById = new Map(asArray(companies).map((company) => [Number(company.id), company]));
    const applicationByJobId = new Map(asArray(applications).map((application) => [Number(application.job_id), application]));
    for (const entry of detail.jobEntries) {
      const known = new Set(entry.candidates.map((candidate) => candidate.jobId));
      for (const job of asArray(jobs)) {
        if (!job || !job.id || known.has(Number(job.id))) continue;
        entry.candidates.push(normalizeCandidate({ job_id: job.id, company_id: job.company_id,
          company_name: companyById.get(Number(job.company_id))?.name,
          job_title: job.title, application_id: applicationByJobId.get(Number(job.id))?.id }));
      }
    }
    if (requestToken !== state.detailRequestToken || String(state.currentEmailId) !== String(id)) return null;
    state.currentEmail = detail;
    state.currentEmailId = detail.id;
    renderEmailDetail(detail);
    return detail;
  }

  async function openEmailDialog(id) {
    const email = state.emails.find((item) => String(item.id) === String(id)) || normalizeEmail({ id });
    state.currentEmail = email;
    state.currentEmailId = email.id;
    const requestToken = ++state.detailRequestToken;
    renderDetailLoading(email);
    showDialog(byId('emailDialog'));
    state.loadingDetail = true;
    try {
      await refreshCurrentDetail(id, requestToken);
    } catch (error) {
      if (requestToken !== state.detailRequestToken || String(state.currentEmailId) !== String(id)) return;
      const content = byId('emailContent');
      if (content) content.innerHTML = `<p class="feedback error">读取失败：${esc(error.message)}</p>`;
      updateDialogActions(email);
    } finally {
      if (requestToken === state.detailRequestToken) state.loadingDetail = false;
    }
  }

  function setActionLoading(button, loading, label) {
    if (!button) return;
    if (loading) {
      button.dataset.originalLabel = button.textContent;
      button.disabled = true;
      button.classList.add('is-loading');
      button.textContent = label;
    } else {
      button.disabled = false;
      button.classList.remove('is-loading');
      button.textContent = button.dataset.originalLabel || button.textContent;
    }
  }

  async function runCleaningAction(action, button) {
    const email = state.currentEmail;
    if (!email) return;
    const cleaningId = first(email.cleaning?.id, email.cleaning_result_id);
    const path = cleaningId ? `/email-cleaning/${encodeURIComponent(cleaningId)}/${action}` : `/email-messages/${encodeURIComponent(email.id)}/process`;
    setActionLoading(button, true, action === 'reprocess' ? '重新清洗中…' : '重试中…');
    try {
      await request(path, { method: 'POST' });
      setFeedback('emailFeedback', action === 'reprocess' ? '已提交新的清洗版本，结果生成后会自动更新。' : '已提交清洗重试，结果生成后会自动更新。');
      await loadEmails({ quiet: true });
      await refreshCurrentDetail(email.id);
    } catch (error) {
      setFeedback('emailFeedback', `操作失败：${error.message}`, true);
    } finally {
      setActionLoading(button, false);
      updateDialogActions(state.currentEmail || email);
    }
  }

  async function runArchiveAction(button) {
    const email = state.currentEmail;
    const cleaningId = first(email?.cleaning?.id, email?.cleaning_result_id);
    if (!email || !cleaningId) return;
    setActionLoading(button, true, '归档中…');
    try {
      await request(`/email-cleaning/${encodeURIComponent(cleaningId)}/archive`, { method: 'POST' });
      setFeedback('emailFeedback', '已提交岗位归档重试。');
      await loadEmails({ quiet: true });
      await refreshCurrentDetail(email.id);
    } catch (error) {
      setFeedback('emailFeedback', `归档失败：${error.message}`, true);
    } finally {
      setActionLoading(button, false);
      updateDialogActions(state.currentEmail || email);
    }
  }

  async function rematchEntry(entryId, button) {
    if (!entryId) return;
    setActionLoading(button, true, '匹配中…');
    try {
      await request(`/email-job-entries/${encodeURIComponent(entryId)}/rematch`, { method: 'POST' });
      setFeedback('emailFeedback', '已提交重新匹配，结果会自动更新。');
      await loadEmails({ quiet: true });
      if (state.currentEmailId) await refreshCurrentDetail(state.currentEmailId);
    } catch (error) {
      setFeedback('emailFeedback', `重新匹配失败：${error.message}`, true);
    } finally {
      setActionLoading(button, false);
    }
  }

  function associationPayload(candidate) {
    const payload = {};
    if (candidate.jobId) payload.job_id = candidate.jobId;
    if (candidate.companyId) payload.company_id = candidate.companyId;
    if (candidate.applicationId) payload.application_id = candidate.applicationId;
    return payload;
  }

  async function associateEntry(entryId, control, button) {
    const candidate = state.candidateMap.get(control?.value);
    if (!entryId || !candidate) {
      setFeedback('emailFeedback', '请先选择要关联的岗位。', true);
      return;
    }
    const payload = associationPayload(candidate);
    if (!Object.keys(payload).length) {
      setFeedback('emailFeedback', '候选岗位缺少可用 ID，暂时无法关联。', true);
      return;
    }
    setActionLoading(button, true, '保存中…');
    try {
      await request(`/email-job-entries/${encodeURIComponent(entryId)}/association`, { method: 'PATCH', body: JSON.stringify(payload) });
      setFeedback('emailFeedback', '岗位关联已保存；申请状态没有被修改。');
      await loadEmails({ quiet: true });
      if (state.currentEmailId) await refreshCurrentDetail(state.currentEmailId);
    } catch (error) {
      setFeedback('emailFeedback', `关联失败：${error.message}`, true);
    } finally {
      setActionLoading(button, false);
    }
  }

  async function rematchAll(button) {
    const entries = state.currentEmail?.jobEntries || [];
    if (!entries.length) return;
    setActionLoading(button, true, '匹配中…');
    try {
      for (const entry of entries) {
        if (entry.id) await request(`/email-job-entries/${encodeURIComponent(entry.id)}/rematch`, { method: 'POST' });
      }
      setFeedback('emailFeedback', '已提交全部岗位条目的重新匹配。');
      await loadEmails({ quiet: true });
      await refreshCurrentDetail(state.currentEmailId);
    } catch (error) {
      setFeedback('emailFeedback', `重新匹配失败：${error.message}`, true);
    } finally {
      setActionLoading(button, false);
    }
  }

  async function deleteCurrentEmail() {
    const email = state.currentEmail;
    if (!email) return;
    const confirmText = '删除这封邮件的本地记录？已经自动创建的岗位会保留；邮箱服务器上的原邮件不受影响。';
    if (typeof window.confirm === 'function' && !window.confirm(confirmText)) return;
    const button = byId('deleteEmail');
    setActionLoading(button, true, '删除中…');
    try {
      const emailId = encodeURIComponent(email.id);
      const result = await request(`/email-messages/${emailId}`, { method: 'DELETE' });
      closeDialog(byId('emailDialog'));
      setFeedback('emailFeedback', `已删除本地邮件${result?.event_id ? '及其旧事件' : ''}；已创建岗位保留。`);
      state.currentEmail = null;
      state.currentEmailId = null;
      await loadEmails({ quiet: true });
    } catch (error) {
      setFeedback('emailFeedback', `删除失败：${error.message}`, true);
      setActionLoading(button, false);
    }
  }

  async function loadEmails(options = {}) {
    if (state.loadingEmails) return;
    state.loadingEmails = true;
    if (!options.quiet) setFeedback('emailFeedback', '正在读取邮件…');
    try {
      const payload = await request('/email-messages?limit=500');
      state.emails = unwrapCollection(payload, ['items', 'messages', 'email_messages']).map(normalizeEmail);
      renderAccountFilter();
      renderEmails();
      if (!options.quiet) setFeedback('emailFeedback', `共 ${state.emails.length} 封邮件；清洗和岗位归档状态会自动更新。`);
      if (state.currentEmailId && !state.loadingDetail) {
        const current = state.emails.find((item) => String(item.id) === String(state.currentEmailId));
        if (current) state.currentEmail = current;
      }
      schedulePolling();
    } catch (error) {
      state.emails = [];
      renderEmails();
      setFeedback('emailFeedback', `读取邮件失败：${error.message}`, true);
    } finally {
      state.loadingEmails = false;
    }
  }

  function shouldPoll() {
    return state.emails.some((email) => ['processing'].includes(email.status) || statusKey(email.archive_status) === 'pending');
  }

  function schedulePolling() {
    if (state.pollTimer) clearTimeout(state.pollTimer);
    if (!shouldPoll()) return;
    state.pollTimer = setTimeout(async () => {
      state.pollTimer = null;
      await loadEmails({ quiet: true });
      if (state.currentEmailId && !state.loadingDetail) {
        try { await refreshCurrentDetail(state.currentEmailId); } catch { /* list status remains useful when detail is temporarily unavailable */ }
      }
    }, 5000);
  }

  function renderLegacyEvent(event) {
    const candidates = asArray(event.candidates).map(normalizeCandidate);
    const candidateOptions = candidates.map((candidate, index) => `<option value="${esc(candidate.applicationId || candidate.jobId || '')}" data-candidate-index="${index}">${esc(candidateLabel(candidate))}</option>`).join('');
    const association = event.application_id
      ? `<p class="association-ok">已关联 Application #${esc(event.application_id)}</p>`
      : candidates.length
        ? `<div class="association-controls"><select aria-label="选择要关联的申请"><option value="">请选择候选申请</option>${candidateOptions}</select><button class="button primary" type="button" data-associate>确认关联</button><button class="button quiet" type="button" data-dismiss>保持未关联</button></div>`
        : '<p class="muted-copy">暂未找到可靠候选，可在邮件详情中重新匹配。</p>';
    return `<article class="event-card${event.source_email_id ? ' has-email' : ''}" data-event-id="${esc(event.id)}"${event.source_email_id ? ` data-email-id="${esc(event.source_email_id)}"` : ''}>
      <div class="event-card-head"><div><span class="event-type">${esc(eventLabel(event.event_type))}</span><h2>${esc(event.company_name || '公司待确认')} · ${esc(event.job_title || '岗位待确认')}</h2></div>${associationTag(event.association_status || 'unmatched')}</div>
      <p class="event-summary">${esc(event.ai_summary || '暂无摘要')}</p>
      <dl class="event-facts"><div><dt>事件时间</dt><dd>${esc(formatTime(event.event_time))}</dd></div><div><dt>截止时间</dt><dd>${esc(formatTime(event.deadline))}</dd></div><div><dt>地点 / 会议</dt><dd>${esc(event.location || event.meeting_url || '未提取')}</dd></div><div><dt>置信度</dt><dd>${event.confidence === undefined ? '未提取' : `${Math.round(Number(event.confidence) * 100)}%`}</dd></div></dl>
      ${event.suggested_application_status ? `<p class="advisory">AI 建议状态：${esc(event.suggested_application_status)}（仅供参考，不会自动修改）</p>` : ''}${association}
      <div class="event-actions"><span class="muted-copy view-hint">${event.source_email_id ? '点击查看来源邮件' : ''}</span>${event.source_email_id ? '<button class="text-button" type="button" data-view-email>查看邮件</button>' : ''}<button class="text-button" type="button" data-notify>按策略发送通知</button></div>
    </article>`;
  }

  function renderEvents(events) {
    const target = byId('eventList');
    if (!target) return;
    if (!events.length) {
      target.innerHTML = '<div class="empty compact-empty"><strong>没有符合条件的招聘事件</strong><p>事件会随邮件清洗结果和旧数据兼容记录显示在这里。</p></div>';
      return;
    }
    target.innerHTML = events.map(renderLegacyEvent).join('');
  }

  async function loadEvents() {
    const target = byId('eventList');
    if (!target || state.loadingEvents) return;
    state.loadingEvents = true;
    setFeedback('eventFeedback', '正在读取历史事件…');
    try {
      const params = new URLSearchParams();
      const type = byId('eventTypeFilter')?.value;
      const association = byId('associationFilter')?.value;
      if (type) params.set('event_type', type);
      if (association) params.set('association_status', association);
      const payload = await request(`/recruitment-events${params.size ? `?${params}` : ''}`);
      state.events = unwrapCollection(payload, ['items', 'events', 'recruitment_events']).map(details);
      state.eventsLoaded = true;
      renderEvents(state.events);
      setFeedback('eventFeedback', `共 ${state.events.length} 个历史事件。申请状态仍由你在 Tracker 手动管理。`);
    } catch (error) {
      setFeedback('eventFeedback', `读取事件失败：${error.message}`, true);
    } finally {
      state.loadingEvents = false;
    }
  }

  async function handleLegacyEventAction(event) {
    const card = event.target.closest('[data-event-id]');
    if (!card) return;
    const eventId = card.dataset.eventId;
    if (event.target.closest('[data-view-email]')) {
      const emailId = card.dataset.emailId;
      if (emailId) await openEmailDialog(emailId);
      return;
    }
    try {
      if (event.target.closest('[data-associate]')) {
        const select = card.querySelector('select');
        const applicationId = numberOrNull(select?.value);
        if (!applicationId) throw new Error('请先选择一个候选申请。');
        await request(`/recruitment-events/${encodeURIComponent(eventId)}/association`, { method: 'PATCH', body: JSON.stringify({ application_id: applicationId }) });
        setFeedback('eventFeedback', '关联已确认；申请状态没有被修改。');
      } else if (event.target.closest('[data-dismiss]')) {
        await request(`/recruitment-events/${encodeURIComponent(eventId)}/association`, { method: 'PATCH', body: JSON.stringify({ dismiss: true }) });
        setFeedback('eventFeedback', '事件已保持未关联。');
      } else if (event.target.closest('[data-notify]')) {
        const sent = await request(`/recruitment-events/${encodeURIComponent(eventId)}/notify`, { method: 'POST' });
        setFeedback('eventFeedback', Array.isArray(sent) && sent.length ? '通知处理完成，可在通知中心查看回执。' : '该事件不属于当前即时通知策略。');
      } else return;
      await loadEvents();
    } catch (error) {
      setFeedback('eventFeedback', `操作失败：${error.message}`, true);
    }
  }

  list.addEventListener('click', (event) => {
    const opener = event.target.closest('[data-open-email]');
    if (opener) {
      const card = opener.closest('[data-email-id]');
      if (card) openEmailDialog(card.dataset.emailId);
    }
  });

  byId('emailContent')?.addEventListener('click', (event) => {
    const rematch = event.target.closest('[data-entry-rematch]');
    if (rematch) {
      rematchEntry(rematch.dataset.entryId, rematch);
      return;
    }
    const associate = event.target.closest('[data-entry-associate]');
    if (associate) {
      const entry = associate.closest('.job-entry-card');
      associateEntry(associate.dataset.entryId, entry?.querySelector('[data-entry-candidate]'), associate);
    }
  });

  byId('retryCleaning')?.addEventListener('click', (event) => runCleaningAction('retry', event.currentTarget));
  byId('reprocessEmail')?.addEventListener('click', (event) => runCleaningAction('reprocess', event.currentTarget));
  byId('retryArchive')?.addEventListener('click', (event) => runArchiveAction(event.currentTarget));
  byId('rematchEmail')?.addEventListener('click', (event) => rematchAll(event.currentTarget));
  byId('deleteEmail')?.addEventListener('click', deleteCurrentEmail);
  byId('refreshEmails')?.addEventListener('click', () => loadEmails());
  byId('emailStatusFilter')?.addEventListener('change', renderEmails);
  byId('emailAccountFilter')?.addEventListener('change', renderEmails);
  byId('emailSearch')?.addEventListener('input', () => {
    window.clearTimeout(state.searchTimer);
    state.searchTimer = window.setTimeout(renderEmails, 100);
  });
  byId('eventList')?.addEventListener('click', handleLegacyEventAction);
  byId('refreshEvents')?.addEventListener('click', loadEvents);
  byId('eventTypeFilter')?.addEventListener('change', loadEvents);
  byId('associationFilter')?.addEventListener('change', loadEvents);
  document.querySelector('.legacy-events-panel')?.addEventListener('toggle', (event) => {
    if (event.target.open && !state.eventsLoaded) loadEvents();
  });
  byId('emailDialog')?.addEventListener('close', () => {
    state.detailRequestToken += 1;
    state.currentEmail = null;
    state.currentEmailId = null;
  });
  document.addEventListener('visibilitychange', () => {
    if (document.hidden && state.pollTimer) {
      clearTimeout(state.pollTimer);
      state.pollTimer = null;
    } else if (!document.hidden) schedulePolling();
  });

  window.emailInbox = {
    normalizeEmail,
    renderEmails,
    renderEmailDetail,
    loadEmails,
    loadEvents,
    statusLabels,
  };
  loadEmails();
  loadEvents();
})();
