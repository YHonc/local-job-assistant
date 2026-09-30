import { categoriesFromConfig, escapeHtml, statusOptions as currentStatusOptions, workbenchConfig } from '../shared.js';
import { openCompanyManager } from './company-editor.js';

const FALLBACK_CATEGORIES = ['数据分析', '运营', '产品', 'AI/算法', '技术研发', '市场/销售', '财务/金融', '咨询/战略', '供应链/制造', '职能/支持', '其他'];
const JOB_FIELDS = [
  'company_id', 'title', 'location', 'employment_type', 'job_category', 'category_review_status',
  'tags', 'organize_status', 'salary_text', 'notes', 'description', 'source_url',
  'source_website', 'original_title', 'published_at', 'structured_data',
];

const asText = (value) => String(value ?? '');
const nullableText = (value) => asText(value).trim() || null;

export function parseTagsText(value) {
  const output = [];
  const seen = new Set();
  for (const part of asText(value).split(/[\n,，;；]/)) {
    const tag = part.trim();
    const folded = tag.toLocaleLowerCase();
    if (!tag || seen.has(folded)) continue;
    output.push(tag);
    seen.add(folded);
  }
  return output;
}

export function parseStructuredData(value) {
  const source = asText(value).trim();
  if (!source) return null;
  const parsed = JSON.parse(source);
  if (!parsed || typeof parsed !== 'object') throw new TypeError('结构化事实必须是 JSON 对象或数组。');
  if (!Array.isArray(parsed) && Object.getPrototypeOf(parsed) !== Object.prototype && Object.getPrototypeOf(parsed) !== null) {
    throw new TypeError('结构化事实必须是 JSON 对象或数组。');
  }
  return parsed;
}

function jobValues(values) {
  return {
    title: asText(values.title).trim(),
    location: nullableText(values.location),
    employment_type: nullableText(values.employment_type),
    job_category: nullableText(values.job_category),
    category_review_status: values.category_review_status || 'pending',
    tags: values.direction === undefined ? parseTagsText(values.tags) : [...parseTagsText(values.tags).filter((tag) => !tag.startsWith('方向:')), ...(values.direction ? ['方向:' + values.direction] : [])],
    organize_status: values.organize_status || 'pending',
    salary_text: nullableText(values.salary_text),
    notes: nullableText(values.notes),
    description: nullableText(values.description),
    source_url: nullableText(values.source_url),
    source_website: nullableText(values.source_website),
    original_title: nullableText(values.original_title),
    published_at: nullableText(values.published_at),
    structured_data: parseStructuredData(values.structured_data),
  };
}

function equalValue(left, right) {
  return JSON.stringify(left ?? null) === JSON.stringify(right ?? null);
}

function formFingerprint(form) {
  return JSON.stringify([...new FormData(form).entries()]);
}

function baselineJobValues(item) {
  return {
    company_id: Number(item.company_id),
    title: asText(item.title),
    location: item.location ?? null,
    employment_type: item.employment_type ?? null,
    job_category: item.job_category ?? null,
    category_review_status: item.category_review_status ?? 'pending',
    tags: Array.isArray(item.tags) ? [...item.tags] : [],
    organize_status: item.organize_status ?? 'pending',
    salary_text: item.salary_text ?? null,
    notes: item.notes ?? null,
    description: item.description ?? null,
    source_url: item.source_url ?? null,
    source_website: item.source_website ?? null,
    original_title: item.original_title ?? null,
    published_at: item.published_at ?? null,
    structured_data: item.structured_data ?? null,
  };
}

