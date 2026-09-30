import { escapeHtml, routeWith, stateMessage, statusValues, statusOptions } from '../shared.js';

const STAGES = [
  { name: '准备阶段', statuses: ['待投递'] },
  { name: '推进阶段', statuses: ['已投递', '待面试', '1面', '2面', '3面', 'HR面'] },
  { name: '结果阶段', statuses: ['拒绝'] },
];
const PAGE_SIZES = [5, 10, 20, 30];

function timeLabel(value) {
  if (!value) return '暂无投递状态时间';
  const date = new Date(value);
  return Number.isNaN(date.valueOf()) ? String(value) : new Intl.DateTimeFormat('zh-CN', { dateStyle: 'medium', timeStyle: 'short' }).format(date);
}

function renderFollowItem(job) {
  const application = job.application;
  const company = job.company?.name || '公司资料缺失';
  if (!application) {
    return `<article class="wb-follow-item"><div class="wb-job-main"><span class="wb-company-mark" aria-hidden="true">${escapeHtml([...company][0] || '职')}</span><div class="wb-job-copy"><strong>${escapeHtml(job.title || '岗位名称缺失')}</strong><span>${escapeHtml(company)} · ${escapeHtml(job.location || '地点未填写')}</span></div></div><span class="wb-badge">未建立投递记录</span><a class="wb-inline-link" href="/workbench?view=library&job_id=${Number(job.id)}">打开工作台详情并维护投递记录 ↗</a></article>`;
  }
  return `<article class="wb-follow-item" data-follow-job="${Number(job.id)}"><div class="wb-job-main"><span class="wb-company-mark" aria-hidden="true">${escapeHtml([...company][0] || '职')}</span><div class="wb-job-copy"><strong>${escapeHtml(job.title || '岗位名称缺失')}</strong><span>${escapeHtml(company)} · ${escapeHtml(job.location || '地点未填写')} · 当前状态更新 ${escapeHtml(timeLabel(application.status_updated_at))}</span></div></div><span class="wb-badge" data-tone="info">${escapeHtml(application.status)}</span><form class="wb-follow-status-form" data-status-form><label class="wb-sr-label" for="follow-status-${Number(job.id)}">修改 ${escapeHtml(job.title)} 的当前投递状态</label><select id="follow-status-${Number(job.id)}" name="status">${statusOptions(application.status)}</select><button class="wb-button wb-button-outline" type="submit">保存状态</button></form><p class="wb-follow-feedback" data-follow-feedback role="status" aria-live="polite"></p></article>`;
}

