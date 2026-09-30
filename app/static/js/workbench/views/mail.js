import { arr, buttonBusy, collection, esc, first, fmtTime, htmlRows, jsonOptions, object, positiveId, setFeedback, text } from '../integrations/shared.js';
import { mount as mountNotifications } from './notifications.js';

const STATUS = { processing: '处理中', linked: '已关联', created: '已新建', pending: '待处理', failed: '失败', filtered: '未命中筛选' };
const EVENT = { APPLICATION_RECEIVED: '收到回复', ASSESSMENT: '测评', WRITTEN_TEST: '笔试', INTERVIEW: '面试', INTERVIEW_CHANGED: '面试变更', OFFER: 'Offer', REJECTED: '拒绝', RECRUITMENT_MARKETING: '招聘营销', OTHER: '其他' };
const ASSOCIATION = { unmatched: '待匹配', suggested: '待确认', confirmed: '已确认', dismissed: '保持未关联', auto_bound: '自动关联', manual: '人工关联', linked: '已关联' };
const token = (value) => text(value).toLowerCase().replace(/[\s-]+/g, '_');
const statusOf = (email) => {
  const raw = token(first(email.status, email.processing_status, email.cleaning_status, email.archive_status));
  if (['running', 'queued', 'processing', 'cleaning', 'pending_cleaning', 'archiving'].includes(raw)) return 'processing';
  if (['failed', 'error', 'cleaning_failed', 'archive_failed', 'match_failed'].includes(raw) || email.processing_error) return 'failed';
  if (['linked', 'associated', 'confirmed', 'auto_bound', 'manual'].includes(raw)) return 'linked';
  if (['created', 'auto_created', 'job_created', 'archived'].includes(raw)) return 'created';
  if (raw === 'filtered' || raw === 'regex_skipped' || raw === 'skipped') return 'filtered';
  return 'pending';
};
const normalizeCandidate = (candidate) => {
  const item = object(candidate); const job = object(item.job); const company = object(item.company);
  return { ...item, jobId: positiveId(first(item.job_id, item.jobId, job.id)), companyId: positiveId(first(item.company_id, item.companyId, company.id)), applicationId: positiveId(first(item.application_id, item.applicationId)), companyName: text(first(item.company_name, company.name)), jobTitle: text(first(item.job_title, job.title, item.title)) };
};
const normalizeEntry = (entry) => {
  const raw = object(entry); const job = object(raw.job); const company = object(raw.company);
  return { ...raw, id: first(raw.id, raw.entry_id, raw.job_entry_id), companyId: positiveId(first(raw.company_id, company.id)), jobId: positiveId(first(raw.job_id, job.id)), applicationId: positiveId(raw.application_id), companyName: text(first(raw.company_name, company.name)), jobTitle: text(first(raw.job_title, job.title)), location: text(first(raw.work_location, raw.location, job.location)), candidates: arr(first(raw.candidates, raw.candidate_jobs, raw.match_candidates)).map(normalizeCandidate), events: arr(raw.events), status: text(first(raw.status, raw.archive_status, raw.association_status, raw.match_status)) || 'pending', manualLocked: Boolean(first(raw.manual_locked, raw.association_locked, raw.manually_associated, false)), reasons: arr(first(raw.match_reasons, raw.reasons, raw.evidence)).map(text).filter(Boolean), version: first(raw.version, raw.entry_version) };
};
const normalizeEmail = (raw) => {
  const source = object(raw); const cleaning = object(first(source.cleaning, source.cleaning_result, source.cleaningResult));
  const entries = arr(first(source.job_entries, source.jobEntries, source.entries, source.cleaning?.job_entries)).map(normalizeEntry);
  return { ...source, id: positiveId(source.id) || source.id, sender: text(source.sender), subject: text(source.subject), receivedAt: first(source.received_at, source.receivedAt), bodyPreview: text(first(source.body_preview, source.preview, source.snippet)), bodyText: text(first(source.body_text, source.original_body_text)), bodyRetained: source.body_retained == null ? Boolean(first(source.body_text, source.original_body_text)) : Boolean(source.body_retained), cleaning: { ...cleaning, id: first(cleaning.id, cleaning.cleaning_result_id, source.cleaning_result_id), version: first(cleaning.version, source.cleaning_version), body: text(first(cleaning.body, cleaning.cleaned_body, source.cleaned_body, source.cleaned_body_text)), summary: text(first(cleaning.summary, cleaning.ai_summary, source.cleaning_summary, source.summary)), originalBody: text(first(source.original_body_text, source.body_text)), company: first(cleaning.company, source.company), companyName: text(first(cleaning.company_name, source.company_name, object(cleaning.company).name)), jobTitle: text(first(cleaning.job_title, source.job_title, object(cleaning.job).title)), workLocation: text(first(cleaning.work_location, cleaning.location, source.work_location)), fieldEvidence: object(first(cleaning.field_evidence, cleaning.evidence, source.field_evidence)), links: arr(first(cleaning.links, source.links)) }, jobEntries: entries, events: arr(first(source.events, source.recruitment_events)), availableActions: first(source.available_actions, source.workflow_actions) };
};