export function buildJobUpdate(item, values, applicationValues = null) {
  const next = { ...jobValues(values), company_id: Number(values.company_id) };
  const previous = baselineJobValues(item);
  const job = {};
  for (const field of JOB_FIELDS) {
    if (!equalValue(next[field], previous[field])) job[field] = next[field];
  }

  let application = null;
  if (item.application && applicationValues) {
    const changes = {};
    const nextStatus = asText(applicationValues.status);
    const nextNotes = nullableText(applicationValues.notes);
    if (nextStatus !== item.application.status) changes.status = nextStatus;
    if (!equalValue(nextNotes, item.application.notes ?? null)) changes.notes = nextNotes;
    if (Object.keys(changes).length) application = changes;
  }

  if (!Object.keys(job).length && !application) return null;
  return {
    expected_job_version: Number(item.version),
    ...(Object.keys(job).length ? { job } : {}),
    ...(application ? {
      expected_application_version: Number(item.application.version),
      application,
    } : {}),
  };
}

export function buildJobCreate({ companyMode, companyId, company, values, application, idempotencyKey }) {
  const job = jobValues(values);
  const body = {
    ...(companyMode === 'new'
      ? { company: {
        name: asText(company.name).trim(),
        aliases: parseTagsText(company.aliases),
        notes: nullableText(company.notes),
      } }
      : { company_id: Number(companyId) }),
    job,
    application: application?.enabled ? {
      status: application.status || '待投递',
      notes: nullableText(application.notes),
    } : null,
    idempotency_key: idempotencyKey,
  };
  return body;
}

export function createDraftKeyFactory(createKey = () => globalThis.crypto?.randomUUID?.() || `wb-${Date.now()}-${Math.random().toString(36).slice(2)}`) {
  const keys = new Map();
  return (payload) => {
    const fingerprint = JSON.stringify(payload);
    if (!keys.has(fingerprint)) keys.set(fingerprint, createKey());
    return keys.get(fingerprint);
  };
}

function apiOptions(method, body) {
  return { method, ...(body === undefined ? {} : { body: JSON.stringify(body) }) };
}

function categoryOptions(item) {
  const configured = categoriesFromConfig();
  const options = Array.isArray(configured) && configured.length ? [...configured] : [...FALLBACK_CATEGORIES];
  if (item?.job_category && !options.includes(item.job_category)) options.push(item.job_category);
  return options;
}

function categoriesMarkup(categories, selected = '') {
  return `<option value="">自动识别</option>${categories.map((category) => `<option value="${escapeHtml(category)}" ${category === selected ? 'selected' : ''}>${escapeHtml(category)}</option>`).join('')}`;
}

function statusOptions(selected = '待投递') {
  return currentStatusOptions(selected);
}

function companyOptions(companies, selectedId) {
  return `<option value="">请选择公司</option>${companies.map((company) => `<option value="${Number(company.id)}" ${Number(company.id) === Number(selectedId) ? 'selected' : ''}>${escapeHtml(company.name)}</option>`).join('')}`;
}

function categoryReviewSelect(value = 'pending') {
  return `<option value="pending" ${value === 'pending' ? 'selected' : ''}>待核对</option><option value="confirmed" ${value === 'confirmed' ? 'selected' : ''}>已确认</option>`;
}

function organizeOptions(value = 'pending') {
  return `<option value="pending" ${value === 'pending' ? 'selected' : ''}>待整理</option><option value="organized" ${value === 'organized' ? 'selected' : ''}>已整理</option><option value="archived" ${value === 'archived' ? 'selected' : ''}>已归档</option>`;
}

