import { escapeHtml, safeHttpUrl, categoriesFromConfig, routeWith, stateMessage, statusValues } from '../shared.js';
import { renderJobCard as renderJob } from '../components/job-card.js';
import { openJobEditor } from '../components/job-editor.js';
import { openCompanyManager } from '../components/company-editor.js';

const SORT_OPTIONS = [
  ['created_at_desc', '添加时间：新到旧'], ['created_at_asc', '添加时间：旧到新'],
  ['title_asc', '岗位名称'], ['location_asc', '地点'], ['employment_type_asc', '招聘类型'],
  ['published_at_desc', '发布时间'], ['application_status_asc', '投递状态'],
];
const PAGE_SIZE = 50;
let restoreAfterDetail = null;

function dateLabel(value) {
  if (!value) return '—';
  const date = new Date(value);
  return Number.isNaN(date.valueOf()) ? String(value) : new Intl.DateTimeFormat('zh-CN', { dateStyle: 'medium' }).format(date);
}

function safeLink(url, label) {
  const href = safeHttpUrl(url);
  return href ? `<a class="wb-inline-link" href="${escapeHtml(href)}" target="_blank" rel="noopener noreferrer">${escapeHtml(label)} ↗</a>` : '—';
}

function selectedDetails(job) {
  const company = job.company?.name || '公司资料缺失';
  const application = job.application;
  const tags = Array.isArray(job.tags) ? job.tags : [];
  const sources = [
    `<div><dt>公司资料</dt><dd>${escapeHtml(company)} · ${escapeHtml(job.company?.notes || '暂无公司备注')} · 别名 ${escapeHtml(Array.isArray(job.company?.aliases) ? job.company.aliases.join('、') || '无' : job.company?.aliases || '无')}</dd></div>`,
    `<div><dt>来源网站</dt><dd>${escapeHtml(job.source_website || '—')}</dd></div>`,
    `<div><dt>原始页面标题</dt><dd>${escapeHtml(job.original_title || '—')}</dd></div>`,
    `<div><dt>来源链接</dt><dd>${safeLink(job.source_url, '打开来源')}</dd></div>`,
    `<div><dt>岗位发布时间</dt><dd>${escapeHtml(dateLabel(job.published_at))}</dd></div>`,
    `<div><dt>添加时间</dt><dd>${escapeHtml(dateLabel(job.created_at))}</dd></div>`,
    `<div><dt>岗位备注</dt><dd>${escapeHtml(job.notes || '—')}</dd></div>`,
    `<div><dt>投递备注</dt><dd>${escapeHtml(application?.notes || '—')}</dd></div>`,
    `<div><dt>整理与类别核对</dt><dd>${escapeHtml(job.organize_status || 'pending')} · ${escapeHtml(job.category_review_status === 'confirmed' ? '类别已确认' : '类别待核对')}</dd></div>`,
  ].join('');
  const description = job.description ? `<section class="wb-detail-description"><h3>岗位描述</h3><p>${escapeHtml(job.description)}</p></section>` : '';
  const structured = job.structured_data == null ? '' : `<details class="wb-structured-data"><summary>结构化事实</summary><pre>${escapeHtml(JSON.stringify(job.structured_data, null, 2))}</pre></details>`;
  return `<dialog class="wb-dialog wb-job-detail-drawer" data-selected-dialog aria-labelledby="selectedJobTitle"><section class="wb-selected-detail" data-selected-detail-job="${Number(job.id)}">
    <div class="wb-selected-head"><div><p class="wb-eyebrow">岗位详情 · #${Number(job.id)} · v${Number(job.version)}</p><h2 id="selectedJobTitle">${escapeHtml(job.title || '岗位名称缺失')}</h2><p>${escapeHtml(company)} · ${escapeHtml(job.location || '地点未填写')}</p></div><div class="wb-detail-actions"><button class="wb-button wb-button-outline" type="button" data-edit-job="${Number(job.id)}">编辑岗位</button><button class="wb-button wb-button-quiet" type="button" data-clear-job>关闭详情</button></div></div>
    <div class="wb-detail-badges"><span class="wb-badge" data-tone="primary">${escapeHtml(job.job_category || '未分类')}</span><span class="wb-badge">${escapeHtml(job.employment_type || '类型未填写')}</span><span class="wb-badge" data-tone="info">${escapeHtml(application?.status || '未建立投递记录')}</span><span class="wb-badge">薪资 ${escapeHtml(job.salary_text || '未知')}</span>${tags.map((tag) => `<span class="wb-badge">${escapeHtml(tag)}</span>`).join('')}</div>
    ${description}<dl class="wb-detail-grid">${sources}</dl>${structured}<section class="wb-status-history"><h3>状态记录</h3><div data-status-history>正在读取…</div></section>
    <div class="wb-detail-actions"><a class="wb-button wb-button-outline" href="/jobs/${Number(job.id)}">打开原有独立详情页 ↗</a></div>
  </section></dialog>`;
}


