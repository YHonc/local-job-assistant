import { escapeHtml, categoriesFromConfig, routeWith, stateMessage } from '../shared.js';

const FIELD_LABELS = Object.freeze({
  company_name: '公司名称', title: '岗位名称', location: '地点', employment_type: '招聘类型',
  job_category: '岗位类别', published_at: '发布时间', description: '岗位描述', source_website: '来源网站',
});
const CATEGORIES = categoriesFromConfig();
const DRAFT_PAGE_SIZE = 20;

function newIdempotencyKey() {
  if (globalThis.crypto?.randomUUID) return crypto.randomUUID();
  const values = new Uint8Array(16);
  globalThis.crypto?.getRandomValues?.(values);
  return `draft-${Date.now()}-${Array.from(values, (value) => value.toString(16).padStart(2, '0')).join('')}`;
}

function makeJobCard(job) {
  const id = Number(job.id);
  const company = job.company?.name || '公司资料缺失';
  const reasons = [];
  if (job.organize_status === 'pending') reasons.push('待整理');
  if (job.category_review_status === 'pending') reasons.push('类别待核对');
  if (!job.location) reasons.push('地点未填写');
  if (!job.employment_type) reasons.push('招聘类型未填写');
  return `<article class="wb-pending-job wb-card"><div class="wb-pending-job-main"><span class="wb-company-mark" aria-hidden="true">${escapeHtml([...company][0] || '职')}</span><div><strong>${escapeHtml(job.title || '岗位名称缺失')}</strong><span>${escapeHtml(company)} · ${escapeHtml(job.location || '地点未填写')} · ${escapeHtml(job.employment_type || '类型未填写')}</span></div></div><div class="wb-pending-reasons">${reasons.map((reason) => `<span class="wb-badge" data-tone="warn">${escapeHtml(reason)}</span>`).join('')}</div><a class="wb-button wb-button-outline" href="/workbench?view=library&job_id=${id}" data-pending-job="${id}">查看工作台详情 ↗</a></article>`;
}

function renderSuggestionFields(suggestions = {}, userFields = {}) {
  if (!suggestions || typeof suggestions !== 'object') return '<p class="wb-muted-copy">没有字段建议。</p>';
  const fields = Object.keys(FIELD_LABELS).filter((key) => suggestions[key] !== undefined && suggestions[key] !== null && suggestions[key] !== '');
  if (!fields.length) return '<p class="wb-muted-copy">没有可供选择的字段建议。</p>';
  return `<div class="wb-suggestion-fields">${fields.map((key) => `<label class="wb-suggestion-field"><input type="checkbox" name="suggestion_fields" value="${key}" data-suggestion-field="${key}"><span><strong>${FIELD_LABELS[key]}</strong><small>人工值：${escapeHtml(userFields[key] ?? '未填写')}</small></span><span class="wb-suggestion-value">${escapeHtml(suggestions[key])}</span></label>`).join('')}</div>${suggestions.warnings?.length ? `<p class="wb-hint-copy">提示：${escapeHtml(suggestions.warnings.join('；'))}</p>` : ''}`;
}