function renderDialog({ item, companies, categories }) {
  const editing = Boolean(item);
  const selectedCompany = item?.company_id || companies[0]?.id || '';
  const currentApplication = item?.application || null;
  const structured = item?.structured_data == null ? '' : JSON.stringify(item.structured_data, null, 2);
  return `<form class="wb-job-form" data-job-form>
    <header class="wb-dialog-heading"><div><p class="wb-eyebrow">${editing ? '岗位资料' : '新增岗位'}</p><h2 id="wbJobEditorTitle">${editing ? '编辑岗位' : '添加岗位'}</h2><p class="wb-dialog-subtitle">岗位、公司和投递备注分别保存到对应资料。</p></div><button class="wb-button wb-button-quiet" type="button" data-job-close aria-label="关闭岗位编辑器">关闭</button></header>
    <div class="wb-job-editor-scroll">
      <section class="wb-editor-section wb-editor-company-section" aria-labelledby="wbJobCompanyHeading"><div class="wb-form-section-head"><div><h3 id="wbJobCompanyHeading">公司</h3><p>选择现有公司，或在新建岗位时原子创建公司和岗位。</p></div><button class="wb-button wb-button-outline" type="button" data-open-companies>公司管理</button></div>
        ${editing ? `<label class="wb-field">所属公司<select name="company_id" required>${companyOptions(companies, selectedCompany)}</select></label><p class="wb-help-text" data-company-note>${escapeHtml(item.company?.notes ? `公司备注：${item.company.notes}` : '公司备注在公司管理中单独编辑。')}</p>` : `<div class="wb-company-mode" role="group" aria-label="公司录入方式"><label><input type="radio" name="company_mode" value="existing" checked> 选择已有公司</label><label><input type="radio" name="company_mode" value="new" ${companies.length ? '' : 'checked'}> 同时新建公司</label></div>
          <div data-existing-company-fields><label class="wb-field">公司<select name="company_id">${companyOptions(companies, selectedCompany)}</select></label><p class="wb-help-text" data-company-note></p></div>
          <div class="wb-new-company-fields" data-new-company-fields hidden><label class="wb-field">公司名称 <span aria-hidden="true">*</span><input name="company_name" maxlength="200" placeholder="例如：某某科技"></label><label class="wb-field">公司别名<textarea name="company_aliases" rows="2" placeholder="每行一个别名"></textarea></label><label class="wb-field">公司备注<textarea name="company_notes" rows="3" placeholder="公司层面的备注"></textarea></label><p class="wb-help-text">系统发现同名或别名公司时会给出已有公司供选择，不会自动创建重复记录。</p></div>`}
      </section>
      <section class="wb-editor-section" aria-labelledby="wbJobBasicsHeading"><div class="wb-form-section-head"><div><h3 id="wbJobBasicsHeading">岗位信息</h3><p>保留招聘页原始信息，也可填写自己的岗位备注。</p></div></div>
        <div class="wb-editor-grid"><label class="wb-field wb-field-wide">岗位名称 <span aria-hidden="true">*</span><input name="title" maxlength="300" required value="${escapeHtml(item?.title || '')}" placeholder="岗位名称"></label><label class="wb-field">工作地点<input name="location" maxlength="300" value="${escapeHtml(item?.location || '')}" placeholder="例如：厦门"></label><label class="wb-field">招聘类型<input name="employment_type" maxlength="100" value="${escapeHtml(item?.employment_type || '')}" placeholder="实习、校招、社招等"></label>
          <label class="wb-field">职位类别<select name="job_category">${categoriesMarkup(categories, item?.job_category || '')}</select></label><label class="wb-field">细分方向<select name="direction"><option value="">自动识别</option>${(workbenchConfig().directions || []).map((direction) => `<option value="${escapeHtml(direction.name)}" ${(item?.tags || []).includes('方向:' + direction.name) ? 'selected' : ''}>${escapeHtml(direction.name)}</option>`).join('')}</select></label><label class="wb-field">类别核对<select name="category_review_status">${categoryReviewSelect(item?.category_review_status || 'pending')}</select></label><label class="wb-field">整理状态<select name="organize_status">${organizeOptions(item?.organize_status || 'pending')}</select></label><label class="wb-field">薪资<input name="salary_text" maxlength="1000" value="${escapeHtml(item?.salary_text || '')}" placeholder="未知、面议或原始范围"></label>
          <label class="wb-field wb-field-wide">标签<textarea name="tags" rows="2" placeholder="多个标签用逗号或换行分隔">${escapeHtml((item?.tags || []).join('\n'))}</textarea></label><label class="wb-field wb-field-wide">岗位备注<textarea name="notes" rows="3" placeholder="只保存岗位层面的备注">${escapeHtml(item?.notes || '')}</textarea></label><label class="wb-field wb-field-wide">岗位描述<textarea name="description" rows="7" placeholder="完整岗位描述">${escapeHtml(item?.description || '')}</textarea></label></div>
      </section>
      <section class="wb-editor-section" aria-labelledby="wbJobSourceHeading"><div class="wb-form-section-head"><div><h3 id="wbJobSourceHeading">来源与原始事实</h3><p>来源网站、来源链接、原始标题、发布日期和结构化事实分别保留。</p></div></div>
        <div class="wb-editor-grid"><label class="wb-field">来源网站<input name="source_website" maxlength="200" value="${escapeHtml(item?.source_website || '')}" placeholder="例如：公司招聘官网"></label><label class="wb-field">岗位来源链接<input name="source_url" type="url" maxlength="1000" value="${escapeHtml(item?.source_url || '')}" placeholder="https://"></label><label class="wb-field wb-field-wide">原始页面标题<input name="original_title" maxlength="500" value="${escapeHtml(item?.original_title || '')}" placeholder="网页展示的原始标题"></label><label class="wb-field">发布日期<input name="published_at" type="date" value="${escapeHtml(asText(item?.published_at).slice(0, 10))}"></label><label class="wb-field wb-field-wide">结构化事实（JSON）<textarea name="structured_data" rows="7" spellcheck="false" placeholder='例如：{"薪资":"面议"}'>${escapeHtml(structured)}</textarea></label></div>
      </section>
      ${editing ? (currentApplication ? `<section class="wb-editor-section" aria-labelledby="wbApplicationHeading"><div class="wb-form-section-head"><div><h3 id="wbApplicationHeading">投递记录</h3><p>申请备注和状态只写入当前岗位的投递记录。</p></div><span class="wb-badge" data-tone="info">#${Number(currentApplication.id)} · v${Number(currentApplication.version)}</span></div><div class="wb-editor-grid"><label class="wb-field">投递状态<select name="application_status">${statusOptions(currentApplication.status)}</select></label><label class="wb-field wb-field-wide">投递备注<textarea name="application_notes" rows="4" placeholder="只保存投递过程中的备注">${escapeHtml(currentApplication.notes || '')}</textarea></label></div></section>` : `<section class="wb-editor-section wb-application-create" aria-labelledby="wbApplicationCreateHeading"><div class="wb-form-section-head"><div><h3 id="wbApplicationCreateHeading">投递记录</h3><p>当前岗位还没有申请记录，可显式创建一条。</p></div></div><div class="wb-editor-grid"><label class="wb-field">初始状态<select name="new_application_status">${statusOptions('待投递')}</select></label><label class="wb-field wb-field-wide">投递备注<textarea name="new_application_notes" rows="3" placeholder="可选；属于投递记录"></textarea></label></div><p class="wb-help-text">创建申请会单独调用申请接口。请先保存岗位资料，再创建投递记录。</p><button class="wb-button wb-button-outline" type="button" data-create-application>＋ 创建投递记录</button></section>`) : `<section class="wb-editor-section wb-application-create" aria-labelledby="wbApplicationHeading"><div class="wb-form-section-head"><div><h3 id="wbApplicationHeading">投递记录（可选）</h3><p>需要跟进投递时，勾选后与新岗位一起保存。</p></div></div><label class="wb-check-field"><input type="checkbox" name="application_enabled"> 同时创建投递记录</label><div class="wb-editor-grid" data-new-application-fields hidden><label class="wb-field">初始状态<select name="application_status">${statusOptions('待投递')}</select></label><label class="wb-field wb-field-wide">投递备注<textarea name="application_notes" rows="3" placeholder="只保存投递过程中的备注"></textarea></label></div></section>`}
      <section class="wb-duplicate-candidates" data-company-candidates hidden aria-live="polite"></section>
    </div>
    <footer class="wb-dialog-footer"><p class="wb-dialog-status" data-job-status role="status"></p><div class="wb-dialog-actions"><button class="wb-button wb-button-quiet" type="button" data-job-cancel>取消</button>${editing ? `<button class="wb-button wb-button-danger" type="button" data-job-delete>删除岗位</button>` : ''}<button class="wb-button wb-button-primary" type="submit" data-job-save>${editing ? '保存岗位' : '创建岗位'}</button></div></footer>
  </form>`;
}

export function openJobEditor({ root, api, notify = () => {}, item, onSaved = () => {}, onClosed = () => {} } = {}) {
  if (!root || typeof api !== 'function') throw new TypeError('root and api are required');
  const returnFocus = document.activeElement;
  const dialog = document.createElement('dialog');
  dialog.className = 'wb-dialog wb-job-dialog';
  dialog.setAttribute('aria-labelledby', 'wbJobEditorTitle');
  root.append(dialog);

  let currentItem = item ? { ...item } : null;
  let companies = [];
  let closed = false;
  let busy = false;
  let nestedCompanyCleanup = null;
  let versionConflict = false;
  let formBaseline = '';
  const categories = categoryOptions(currentItem);
  const keyForPayload = createDraftKeyFactory();
  const setStatus = (message, kind = 'info') => {
    const node = dialog.querySelector('[data-job-status]');
    if (!node) return;
    node.textContent = message;
    node.dataset.kind = kind;
  };

  function mountForm() {
    dialog.innerHTML = renderDialog({ item: currentItem, companies, categories });
    const form = dialog.querySelector('[data-job-form]');
    dialog.querySelector('[name="title"]')?.focus();
    updateCompanyMode();
    updateCompanyNote();
    updateApplicationCreateState();
    formBaseline = formFingerprint(form);
    form?.addEventListener('change', (event) => {
      const target = event.target;
      if (target.name === 'company_mode') updateCompanyMode();
      if (target.name === 'company_id') updateCompanyNote();
      if (target.name === 'application_enabled') updateApplicationCreateState();
      if (target.name === 'job_category' && target.value) {
        form.elements.category_review_status.value = 'confirmed';
      }
    });
    form?.addEventListener('input', (event) => {
      if (event.target.name === 'company_name') clearCandidates();
    });
  }

  function restoreFormDraft(values) {
    const form = dialog.querySelector('[data-job-form]');
    if (!form) return;
    for (const control of form.elements) {
      if (!control.name) continue;
      if (control.type === 'radio') control.checked = values[control.name] === control.value;
      else if (control.type === 'checkbox') control.checked = Boolean(values[control.name]);
      else if (Object.hasOwn(values, control.name)) control.value = values[control.name];
    }
    updateCompanyMode();
    updateCompanyNote();
    updateApplicationCreateState();
  }

  function updateCompanyMode() {
    const mode = dialog.querySelector('[name="company_mode"]:checked')?.value || 'existing';
    const existing = dialog.querySelector('[data-existing-company-fields]');
    const creating = dialog.querySelector('[data-new-company-fields]');
    if (!existing || !creating) return;
    existing.hidden = mode !== 'existing';
    creating.hidden = mode !== 'new';
    const select = existing.querySelector('[name="company_id"]');
    select.required = mode === 'existing';
    dialog.querySelector('[name="company_name"]').required = mode === 'new';
  }

  function updateCompanyNote() {
    const note = dialog.querySelector('[data-company-note]');
    const id = Number(dialog.querySelector('[name="company_id"]')?.value);
    const company = companies.find((entry) => Number(entry.id) === id) || currentItem?.company;
    if (note) note.textContent = company?.notes ? `公司备注：${company.notes}` : '公司备注在公司管理中单独编辑。';
  }

  function updateApplicationCreateState() {
    const enabled = dialog.querySelector('[name="application_enabled"]')?.checked;
    const fields = dialog.querySelector('[data-new-application-fields]');
    if (fields) fields.hidden = !enabled;
  }

  function clearCandidates() {
    const node = dialog.querySelector('[data-company-candidates]');
    node.hidden = true;
    node.replaceChildren();
  }

  function readValues() {
    const form = dialog.querySelector('[data-job-form]');
    const data = new FormData(form);
    return Object.fromEntries(data.entries());
  }

  function jobPatchPreview() {
    if (!currentItem) return null;
    try {
      const values = readValues();
      return buildJobUpdate(currentItem, values, currentItem.application ? {
        status: values.application_status,
        notes: values.application_notes,
      } : null);
    } catch {
      return { invalid: true };
    }
  }

  function hasUnsavedChanges() {
    const form = dialog.querySelector('[data-job-form]');
    return Boolean(form && formFingerprint(form) !== formBaseline);
  }

  function textValue(value) {
    if (typeof value === 'boolean') return value ? 'true' : '';
    return asText(value);
  }

  function canLeave() {
    if (nestedCompanyCleanup?.canLeave && !nestedCompanyCleanup.canLeave()) return false;
    return !hasUnsavedChanges() || window.confirm('岗位资料有未保存修改，确定离开并放弃草稿吗？');
  }

  const onBeforeUnload = (event) => {
    if (!hasUnsavedChanges()) return;
    event.preventDefault();
    event.returnValue = '';
  };
  window.addEventListener('beforeunload', onBeforeUnload);

  function close({ force = false } = {}) {
    if (closed || (!force && !canLeave())) return false;
    dialog.close();
    return true;
  }

  function cleanup() {
    if (closed) return;
    closed = true;
    nestedCompanyCleanup?.();
    nestedCompanyCleanup = null;
    window.removeEventListener('beforeunload', onBeforeUnload);
    dialog.remove();
    if (returnFocus?.isConnected) returnFocus.focus();
    onClosed();
  }

  async function loadCompanies() {
    const result = await api('/companies');
    if (closed) return;
    companies = Array.isArray(result) ? result : [];
    mountForm();
  }

  function validateForm(values) {
    if (!values.title?.trim()) throw new TypeError('请填写岗位名称。');
    if (!currentItem) {
      const mode = dialog.querySelector('[name="company_mode"]:checked')?.value || 'existing';
      if (mode === 'existing' && !Number(values.company_id)) throw new TypeError('请选择公司。');
      if (mode === 'new' && !values.company_name?.trim()) throw new TypeError('请填写新公司的名称。');
    }
    return jobValues(values);
  }

  function showCompanyCandidates(error) {
    const candidates = error?.detail?.code === 'company_selection_required' ? error.detail.candidates : null;
    const node = dialog.querySelector('[data-company-candidates]');
    if (!Array.isArray(candidates) || !candidates.length || !node) return false;
    node.hidden = false;
    node.innerHTML = `<strong>发现已有同名或别名公司</strong><p>选择已有公司后，岗位会关联到该公司，不会创建重复记录。</p><div class="wb-candidate-buttons">${candidates.map((company) => `<button class="wb-button wb-button-outline" type="button" data-select-company="${Number(company.id)}">${escapeHtml(company.name)} · #${Number(company.id)}</button>`).join('')}</div>`;
    setStatus('请确认公司后重新保存。当前岗位草稿已保留。', 'error');
    return true;
  }

  function applyCandidate(companyId) {
    const candidate = companies.find((entry) => Number(entry.id) === companyId);
    const optionData = dialog.querySelector('[name="company_id"]');
    if (!optionData) return;
    if (!candidate) {
      const button = dialog.querySelector(`[data-select-company="${companyId}"]`);
      const name = button?.textContent.split(' · #')[0] || `公司 #${companyId}`;
      const option = document.createElement('option');
      option.value = String(companyId);
      option.textContent = name;
      optionData.append(option);
      companies.push({ id: companyId, name, aliases: [], notes: null });
    }
    const existingMode = dialog.querySelector('[name="company_mode"][value="existing"]');
    if (existingMode) existingMode.checked = true;
    optionData.value = String(companyId);
    updateCompanyMode();
    updateCompanyNote();
    clearCandidates();
  }

  function saveError(error) {
    versionConflict = error?.status === 409;
    if (showCompanyCandidates(error)) return;
    const message = versionConflict
      ? '岗位或投递记录已被其他操作更新。草稿仍保留；可加载服务端版本后人工合并。'
      : error?.message || '保存失败，表单内容仍保留。';
    setStatus(message, 'error');
    const footer = dialog.querySelector('.wb-dialog-footer .wb-dialog-actions');
    if (versionConflict && !footer.querySelector('[data-reload-version]')) {
      const button = document.createElement('button');
      button.className = 'wb-button wb-button-outline';
      button.type = 'button';
      button.dataset.reloadVersion = '';
      button.textContent = '加载服务端版本';
      footer.prepend(button);
    }
  }

  async function save() {
    if (busy) return;
    const form = dialog.querySelector('[data-job-form]');
    const values = readValues();
    try {
      validateForm(values);
      if (values.source_url && !/^https?:\/\//i.test(values.source_url.trim())) {
        throw new TypeError('来源链接须以 http:// 或 https:// 开头。');
      }
      if (currentItem && !currentItem.application && (values.new_application_notes?.trim() || values.new_application_status)) {
        // Application creation is a separate explicit transaction and must not be mixed with job edits.
        if (jobPatchPreview()) throw new TypeError('请先保存岗位资料，再创建投递记录。');
      }
      const payload = currentItem
        ? buildJobUpdate(currentItem, values, currentItem.application ? {
          status: values.application_status,
          notes: values.application_notes,
        } : null)
        : buildJobCreate({
          companyMode: dialog.querySelector('[name="company_mode"]:checked')?.value || 'existing',
          companyId: values.company_id,
          company: { name: values.company_name, aliases: values.company_aliases, notes: values.company_notes },
          values,
          application: { enabled: dialog.querySelector('[name="application_enabled"]')?.checked, status: values.application_status, notes: values.application_notes },
          idempotencyKey: '',
        });
      if (currentItem && !payload) {
        setStatus('没有需要保存的修改。', 'info');
        return;
      }
      if (!currentItem) payload.idempotency_key = keyForPayload(payload);
      busy = true;
      versionConflict = false;
      const saveButton = form.querySelector('[data-job-save]');
      saveButton.disabled = true;
      saveButton.textContent = currentItem ? '正在保存…' : '正在创建…';
      setStatus(currentItem ? '正在保存岗位和投递资料…' : '正在原子保存公司、岗位和可选投递资料…');
      const saved = currentItem
        ? await api(`/workbench/jobs/${currentItem.id}`, apiOptions('PATCH', payload))
        : (await api('/workbench/jobs', apiOptions('POST', payload))).item;
      if (closed) return;
      notify(currentItem ? '岗位资料已保存。' : '岗位已创建。', 'success');
      close({ force: true });
      onSaved(saved);
    } catch (error) {
      if (closed) return;
      const saveButton = form?.querySelector('[data-job-save]');
      if (saveButton) {
        saveButton.disabled = false;
        saveButton.textContent = currentItem ? '保存岗位' : '创建岗位';
      }
      if (error instanceof SyntaxError || error instanceof TypeError) {
        setStatus(error.message, 'error');
        return;
      }
      saveError(error);
    } finally {
      busy = false;
    }
  }

  async function createApplication() {
    if (busy || !currentItem || currentItem.application) return;
    if (jobPatchPreview()) {
      setStatus('请先保存岗位资料，再创建投递记录。岗位草稿仍保留。', 'error');
      return;
    }
    const values = readValues();
    const button = dialog.querySelector('[data-create-application]');
    if (!window.confirm(`为“${currentItem.title}”创建一条投递记录吗？初始状态为${values.new_application_status || '收藏'}。`)) return;
    busy = true;
    button.disabled = true;
    setStatus('正在创建投递记录…');
    try {
      const application = await api('/applications', apiOptions('POST', {
        job_id: Number(currentItem.id),
        status: values.new_application_status || '收藏',
        notes: nullableText(values.new_application_notes),
      }));
      if (closed) return;
      const saved = { ...currentItem, application };
      notify('投递记录已创建。', 'success');
      close({ force: true });
      onSaved(saved);
    } catch (error) {
      if (closed) return;
      button.disabled = false;
      setStatus(error?.status === 409 ? '投递记录可能已由其他操作创建。请重新载入岗位后核对。' : error?.message || '投递记录创建失败，内容仍保留。', 'error');
    } finally {
      busy = false;
    }
  }

  async function deleteJob() {
    if (busy || !currentItem) return;
    const dirty = hasUnsavedChanges();
    const message = dirty
      ? `确定删除“${currentItem.title}”及其投递记录吗？岗位还有未保存修改。`
      : `确定删除“${currentItem.title}”及其投递记录吗？`;
    if (!window.confirm(message)) return;
    busy = true;
    const button = dialog.querySelector('[data-job-delete]');
    button.disabled = true;
    setStatus('正在删除岗位…');
    try {
      await api(`/jobs/${currentItem.id}`, apiOptions('DELETE'));
      if (closed) return;
      notify('岗位已删除。', 'success');
      close({ force: true });
      onSaved(null);
    } catch (error) {
      if (closed) return;
      button.disabled = false;
      setStatus(error?.message || '删除失败，岗位资料仍保留。', 'error');
    } finally {
      busy = false;
    }
  }

  async function reloadLatest() {
    if (!currentItem || !window.confirm('加载服务端最新版本会放弃当前未保存草稿，继续吗？')) return;
    const button = dialog.querySelector('[data-reload-version]');
    button.disabled = true;
    try {
      currentItem = await api(`/workbench/jobs/${currentItem.id}`);
      if (closed) return;
      versionConflict = false;
      mountForm();
      setStatus('已加载服务端最新版本。请检查后重新编辑。', 'success');
    } catch (error) {
      if (closed) return;
      button.disabled = false;
      setStatus(error?.message || '最新版本读取失败，草稿仍保留。', 'error');
    }
  }

  dialog.addEventListener('cancel', (event) => {
    if (!canLeave()) event.preventDefault();
  });
  dialog.addEventListener('close', cleanup, { once: true });
  dialog.addEventListener('submit', (event) => {
    if (!event.target.closest('[data-job-form]')) return;
    event.preventDefault();
    void save();
  });
  dialog.addEventListener('click', (event) => {
    if (event.target.closest('[data-job-close]') || event.target.closest('[data-job-cancel]')) {
      close();
      return;
    }
    if (event.target.closest('[data-job-delete]')) {
      void deleteJob();
      return;
    }
    if (event.target.closest('[data-create-application]')) {
      void createApplication();
      return;
    }
    if (event.target.closest('[data-reload-version]')) {
      void reloadLatest();
      return;
    }
    const candidate = event.target.closest('[data-select-company]');
    if (candidate) {
      applyCandidate(Number(candidate.dataset.selectCompany));
      setStatus('已选择已有公司。请再次保存岗位。');
      return;
    }
    if (event.target.closest('[data-open-companies]')) {
      if (nestedCompanyCleanup) return;
      const formDraft = readValues();
      nestedCompanyCleanup = openCompanyManager({
        root,
        api,
        notify,
        onClosed: () => { nestedCompanyCleanup = null; },
        onChange: async () => {
          try {
            companies = await api('/companies');
            if (!closed) {
              mountForm();
              restoreFormDraft(formDraft);
              setStatus('公司列表已更新。');
            }
          } catch (error) {
            setStatus(error?.message || '公司列表刷新失败。', 'error');
          }
          nestedCompanyCleanup = null;
        },
      });
    }
  });

  dialog.showModal();
  dialog.innerHTML = '<div class="wb-dialog-panel wb-job-editor-panel"><p class="wb-muted-label">正在读取公司资料…</p></div>';
  loadCompanies().catch((error) => {
    if (closed) return;
    companies = [];
    mountForm();
    setStatus(`公司列表读取失败：${error?.message || '请求未成功。'} 可重试或打开公司管理。`, 'error');
  });

  cleanup.canLeave = canLeave;
  return cleanup;
}