export async function mount({ root, route, navigate, api, notify = () => {}, setNavigationGuard = () => {}, signal }) {
  const STATUSES = statusValues();
  const categories = categoriesFromConfig();
  const params = new URLSearchParams(route.params);
  const selectedIds = new Set();
  const listParams = new URLSearchParams(params);
  listParams.delete('view');
  listParams.delete('job_id');
  let listContextKey = listParams.toString();
  if (restoreAfterDetail && restoreAfterDetail.listContextKey === listContextKey) {
    restoreAfterDetail.selection.forEach((id) => selectedIds.add(id));
  } else if (restoreAfterDetail) {
    restoreAfterDetail = null;
  }
  let items = [];
  let companies = [];
  let searchTimer = 0;
  let loadSequence = 0;
  let loadController = null;
  const busyJobs = new Set();
  let displayMode = 'rows';
  let activeEditor = null;
  let activeManager = null;
  const appRoot = document.querySelector('#workbenchApp') || root;

  root.innerHTML = `<header class="wb-page-heading"><div><h1>岗位库</h1><p class="wb-page-poetry wb-poetry" data-workbench-poetry aria-label="诗词展示"></p></div><div class="wb-heading-actions"><button class="wb-button wb-button-primary" type="button" data-new-job>＋ 新增岗位</button><button class="wb-button wb-button-outline" type="button" data-company-manager>公司管理</button></div></header>
    <section class="wb-card wb-categories-card" aria-labelledby="categoryHeading"><div class="wb-section-heading"><div><h2 id="categoryHeading">职位分类</h2><p>先看分布，再决定下一步整理什么。</p></div><span class="wb-muted-label" data-results-total>加载中</span></div><div class="wb-category-cloud" data-category-cloud></div></section>
    <section class="wb-card wb-toolbar-card" aria-label="岗位筛选"><form class="wb-filter-form" data-filter-form>
      <div class="wb-filter-topline"><label class="wb-field wb-search"><span class="wb-sr-label">搜索公司、岗位、地点或标签</span><input name="q" type="search" value="${escapeHtml(params.get('q') || '')}" maxlength="200" placeholder="搜索公司、职位、标签" autocomplete="off"></label><button class="wb-button wb-button-quiet" type="button" data-clear-filters>清空筛选</button></div>
      <div class="wb-filter-grid"><fieldset class="wb-facet-field" data-facet="locations"><legend>地点</legend><details class="wb-facet-filter"><summary><span data-facet-summary>全部地点</span><span class="wb-facet-arrow" aria-hidden="true">⌄</span></summary><div class="wb-facet-popover"><div class="wb-facet-options" data-facet-options></div><button class="wb-text-button" type="button" data-facet-clear>清空地点</button></div></details></fieldset><fieldset class="wb-facet-field" data-facet="employment_types"><legend>招聘类型</legend><details class="wb-facet-filter"><summary><span data-facet-summary>全部类型</span><span class="wb-facet-arrow" aria-hidden="true">⌄</span></summary><div class="wb-facet-popover"><div class="wb-facet-options" data-facet-options></div><button class="wb-text-button" type="button" data-facet-clear>清空类型</button></div></details></fieldset><label class="wb-field">投递状态<select name="application_status"><option value="">全部状态</option>${STATUSES.map((status) => `<option value="${escapeHtml(status)}">${escapeHtml(status)}</option>`).join('')}</select></label></div>
      <details class="wb-advanced-filters" ${params.has('company_id') || params.has('category_review_status') || params.has('sort') ? 'open' : ''}><summary>更多筛选</summary><div class="wb-filter-grid"><label class="wb-field">归档范围<select name="organize_status"><option value="active">未归档</option><option value="organized">已整理</option><option value="archived">已归档</option><option value="all">全部</option></select></label><a class="wb-text-button" href="/workbench?view=library&legacy_status=true">核对历史状态</a><label class="wb-field">公司<select name="company_id"><option value="">所有公司</option></select></label><label class="wb-field">类别核对<select name="category_review_status"><option value="">全部</option><option value="pending">待核对</option><option value="confirmed">已确认</option></select></label><label class="wb-field">排序<select name="sort">${SORT_OPTIONS.map(([value, label]) => `<option value="${value}">${label}</option>`).join('')}</select></label></div></details>
    </form><div class="wb-active-filters" data-active-filters aria-label="已选筛选条件"></div></section>
    <section class="wb-card wb-bulk-toolbar" data-bulk-bar hidden aria-label="批量整理"><label class="wb-check-field"><input type="checkbox" data-select-page>选择本页</label><strong data-selected-count>已选择 0 条</strong><label class="wb-field">批量类别<select data-bulk-category><option value="">不修改类别</option>${categories.map((category) => `<option value="${escapeHtml(category)}">${escapeHtml(category)}</option>`).join('')}</select></label><label class="wb-field">追加标签<input type="text" data-bulk-tags maxlength="300" placeholder="逗号分隔"></label><button class="wb-button wb-button-outline" type="button" data-bulk-organize>应用类别/标签</button><button class="wb-button wb-button-outline" type="button" data-bulk-archive>归档</button><button class="wb-button wb-button-outline" type="button" data-bulk-restore>恢复到已整理</button><label class="wb-field">批量投递状态<select data-bulk-status><option value="">选择状态</option>${STATUSES.map((status) => `<option value="${escapeHtml(status)}">${escapeHtml(status)}</option>`).join('')}</select></label><button class="wb-button wb-button-outline" type="button" data-bulk-apply-status>更新状态</button><button class="wb-button wb-button-danger" type="button" data-bulk-delete>删除所选</button><button class="wb-button wb-button-quiet" type="button" data-clear-selection>取消选择</button></section>
    <div data-selected-area></div><div class="wb-results-head"><strong>岗位列表</strong><span data-result-caption>正在读取工作台岗位资料</span><div class="wb-layout-switch" role="group" aria-label="列表显示方式"><button class="is-active" type="button" data-layout-choice="rows" aria-pressed="true">列表</button><button type="button" data-layout-choice="cards" aria-pressed="false">卡片</button></div></div><section class="wb-job-list" data-job-results data-layout="rows" aria-live="polite"></section><div class="wb-table-foot"><span data-page-caption></span><span><button class="wb-button wb-button-quiet" type="button" data-page-prev>上一页</button> <button class="wb-button wb-button-quiet" type="button" data-page-next>下一页</button></span></div>`;
  root.querySelector('[data-filter-form]').id = 'filterApply';

  const filterForm = root.querySelector('[data-filter-form]');
  const resultArea = root.querySelector('[data-job-results]');
  const companySelect = filterForm.elements.company_id;
  const facetFields = [...filterForm.querySelectorAll('[data-facet]')];
  const categoryCloud = root.querySelector('[data-category-cloud]');
  const selectedArea = root.querySelector('[data-selected-area]');

  function updateFilters(next) {
    window.clearTimeout(searchTimer);
    next.delete('job_id');
    route = navigate({ view: 'library', params: next, refresh: false });
    [...params.keys()].forEach((key) => params.delete(key));
    next.forEach((value, key) => params.append(key, value));
    listContextKey = next.toString();
    selectedIds.clear();
    void load();
  }

  function updateRows(payload) {
    for (const updated of payload.items || []) {
      const index = items.findIndex((item) => item.id === updated.id);
      if (index < 0) continue;
      items[index] = updated;
      const row = resultArea.querySelector(`[data-job-id="${updated.id}"]`);
      if (row) row.outerHTML = renderJob(updated, selectedIds.has(updated.id));
    }
    window.JobWorkbench?.preferences?.apply?.();
  }

  function savedFeedback(payload, label) {
    updateRows(payload);
    const change = payload.changes?.[0];
    notify(label, 'success', change ? { label: '撤销', run: async () => {
      const result = await api(`/workbench/status-changes/${change.id}/undo`, { method: 'POST' });
      if (!signal?.aborted) updateRows(result);
      if (!signal?.aborted && params.has('application_status')) await load();
    } } : null);
  }

  async function automaticStatus(job, action) {
    if (!job) return job;
    try {
      const payload = await api('/workbench/jobs/status-action', { method: 'POST', body: JSON.stringify({ job_id: job.id, expected_application_version: job.application?.version ?? null, action }), signal });
      if (signal?.aborted) return job;
      if (payload.changes?.length) savedFeedback(payload, '已按点击行为标记为已投递');
      return payload.items?.[0] || job;
    } catch (error) { if (!signal?.aborted) notify(`自动记录未保存：${error.message}`, 'error'); return job; }
  }

  const fillOptions = (select, values, selected) => {
    const selectedValues = new Set(Array.isArray(selected) ? selected.map(String) : [String(selected || '')]);
    for (const option of [...select.options]) if (option.value !== '') option.remove();
    for (const value of values) {
      const option = document.createElement('option');
      option.value = String(value.value ?? value.id ?? '');
      option.textContent = String(value.label ?? value.name ?? value.value ?? '');
      option.selected = selectedValues.has(option.value);
      select.append(option);
    }
    if (!select.multiple && selectedValues.has('')) select.value = '';
  };

  function updateFacetSummary(field) {
    const selected = [...field.querySelectorAll('input[type="checkbox"]:checked')].map((input) => input.dataset.label);
    field.querySelector('[data-facet-summary]').textContent = selected.length === 0
      ? (field.dataset.facet === 'locations' ? '全部地点' : '全部类型')
      : selected.length <= 2 ? selected.join('、') : `已选 ${selected.length} 项`;
    field.classList.toggle('has-selection', selected.length > 0);
  }

  function fillFacet(field, values, selectedValues) {
    const selected = new Set(selectedValues.map(String));
    const options = values.map((facet) => ({ value: String(facet.value || ''), count: Number(facet.count) || 0 }));
    for (const value of selected) if (value && !options.some((option) => option.value === value)) options.push({ value, count: null });
    field.querySelector('[data-facet-options]').innerHTML = options.length
      ? options.filter((option) => option.value).map((option) => `<label class="wb-facet-option"><input type="checkbox" name="${field.dataset.facet}" value="${escapeHtml(option.value)}" data-label="${escapeHtml(option.value)}" ${selected.has(option.value) ? 'checked' : ''}><span>${escapeHtml(option.value)}</span>${option.count === null ? '' : `<small>${option.count}</small>`}</label>`).join('')
      : '<p class="wb-facet-empty">暂无可选项</p>';
    updateFacetSummary(field);
  }

  function setActiveFilters() {
    filterForm.elements.q.value = params.get('q') || '';
    filterForm.elements.company_id.value = params.get('company_id') || '';
    filterForm.elements.application_status.value = params.get('application_status') || '';
    filterForm.elements.category_review_status.value = params.get('category_review_status') || '';
    filterForm.elements.organize_status.value = params.get('organize_status') || 'active';
    filterForm.elements.sort.value = params.get('sort') || 'created_at_desc';
    facetFields.forEach((field) => {
      const selected = new Set(params.getAll(field.dataset.facet));
      field.querySelectorAll('input[type="checkbox"]').forEach((input) => { input.checked = selected.has(input.value); });
      updateFacetSummary(field);
    });
  }

  function updateSelectionToolbar() {
    const selected = root.querySelector('[data-bulk-bar]');
    selected.hidden = selectedIds.size === 0;
    root.querySelector('[data-selected-count]').textContent = `已选择 ${selectedIds.size} 条（仅当前页）`;
    const pageSelector = root.querySelector('[data-select-page]');
    pageSelector.checked = items.length > 0 && items.every((item) => selectedIds.has(Number(item.id)));
    pageSelector.indeterminate = selectedIds.size > 0 && !pageSelector.checked;
  }

  function renderItems() {
    resultArea.dataset.layout = displayMode;
    if (!items.length) {
      resultArea.innerHTML = '<div class="wb-empty-state"><strong>当前筛选没有岗位资料</strong><p>调整筛选条件，或新增一条岗位资料。</p><button class="wb-button wb-button-primary" type="button" data-new-job>＋ 新增岗位</button></div>';
    } else resultArea.innerHTML = items.map((item) => renderJob(item, selectedIds.has(Number(item.id)))).join('');
    window.JobWorkbench?.preferences?.apply?.();
  }

  function restoreListContext() {
    if (params.has('job_id')) return;
    if (restoreAfterDetail) {
      const context = restoreAfterDetail;
      restoreAfterDetail = null;
      requestAnimationFrame(() => {
        window.scrollTo({ top: context.scrollY, behavior: 'auto' });
        const focus = root.querySelector(`[data-open-workbench-job="${context.jobId}"]`);
        if (focus) focus.focus({ preventScroll: true });
        else root.focus({ preventScroll: true });
      });
    }
  }

  async function load() {
    const sequence = ++loadSequence;
    loadController?.abort();
    loadController = new AbortController();
    const requestSignal = signal ? AbortSignal.any([signal, loadController.signal]) : loadController.signal;
    resultArea.setAttribute('aria-busy', 'true');
    if (!items.length) resultArea.innerHTML = '<div class="wb-view-skeleton"><span class="wb-spinner" aria-hidden="true"></span><span>正在读取岗位资料…</span></div>';
    selectedArea.replaceChildren();
    const requestParams = new URLSearchParams(params);
    requestParams.delete('view');
    const selectedJobId = Number(requestParams.get('job_id'));
    requestParams.delete('job_id');
    requestParams.set('limit', String(PAGE_SIZE));
    if (!requestParams.has('organize_status')) requestParams.set('organize_status', 'active');
    const requests = [api(`/workbench/jobs?${requestParams.toString()}`, { signal: requestSignal }), companies.length ? Promise.resolve(companies) : api('/companies', { signal: requestSignal })];
    if (selectedJobId > 0) requests.push(api(`/workbench/jobs/${selectedJobId}`, { signal: requestSignal }));
    const [jobsResult, companiesResult, detailResult] = await Promise.allSettled(requests);
    if (requestSignal.aborted || sequence !== loadSequence) return;
    resultArea.setAttribute('aria-busy', 'false');
    if (jobsResult.status !== 'fulfilled') {
      const error = jobsResult.reason;
      resultArea.innerHTML = stateMessage('岗位列表暂时无法读取', error?.message || '工作台岗位查询失败。', { kind: 'error', link: '/legacy/tracker', linkLabel: '打开现有岗位管理' });
      root.querySelector('[data-result-caption]').textContent = '查询未成功';
      root.querySelector('[data-results-total]').textContent = '不可用';
      root.querySelector('[data-page-caption]').textContent = '';
      return;
    }

    const payload = jobsResult.value || {};
    const legacyLink = root.querySelector('a[href*="legacy_status=true"]');
    if (legacyLink) legacyLink.textContent = `核对历史状态（${Number(payload.counts?.legacy_status) || 0}）`;
    items = Array.isArray(payload.items) ? payload.items : [];
    const total = Number(payload.total) || 0;
    const facets = payload.facets || {};
    const categoryCounts = new Map((facets.categories || []).map((facet) => [facet.value, facet.count]));
    categoryCloud.innerHTML = `<a class="wb-chip ${params.get('job_category') ? '' : 'is-active'}" href="/workbench?view=library" data-category-value="">全部类别</a>${categories.map((category) => `<a class="wb-chip ${category === params.get('job_category') ? 'is-active' : ''}" href="/workbench?view=library&job_category=${encodeURIComponent(category)}" data-category-value="${escapeHtml(category)}">${escapeHtml(category)}${categoryCounts.has(category) ? `<span class="wb-chip-count">${escapeHtml(categoryCounts.get(category))}</span>` : ''}</a>`).join('')}`;
    root.querySelector('[data-active-filters]').innerHTML = [...params.entries()].filter(([key]) => !['offset', 'job_id', 'view'].includes(key)).map(([key, value]) => `<button class="wb-filter-token" type="button" data-remove-filter="${escapeHtml(key)}" data-filter-value="${escapeHtml(value)}" aria-label="移除筛选 ${escapeHtml(value)}">${escapeHtml(key === 'legacy_status' ? '历史状态待核对' : key === 'company_id' ? companies.find((company) => String(company.id) === value)?.name || '指定公司' : value)} ×</button>`).join('');
    if (companiesResult.status === 'fulfilled' && Array.isArray(companiesResult.value)) {
      companies = companiesResult.value;
      fillOptions(companySelect, companies.map((company) => ({ id: company.id, label: company.name })), params.get('company_id'));
    } else if (companiesResult.status === 'rejected') {
      companies = [];
    }
    fillFacet(facetFields[0], facets.locations || [], params.getAll('locations'));
    fillFacet(facetFields[1], facets.employment_types || [], params.getAll('employment_types'));
    setActiveFilters();
    renderItems();
    root.querySelector('[data-results-total]').textContent = `${total} 条结果`;
    root.querySelector('[data-result-caption]').textContent = '点击状态即可修改';
    const offset = Number(params.get('offset') || 0);
    root.querySelector('[data-page-caption]').textContent = `显示 ${offset + (items.length ? 1 : 0)}–${offset + items.length} 条，共 ${total} 条`;
    root.querySelector('[data-page-prev]').disabled = offset <= 0;
    root.querySelector('[data-page-next]').disabled = offset + items.length >= total;

    if (selectedJobId > 0) {
      if (detailResult?.status === 'fulfilled') {
        const detail = await automaticStatus(detailResult.value, 'detail');
        if (requestSignal.aborted || sequence !== loadSequence) return;
        selectedArea.innerHTML = selectedDetails(detail);
        const detailDialog = selectedArea.querySelector('[data-selected-dialog]');
        detailDialog.addEventListener('cancel', (event) => {
          event.preventDefault();
          navigate({ view: 'library', params: routeWith(route, { job_id: null }) });
        });
        detailDialog.showModal();
        try {
          const history = await api(`/workbench/jobs/${selectedJobId}/status-history`, { signal: requestSignal });
          if (!requestSignal.aborted && sequence === loadSequence) selectedArea.querySelector('[data-status-history]').innerHTML = history.length
            ? history.map((entry) => `<p><time>${escapeHtml(dateLabel(entry.created_at))}</time> ${escapeHtml(entry.previous_status || '建立记录')} → <strong>${escapeHtml(entry.status)}</strong> <small>${escapeHtml(({ manual: '手动修改', detail: '查看详情', apply: '点击投递', undo: '撤销', email: '邮件确认' })[entry.source] || '状态修改')}</small></p>`).join('')
            : '<p class="wb-muted-label">尚无状态变更记录；不会推断过去经历的阶段。</p>';
        } catch { if (!requestSignal.aborted) selectedArea.querySelector('[data-status-history]').textContent = '历史记录暂时无法读取'; }
      }
      else selectedArea.innerHTML = stateMessage(`岗位 #${selectedJobId} 暂时无法读取`, detailResult?.reason?.message || '该岗位不存在或当前不可用。', { kind: 'error', link: '/workbench?view=library', linkLabel: '返回岗位库' });
    }
    updateSelectionToolbar();
    restoreListContext();
  }

  function applyForm(event) {
    event?.preventDefault();
    const next = new URLSearchParams(params);
    const keys = ['q', 'company_id', 'locations', 'employment_types', 'application_status', 'category_review_status', 'organize_status', 'sort'];
    keys.forEach((key) => next.delete(key));
    const fields = new FormData(filterForm);
    for (const key of keys) {
      const values = key === 'locations' || key === 'employment_types' ? fields.getAll(key) : [fields.get(key)];
      for (const raw of values) {
        const value = String(raw || '').trim();
        if (value && !(key === 'organize_status' && value === 'active') && !(key === 'sort' && value === 'created_at_desc')) next.append(key, value);
      }
    }
    next.delete('offset');
    updateFilters(next);
  }

  function selectedItems() {
    return items.filter((item) => selectedIds.has(Number(item.id)));
  }

  async function runBulkOrganize(changes) {
    const selected = selectedItems();
    if (!selected.length) return;
    const payload = { items: selected.map((item) => ({ job_id: Number(item.id), expected_version: Number(item.version) })), ...changes };
    try {
      await api('/workbench/jobs/bulk-organize', { method: 'POST', body: JSON.stringify(payload), signal });
      notify('所选岗位资料已更新。', 'success');
      selectedIds.clear();
      await load();
    } catch (error) {
      notify(error?.status === 409 ? '所选岗位已被其他操作更新。请刷新后重新选择。' : error?.message || '批量整理失败，所选内容仍保留。', 'error');
    }
  }

  function registerGuard(cleanup) {
    activeEditor = cleanup;
    setNavigationGuard(() => cleanup?.canLeave?.() ?? true);
  }
  function clearGuard(cleanup) {
    if (activeEditor !== cleanup && activeManager !== cleanup) return;
    if (activeEditor === cleanup) activeEditor = null;
    if (activeManager === cleanup) activeManager = null;
    setNavigationGuard(null);
  }

  function openEditor(item = null) {
    if (activeEditor || activeManager) return;
    const cleanup = openJobEditor({
      root: appRoot, api, item, notify,
      onClosed: () => clearGuard(cleanup),
      onSaved: async (saved) => {
        if (saved?.id) {
          const next = new URLSearchParams(route.params);
          next.set('job_id', String(saved.id));
          if (String(route.params.get('job_id')) !== String(saved.id)) {
            navigate({ view: 'library', params: next });
            return;
          }
        }
        await load();
      },
    });
    registerGuard(cleanup);
  }

  function openCompanyManagerDialog() {
    if (activeEditor || activeManager) return;
    const cleanup = openCompanyManager({
      root: appRoot, api, notify,
      onClosed: () => clearGuard(cleanup),
      onChange: () => { void load(); },
    });
    activeManager = cleanup;
    setNavigationGuard(() => cleanup?.canLeave?.() ?? true);
  }

  const onSearchInput = (event) => {
    if (event?.isComposing) return;
    window.clearTimeout(searchTimer);
    searchTimer = window.setTimeout(() => { if (!signal?.aborted) applyForm(); }, 320);
  };
  const onClearFilters = () => updateFilters(new URLSearchParams());
  const onPreviousPage = () => navigate({ view: 'library', params: routeWith(route, { offset: Math.max(0, Number(params.get('offset') || 0) - PAGE_SIZE) }) });
  const onNextPage = () => navigate({ view: 'library', params: routeWith(route, { offset: Number(params.get('offset') || 0) + PAGE_SIZE }) });
  filterForm.addEventListener('submit', applyForm);
  filterForm.elements.q.addEventListener('input', onSearchInput);
  root.querySelector('[data-clear-filters]').addEventListener('click', onClearFilters);
  root.querySelector('[data-page-prev]').addEventListener('click', onPreviousPage);
  root.querySelector('[data-page-next]').addEventListener('click', onNextPage);
  const onCategoryClick = (event) => {
    const link = event.target.closest('[data-category-value]');
    if (!link) return;
    event.preventDefault();
    const next = new URLSearchParams(params);
    next.delete('job_category');
    next.delete('direction');
    if (link.dataset.categoryValue) next.set('job_category', link.dataset.categoryValue);
    next.delete('offset');
    updateFilters(next);
  };
  categoryCloud.addEventListener('click', onCategoryClick);
  const onRootChange = async (event) => {
    const statusInput = event.target.closest('[data-quick-status]');
    if (statusInput) {
      const job = items.find((item) => item.id === Number(statusInput.dataset.quickStatus));
      if (!job || busyJobs.has(job.id)) return;
      const previous = job.application?.status || '待投递';
      busyJobs.add(job.id); statusInput.disabled = true;
      try {
        const result = await api('/workbench/jobs/status', { method: 'POST', body: JSON.stringify({ items: [{ job_id: job.id, expected_application_version: job.application?.version ?? null }], status: statusInput.value }), signal });
        if (!signal?.aborted) { savedFeedback(result, '投递状态已保存'); resultArea.querySelector(`[data-quick-status="${job.id}"]`)?.focus({ preventScroll: true }); if (params.has('application_status') || params.has('legacy_status')) await load(); }
      } catch (error) { statusInput.value = previous; if (!signal?.aborted) notify(error.message || '保存失败，已恢复原状态', 'error'); }
      finally { busyJobs.delete(job.id); statusInput.disabled = false; }
      return;
    }
    const facet = event.target.closest('[data-facet]');
    if (facet && event.target.matches('input[type="checkbox"]')) { updateFacetSummary(facet); applyForm(); return; }
    if (event.target.closest('[data-filter-form]') && event.target.matches('select')) { applyForm(); return; }
    const checkbox = event.target.closest('[data-select-job]');
    if (checkbox) {
      const id = Number(checkbox.dataset.selectJob);
      if (checkbox.checked) selectedIds.add(id);
      else selectedIds.delete(id);
      root.querySelector(`[data-job-id="${id}"]`)?.classList.toggle('is-selected', checkbox.checked);
      updateSelectionToolbar();
      return;
    }
    if (event.target.matches('[data-select-page]')) {
      if (event.target.checked) items.forEach((item) => selectedIds.add(Number(item.id)));
      else selectedIds.clear();
      resultArea.querySelectorAll('[data-select-job]').forEach((input) => { input.checked = event.target.checked; input.closest('[data-job-id]')?.classList.toggle('is-selected', event.target.checked); });
      updateSelectionToolbar();
    }
  };
  root.addEventListener('change', onRootChange);
  const onRootClick = async (event) => {
    const removeFilter = event.target.closest('[data-remove-filter]');
    if (removeFilter) { const next = new URLSearchParams(params); next.delete(removeFilter.dataset.removeFilter, removeFilter.dataset.filterValue); next.delete('offset'); updateFilters(next); return; }
    const apply = event.target.closest('[data-apply-job]');
    if (apply) { void automaticStatus(items.find((item) => item.id === Number(apply.dataset.applyJob)), 'apply'); return; }
    const clearFacet = event.target.closest('[data-facet-clear]');
    if (clearFacet) {
      const field = clearFacet.closest('[data-facet]');
      field.querySelectorAll('input[type="checkbox"]').forEach((input) => { input.checked = false; });
      updateFacetSummary(field);
      applyForm();
      return;
    }
    const layoutChoice = event.target.closest('[data-layout-choice]');
    if (layoutChoice) {
      displayMode = layoutChoice.dataset.layoutChoice === 'rows' ? 'rows' : 'cards';
      root.querySelectorAll('[data-layout-choice]').forEach((button) => {
        const active = button === layoutChoice;
        button.classList.toggle('is-active', active);
        button.setAttribute('aria-pressed', String(active));
      });
      renderItems();
      updateSelectionToolbar();
      return;
    }
    if (event.target.closest('[data-new-job]')) { openEditor(); return; }
    if (event.target.closest('[data-company-manager]')) { openCompanyManagerDialog(); return; }
    const edit = event.target.closest('[data-edit-job]');
    if (edit) {
      const item = items.find((entry) => Number(entry.id) === Number(edit.dataset.editJob));
      if (item) openEditor(item);
      else {
        try { openEditor(await api(`/workbench/jobs/${Number(edit.dataset.editJob)}`, { signal })); }
        catch (error) { notify(error?.message || '岗位暂时无法读取。', 'error'); }
      }
      return;
    }
    const open = event.target.closest('[data-open-workbench-job]');
    if (open) {
      if (event.ctrlKey || event.metaKey || event.shiftKey || event.altKey || event.button !== 0) return;
      event.preventDefault();
      restoreAfterDetail = {
        jobId: Number(open.dataset.openWorkbenchJob), scrollY: window.scrollY,
        selection: [...selectedIds], listContextKey,
      };
      navigate({ view: 'library', params: routeWith(route, { job_id: open.dataset.openWorkbenchJob }) });
      return;
    }
    if (event.target.closest('[data-clear-job]')) {
      navigate({ view: 'library', params: routeWith(route, { job_id: null }) });
      return;
    }
    if (event.target.closest('[data-clear-selection]')) {
      selectedIds.clear();
      resultArea.querySelectorAll('[data-select-job]').forEach((input) => { input.checked = false; input.closest('[data-job-id]')?.classList.remove('is-selected'); });
      updateSelectionToolbar();
      return;
    }
    if (event.target.closest('[data-bulk-organize]')) {
      const jobCategory = root.querySelector('[data-bulk-category]').value;
      const tags = root.querySelector('[data-bulk-tags]').value.split(/[\n,，;；]/).map((tag) => tag.trim()).filter(Boolean);
      if (!jobCategory && !tags.length) { notify('选择一个类别或填写要追加的标签。', 'error'); return; }
      await runBulkOrganize({ ...(jobCategory ? { job_category: jobCategory } : {}), ...(tags.length ? { tags_to_add: [...new Set(tags)] } : {}) });
      return;
    }
    if (event.target.closest('[data-bulk-archive]')) { await runBulkOrganize({ organize_status: 'archived' }); return; }
    if (event.target.closest('[data-bulk-restore]')) { await runBulkOrganize({ organize_status: 'organized' }); return; }
    if (event.target.closest('[data-bulk-delete]')) {
      const selected = selectedItems();
      if (!selected.length || !window.confirm(`永久删除所选 ${selected.length} 条岗位及其投递记录？归档岗位请使用归档筛选和恢复操作。`)) return;
      const deletedIds = new Set(selected.map((item) => Number(item.id)));
      try {
        const result = await api('/jobs/bulk-delete', { method: 'POST', body: JSON.stringify({ job_ids: selected.map((item) => Number(item.id)) }), signal });
        notify(`已删除 ${Number(result?.deleted_count) || selected.length} 条岗位。`, 'success');
        selectedIds.clear();
        if (params.has('job_id') && deletedIds.has(Number(params.get('job_id')))) navigate({ view: 'library', params: routeWith(route, { job_id: null }) });
        else await load();
      } catch (error) { notify(error?.message || '批量删除失败，所选记录仍保留。', 'error'); }
      return;
    }
    if (event.target.closest('[data-bulk-apply-status]')) {
      const selected = selectedItems();
      const status = root.querySelector('[data-bulk-status]').value;
      if (!status) { notify('请先选择要设置的投递状态。', 'error'); return; }
      try {
        await api('/workbench/jobs/status', { method: 'POST', body: JSON.stringify({ items: selected.map((item) => ({ job_id: item.id, expected_application_version: item.application?.version ?? null })), status }), signal });
        notify('所选投递状态已保存。', 'success');
        selectedIds.clear();
        await load();
      } catch (error) { notify(error?.status === 409 ? '部分投递记录已变化，请刷新后重新选择。' : error?.message || '批量状态更新失败。', 'error'); }
    }
  };
  root.addEventListener('click', onRootClick);
  const onDocumentClick = (event) => {
    const active = event.target.closest?.('[data-facet]');
    facetFields.forEach((field) => { if (field !== active) field.querySelector('details').open = false; });
  };
  const onDocumentKeydown = (event) => {
    if (event.key !== 'Escape') return;
    const open = facetFields.find((field) => field.querySelector('details').open);
    if (!open) return;
    event.preventDefault();
    open.querySelector('details').open = false;
    open.querySelector('summary').focus();
  };
  document.addEventListener('click', onDocumentClick);
  document.addEventListener('keydown', onDocumentKeydown);

  setActiveFilters();
  await load();
  return () => {
    loadController?.abort();
    window.clearTimeout(searchTimer);
    filterForm.removeEventListener('submit', applyForm);
    filterForm.elements.q.removeEventListener('input', onSearchInput);
    root.querySelector('[data-clear-filters]')?.removeEventListener('click', onClearFilters);
    root.querySelector('[data-page-prev]')?.removeEventListener('click', onPreviousPage);
    root.querySelector('[data-page-next]')?.removeEventListener('click', onNextPage);
    categoryCloud.removeEventListener('click', onCategoryClick);
    root.removeEventListener('change', onRootChange);
    root.removeEventListener('click', onRootClick);
    document.removeEventListener('click', onDocumentClick);
    document.removeEventListener('keydown', onDocumentKeydown);
    if (activeEditor) activeEditor();
    if (activeManager) activeManager();
    setNavigationGuard(null);
  };
}