function makeDraftCard(draft) {
  const id = Number(draft.id);
  const userFields = draft.user_fields && typeof draft.user_fields === 'object' ? draft.user_fields : {};
  const suggestions = draft.suggested_fields && typeof draft.suggested_fields === 'object' ? draft.suggested_fields : {};
  const isConfirmed = draft.status === 'confirmed';
  const input = (key, type = 'text') => `<label class="wb-field">${FIELD_LABELS[key]}<input data-draft-field="${key}" name="${key}" type="${type}" value="${escapeHtml(userFields[key] ?? '')}" ${isConfirmed ? 'disabled' : ''}></label>`;
  const categoryOptions = `<option value="">请选择</option>${CATEGORIES.map((category) => `<option value="${escapeHtml(category)}" ${userFields.job_category === category ? 'selected' : ''}>${escapeHtml(category)}</option>`).join('')}`;
  return `<article class="wb-draft-card wb-card" data-draft-card="${id}" data-draft-version="${Number(draft.version)}" data-user-fields="${escapeHtml(JSON.stringify(userFields))}" data-allow-duplicate="false">
    <header class="wb-draft-head"><div><p class="wb-eyebrow">草稿 #${id} · v${Number(draft.version)}</p><h3>${escapeHtml(draft.original_title || userFields.title || suggestions.title || '待补充岗位信息')}</h3><p>${escapeHtml(draft.source_website || '来源待补充')} · ${escapeHtml(draft.status === 'extracted' ? '有待核对建议' : isConfirmed ? '已确认入库' : '待整理')}</p></div><span class="wb-badge" data-tone="${isConfirmed ? 'primary' : 'warn'}">${isConfirmed ? '已确认' : draft.status === 'extracted' ? '待核对' : '待整理'}</span></header>
    ${draft.error ? `<p class="wb-draft-error">${escapeHtml(draft.error)}</p>` : ''}
    <details class="wb-capture-details"><summary>查看已保存的网页文本</summary><pre>${escapeHtml(draft.captured_text || '（没有网页正文）')}</pre></details>
    <details class="wb-suggestion-details" ${draft.status === 'extracted' ? 'open' : ''}><summary>字段建议（逐项选择后才会写入人工字段）</summary>${renderSuggestionFields(suggestions, userFields)}<button class="wb-button wb-button-outline" type="button" data-accept-suggestions ${draft.status === 'extracted' && !isConfirmed ? '' : 'disabled'}>接受勾选建议</button></details>
    <form class="wb-draft-form" data-draft-form><div class="wb-draft-fields">${input('company_name')}${input('title')}${input('location')}${input('employment_type')}<label class="wb-field">岗位类别<select data-draft-field="job_category" name="job_category" ${isConfirmed ? 'disabled' : ''}>${categoryOptions}</select></label>${input('published_at', 'date')}${input('source_website')}</div><label class="wb-field">岗位描述<textarea name="description" data-draft-field="description" rows="3" ${isConfirmed ? 'disabled' : ''}>${escapeHtml(userFields.description ?? '')}</textarea></label><p class="wb-feedback" data-draft-feedback role="status" aria-live="polite"></p>
      <div class="wb-draft-actions">${isConfirmed ? `<span class="wb-confirmed-label">已确认岗位 #${Number(draft.confirmed_job_id) || '未知'}</span>` : `<button class="wb-button wb-button-outline" type="submit" data-save-draft>保存人工字段</button><button class="wb-button wb-button-outline" type="button" data-extract-draft title="此操作会调用已配置的模型">可选 AI 提取</button><button class="wb-button wb-button-primary" type="button" data-confirm-draft>确认并加入岗位库</button>`}${Number(draft.id) ? `<a class="wb-inline-link" href="/workbench?view=pending&draft_id=${id}" data-focus-draft="${id}">复制草稿链接 ↗</a>` : ''}</div>
    </form><div class="wb-duplicate-zone" data-duplicate-zone></div>
  </article>`;
}

function draftValuesFromForm(form) {
  return Object.fromEntries([...form.querySelectorAll('[data-draft-field]')].map((input) => [
    input.dataset.draftField,
    input.value.trim() || null,
  ]));
}

function parseDraftFields(card) {
  try { return JSON.parse(card.dataset.userFields || '{}') || {}; }
  catch { return {}; }
}

function changedDraftFields(current, baseline) {
  const changed = {};
  for (const [key, value] of Object.entries(current)) {
    const before = baseline?.[key] == null || baseline[key] === '' ? null : String(baseline[key]);
    const after = value == null || value === '' ? null : String(value);
    if (before !== after) changed[key] = value;
  }
  return changed;
}

export async function mount({ root, route, navigate, api, notify = () => {}, setNavigationGuard = () => {}, signal }) {
  root.innerHTML = `<header class="wb-page-heading"><div><h1>待整理</h1><p class="wb-page-subtitle">网页草稿、缺失资料与需要核对的分类集中在这里。</p></div><div class="wb-heading-actions"><a class="wb-button" href="#wbCaptureCard">保存网页草稿</a><a class="wb-button wb-button-outline" href="/legacy/tracker" title="旧岗位管理页继续可用">打开现有岗位管理</a></div></header>
    <section class="wb-callout" data-workbench-page-hint="pending"><span aria-hidden="true">↳</span><div><strong>网页草稿可以在不调用 AI 的情况下保存</strong><p>公司和岗位信息要由你填写或明确接受字段建议；确认前不会创建岗位。</p><button class="wb-button wb-button-quiet wb-hint-dismiss" type="button" data-dismiss-workbench-hint>关闭本页说明</button></div></section>
    <section class="wb-card wb-capture-card" id="wbCaptureCard"><div class="wb-section-heading"><div><h2>保存网页草稿</h2><p>保存来源和捕获文本不会调用模型，也不会创建公司或岗位。</p></div></div><form data-create-draft><div class="wb-draft-fields"><label class="wb-field">来源链接<input name="source_url" type="url" placeholder="https://..." maxlength="1000"></label><label class="wb-field">来源网站<input name="source_website" maxlength="200"></label><label class="wb-field">原始页面标题<input name="original_title" maxlength="500"></label></div><label class="wb-field">网页文本<textarea name="captured_text" rows="5" maxlength="100000" placeholder="粘贴招聘页面中与岗位有关的文字"></textarea></label><details class="wb-manual-fields"><summary>同时填写已核对的职位信息（可选）</summary><div class="wb-draft-fields"><label class="wb-field">公司名称<input name="company_name" maxlength="200"></label><label class="wb-field">岗位名称<input name="title" maxlength="300"></label><label class="wb-field">地点<input name="location" maxlength="300"></label><label class="wb-field">招聘类型<input name="employment_type" maxlength="100"></label><label class="wb-field">岗位类别<select name="job_category"><option value="">请选择</option>${CATEGORIES.map((category) => `<option value="${escapeHtml(category)}">${escapeHtml(category)}</option>`).join('')}</select></label><label class="wb-field">发布时间<input name="published_at" type="date"></label><label class="wb-field">来源网站<input name="source_website_field" maxlength="200"></label></div><label class="wb-field">岗位描述<textarea name="description" rows="3"></textarea></label></details><p class="wb-feedback" data-create-feedback role="status" aria-live="polite"></p><div class="wb-draft-actions"><button class="wb-button wb-button-primary" type="submit">保存草稿</button></div></form></section>
    <section class="wb-pending-section"><div class="wb-section-heading"><div><h2>需要核对的岗位</h2><p>显示未归档且标记为待整理或待核对类别的岗位。</p></div><span class="wb-section-count" data-pending-count>读取中</span></div><div class="wb-pending-jobs" data-pending-jobs></div><div class="wb-table-foot"><span data-pending-page-caption></span><span><button class="wb-button wb-button-quiet" type="button" data-pending-prev>上一页岗位</button> <button class="wb-button wb-button-quiet" type="button" data-pending-next>下一页岗位</button></span></div></section>
    <section class="wb-pending-section"><div class="wb-section-heading"><div><h2>网页草稿</h2><p>编辑人工字段、逐项接受建议，或确认创建岗位。</p></div><span class="wb-section-count" data-draft-count>读取中</span></div><div class="wb-drafts" data-drafts></div><div class="wb-table-foot"><span data-draft-page-caption></span><span><button class="wb-button wb-button-quiet" type="button" data-draft-prev>上一页草稿</button> <button class="wb-button wb-button-quiet" type="button" data-draft-next>下一页草稿</button></span></div></section>`;
  root.append(root.querySelector('.wb-capture-card'));

  const jobsArea = root.querySelector('[data-pending-jobs]');
  const draftsArea = root.querySelector('[data-drafts]');
  let createDraftKey = newIdempotencyKey();
  let draftTotal = 0;
  let jobTotal = 0;
  const draftInputCache = new Map();
  let draftOffset = Math.max(0, Number(route.params.get('draft_offset') || 0));
  let jobOffset = Math.max(0, Number(route.params.get('pending_offset') || 0));
  let jobLoadSequence = 0;
  let draftLoadSequence = 0;
  const setFeedback = (node, message, isError = false) => { node.textContent = message || ''; node.classList.toggle('is-error', isError); };
  const errorText = (error) => {
    if (error?.detail && typeof error.detail === 'object' && error.detail.code === 'confirmed_job_missing') return '该草稿已确认，但关联岗位已删除。为避免重复创建，这里不会再次入库。';
    if (error?.detail && typeof error.detail === 'object' && error.detail.message) return error.detail.message;
    return error?.message || '请求失败，请保留当前输入并重试。';
  };

  function updateDraftInputCache(card) {
    if (!card) return;
    const id = Number(card.dataset.draftCard);
    const current = draftValuesFromForm(card.querySelector('[data-draft-form]'));
    if (Object.keys(changedDraftFields(current, parseDraftFields(card))).length) draftInputCache.set(id, current);
    else draftInputCache.delete(id);
  }

  function restoreDraftInputCache() {
    for (const card of draftsArea.querySelectorAll('[data-draft-card]')) {
      const values = draftInputCache.get(Number(card.dataset.draftCard));
      if (!values) continue;
      for (const [key, value] of Object.entries(values)) {
        const input = card.querySelector(`[data-draft-field="${key}"]`);
        if (input) input.value = value ?? '';
      }
    }
  }

  function hasUnsavedDraftChanges() {
    if (draftInputCache.size) return true;
    const form = root.querySelector('[data-create-draft]');
    return Boolean(form && [...new FormData(form).values()].some((value) => String(value || '').trim()));
  }

  setNavigationGuard(() => !hasUnsavedDraftChanges() || window.confirm('仍有尚未保存的网页草稿字段。离开此页会放弃这些修改，仍要离开吗？'));
  const onBeforeUnload = (event) => {
    if (!hasUnsavedDraftChanges()) return;
    event.preventDefault();
    event.returnValue = '';
  };
  window.addEventListener('beforeunload', onBeforeUnload);

  async function loadJobs() {
    const sequence = ++jobLoadSequence;
    let payload;
    try { payload = await api(`/workbench/jobs?needs_review=true&limit=50&offset=${jobOffset}`, { signal }); }
    catch (error) {
      if (signal?.aborted || sequence !== jobLoadSequence) return;
      jobsArea.innerHTML = stateMessage('待整理岗位暂时无法读取', errorText(error), { kind: 'error', link: '/legacy/tracker', linkLabel: '打开现有岗位管理' });
      root.querySelector('[data-pending-count]').textContent = '不可用';
      return;
    }
    if (signal?.aborted || sequence !== jobLoadSequence) return;
    const items = Array.isArray(payload?.items) ? payload.items : [];
    jobTotal = Number(payload?.total) || 0;
    root.querySelector('[data-pending-count]').textContent = `${jobTotal} 条`;
    root.querySelector('[data-pending-page-caption]').textContent = `显示 ${jobOffset + (items.length ? 1 : 0)}–${jobOffset + items.length} 条，共 ${jobTotal} 条`;
    root.querySelector('[data-pending-prev]').disabled = jobOffset <= 0;
    root.querySelector('[data-pending-next]').disabled = jobOffset + items.length >= jobTotal;
    jobsArea.innerHTML = items.length ? items.map(makeJobCard).join('') : '<div class="wb-empty-state"><strong>当前没有待核对岗位</strong><p>待整理的导入草稿会显示在下方；类别与整理状态分开判断。</p></div>';
  }

  async function loadDrafts(focusId = Number(route.params.get('draft_id')) || 0) {
    const sequence = ++draftLoadSequence;
    draftsArea.innerHTML = '<div class="wb-view-skeleton"><span class="wb-spinner" aria-hidden="true"></span><span>正在读取网页草稿…</span></div>';
    try {
      const payload = await api(`/import-drafts?limit=${DRAFT_PAGE_SIZE}&offset=${draftOffset}`, { signal });
      if (signal?.aborted || sequence !== draftLoadSequence) return;
      let items = Array.isArray(payload?.items) ? [...payload.items] : [];
      draftTotal = Number(payload?.total) || items.length;
      if (focusId && !items.some((item) => Number(item.id) === focusId)) {
        try {
          const focused = await api(`/import-drafts/${focusId}`, { signal });
          if (signal?.aborted || sequence !== draftLoadSequence) return;
          items.unshift(focused);
        } catch { /* keep the requested link visible as an unavailable state below */ }
      }
      root.querySelector('[data-draft-count]').textContent = `${draftTotal} 条`;
      const pageItems = Math.min(items.length, DRAFT_PAGE_SIZE);
      root.querySelector('[data-draft-page-caption]').textContent = pageItems
        ? `显示 ${draftOffset + 1}–${draftOffset + pageItems} 条，共 ${draftTotal} 条草稿`
        : `共 ${draftTotal} 条草稿`;
      root.querySelector('[data-draft-prev]').disabled = draftOffset <= 0;
      root.querySelector('[data-draft-next]').disabled = draftOffset + DRAFT_PAGE_SIZE >= draftTotal;
      draftsArea.innerHTML = items.length ? items.map(makeDraftCard).join('') : '<div class="wb-empty-state"><strong>还没有保存的网页草稿</strong><p>粘贴招聘页面文字或保存网页来源后，草稿会留在这里等待核对。</p></div>';
      restoreDraftInputCache();
      if (focusId) {
        const card = draftsArea.querySelector(`[data-draft-card="${focusId}"]`);
        if (card) { card.classList.add('is-focused'); card.scrollIntoView({ block: 'center', behavior: 'smooth' }); }
        else draftsArea.insertAdjacentHTML('afterbegin', stateMessage(`草稿 #${focusId} 不在当前列表`, '列表按页显示草稿；该链接可能已过期或草稿不再存在。', { kind: 'error' }));
      }
    } catch (error) {
      if (signal?.aborted || sequence !== draftLoadSequence) return;
      root.querySelector('[data-draft-count]').textContent = '不可用';
      draftsArea.innerHTML = stateMessage('网页草稿暂时无法读取', errorText(error), { kind: 'error' });
    }
  }

  async function persistDraftEdits(card) {
    const form = card.querySelector('[data-draft-form]');
    const changed = changedDraftFields(draftValuesFromForm(form), parseDraftFields(card));
    if (!Object.keys(changed).length) {
      return { version: Number(card.dataset.draftVersion), user_fields: parseDraftFields(card), changed: false };
    }
    const saved = await api(`/import-drafts/${Number(card.dataset.draftCard)}`, {
      method: 'PATCH', body: JSON.stringify({ expected_version: Number(card.dataset.draftVersion), user_fields: changed }), signal,
    });
    card.dataset.draftVersion = String(saved.version);
    card.dataset.userFields = JSON.stringify(saved.user_fields || {});
    draftInputCache.delete(Number(card.dataset.draftCard));
    return { ...saved, changed: true };
  }

  root.querySelector('[data-create-draft]').addEventListener('submit', async (event) => {
    event.preventDefault();
    const form = event.currentTarget;
    const data = new FormData(form);
    const userFields = {};
    for (const key of Object.keys(FIELD_LABELS)) {
      const value = String(data.get(key) || '').trim();
      if (value) userFields[key] = value;
    }
    const payload = {
      source_url: String(data.get('source_url') || '').trim() || null,
      source_website: String(data.get('source_website') || data.get('source_website_field') || '').trim() || null,
      original_title: String(data.get('original_title') || '').trim() || null,
      captured_text: String(data.get('captured_text') || ''),
      user_fields: userFields,
      idempotency_key: createDraftKey,
    };
    const feedback = root.querySelector('[data-create-feedback]');
    const button = form.querySelector('[type="submit"]');
    if (!payload.captured_text.trim() && !payload.source_url && !payload.original_title) {
      setFeedback(feedback, '请填写来源链接、页面标题或捕获文本中的至少一项。', true);
      return;
    }
    button.disabled = true;
    try {
      const result = await api('/import-drafts', { method: 'POST', body: JSON.stringify(payload), signal });
      if (signal?.aborted) return;
      notify('网页草稿已保存。', 'success');
      createDraftKey = newIdempotencyKey();
      form.reset();
      const next = new URLSearchParams(route.params);
      next.set('draft_offset', '0');
      if (result?.id) next.set('draft_id', String(result.id));
      navigate({ view: 'pending', params: next });
    } catch (error) {
      if (signal?.aborted) return;
      setFeedback(feedback, errorText(error), true);
      button.disabled = false;
    }
  });

  const onDraftFieldChange = (event) => {
    if (event.target.matches('[data-draft-field]')) updateDraftInputCache(event.target.closest('[data-draft-card]'));
  };
  draftsArea.addEventListener('input', onDraftFieldChange);
  draftsArea.addEventListener('change', onDraftFieldChange);
  draftsArea.addEventListener('submit', async (event) => {
    const form = event.target.closest('[data-draft-form]');
    if (!form) return;
    event.preventDefault();
    const card = form.closest('[data-draft-card]');
    const fields = {};
    for (const input of form.querySelectorAll('[data-draft-field]')) fields[input.dataset.draftField] = input.value.trim() || null;
      const button = form.querySelector('[data-save-draft]');
      const feedback = form.querySelector('[data-draft-feedback]');
    if (button) button.disabled = true;
    try {
      const changed = changedDraftFields(fields, parseDraftFields(card));
      if (!Object.keys(changed).length) { setFeedback(feedback, '人工字段没有变化。'); return; }
      const saved = await api(`/import-drafts/${Number(card.dataset.draftCard)}`, {
        method: 'PATCH', body: JSON.stringify({ expected_version: Number(card.dataset.draftVersion), user_fields: changed }), signal,
      });
      if (signal?.aborted) return;
      card.dataset.draftVersion = String(saved.version);
      card.dataset.userFields = JSON.stringify(saved.user_fields || {});
      draftInputCache.delete(Number(card.dataset.draftCard));
      notify('人工字段已保存。', 'success');
      await loadDrafts(Number(card.dataset.draftCard));
    } catch (error) {
      if (!signal?.aborted) setFeedback(feedback, errorText(error), true);
      if (button) button.disabled = false;
    } finally {
      if (button && button.isConnected) button.disabled = false;
    }
  });

  draftsArea.addEventListener('click', async (event) => {
    const card = event.target.closest('[data-draft-card]');
    if (!card) return;
    const draftId = Number(card.dataset.draftCard);
    if (event.target.closest('[data-accept-suggestions]')) {
      const fields = [...card.querySelectorAll('[data-suggestion-field]:checked')].map((input) => input.value);
      const feedback = card.querySelector('[data-draft-feedback]');
      if (!fields.length) { setFeedback(feedback, '先勾选要接受的字段建议。', true); return; }
      const button = event.target.closest('[data-accept-suggestions]');
      button.disabled = true;
      let currentFormValues = null;
      let acceptedDraft = null;
      let acceptanceCommitted = false;
      try {
        currentFormValues = draftValuesFromForm(card.querySelector('[data-draft-form]'));
        const baseline = parseDraftFields(card);
        const dirty = changedDraftFields(currentFormValues, baseline);
        const conflicts = fields.filter((field) => Object.hasOwn(dirty, field));
        if (conflicts.length && !window.confirm(`你手动修改了 ${conflicts.map((field) => FIELD_LABELS[field] || field).join('、')}。接受对应建议会覆盖这些手动值，是否继续？`)) {
          button.disabled = false;
          return;
        }
        const accepted = acceptedDraft = await api(`/import-drafts/${draftId}/accept-suggestions`, { method: 'POST', body: JSON.stringify({ expected_version: Number(card.dataset.draftVersion), fields }), signal });
        acceptanceCommitted = true;
        card.dataset.draftVersion = String(accepted.version);
        card.dataset.userFields = JSON.stringify(accepted.user_fields || {});
        const manualChanges = { ...dirty };
        for (const field of fields) delete manualChanges[field];
        if (Object.keys(manualChanges).length) {
          const saved = await api(`/import-drafts/${draftId}`, { method: 'PATCH', body: JSON.stringify({ expected_version: Number(accepted.version), user_fields: manualChanges }), signal });
          card.dataset.draftVersion = String(saved.version);
          card.dataset.userFields = JSON.stringify(saved.user_fields || {});
        }
        draftInputCache.delete(draftId);
        if (!signal?.aborted) { notify('所选建议已写入人工字段，手动修改也已保留。', 'success'); await loadDrafts(draftId); }
      } catch (error) {
        if (!signal?.aborted) {
          if (acceptanceCommitted) {
            const valuesToRestore = { ...(currentFormValues || {}) };
            for (const field of fields) valuesToRestore[field] = acceptedDraft?.user_fields?.[field] ?? null;
            draftInputCache.set(draftId, valuesToRestore);
            await loadDrafts(draftId);
            const refreshed = draftsArea.querySelector(`[data-draft-card="${draftId}"]`);
            if (refreshed) {
              setFeedback(refreshed.querySelector('[data-draft-feedback]'), `${errorText(error)} 人工修改仍显示在表单中，可再次保存。`, true);
            }
          } else setFeedback(feedback, `${errorText(error)} 当前未保存的输入仍留在表单中。`, true);
          button.disabled = false;
        }
      }
      return;
    }
    const extractButton = event.target.closest('[data-extract-draft]');
    if (extractButton) {
      const feedback = card.querySelector('[data-draft-feedback]');
      extractButton.disabled = true;
      try {
        const saved = await persistDraftEdits(card);
        const result = await api(`/import-drafts/${draftId}/extract`, {
          method: 'POST', body: JSON.stringify({ expected_version: Number(saved.version) }), signal,
        });
        if (!signal?.aborted) { notify('模型建议已生成；人工字段保持独立。', 'success'); await loadDrafts(draftId); }
      } catch (error) {
        if (!signal?.aborted) setFeedback(feedback, `${errorText(error)} 已填写的人工字段仍保留。`, true);
      } finally {
        if (extractButton.isConnected) extractButton.disabled = false;
      }
      return;
    }
    const confirmButton = event.target.closest('[data-confirm-draft]');
    if (!confirmButton) return;
    const feedback = card.querySelector('[data-draft-feedback]');
    const duplicateZone = card.querySelector('[data-duplicate-zone]');
    let savedDraft;
    try { savedDraft = await persistDraftEdits(card); }
    catch (error) { setFeedback(feedback, errorText(error), true); return; }
    const payload = { expected_version: Number(savedDraft.version), allow_duplicate: card.dataset.allowDuplicate === 'true' };
    confirmButton.disabled = true;
    try {
      const result = await api(`/import-drafts/${draftId}/confirm`, { method: 'POST', body: JSON.stringify(payload), signal });
      if (signal?.aborted) return;
      if (result?.job?.id) {
        notify('岗位已由服务端确认入库。', 'success');
        const next = new URLSearchParams({ job_id: String(result.job.id) });
        navigate({ view: 'library', params: next });
      } else {
        notify('草稿已确认；服务端没有返回岗位详情。', 'info');
        await loadDrafts(draftId);
      }
    } catch (error) {
      if (signal?.aborted) return;
      if (error?.detail?.code === 'duplicate_job' && Array.isArray(error.detail.candidates)) {
        const candidates = error.detail.candidates.map((candidate) => `<li><strong>${escapeHtml(candidate.company_name)}</strong> · ${escapeHtml(candidate.title)} <small>已有岗位 #${Number(candidate.job_id)} · ${escapeHtml((candidate.reasons || []).join('、'))}</small></li>`).join('');
        duplicateZone.innerHTML = `<div class="wb-duplicate-warning" role="alert"><strong>发现可能重复的岗位</strong><p>请核对以下记录。若仍需创建重复岗位，请明确确认。</p><ul>${candidates}</ul><button class="wb-button wb-button-outline" type="button" data-confirm-duplicate>我已核对，仍要新建</button></div>`;
        confirmButton.disabled = false;
      } else {
        setFeedback(feedback, errorText(error), true);
        confirmButton.disabled = false;
      }
    }
  });

  draftsArea.addEventListener('click', async (event) => {
    const force = event.target.closest('[data-confirm-duplicate]');
    if (!force) return;
    const card = force.closest('[data-draft-card]');
    card.dataset.allowDuplicate = 'true';
    card.querySelector('[data-confirm-draft]')?.click();
  });

  jobsArea.addEventListener('click', (event) => {
    const link = event.target.closest('[data-pending-job]');
    if (!link) return;
    event.preventDefault();
    navigate({ view: 'library', params: new URLSearchParams({ job_id: link.dataset.pendingJob }) });
  });

  root.querySelector('[data-draft-prev]').addEventListener('click', () => navigate({ view: 'pending', params: routeWith(route, { draft_offset: Math.max(0, draftOffset - DRAFT_PAGE_SIZE), draft_id: null }) }));
  root.querySelector('[data-draft-next]').addEventListener('click', () => navigate({ view: 'pending', params: routeWith(route, { draft_offset: draftOffset + DRAFT_PAGE_SIZE, draft_id: null }) }));
  root.querySelector('[data-pending-prev]').addEventListener('click', () => navigate({ view: 'pending', params: routeWith(route, { pending_offset: Math.max(0, jobOffset - 50) }) }));
  root.querySelector('[data-pending-next]').addEventListener('click', () => navigate({ view: 'pending', params: routeWith(route, { pending_offset: jobOffset + 50 }) }));
  const cleanup = () => {
    draftsArea.removeEventListener('input', onDraftFieldChange);
    draftsArea.removeEventListener('change', onDraftFieldChange);
    window.removeEventListener('beforeunload', onBeforeUnload);
    setNavigationGuard(null);
  };

  await Promise.all([loadJobs(), loadDrafts()]);
  return cleanup;
}