export async function mount({ root, route, navigate, api, notify = () => {}, signal }) {
  const STATUS_VALUES = statusValues();
  const params = new URLSearchParams(route.params);
  const selectedStatus = params.get('application_status') || '';
  const requestedSize = Number(params.get('limit'));
  const pageSize = PAGE_SIZES.includes(requestedSize) ? requestedSize : 10;
  let currentJobs = [];
  let totalJobs = 0;
  root.innerHTML = `<header class="wb-page-heading"><div><h1>投递跟进</h1><p class="wb-page-subtitle">每条记录只显示服务端当前状态；没有历史数据时不推断经历过哪些阶段。</p></div><div class="wb-heading-actions"><a class="wb-button wb-button-outline" href="/legacy/tracker">打开现有岗位管理</a></div></header>
    <section class="wb-callout" data-workbench-page-hint="follow"><span aria-hidden="true">↳</span><div><strong>状态修改会保存到对应的投递记录</strong><p>岗位库中没有申请记录的职位不会被默认标记为“待投递”；可从岗位详情中明确建立记录。</p><button class="wb-button wb-button-quiet wb-hint-dismiss" type="button" data-dismiss-workbench-hint>关闭本页说明</button></div></section>
    <section class="wb-follow-summary" aria-label="当前页统计"><div class="wb-summary-tile wb-card"><span>本页岗位</span><strong data-total-jobs>读取中</strong></div><div class="wb-summary-tile wb-card"><span>本页有投递记录</span><strong data-application-count>—</strong></div><div class="wb-summary-tile wb-card"><span>当前筛选</span><strong data-stage-caption>${escapeHtml(selectedStatus || '全部状态')}</strong></div></section>
    <section class="wb-card wb-toolbar-card wb-follow-toolbar"><label class="wb-field">投递状态筛选<select data-follow-filter><option value="">全部当前状态</option>${STATUS_VALUES.map((status) => `<option value="${escapeHtml(status)}" ${status === selectedStatus ? 'selected' : ''}>${escapeHtml(status)}</option>`).join('')}</select></label><p>状态标签不代表已保存的完整历史时间线。</p></section>
    <section class="wb-follow-groups" data-follow-results aria-live="polite"></section><div class="wb-table-foot wb-follow-pagination"><span data-follow-page></span><div class="wb-follow-page-controls"><label class="wb-field">每页显示<select data-follow-page-size aria-label="每页显示岗位数量">${PAGE_SIZES.map((size) => `<option value="${size}" ${size === pageSize ? 'selected' : ''}>${size} 条</option>`).join('')}</select></label><button class="wb-button wb-button-quiet" type="button" data-follow-prev>上一页</button><form class="wb-follow-jump" data-follow-jump novalidate><label class="wb-field">跳转页码<input type="number" name="page" min="1" step="1" value="1" required aria-label="跳转页码"></label><button class="wb-button wb-button-outline" type="submit">跳转</button></form><button class="wb-button wb-button-quiet" type="button" data-follow-next>下一页</button></div><span class="wb-follow-page-feedback" data-follow-page-feedback role="status"></span></div>`;

  const resultArea = root.querySelector('[data-follow-results]');
  const load = async () => {
    resultArea.innerHTML = '<div class="wb-view-skeleton"><span class="wb-spinner" aria-hidden="true"></span><span>正在读取投递记录…</span></div>';
    const query = new URLSearchParams(params);
    query.delete('view');
    query.delete('job_id');
    query.set('limit', String(pageSize));
    if (!query.has('organize_status')) query.set('organize_status', 'active');
    try {
      const payload = await api(`/workbench/jobs?${query.toString()}`, { signal });
      if (signal?.aborted) return;
      const items = Array.isArray(payload?.items) ? payload.items : [];
      currentJobs = items;
      const withApplications = items.filter((job) => job.application);
      const total = Number(payload?.total) || 0;
      totalJobs = total;
      const offset = Number(params.get('offset') || 0);
      if (total > 0 && offset >= total) {
        navigate({ view: 'follow', params: routeWith(route, { offset: Math.floor((total - 1) / pageSize) * pageSize }) });
        return;
      }
      const pageCount = Math.max(1, Math.ceil(total / pageSize));
      root.querySelector('[data-total-jobs]').textContent = String(items.length);
      root.querySelector('[data-application-count]').textContent = String(withApplications.length);
      root.querySelector('[data-stage-caption]').textContent = selectedStatus || '全部状态';
      root.querySelector('[data-follow-page]').textContent = `显示 ${offset + (items.length ? 1 : 0)}–${offset + items.length} 条岗位，共 ${total} 条 · 第 ${Math.floor(offset / pageSize) + 1} / ${pageCount} 页`;
      const jumpInput = root.querySelector('[data-follow-jump] input[name="page"]');
      jumpInput.max = String(pageCount);
      jumpInput.value = String(Math.floor(offset / pageSize) + 1);
      root.querySelector('[data-follow-prev]').disabled = offset <= 0;
      root.querySelector('[data-follow-next]').disabled = offset + items.length >= total;
      if (!items.length) resultArea.innerHTML = '<div class="wb-empty-state"><strong>当前筛选没有岗位</strong><p>调整状态筛选，或通过现有岗位管理维护投递记录。</p><a class="wb-button wb-button-outline" href="/legacy/tracker">打开现有岗位管理</a></div>';
      else {
        const staged = new Set(STAGES.flatMap((stage) => stage.statuses));
        const stageMarkup = STAGES.map((stage) => {
          const rows = withApplications.filter((job) => stage.statuses.includes(job.application.status));
          return `<section class="wb-card wb-stage-group"><header class="wb-stage-head"><h2>${stage.name}</h2><span>本页 ${rows.length} 条当前状态</span></header><div class="wb-application-list">${rows.length ? rows.map(renderFollowItem).join('') : '<p class="wb-stage-empty">本页暂无该阶段记录</p>'}</div></section>`;
        }).join('');
        const otherApplications = withApplications.filter((job) => !staged.has(job.application.status));
        const otherMarkup = otherApplications.length ? `<section class="wb-card wb-stage-group"><header class="wb-stage-head"><h2>其他当前状态</h2><span>本页 ${otherApplications.length} 条</span></header><div class="wb-application-list">${otherApplications.map(renderFollowItem).join('')}</div></section>` : '';
        const withoutApplications = items.filter((job) => !job.application);
        const unappliedMarkup = withoutApplications.length ? `<section class="wb-card wb-stage-group"><header class="wb-stage-head"><h2>尚未建立投递记录</h2><span>不会默认写入“待投递”</span></header><div class="wb-application-list">${withoutApplications.map(renderFollowItem).join('')}</div></section>` : '';
        resultArea.innerHTML = stageMarkup + otherMarkup + unappliedMarkup;
      }
    } catch (error) {
      if (signal?.aborted) return;
      resultArea.innerHTML = stateMessage('投递记录暂时无法读取', error?.message || '工作台岗位查询失败。', { kind: 'error', link: '/legacy/tracker', linkLabel: '打开现有岗位管理' });
    }
  };

  root.querySelector('[data-follow-filter]').addEventListener('change', (event) => {
    const next = new URLSearchParams(params);
    next.delete('application_status');
    next.delete('offset');
    if (event.currentTarget.value) next.set('application_status', event.currentTarget.value);
    navigate({ view: 'follow', params: next });
  });
  root.querySelector('[data-follow-page-size]').addEventListener('change', (event) => {
    const next = new URLSearchParams(params);
    next.set('limit', event.currentTarget.value);
    next.delete('offset');
    navigate({ view: 'follow', params: next });
  });
  root.querySelector('[data-follow-prev]').addEventListener('click', () => navigate({ view: 'follow', params: routeWith(route, { offset: Math.max(0, Number(params.get('offset') || 0) - pageSize) }) }));
  root.querySelector('[data-follow-next]').addEventListener('click', () => navigate({ view: 'follow', params: routeWith(route, { offset: Number(params.get('offset') || 0) + pageSize }) }));
  root.querySelector('[data-follow-jump]').addEventListener('submit', (event) => {
    event.preventDefault();
    const page = Number(event.currentTarget.elements.page.value);
    const maxPage = Math.max(1, Math.ceil(totalJobs / pageSize));
    const feedback = root.querySelector('[data-follow-page-feedback]');
    if (!Number.isInteger(page) || page < 1 || page > maxPage) { feedback.textContent = `请输入 1–${maxPage} 的页码。`; return; }
    feedback.textContent = '';
    navigate({ view: 'follow', params: routeWith(route, { offset: (page - 1) * pageSize }) });
  });
  resultArea.addEventListener('submit', async (event) => {
    const form = event.target.closest('[data-status-form]');
    if (!form) return;
    event.preventDefault();
    const card = form.closest('[data-follow-job]');
    const jobId = Number(card.dataset.followJob);
    const job = currentJobs.find((item) => Number(item.id) === jobId);
    if (!job?.application) {
      card.querySelector('[data-follow-feedback]').textContent = '缺少当前版本数据，请刷新后重试。';
      return;
    }
    const button = form.querySelector('button[type="submit"]');
    const feedback = card.querySelector('[data-follow-feedback]');
    button.disabled = true;
    try {
      await api('/workbench/jobs/status', {
        method: 'POST',
        body: JSON.stringify({ items: [{ job_id: jobId, expected_application_version: job.application.version }], status: form.elements.status.value }),
        signal,
      });
      if (signal?.aborted) return;
      notify('投递状态已由服务端保存。', 'success');
      await load();
    } catch (error) {
      if (!signal?.aborted) { feedback.textContent = error?.detail?.message || error?.message || '状态保存失败。'; feedback.classList.add('is-error'); button.disabled = false; }
    }
  });
  await load();
  return () => {};
}