function mailMarkup() {
  return `<div class="wb-integrations" data-mail-view>
    <header class="wb-view-head"><div><h1>邮件与提醒</h1><p>邮件原文、清洗、岗位关联和招聘事件在同一处核对。</p></div><div class="wb-head-actions"><button class="wb-button" data-action="manual-email">手动导入邮件</button><button class="wb-button" data-action="refresh">刷新</button></div></header>
    <nav class="wb-tabs" aria-label="邮件与提醒分区"><button type="button" data-tab="inbox" aria-current="page">邮件收件箱</button><button type="button" data-tab="events">招聘事件</button><button type="button" data-tab="notifications">通知历史</button></nav>
    <section data-panel="inbox"><div class="wb-toolbar"><label class="wb-search">搜索邮件<input type="search" data-filter="query" placeholder="主题、发件人、公司或岗位"></label><label>处理状态<select data-filter="status"><option value="">全部状态</option><option value="processing">处理中</option><option value="linked">已关联</option><option value="created">已新建</option><option value="pending">待处理</option><option value="failed">失败</option><option value="filtered">未命中筛选</option></select></label><label>来源账户<select data-filter="account"><option value="">全部账户</option></select></label><label>每页数量<select data-filter="limit"><option value="100">100 封</option><option value="250">250 封</option><option value="500" selected>500 封</option></select></label><span class="wb-count" data-count></span></div><p class="wb-hint">搜索和状态筛选仅作用于当前页；页码读取来自服务端排序后的完整邮件列表。</p><p class="wb-feedback" data-feedback role="status" aria-live="polite"></p><div class="wb-mail-list" data-email-list aria-live="polite"></div><div class="wb-pagination"><button class="wb-button" type="button" data-page="previous" disabled>上一页</button><span data-page-readout>第 1 页</span><button class="wb-button" type="button" data-page="next" disabled>下一页</button></div></section>
    <section data-panel="events" hidden><div class="wb-toolbar"><label>事件类型<select data-event-filter="type"><option value="">全部事件类型</option>${Object.entries(EVENT).map(([key, label]) => `<option value="${key}">${label}</option>`).join('')}</select></label><label>关联状态<select data-event-filter="association"><option value="">全部关联状态</option><option value="unmatched">待匹配</option><option value="suggested">待确认</option><option value="auto_bound">自动关联</option><option value="confirmed">已确认</option><option value="dismissed">保持未关联</option></select></label><button class="wb-button" data-action="refresh-events">刷新事件</button></div><p class="wb-feedback" data-event-feedback role="status" aria-live="polite"></p><div class="wb-event-list" data-event-list aria-live="polite"></div></section>
    <section data-panel="notifications" hidden><div data-notifications-panel></div></section>
    <dialog class="wb-dialog wb-mail-dialog" data-dialog="email" aria-labelledby="wbEmailTitle"><div class="wb-dialog-head"><div><p class="wb-eyebrow">邮件详情</p><h2 data-email-title id="wbEmailTitle">邮件详情</h2><p data-email-subtitle class="wb-muted"></p></div><button class="wb-icon-button" type="button" data-close aria-label="关闭">×</button></div><p class="wb-feedback" data-detail-feedback role="status" aria-live="polite"></p><div class="wb-email-detail" data-email-content></div><div class="wb-dialog-actions"><button class="wb-button" data-email-action="retry">重试清洗</button><button class="wb-button" data-email-action="reprocess">重新清洗（新版本）</button><button class="wb-button" data-email-action="rematch">全部重新匹配</button><button class="wb-button" data-email-action="archive">重试本地归档</button><button class="wb-button wb-danger" data-email-action="delete">删除本地邮件</button></div></dialog>
    <dialog class="wb-dialog" data-dialog="manual-email" aria-labelledby="wbManualEmailTitle"><form data-form="manual-email"><div class="wb-dialog-head"><div><p class="wb-eyebrow">手动导入</p><h2 id="wbManualEmailTitle">手动加入指定邮件</h2></div><button class="wb-icon-button" type="button" data-close aria-label="关闭">×</button></div><label>来源邮箱<select name="account_id" required data-manual-accounts></select></label><div class="wb-form-grid"><label>发件人<input name="sender" maxlength="500" required></label><label>主题<input name="subject" maxlength="1000" required></label></div><label>Message-ID（可选）<input name="message_id" maxlength="998"></label><label>正文（可选）<textarea name="body_text" rows="6" placeholder="粘贴邮件正文文本"></textarea></label><p class="wb-hint">此入口调用现有邮件识别流程，需要可用的邮件解析模型；手动导入不会自动修改投递状态。</p><p class="wb-feedback" data-manual-feedback role="status"></p><div class="wb-dialog-actions"><button class="wb-button" type="button" data-close>取消</button><button class="wb-button wb-primary" type="submit">导入并识别</button></div></form></dialog>
    <dialog class="wb-dialog" data-dialog="manual-facts" aria-labelledby="wbManualFactsTitle"><form data-form="manual-facts"><div class="wb-dialog-head"><div><p class="wb-eyebrow">逐条邮件事实</p><h2 id="wbManualFactsTitle">人工核对岗位信息</h2></div><button class="wb-icon-button" type="button" data-close aria-label="关闭">×</button></div><input type="hidden" name="entry_id"><label>公司<input name="company_name" maxlength="300" required></label><div class="wb-form-grid"><label>岗位<input name="job_title" maxlength="500" required></label><label>工作地点<input name="work_location" maxlength="300"></label></div><div class="wb-form-grid"><label>邮件事件<select name="event_type" required><option value="">选择邮件中明确的事件</option>${Object.entries(EVENT).map(([key, label]) => `<option value="${key}">${label}</option>`).join('')}</select></label><label>面试轮次（无关则留空）<input name="event_round" maxlength="50" placeholder="例如：一面、二面、HR 面"></label></div><label>原文依据<textarea name="field_evidence" rows="5" required placeholder="粘贴能支持以上事实的邮件原文片段"></textarea></label><p class="wb-hint" data-facts-hint></p><p class="wb-feedback" data-facts-feedback role="status"></p><div class="wb-dialog-actions"><button class="wb-button" type="button" data-close>取消</button><button class="wb-button wb-primary" type="submit">保存人工事实</button></div></form></dialog>
  </div>`;
}

export function mount({ root, route, navigate, api, notify = () => {} }) {
  root.innerHTML = mailMarkup();
  const q = (selector) => root.querySelector(selector);
  const inbox = q('[data-panel="inbox"]');
  const split = document.createElement('div');
  split.className = 'wb-mail-split';
  const listColumn = document.createElement('div');
  listColumn.className = 'wb-mail-list-column';
  listColumn.append(q('[data-email-list]'), q('.wb-pagination'));
  const detailColumn = document.createElement('div');
  detailColumn.className = 'wb-mail-detail-column';
  detailColumn.innerHTML = '<div class="wb-mail-placeholder" data-email-placeholder><strong>选择一封邮件</strong><p>在右侧核对清洗结果、岗位条目、招聘事件与原文依据。</p></div>';
  detailColumn.append(q('[data-dialog="email"]'));
  split.append(listColumn, detailColumn);
  inbox.append(split);
  const state = { emails: [], events: [], eventsLoaded: false, accounts: [], jobs: [], companies: [], applications: [], email: null, tab: 'inbox', limit: 500, offset: 0, listSeq: 0, eventSeq: 0, detailSeq: 0, detailController: null, controllers: new Set(), cleaned: false, routeParams: new URLSearchParams(route?.params || '') };
  const notificationPanel = mountNotifications({ root: q('[data-notifications-panel]'), route, navigate, api, notify, autoLoad: false });
  const updateRoute = (params) => { state.routeParams = params; if (navigate) navigate({ view: 'mail', params, replace: true, refresh: false }); };
  const request = async (path, options = {}) => api(path, options);
  const track = () => { const controller = new AbortController(); state.controllers.add(controller); return controller; };
  const stopController = (controller) => { state.controllers.delete(controller); };
  const safeLink = (value) => { if (typeof value !== 'string' || !value.trim()) return ''; try { const url = new URL(value, location.origin); return ['http:', 'https:'].includes(url.protocol) ? url.href : ''; } catch { return ''; } };
  const feedback = (selector, message, kind = 'info') => { if (!state.cleaned) setFeedback(root, selector, message, kind); };
  const emailSearchText = (email) => [email.sender, email.subject, email.bodyPreview, email.cleaning.summary, email.cleaning.companyName, email.cleaning.jobTitle, ...email.jobEntries.flatMap((entry) => [entry.companyName, entry.jobTitle])].join(' ').toLocaleLowerCase('zh-CN');
  function renderEmailRows() {
    const query = text(q('[data-filter="query"]').value).trim().toLocaleLowerCase('zh-CN'); const status = q('[data-filter="status"]').value; const account = q('[data-filter="account"]').value;
    const rows = state.emails.filter((item) => (!status || statusOf(item) === status) && (!account || String(first(item.email_account_id, item.emailAccountId, '')) === account) && (!query || emailSearchText(item).includes(query)));
    q('[data-count]').textContent = rows.length === state.emails.length ? `本页 ${rows.length} 封` : `本页显示 ${rows.length} / ${state.emails.length} 封`;
    q('[data-page-readout]').textContent = `第 ${Math.floor(state.offset / state.limit) + 1} 页`;
    q('[data-page="previous"]').disabled = state.offset === 0;
    q('[data-page="next"]').disabled = state.emails.length < state.limit;
    q('[data-email-list]').innerHTML = rows.length ? rows.map((email) => `<article class="wb-mail-card${String(state.email?.id) === String(email.id) ? ' is-selected' : ''}" data-email-row="${esc(email.id)}"><button class="wb-mail-open" type="button" data-open-email="${esc(email.id)}"><span class="wb-mail-meta"><strong>${esc(email.sender || '发件人待提取')}</strong><time>${esc(fmtTime(first(email.received_at, email.receivedAt)) )}</time></span><strong class="wb-mail-subject">${esc(email.subject || '无主题')}</strong><span class="wb-mail-summary">${esc(email.cleaning.summary || email.bodyPreview || '暂无摘要')}</span><span class="wb-chip status-${esc(statusOf(email))}">${esc(STATUS[statusOf(email)])}</span><span class="wb-mail-meta"><span>${email.jobEntries.length} 个岗位条目 · ${email.events.length} 个事件</span>${email.processing_error ? `<span class="wb-error-copy">${esc(email.processing_error)}</span>` : ''}</span></button></article>`).join('') : '<div class="wb-empty"><strong>没有符合条件的邮件</strong><p>先配置并同步招聘邮箱，或导入一封需要识别的邮件。</p></div>';
  }
  async function loadEmails(quiet = false) {
    const seq = ++state.listSeq; const controller = track(); if (!quiet) feedback('[data-feedback]', '正在读取邮件…');
    try {
      const [payload, accountPayload] = await Promise.all([request(`/email-messages?limit=${state.limit}&offset=${state.offset}`, { signal: controller.signal }), request('/email-accounts', { signal: controller.signal })]);
      if (state.cleaned || seq !== state.listSeq) return;
      state.emails = collection(payload, ['items', 'messages', 'email_messages']).map(normalizeEmail); state.accounts = collection(accountPayload, ['items', 'accounts']);
      const selected = q('[data-filter="account"]').value; q('[data-filter="account"]').innerHTML = '<option value="">全部账户</option>' + state.accounts.map((account) => `<option value="${esc(account.id)}">${esc(account.email_address)}</option>`).join('');
      if (state.accounts.some((item) => String(item.id) === selected)) q('[data-filter="account"]').value = selected;
      q('[data-manual-accounts]').innerHTML = state.accounts.map((item) => `<option value="${esc(item.id)}">${esc(item.email_address)}</option>`).join('');
      renderEmailRows(); if (!quiet) feedback('[data-feedback]', `读取到 ${state.emails.length} 封邮件。`);
    } catch (error) { if (error.name === 'AbortError' || state.cleaned) return; feedback('[data-feedback]', `读取邮件失败：${error.message}`, 'error'); }
    finally { stopController(controller); }
  }
  const companyNameFor = (id) => state.companies.find((item) => Number(item.id) === Number(id))?.name || '';
  const jobOptions = (selected = null) => state.jobs.map((job) => {
    const companyId = positiveId(job.company_id); const appItem = state.applications.find((item) => Number(item.job_id) === Number(job.id));
    return { jobId: Number(job.id), companyId, applicationId: positiveId(appItem?.id), companyName: companyNameFor(companyId), jobTitle: job.title };
  }).map((candidate) => `<option value="${esc(candidate.jobId)}"${Number(selected) === candidate.jobId ? ' selected' : ''}>${esc([candidate.companyName || '公司未知', candidate.jobTitle || `岗位 #${candidate.jobId}`, candidate.applicationId ? `申请 #${candidate.applicationId}` : '无申请记录'].join(' · '))}</option>`).join('');
  const entryRows = (entry, index) => {
    const suggested = arr(entry.candidates).map((candidate) => normalizeCandidate(candidate));
    const links = arr([first(entry.job_url, entry.url, entry.source_url), ...arr(entry.links)])
      .map((raw) => typeof raw === 'string' ? { url: raw, label: '' } : object(raw))
      .filter((item, index, items) => safeLink(item.url) && items.findIndex((candidate) => safeLink(candidate.url) === safeLink(item.url)) === index);
    const reasons = arr(entry.reasons).map(text).filter(Boolean);
    const selected = entry.jobId || suggested[0]?.jobId || '';
    const eventNames = arr(entry.events).map((event) => EVENT[event.event_type || event.type] || text(event.event_type || event.type)).filter(Boolean);
    const hasEntry = positiveId(entry.id);
    return `<article class="wb-entry-card" data-entry-card="${esc(entry.id || index)}"><div class="wb-entry-head"><div><span class="wb-eyebrow">岗位条目 ${index + 1}</span><h3>${esc(entry.companyName || '公司待确认')} · ${esc(entry.jobTitle || '岗位待确认')}</h3></div><span class="wb-chip">${esc(ASSOCIATION[token(entry.status)] || entry.status)}</span></div><dl class="wb-facts">${htmlRows([['工作地点', entry.location], ['岗位链接', links.length ? `${links.length} 个` : '未提取'], ['招聘事件', eventNames.join('、') || '暂无'], ['人工关联', entry.manualLocked ? '已锁定' : '未锁定']])}</dl>${links.length ? `<p>${links.map((item, linkIndex) => `<a href="${esc(safeLink(item.url))}" target="_blank" rel="noopener noreferrer">${esc(item.label || `打开岗位来源 ${linkIndex + 1}`)}</a>`).join(' · ')}</p>` : ''}${reasons.length ? `<ul class="wb-reasons">${reasons.map((reason) => `<li>${esc(reason)}</li>`).join('')}</ul>` : ''}<div class="wb-form-grid wb-association-controls"><label>关联到本地岗位（完整岗位列表）<select data-entry-job>${'<option value="">选择岗位</option>' + jobOptions(selected)}</select></label><button class="wb-button wb-primary" type="button" data-entry-associate="${esc(entry.id)}" ${hasEntry ? '' : 'disabled'}>保存关联</button></div><div class="wb-inline-actions"><button class="wb-text-button" type="button" data-entry-rematch="${esc(entry.id)}" ${hasEntry ? '' : 'disabled'}>重新匹配</button><button class="wb-text-button" type="button" data-entry-facts="${esc(entry.id || '')}" ${hasEntry ? '' : 'data-message-facts="1"'}>录入/修订人工事实</button><button class="wb-text-button" type="button" data-entry-suggest="${esc(entry.id || '')}" ${hasEntry ? '' : 'disabled'}>生成进度建议</button></div><div class="wb-suggestion-list" data-suggestions-for="${esc(entry.id || '')}"></div></article>`;
  };
  function renderEmailDetail(email) {
    const cleaning = email.cleaning; const original = email.bodyRetained ? first(cleaning.originalBody, email.bodyText) : '';
    q('[data-email-title]').textContent = email.subject || '邮件详情'; q('[data-email-subtitle]').textContent = `${email.sender || '发件人待提取'} · ${fmtTime(first(email.received_at, email.receivedAt))}`;
    const facts = [['公司', cleaning.companyName], ['岗位', cleaning.jobTitle], ['地点', cleaning.workLocation], ['清洗版本', cleaning.version], ['清洗状态', cleaning.status], ['错误', cleaning.error || email.processing_error]];
    const events = email.events.map((event) => `<article class="wb-event-card"><span class="wb-chip">${esc(EVENT[event.event_type || event.type] || text(event.event_type || event.type))}</span><h3>${esc([event.company_name, event.job_title].filter(Boolean).join(' · ') || '事件信息待确认')}</h3><p>${esc(event.ai_summary || event.summary || '暂无摘要')}</p><span class="wb-muted">${esc(fmtTime(event.event_time))} · ${esc(ASSOCIATION[token(event.association_status)] || text(event.association_status) || '待匹配')}</span></article>`).join('');
    const links = arr(cleaning.links).map((item) => typeof item === 'string' ? { url: item, label: '相关链接' } : object(item)).filter((item) => safeLink(item.url));
    q('[data-email-content]').innerHTML = `<section class="wb-detail-section"><h3>AI 清洗结果</h3>${cleaning.summary ? `<p>${esc(cleaning.summary)}</p>` : '<p class="wb-muted">尚无清洗摘要。</p>'}${cleaning.body ? `<pre class="wb-pre">${esc(cleaning.body)}</pre>` : ''}<dl class="wb-facts">${htmlRows(facts)}</dl>${Object.keys(cleaning.fieldEvidence || {}).length ? `<details><summary>字段依据</summary><pre class="wb-pre">${esc(JSON.stringify(cleaning.fieldEvidence, null, 2))}</pre></details>` : ''}${links.length ? `<p>${links.map((item) => `<a href="${esc(safeLink(item.url))}" target="_blank" rel="noopener noreferrer">${esc(item.label || '相关链接')}</a>`).join(' · ')}</p>` : ''}</section><section class="wb-detail-section"><div class="wb-section-heading"><h3>岗位条目</h3><button class="wb-button" type="button" data-action="new-manual-entry">新增人工事实条目</button></div>${email.jobEntries.length ? email.jobEntries.map(entryRows).join('') : '<p class="wb-muted">尚未形成岗位条目。可人工录入事实后继续本地关联。</p>'}<p class="wb-hint" data-manual-api-note>人工事实保存在邮件事实接口中，不能由重清洗覆盖；关联只修改条目与岗位关系，不改变投递状态。</p></section><section class="wb-detail-section"><h3>邮件内招聘事件</h3>${events || '<p class="wb-muted">这封邮件尚未生成招聘事件。</p>'}</section><details class="wb-original"><summary>查看邮件原文（纯文本）</summary><dl class="wb-facts">${htmlRows([['主题', email.subject], ['发件人', email.sender], ['接收时间', fmtTime(first(email.received_at, email.receivedAt))], ['同步时间', fmtTime(email.synced_at)]])}</dl>${original ? `<pre class="wb-pre">${esc(original)}</pre>` : '<p class="wb-hint">服务端没有保留原始正文；当前只可查看列表摘要。</p>'}<p class="wb-hint">以纯文本显示；邮件 HTML 和内嵌指令不会执行。</p></details>`;
    email.jobEntries.filter((entry) => positiveId(entry.id)).forEach((entry) => loadSuggestions(entry.id));
    q('[data-email-action="retry"]').disabled = !(email.id);
    q('[data-email-action="reprocess"]').disabled = !cleaning.id;
    q('[data-email-action="rematch"]').disabled = !email.jobEntries.length;
    q('[data-email-action="archive"]').disabled = !cleaning.id;
  }
  async function loadAssociationData(signal, sequence) {
    const results = await Promise.all([request('/jobs', { signal }), request('/companies', { signal }), request('/applications', { signal })]);
    if (state.cleaned || signal.aborted || sequence !== state.detailSeq) return;
    state.jobs = collection(results[0], ['items', 'jobs']); state.companies = collection(results[1], ['items', 'companies']); state.applications = collection(results[2], ['items', 'applications']);
  }
  async function openEmail(id) {
    if (state.cleaned) return;
    const sequence = ++state.detailSeq; state.detailController?.abort(); const controller = track(); state.detailController = controller;
    const dlg = q('[data-dialog="email"]'); q('[data-email-content]').innerHTML = '<p class="wb-muted">正在读取邮件详情…</p>';
    q('[data-email-placeholder]').hidden = true;
    q('[data-detail-feedback]').textContent = '';
    if (!dlg.open) {
      if (window.matchMedia('(max-width: 900px)').matches) dlg.showModal();
      else dlg.show();
    }
    root.querySelectorAll('[data-email-row]').forEach((row) => row.classList.toggle('is-selected', row.dataset.emailRow === String(id)));
    if (String(state.routeParams.get('email_id') || '') !== String(id)) { const params = new URLSearchParams(state.routeParams); params.set('email_id', String(id)); updateRoute(params); }
    try {
      await loadAssociationData(controller.signal, sequence); if (state.cleaned || controller.signal.aborted || sequence !== state.detailSeq) return;
      const raw = await request(`/email-messages/${encodeURIComponent(id)}`, { signal: controller.signal }); if (state.cleaned || controller.signal.aborted || sequence !== state.detailSeq) return;
      state.email = normalizeEmail(raw); renderEmailDetail(state.email);
    } catch (error) { if (error.name !== 'AbortError' && sequence === state.detailSeq) feedback('[data-detail-feedback]', `读取失败：${error.message}`, 'error'); }
    finally { stopController(controller); if (state.detailController === controller) state.detailController = null; }
  }
  async function reloadCurrent() { if (!state.email?.id || state.cleaned) return; await loadEmails(true); if (state.cleaned) return; const data = await request(`/email-messages/${encodeURIComponent(state.email.id)}`); if (state.cleaned) return; state.email = normalizeEmail(data); renderEmailDetail(state.email); }
  async function emailAction(action, button) {
    const email = state.email; if (!email) return;
    buttonBusy(button, true, ({ retry: '正在重试…', reprocess: '正在重新处理…', rematch: '正在匹配…', archive: '正在归档…', delete: '正在删除…' })[action]);
    try {
      if (action === 'delete') {
        if (!confirm('删除这封邮件的本地记录？已创建岗位保留，远端邮件不会删除。')) return;
        await request(`/email-messages/${encodeURIComponent(email.id)}`, { method: 'DELETE' }); q('[data-dialog="email"]').close(); state.email = null; await loadEmails(); feedback('[data-feedback]', '本地邮件记录已删除，岗位记录保留。', 'success'); return;
      }
      if (action === 'retry') await request(email.cleaning.id ? `/email-cleaning/${encodeURIComponent(email.cleaning.id)}/retry` : `/email-messages/${encodeURIComponent(email.id)}/process`, { method: 'POST' });
      else if (action === 'reprocess') await request(`/email-cleaning/${encodeURIComponent(email.cleaning.id)}/reprocess`, { method: 'POST' });
      else if (action === 'archive') await request(`/email-cleaning/${encodeURIComponent(email.cleaning.id)}/archive`, { method: 'POST' });
      else if (action === 'rematch') for (const entry of email.jobEntries) if (entry.id) await request(`/email-job-entries/${encodeURIComponent(entry.id)}/rematch`, { method: 'POST' });
      feedback('[data-detail-feedback]', action === 'archive' ? '已提交本地岗位归档，不会重新调用模型。' : action === 'rematch' ? '已提交条目重新匹配。' : action === 'reprocess' ? '已提交新的清洗版本。' : '已提交清洗重试。', 'success');
      await reloadCurrent();
    } catch (error) { feedback('[data-detail-feedback]', `操作失败：${error.message}`, 'error'); }
    finally { buttonBusy(button, false); }
  }
  async function associateEntry(entryId, button) {
    const card = button.closest('[data-entry-card]'); const jobId = positiveId(card?.querySelector('[data-entry-job]')?.value); const job = state.jobs.find((item) => Number(item.id) === jobId); if (!job) { feedback('[data-detail-feedback]', '请从完整岗位列表选择一个岗位。', 'error'); return; }
    const application = state.applications.find((item) => Number(item.job_id) === jobId);
    buttonBusy(button, true, '保存中…');
    try {
      await request(`/email-job-entries/${encodeURIComponent(entryId)}/association`, jsonOptions('PATCH', { job_id: jobId, company_id: positiveId(job.company_id), ...(application ? { application_id: Number(application.id) } : {}) }));
      feedback('[data-detail-feedback]', '岗位关联已保存，投递状态没有变化。', 'success'); await reloadCurrent();
    } catch (error) { feedback('[data-detail-feedback]', `保存关联失败：${error.message}`, 'error'); }
    finally { buttonBusy(button, false); }
  }
  function openFacts(entryId = '') {
    const form = q('[data-form="manual-facts"]'); form.reset(); form.elements.entry_id.value = entryId;
    const entry = entryId ? state.email?.jobEntries.find((item) => String(item.id) === String(entryId)) : null;
    if (entry) {
      form.elements.company_name.value = entry.companyName || '';
      form.elements.job_title.value = entry.jobTitle || '';
      form.elements.work_location.value = entry.location || '';
      form.elements.event_type.value = entry.event_type || '';
      form.elements.event_round.value = entry.event_round || '';
      const evidence = object(entry.field_evidence);
      form.elements.field_evidence.value = text(first(evidence.source_excerpt, evidence.original_excerpt, evidence.excerpt, evidence.text, ...Object.values(evidence).filter((value) => typeof value === 'string')));
    }
    form.dataset.idempotencyKey = entryId ? '' : `manual-email-${state.email?.id}-${Date.now()}`;
    q('[data-facts-hint]').textContent = entryId ? '保存会为现有条目留下人工事实标记，并按版本校验。' : '将从当前邮件直接创建一个人工事实条目；该流程不调用模型。';
    q('[data-dialog="manual-facts"]').showModal();
  }
  async function saveFacts(event) {
    event.preventDefault(); const form = event.target.closest('form'); if (!form) return; const data = new FormData(form); const entryId = text(data.get('entry_id')); const sourceExcerpt = text(data.get('field_evidence')).trim(); const payload = { company_name: text(data.get('company_name')).trim(), job_title: text(data.get('job_title')).trim(), work_location: text(data.get('work_location')).trim() || null, event_type: text(data.get('event_type')), event_round: text(data.get('event_round')).trim() || null, field_evidence: sourceExcerpt ? { source_excerpt: sourceExcerpt, checked_by: 'user' } : {} };
    if (!sourceExcerpt) { feedback('[data-facts-feedback]', '请提供人工核对过的邮件原文依据。', 'error'); return; }
    if (!payload.event_type) { feedback('[data-facts-feedback]', '请选择邮件中明确表达的事件类型。', 'error'); return; }
    const button = form.querySelector('[type="submit"]'); buttonBusy(button, true, '保存中…');
    try {
      if (entryId) {
        const entry = state.email?.jobEntries.find((item) => String(item.id) === entryId);
        const version = positiveId(entry?.version);
        if (!entry || !version) throw new Error('条目版本信息不可用，请刷新邮件后重试。');
        const existingEvidence = object(entry.field_evidence);
        payload.field_evidence = { ...existingEvidence, source_excerpt: sourceExcerpt, checked_by: 'user' };
        payload.links = arr(entry.links);
        payload.event_time = entry.event_time ?? null;
        payload.deadline = entry.deadline ?? null;
        payload.action_required = Boolean(entry.action_required);
        payload.actions = arr(entry.actions);
        payload.summary = entry.summary ?? null;
        payload.expected_entry_version = version;
        await request(`/email-job-entries/${encodeURIComponent(entryId)}/manual-facts`, jsonOptions('POST', payload));
      }
      else { payload.idempotency_key = form.dataset.idempotencyKey; await request(`/email-messages/${encodeURIComponent(state.email?.id)}/manual-entries`, jsonOptions('POST', payload)); }
      if (state.cleaned) return;
      q('[data-dialog="manual-facts"]').close(); feedback('[data-detail-feedback]', '人工事实已保存。', 'success'); await reloadCurrent();
    } catch (error) { feedback('[data-facts-feedback]', `保存失败：${error.message}`, 'error'); }
    finally { buttonBusy(button, false); }
  }
  async function requestSuggestions(entryId, button) {
    const entry = state.email?.jobEntries.find((item) => String(item.id) === String(entryId)); if (!entry) return;
    buttonBusy(button, true, '生成中…');
    try { await request(`/email-job-entries/${encodeURIComponent(entryId)}/progress-suggestions`, jsonOptions('POST', { expected_entry_version: Number(entry.version || 1) })); await loadSuggestions(entryId); }
    catch (error) { feedback('[data-detail-feedback]', `生成进度建议失败：${error.message}`, 'error'); }
    finally { buttonBusy(button, false); }
  }
  const suggestionsFor = (payload) => collection(payload, ['items', 'suggestions', 'progress_suggestions']);
  async function loadSuggestions(entryId) {
    const target = [...root.querySelectorAll('[data-suggestions-for]')].find((item) => item.dataset.suggestionsFor === String(entryId)); if (!target) return;
    try {
      const items = suggestionsFor(await request(`/email-job-entries/${encodeURIComponent(entryId)}/progress-suggestions`));
      if (state.cleaned) return;
      target.innerHTML = items.length ? items.map((item) => {
        const id = positiveId(item.id || item.suggestion_id); const version = positiveId(item.version); const applicationVersion = positiveId(item.application_version);
        const evidence = item.evidence && typeof item.evidence === 'object' ? JSON.stringify(item.evidence, null, 2) : text(item.evidence);
        const actionable = item.status === 'pending' && id && version;
        return `<article class="wb-suggestion" data-suggestion-id="${esc(id || '')}"><strong>${esc(item.target_status || '状态待确认')}</strong><p>${esc(item.reason || '请先核对下面的邮件依据。')}</p><pre class="wb-pre">${esc(evidence)}</pre><span class="wb-muted">${esc(item.status || '待处理')} · 事件 ${esc(EVENT[item.event_type] || item.event_type || '未标注')} ${esc(item.event_round || '')} · 条目版本 ${esc(item.entry_version ?? '—')} · 申请版本 ${esc(item.application_version ?? '—')}</span>${actionable ? `<div class="wb-inline-actions"><button class="wb-button wb-primary" type="button" data-suggestion-accept="${esc(id)}" data-suggestion-version="${version}" data-application-version="${esc(applicationVersion || '')}" ${applicationVersion ? '' : 'disabled'}>确认更新投递状态</button><button class="wb-button" type="button" data-suggestion-reject="${esc(id)}" data-suggestion-version="${version}">拒绝建议</button></div>${!applicationVersion ? '<p class="wb-hint">服务端未返回申请版本，暂不能安全确认；拒绝建议仍可用。</p>' : ''}` : ''}</article>`;
      }).join('') : '<p class="wb-muted">尚无进度建议。</p>';
    }
    catch (error) { target.textContent = `读取建议失败：${error.message}`; }
  }
  async function suggestionAction(id, accept, button) {
    const entryCard = button.closest('[data-entry-card]'); const entryId = entryCard?.dataset.entryCard; const entry = state.email?.jobEntries.find((item) => String(item.id) === String(entryId));
    buttonBusy(button, true, accept ? '确认中…' : '提交中…');
    try {
      const version = positiveId(button.dataset.suggestionVersion); const applicationVersion = positiveId(button.dataset.applicationVersion);
      if (accept && (!version || !applicationVersion)) throw new Error('页面中没有可确认的建议/申请版本快照，请刷新建议后重试。');
      if (accept) await request(`/progress-suggestions/${encodeURIComponent(id)}/accept`, jsonOptions('POST', { expected_suggestion_version: version, expected_application_version: applicationVersion, idempotency_key: `suggestion-${id}-${version}` }));
      else await request(`/progress-suggestions/${encodeURIComponent(id)}/reject`, jsonOptions('POST', { expected_suggestion_version: version }));
      if (state.cleaned) return; feedback('[data-detail-feedback]', accept ? '投递状态已按已确认建议更新。' : '已拒绝建议，投递状态未改变。', 'success'); await reloadCurrent(); await loadSuggestions(entryId);
    } catch (error) { feedback('[data-detail-feedback]', `提交失败：${error.message}`, 'error'); }
    finally { buttonBusy(button, false); }
  }

  function renderEvents() {
    const list = q('[data-event-list]');
    list.innerHTML = state.events.length ? state.events.map((event) => {
      const apps = state.applications.map((app) => { const job = state.jobs.find((item) => Number(item.id) === Number(app.job_id)); return { ...app, job, company: companyNameFor(job?.company_id) }; });
      const candidates = arr(event.candidates); const options = apps.map((app) => `<option value="${esc(app.id)}"${Number(event.application_id) === Number(app.id) ? ' selected' : ''}>${esc(`${app.company || '公司未知'} · ${app.job?.title || `岗位 #${app.job_id}`} · 申请 #${app.id}`)}</option>`).join('');
      const hasEmail = positiveId(event.source_email_id);
      return `<article class="wb-event-card" data-event-card="${esc(event.id)}" data-email-id="${esc(event.source_email_id || '')}"><div class="wb-entry-head"><span class="wb-chip">${esc(EVENT[event.event_type] || event.event_type || '其他')}</span><span class="wb-chip">${esc(ASSOCIATION[token(event.association_status)] || event.association_status || '待匹配')}</span></div><h3>${esc([event.company_name, event.job_title].filter(Boolean).join(' · ') || '事件信息待确认')}</h3><p>${esc(event.ai_summary || '暂无摘要')}</p><dl class="wb-facts">${htmlRows([['事件时间', fmtTime(event.event_time)], ['截止时间', fmtTime(event.deadline)], ['地点/会议', event.location || event.meeting_url], ['状态建议（未应用）', event.suggested_application_status]])}</dl>${candidates.length || apps.length ? `<div class="wb-inline-actions"><label>关联申请<select data-event-application><option value="">选择申请记录</option>${options}</select></label><button class="wb-button" type="button" data-event-associate>确认关联</button><button class="wb-button" type="button" data-event-dismiss>保持未关联</button></div>` : '<p class="wb-muted">暂无申请候选；可先补充岗位及申请记录。</p>'}<div class="wb-inline-actions">${hasEmail ? '<button class="wb-text-button" data-event-open-email type="button">查看来源邮件</button>' : ''}<button class="wb-button" type="button" data-event-notify>按策略发送通知</button></div></article>`;
    }).join('') : '<div class="wb-empty"><strong>没有招聘事件</strong><p>可调整事件类型或关联状态筛选。</p></div>';
  }
  async function loadEvents() {
    const seq = ++state.eventSeq; const controller = track(); feedback('[data-event-feedback]', '正在读取历史招聘事件…');
    try { const params = new URLSearchParams(); const type = q('[data-event-filter="type"]').value; const association = q('[data-event-filter="association"]').value; if (type) params.set('event_type', type); if (association) params.set('association_status', association); const result = await request(`/recruitment-events${params.size ? `?${params}` : ''}`, { signal: controller.signal }); if (state.cleaned || controller.signal.aborted || seq !== state.eventSeq) return; state.events = collection(result, ['items', 'events', 'recruitment_events']); await loadAssociationData(); if (state.cleaned || controller.signal.aborted || seq !== state.eventSeq) return; state.eventsLoaded = true; renderEvents(); feedback('[data-event-feedback]', `共 ${state.events.length} 个历史招聘事件。`, 'success'); }
    catch (error) { if (error.name !== 'AbortError' && !state.cleaned) feedback('[data-event-feedback]', `读取失败：${error.message}`, 'error'); }
    finally { stopController(controller); }
  }
  async function eventAction(event, button) {
    const card = button.closest('[data-event-card]'); const eventId = card?.dataset.eventCard;
    try {
      if (button.matches('[data-event-associate]')) { const applicationId = positiveId(card.querySelector('[data-event-application]')?.value); if (!applicationId) throw new Error('请选择一个本地申请记录。'); await request(`/recruitment-events/${encodeURIComponent(eventId)}/association`, jsonOptions('PATCH', { application_id: applicationId })); feedback('[data-event-feedback]', '事件关联已保存，投递状态没有改变。', 'success'); }
      else if (button.matches('[data-event-dismiss]')) { await request(`/recruitment-events/${encodeURIComponent(eventId)}/association`, jsonOptions('PATCH', { dismiss: true })); feedback('[data-event-feedback]', '事件已保持未关联。', 'success'); }
      else if (button.matches('[data-event-notify]')) { const result = await request(`/recruitment-events/${encodeURIComponent(eventId)}/notify`, { method: 'POST' }); feedback('[data-event-feedback]', Array.isArray(result) && result.length ? '已按通知策略提交；可在通知历史查看回执。' : '此事件未命中当前即时通知策略。', 'success'); }
      else if (button.matches('[data-event-open-email]')) { const emailId = card.dataset.emailId; if (emailId) { q('[data-tab="inbox"]').click(); await openEmail(emailId); } return; }
      await loadEvents();
    } catch (error) { feedback('[data-event-feedback]', `操作失败：${error.message}`, 'error'); }
  }
  async function manualImport(event) {
    event.preventDefault(); const form = event.target.closest('form'); if (!form) return; const data = new FormData(form); const payload = { account_id: positiveId(data.get('account_id')), sender: text(data.get('sender')).trim(), subject: text(data.get('subject')).trim(), body_text: text(data.get('body_text')).trim() || null, message_id: text(data.get('message_id')).trim() || null }; const submit = form.querySelector('[type="submit"]'); buttonBusy(submit, true, '导入中…');
    try { await request('/email-messages/manual', jsonOptions('POST', payload)); if (state.cleaned) return; q('[data-dialog="manual-email"]').close(); form.reset(); feedback('[data-feedback]', '邮件已加入识别队列；此入口需要邮件解析模型。', 'success'); await loadEmails(true); }
    catch (error) { feedback('[data-manual-feedback]', `导入失败：${error.message}`, 'error'); }
    finally { buttonBusy(submit, false); }
  }
  function setTab(tab, updateUrl = false) {
    state.tab = tab; root.querySelectorAll('[data-tab]').forEach((button) => { if (button.dataset.tab === tab) button.setAttribute('aria-current', 'page'); else button.removeAttribute('aria-current'); }); root.querySelectorAll('[data-panel]').forEach((panel) => { panel.hidden = panel.dataset.panel !== tab; });
    if (tab === 'events' && !state.eventsLoaded) loadEvents(); if (tab === 'notifications') notificationPanel.load();
    if (updateUrl) { const params = new URLSearchParams(state.routeParams); if (tab === 'inbox') params.delete('tab'); else params.set('tab', tab); updateRoute(params); }
  }
  const onClick = async (event) => {
    const tab = event.target.closest('[data-tab]'); if (tab) { setTab(tab.dataset.tab, true); return; }
    const close = event.target.closest('[data-close]'); if (close) { close.closest('dialog')?.close(); return; }
    const opener = event.target.closest('[data-open-email]'); if (opener) { await openEmail(opener.dataset.openEmail); return; }
    if (event.target.closest('[data-action="refresh"]')) { if (state.tab === 'inbox') await loadEmails(); else if (state.tab === 'events') await loadEvents(); else await notificationPanel.load(); return; }
    if (event.target.closest('[data-action="refresh-events"]')) { await loadEvents(); return; }
    if (event.target.closest('[data-action="manual-email"]')) { if (!state.accounts.length) { feedback('[data-feedback]', '请先在设置中添加招聘邮箱账户。', 'error'); return; } q('[data-dialog="manual-email"]').showModal(); return; }
    const pageButton = event.target.closest('[data-page]'); if (pageButton) { if (pageButton.dataset.page === 'next' && state.emails.length >= state.limit) state.offset += state.limit; else if (pageButton.dataset.page === 'previous' && state.offset > 0) state.offset = Math.max(0, state.offset - state.limit); else return; await loadEmails(); return; }
    const emailActionButton = event.target.closest('[data-email-action]'); if (emailActionButton) { await emailAction(emailActionButton.dataset.emailAction, emailActionButton); return; }
    const associate = event.target.closest('[data-entry-associate]'); if (associate) { await associateEntry(associate.dataset.entryAssociate, associate); return; }
    const rematch = event.target.closest('[data-entry-rematch]'); if (rematch) { buttonBusy(rematch, true, '匹配中…'); try { await request(`/email-job-entries/${encodeURIComponent(rematch.dataset.entryRematch)}/rematch`, { method: 'POST' }); await reloadCurrent(); } catch (error) { feedback('[data-detail-feedback]', `重新匹配失败：${error.message}`, 'error'); } finally { buttonBusy(rematch, false); } return; }
    const facts = event.target.closest('[data-entry-facts]'); if (facts) { openFacts(facts.dataset.entryFacts); return; }
    if (event.target.closest('[data-action="new-manual-entry"]')) { openFacts(''); return; }
    const suggest = event.target.closest('[data-entry-suggest]'); if (suggest) { await requestSuggestions(suggest.dataset.entrySuggest, suggest); return; }
    const accept = event.target.closest('[data-suggestion-accept]'); if (accept) { await suggestionAction(accept.dataset.suggestionAccept, true, accept); return; }
    const reject = event.target.closest('[data-suggestion-reject]'); if (reject) { await suggestionAction(reject.dataset.suggestionReject, false, reject); return; }
    const eventButton = event.target.closest('[data-event-associate], [data-event-dismiss], [data-event-notify], [data-event-open-email]'); if (eventButton) await eventAction(event, eventButton);
  };
  const onInput = (event) => { if (event.target.matches('[data-filter="query"]')) { clearTimeout(state.searchTimer); state.searchTimer = setTimeout(renderEmailRows, 100); } };
  const onChange = (event) => { if (event.target.matches('[data-filter="limit"]')) { state.limit = Number(event.target.value) || 500; state.offset = 0; loadEmails(); } else if (event.target.matches('[data-filter]')) renderEmailRows(); else if (event.target.matches('[data-event-filter]')) loadEvents(); };
  const onSubmit = (event) => { if (event.target.matches('[data-form="manual-email"]')) manualImport(event); if (event.target.matches('[data-form="manual-facts"]')) saveFacts(event); };
  root.addEventListener('click', onClick); root.addEventListener('input', onInput); root.addEventListener('change', onChange); root.addEventListener('submit', onSubmit);
  const statusValue = route?.params?.get?.('email_id');
  loadEmails().then(async () => { if (statusValue && positiveId(statusValue)) await openEmail(positiveId(statusValue)); });
  if (route?.params?.get?.('tab')) setTab(route.params.get('tab'));
  const onEmailClose = () => { if (state.cleaned) return; state.detailSeq += 1; state.detailController?.abort(); state.email = null; q('[data-email-placeholder]').hidden = false; root.querySelectorAll('[data-email-row]').forEach((row) => row.classList.remove('is-selected')); const params = new URLSearchParams(state.routeParams); params.delete('email_id'); updateRoute(params); };
  q('[data-dialog="email"]').addEventListener('close', onEmailClose);
  return () => { state.cleaned = true; clearTimeout(state.searchTimer); state.controllers.forEach((controller) => controller.abort()); state.controllers.clear(); notificationPanel.cleanup(); q('[data-dialog="email"]').removeEventListener('close', onEmailClose); root.removeEventListener('click', onClick); root.removeEventListener('input', onInput); root.removeEventListener('change', onChange); root.removeEventListener('submit', onSubmit); };
}
