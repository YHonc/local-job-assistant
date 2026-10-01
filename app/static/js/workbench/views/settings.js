import { modelSummary } from '../components/model-settings.js?v=20260930-settings';
import { mountPromptSettings } from '../components/prompt-settings.js?v=20260929-settings';
import { buttonBusy, collection, esc, fmtTime, jsonOptions, setFeedback } from '../integrations/shared.js';
import { emailFilterRequest, modelProbeRequest, modelSaveRequest } from '../integrations/settings-payloads.mjs';
import { mountBehaviorSettings } from '../components/behavior-settings.js?v=20260929-settings';
import { mountProxySettings } from '../components/proxy-settings.js?v=20260929-settings';
import { settingsMarkup } from '../components/settings-markup.js?v=20260930-settings';
import { SETTINGS_SECTIONS, SETTINGS_PANELS, findSettings } from '../components/settings-navigation.js?v=20260930-settings';
import { createSettingsDrafts } from '../components/settings-drafts.js';

const EVENTS = { APPLICATION_RECEIVED: '收到回复', ASSESSMENT: '测评', WRITTEN_TEST: '笔试', INTERVIEW: '面试', INTERVIEW_CHANGED: '面试变更', OFFER: 'Offer', REJECTED: '拒绝', RECRUITMENT_MARKETING: '招聘营销', OTHER: '其他' };
const PROTOCOLS = {
  openai: { label: 'OpenAI 兼容 · Chat Completions', base: 'https://api.openai.com/v1', path: '/chat/completions', note: 'OpenAI 兼容服务（也可用于 DeepSeek、千问、GLM、Kimi、Ollama 等）' },
  anthropic: { label: 'Anthropic 兼容 · Messages', base: 'https://api.anthropic.com', path: '/v1/messages', note: 'Anthropic Messages API' },
  gemini: { label: 'Gemini', base: 'https://generativelanguage.googleapis.com/v1beta', path: 'generateContent', note: 'Gemini 原生 API' },
};
const METRICS = [
  ['total_tokens', 'Token 合计', 'Token'], ['total_prompt_tokens', '输入 Token', 'Token'], ['total_completion_tokens', '输出 Token', 'Token'],
  ['average_duration_ms', '平均响应', 'ms'], ['call_count', '调用次数', '次'], ['success_count', '成功调用', '次'], ['failure_count', '失败调用', '次'],
];
const n = (value) => value == null ? null : Number(value);
const int = (value) => value == null || !Number.isFinite(Number(value)) ? '—' : Math.round(Number(value)).toLocaleString('zh-CN');
const todayUtc8 = () => new Date(Date.now() + 8 * 3600000).toISOString().slice(0, 10);
const shiftDay = (day, count) => new Date(Date.parse(`${day}T00:00:00Z`) + count * 86400000).toISOString().slice(0, 10);


export function mount({ root, route, navigate, api, preferences, notify = () => {}, setNavigationGuard = () => {} }) {
  root.innerHTML = settingsMarkup(EVENTS, PROTOCOLS); const q = (selector) => root.querySelector(selector);
  const shell = root.closest('.wb-app');
  shell.dataset.settingsActive = 'true';
  const state = { cleaned: false, seq: 0, configs: [], routing: null, accounts: [], policies: [], usage: null, usageConfig: null, usageMetric: 'total_tokens', usageController: null, probeModels: [], section: 'appearance', panel: '', internalNavigation: false };
  const msg = (selector, text, kind = 'info') => { if (!state.cleaned) setFeedback(root, selector, text, kind); };
  const controller = new AbortController();
  const req = (path, options = {}) => api(path, { signal: controller.signal, ...options });
  let prompts;
  const drafts = createSettingsDrafts(root, updateDraftStatus);
  for (const name of ['appearance', 'model', 'routing', 'account', 'policy']) drafts.register(name, q(`[data-form="${name}"]`));
  drafts.register('filter', q('[data-filter-form]'), '[data-action="save-filter"]');
  const behaviors = mountBehaviorSettings(root, req, () => loadModels(), drafts);
  prompts = mountPromptSettings(root, req, updateDraftStatus);
  const proxy = mountProxySettings(root, req, drafts);
  let modelSaving = false;
  let probeSequence = 0;
  let probeController = null;
  let modelReturnFocus = null;
  const modelDialog = q('[data-dialog="model"]');
  const onDialogCancel = (event) => { event.preventDefault(); hideDialog(event.target); };
  root.querySelectorAll('dialog').forEach((dialog) => dialog.addEventListener('cancel', onDialogCancel));
  function updateDraftStatus() {
    if (state.cleaned) return;
    const groups = { appearance: ['appearance'], delivery: ['delivery'], models: ['model', 'routing', 'health', 'proxy'], mailbox: ['account'], processing: ['filter'], policies: ['policy'], usage: [] };
    for (const [section, names] of Object.entries(groups)) q(`[data-section-dirty="${section}"]`).hidden = !names.some((name) => drafts.dirty(name)) && !(section === 'models' && prompts?.dirty);
    q('[data-settings-draft-status]').textContent = drafts.busy || prompts?.busy ? '正在处理，请稍候…' : drafts.hasDirty || prompts?.dirty ? '有未保存修改。切换板块保留草稿；离开前请保存。' : '切换板块保留草稿；保存后生效。';
  }
  function canLeave() {
    if (state.internalNavigation) return true;
    if (drafts.busy || prompts.busy) { notify('正在处理设置，请完成后再离开。'); return false; }
    return !(drafts.hasDirty || prompts.dirty) || confirm('有未保存的设置修改。离开或刷新会丢弃草稿，继续？');
  }
  setNavigationGuard(canLeave);
  function hideDialog(dialog) {
    if (!dialog?.open) return;
    const name = dialog.dataset.dialog;
    if (drafts.saving(name) && drafts.dirty(name)) return;
    if (drafts.dirty(name) && !confirm('放弃这个表单中未保存的修改？')) return;
    if (name === 'model') cancelModelProbe();
    dialog.close();
    dialog.querySelectorAll('input[type="password"], [name="api_key"]').forEach((input) => { input.value = ''; input.type = 'password'; });
    drafts.accept(name);
    if (name === 'model') modelReturnFocus?.focus({ preventScroll: true });
  }
  function setTab(name, updateUrl = false, requestedPanel = '') {
    const section = SETTINGS_SECTIONS.find((item) => item.id === name) || SETTINGS_SECTIONS[0];
    const panels = SETTINGS_PANELS[section.id] || [];
    state.section = section.id; state.panel = panels.includes(requestedPanel) ? requestedPanel : panels[0] || '';
    root.querySelectorAll('[data-setting-tab]').forEach((button) => button.dataset.settingTab === section.id ? button.setAttribute('aria-current', 'page') : button.removeAttribute('aria-current'));
    root.querySelectorAll('[data-setting-panel]').forEach((panel) => { panel.hidden = panel.dataset.settingPanel !== section.id; });
    root.querySelectorAll('[data-settings-subpanel]').forEach((panel) => { panel.hidden = panel.dataset.settingsSubpanel !== `${section.id}:${state.panel}`; });
    root.querySelectorAll('[data-settings-panel]').forEach((button) => button.dataset.settingsSection === section.id && button.dataset.settingsPanel === state.panel ? button.setAttribute('aria-current', 'page') : button.removeAttribute('aria-current'));
    q('[data-settings-select]').value = section.id;
    q('[data-settings-group]').textContent = section.group; q('[data-settings-title]').textContent = section.title; q('[data-settings-description]').textContent = section.description;
    q('[data-settings-scroll]').scrollTop = 0;
    if (section.id === 'models' && state.panel === 'prompts') q('[data-prompt-card]').open = true;
    if (section.id === 'usage' && !state.usageConfig && state.configs.length) openUsage(state.configs[0], false);
    if (updateUrl && typeof navigate === 'function') {
      state.internalNavigation = true;
      try { navigate({ view: 'settings', params: { section: section.id, ...(state.panel ? { settings_panel: state.panel } : {}) }, refresh: false }); }
      finally { state.internalNavigation = false; }
    }
  }
  function renderSearch() {
    const value = q('[data-settings-search]').value.trim();
    const results = findSettings(value);
    q('[data-settings-results]').hidden = !value;
    q('[data-settings-results]').innerHTML = results.length ? results.map(([title, , section, panel, target], index) => `<button type="button" data-settings-result="${index}" data-result-section="${section}" data-result-panel="${panel}" data-result-target="${esc(target)}">${esc(title)}</button>`).join('') : '<p role="status">没有找到对应设置，请换个关键词。</p>';
  }
  async function loadAppearance() {
    await preferences.ready;
    if (state.cleaned) return;
    const form = q('[data-form="appearance"]'), values = preferences.value;
    for (const name of ['theme_mode', 'density', 'seed_color', 'poetry_preset']) form.elements[name].value = values[name];
    form.elements.reduce_motion.checked = values.reduce_motion;
    form.querySelectorAll('[name="visible_job_fields"]').forEach((input) => { input.checked = values.visible_job_fields.includes(input.value); });
    drafts.accept('appearance');
  }
  async function saveAppearance(event) {
    event.preventDefault();
    const form = event.target;
    const patch = Object.fromEntries(['theme_mode', 'density', 'seed_color', 'poetry_preset'].map((name) => [name, form.elements[name].value]));
    patch.reduce_motion = form.elements.reduce_motion.checked;
    patch.visible_job_fields = [...form.querySelectorAll('[name="visible_job_fields"]:checked')].map((input) => input.value);
    await drafts.save('appearance', async () => {
      msg('[data-appearance-feedback]', '正在保存…');
      if (await preferences.save(patch, { quiet: true })) { if (state.cleaned) return; drafts.accept('appearance'); msg('[data-appearance-feedback]', '显示偏好已保存。', 'success'); }
      else msg('[data-appearance-feedback]', '保存失败，输入已保留，请稍后重试。', 'error');
    });
  }
  const llmSummary = modelSummary;
  async function loadModels() {
    const seq = ++state.seq; msg('[data-model-feedback]', '正在读取模型配置…');
    try {
      const [configs, routing] = await Promise.all([req('/llm/configs'), req('/llm/routing')]); if (state.cleaned || seq !== state.seq) return;
      state.configs = collection(configs); state.routing = routing; q('[data-model-cards]').innerHTML = state.configs.length ? state.configs.map(llmSummary).join('') : '<div class="wb-empty"><strong>还没有模型配置</strong><p>不配置模型时，邮箱和岗位记录仍可手动管理；邮件清洗等调用会显示服务端错误原因。</p></div>';
      if (!drafts.saving('routing')) {
        if (drafts.dirty('routing')) refreshRoutingOptions();
        else renderRouting();
      }
      renderModelResources(); renderDiagnostics();
      msg('[data-model-feedback]', `${state.configs.length} 个模型配置；${state.configs.filter((item) => item.enabled).length} 个启用。`, 'success');
    } catch (error) { if (!state.cleaned && seq === state.seq) msg('[data-model-feedback]', `读取模型设置失败：${error.message}`, 'error'); }
  }
  let pollingModels = false;
  const modelPoll = window.setInterval(async () => {
    if (state.cleaned || document.hidden || modelDialog.open || pollingModels || document.activeElement?.closest('[data-model-card]')) return;
    pollingModels = true;
    try {
      const configs = collection(await req('/llm/configs'));
      if (state.cleaned) return;
      for (const config of configs) {
        const previous = state.configs.find((item) => item.id === config.id);
        if (JSON.stringify(previous?.health) !== JSON.stringify(config.health)) {
          const card = q(`[data-model-card="${config.id}"]`);
          if (card) card.outerHTML = modelSummary(config);
        }
      }
      state.configs = configs;
      renderModelResources(); renderDiagnostics();
    } catch { /* Keep the last result; explicit refresh reports connection errors. */ }
    finally { pollingModels = false; }
  }, 15000);
  function renderRouting() {
    const form = q('[data-form="routing"]'); const enabled = state.configs.filter((config) => config.enabled); const oldDefault = form.elements.default_config_id.value; const oldEmail = form.elements.email_config_id.value;
    form.elements.auto_select_enabled.checked = Boolean(state.routing?.auto_select_enabled); form.elements.max_parallel_calls.value = state.routing?.max_parallel_calls ?? 1;
    form.elements.default_config_id.innerHTML = '<option value="">不指定</option>' + enabled.map((item) => `<option value="${esc(item.id)}">${esc(item.display_name)}</option>`).join('');
    form.elements.email_config_id.innerHTML = '<option value="">跟随全局路由</option>' + enabled.map((item) => `<option value="${esc(item.id)}">${esc(item.display_name)}</option>`).join('');
    const defaultId = state.routing?.default_config_id; const emailId = state.routing?.email_config_id;
    form.elements.default_config_id.value = enabled.some((item) => String(item.id) === String(defaultId)) ? String(defaultId) : enabled.some((item) => String(item.id) === oldDefault) ? oldDefault : '';
    form.elements.email_config_id.value = enabled.some((item) => String(item.id) === String(emailId)) ? String(emailId) : enabled.some((item) => String(item.id) === oldEmail) ? oldEmail : '';
    q('[data-fixed-model]').hidden = form.elements.auto_select_enabled.checked;
    drafts.accept('routing');
  }
  function refreshRoutingOptions() {
    const form = q('[data-form="routing"]');
    const enabled = state.configs.filter((config) => config.enabled);
    let invalidated = false;
    for (const [name, label] of [['default_config_id', '不指定'], ['email_config_id', '跟随全局路由']]) {
      const select = form.elements[name], value = select.value;
      select.innerHTML = `<option value="">${label}</option>` + enabled.map((item) => `<option value="${esc(item.id)}">${esc(item.display_name)}</option>`).join('');
      const valid = enabled.some((item) => String(item.id) === value);
      select.value = valid ? value : '';
      invalidated ||= Boolean(value && !valid);
    }
    drafts.sync('routing');
    if (invalidated) msg('[data-routing-feedback]', '所选模型已停用或删除，已清除失效选择；其他修改已保留，请确认后保存。', 'info');
  }
  function renderModelResources() {
    const rows = (description) => state.configs.map((config) => `<div class="wb-settings-resource-row"><div><strong>${esc(config.display_name)}</strong><p>${esc(description(config))}</p></div><button class="wb-text-button" type="button" data-model-edit="${esc(config.id)}">编辑</button></div>`).join('') || '<p class="wb-muted">尚无模型，请先添加模型连接。</p>';
    q('[data-health-models]').innerHTML = rows((config) => `${config.enabled ? '已启用' : '已停用'} · ${config.health?.mode === 'off' ? '排除此模型' : config.health?.mode === 'custom' ? `自定义 ${config.health.interval_minutes} 分钟` : '跟随全局间隔'}`);
    q('[data-proxy-models]').innerHTML = rows((config) => config.use_proxy ? '使用统一代理' : '未启用代理');
    const select = q('[data-usage-config]'), selected = select.value;
    select.innerHTML = '<option value="">请选择模型</option>' + state.configs.map((config) => `<option value="${esc(config.id)}">${esc(config.display_name)}</option>`).join('');
    select.value = state.configs.some((config) => String(config.id) === selected) ? selected : '';
    if (state.usageConfig && !state.configs.some((config) => config.id === state.usageConfig.id)) {
      state.usageController?.abort(); state.usageConfig = null; state.usage = null;
      renderUsageMetrics(null); renderUsageChart();
      q('[data-usage-lifetime]').textContent = ''; q('[data-usage-note]').textContent = '';
    }
    if (state.section === 'usage' && !state.usageConfig && state.configs.length) openUsage(state.configs[0], false);
    q('[data-usage-config]').disabled = !state.configs.length;
  }
  function renderDiagnostics() {
    const statuses = { passed: '测试通过', failed: '测试失败', running: '进行中', interrupted: '测试中断', never: '尚无记录', success: '同步成功', partial: '部分成功', error: '同步失败' };
    const models = state.configs.map((config) => `<div class="wb-settings-resource-row"><div><strong>${esc(config.display_name)}</strong><p>模型 · ${esc(statuses[config.health?.status] || '尚未测试')} · ${esc(fmtTime(config.health?.last_test_at))}${config.health?.error ? ` · ${esc(config.health.error)}` : ''}</p></div><button class="wb-text-button" type="button" data-model-usage="${esc(config.id)}">用量</button></div>`);
    const accounts = state.accounts.map((account) => `<div class="wb-settings-resource-row"><div><strong>${esc(account.email_address)}</strong><p>邮箱 · ${esc(statuses[account.last_sync_status] || '尚未同步')} · ${esc(fmtTime(account.last_sync_at))}${account.last_sync_error ? ` · ${esc(account.last_sync_error)}` : ''}</p></div><button class="wb-text-button" type="button" data-account-edit="${esc(account.id)}">账户</button></div>`);
    q('[data-settings-diagnostics]').innerHTML = [...models, ...accounts].join('') || '<p class="wb-muted">添加模型或邮箱后，这里显示已有运行结果。</p>';
  }
  async function loadAccounts() {
    try { const payload = await req('/email-accounts'); if (state.cleaned) return; state.accounts = collection(payload); q('[data-account-cards]').innerHTML = state.accounts.length ? state.accounts.map((account) => `<article class="wb-config-card${account.enabled ? '' : ' is-paused'}" data-account-card="${esc(account.id)}"><div class="wb-entry-head"><strong>${esc(account.email_address)}</strong><span class="wb-chip ${account.enabled ? 'is-active' : ''}">${esc(account.provider_type.toUpperCase())} · ${account.enabled ? '同步启用' : '已停用'}</span></div><p>每 ${esc(account.check_interval_minutes)} 分钟增量同步 · 回看 ${esc(account.sync_lookback_days ?? '不限')} 天</p><p class="wb-muted">上次同步：${esc(fmtTime(account.last_sync_at))} · ${esc(({ never: '尚未同步', running: '同步中', success: '成功', partial: '部分成功', error: '失败' })[account.last_sync_status] || account.last_sync_status || '尚未同步')}${account.last_sync_error ? ` · ${esc(account.last_sync_error)}` : ''}</p><div class="wb-inline-actions"><button class="wb-text-button" type="button" data-account-test="${esc(account.id)}">测试连接</button><button class="wb-text-button" type="button" data-account-sync="${esc(account.id)}">立即同步</button><button class="wb-text-button" type="button" data-account-edit="${esc(account.id)}">编辑</button><button class="wb-text-button" type="button" data-account-toggle="${esc(account.id)}">${account.enabled ? '停用' : '启用'}</button><button class="wb-text-button wb-danger-text" type="button" data-account-delete="${esc(account.id)}">删除账户</button></div></article>`).join('') : '<div class="wb-empty"><strong>尚未添加邮箱账户</strong><p>添加账户后可测试连接并手动执行一次同步。</p></div>'; renderDiagnostics(); }
    catch (error) { if (!state.cleaned) msg('[data-account-feedback]', `读取邮箱账户失败：${error.message}`, 'error'); }
  }
  async function loadFilter() {
    try { const config = await req('/email-filter'); if (state.cleaned) return; q('[data-filter-keywords]').value = Array.isArray(config?.keywords) ? config.keywords.join('\n') : ''; q('[data-filter-regex]').value = config?.regex || config?.pattern || ''; drafts.accept('filter'); }
    catch (error) { if (!state.cleaned) msg('[data-filter-feedback]', `读取预筛规则失败：${error.message}`, 'error'); }
  }
  async function loadPolicies() {
    try { const payload = await req('/notification-policies'); if (state.cleaned) return; state.policies = collection(payload); q('[data-policy-cards]').innerHTML = state.policies.length ? state.policies.map((policy) => `<article class="wb-config-card${policy.enabled ? '' : ' is-paused'}" data-policy-card="${esc(policy.id)}"><div class="wb-entry-head"><strong>${esc(policy.name)}</strong><span class="wb-chip ${policy.enabled ? 'is-active' : ''}">${policy.enabled ? '启用中' : '已停用'}</span></div><p>渠道：${[policy.desktop_enabled ? '桌面通知' : '', policy.push_enabled ? `Push（${policy.push_provider || 'ntfy'}）` : ''].filter(Boolean).map(esc).join(' + ') || '未启用'}${policy.has_push_url ? ' · Push URL 已安全保存' : ''}</p><p>${(policy.important_event_types || []).map((type) => `<span class="wb-chip">${esc(EVENTS[type] || type)}</span>`).join(' ')}${policy.notify_actionable_deadlines ? '<span class="wb-muted"> · 明确行动截止时间</span>' : ''}</p><div class="wb-inline-actions"><button class="wb-text-button" type="button" data-policy-edit="${esc(policy.id)}">编辑</button>${policy.desktop_enabled ? `<button class="wb-text-button" type="button" data-policy-test="${esc(policy.id)}" data-provider="desktop">发送桌面测试</button>` : ''}${policy.push_enabled ? `<button class="wb-text-button" type="button" data-policy-test="${esc(policy.id)}" data-provider="push">发送 Push 测试</button>` : ''}<button class="wb-text-button wb-danger-text" type="button" data-policy-delete="${esc(policy.id)}">删除策略</button></div></article>`).join('') : '<div class="wb-empty"><strong>还没有通知策略</strong><p>新建策略并选择事件、桌面或 Push 渠道。</p></div>'; }
    catch (error) { if (!state.cleaned) msg('[data-policy-feedback]', `读取通知策略失败：${error.message}`, 'error'); }
  }
  function openModel(config = null) {
    if (modelSaving || (drafts.dirty('model') && !confirm('切换模型会丢弃未保存的修改，继续？'))) return;
    cancelModelProbe();
    modelReturnFocus = document.activeElement;
    q('[data-form="model"]').elements.api_key.type = 'password';
    q('[data-action="toggle-api-key"]').setAttribute('aria-pressed', 'false');
    q('[data-action="toggle-api-key"]').setAttribute('aria-label', '显示本次输入的 API Key');
    const form = q('[data-form="model"]'); form.reset(); form.elements.id.value = config?.id || ''; form.elements.display_name.value = config?.display_name || ''; form.elements.model.value = config?.model || ''; form.elements.api_base.value = config?.api_base || ''; form.elements.temperature.value = config?.temperature ?? 0.2; form.elements.timeout_seconds.value = config?.timeout_seconds ?? 60; form.elements.enabled.checked = config ? Boolean(config.enabled) : true; form.elements.use_proxy.checked = Boolean(config?.use_proxy); form.elements.api_key.value = ''; form.elements.api_key.disabled = false; form.elements.clear_api_key.checked = false; form.elements.clear_api_key.disabled = !config?.has_api_key; form.elements.api_key.placeholder = config?.has_api_key ? '已安全保存；留空表示保持现有值' : '仅本次输入会覆盖；留空表示无密钥/保留现值';
    const provider = Object.hasOwn(PROTOCOLS, config?.provider) ? config.provider : config ? 'custom' : 'openai'; form.elements.provider.value = provider; form.elements.custom_provider.value = provider === 'custom' ? config.provider : ''; q('[data-custom-provider]').hidden = provider !== 'custom'; q('[data-model-dialog-title]').textContent = config ? '编辑模型配置' : '新增模型'; q('[data-provider-hint]').textContent = provider === 'custom' ? '自定义 LiteLLM provider 前缀；按服务要求填写 API Base。' : `${PROTOCOLS[provider].note} · ${PROTOCOLS[provider].path} · 示例 ${PROTOCOLS[provider].base}`; q('[data-model-dialog-feedback]').textContent = ''; q('[data-probe-feedback]').textContent = ''; q('[data-probed-model]').innerHTML = '<option value="">先运行模型探测</option>'; state.probeModels = []; form.elements.health_mode.value = config?.health?.mode || 'inherit'; form.elements.health_interval.value = config?.health?.interval_minutes || 30; q('[data-health-interval]').hidden = form.elements.health_mode.value !== 'custom'; form.elements.health_interval.disabled = form.elements.health_mode.value !== 'custom'; drafts.accept('model'); q('[data-dialog="model"]').showModal(); form.elements.display_name.focus({ preventScroll: true });
  }
  function openAccount(account = null) {
    const form = q('[data-form="account"]'); form.reset(); form.elements.id.value = account?.id || ''; form.elements.provider_type.value = account?.provider_type || 'qq'; form.elements.email_address.value = account?.email_address || ''; form.elements.email_address.readOnly = Boolean(account); form.elements.imap_host.value = account?.imap_host || ''; form.elements.imap_port.value = account?.imap_port || 993; form.elements.username.value = account?.username || ''; form.elements.use_ssl.checked = account?.use_ssl ?? true; form.elements.authorization_code.value = ''; form.elements.authorization_code.required = !account; form.elements.authorization_code.placeholder = account?.has_authorization_code ? '已安全保存；留空保留现值' : 'IMAP 授权码'; form.elements.check_interval_minutes.value = account?.check_interval_minutes ?? 15; form.elements.sync_lookback_days.value = account?.sync_lookback_days ?? ''; q('[data-imap-fields]').hidden = form.elements.provider_type.value !== 'custom'; q('[data-account-dialog-title]').textContent = account ? '编辑邮箱账户' : '添加邮箱账户'; msg('[data-account-dialog-feedback]', ''); form.elements.clear_authorization_code.checked = false; form.elements.clear_authorization_code.disabled = !account?.has_authorization_code; form.elements.authorization_code.disabled = false; drafts.accept('account'); q('[data-dialog="account"]').showModal();
  }
  function openPolicy(policy = null) {
    const form = q('[data-form="policy"]'); form.reset(); form.elements.id.value = policy?.id || ''; form.elements.name.value = policy?.name || ''; form.elements.desktop_enabled.checked = policy ? Boolean(policy.desktop_enabled) : true; form.elements.push_enabled.checked = Boolean(policy?.push_enabled); form.elements.push_provider.value = policy?.push_provider || 'ntfy'; form.elements.push_url.value = ''; form.elements.push_url.placeholder = policy?.has_push_url ? '已安全保存；留空保留现值' : 'ntfys://… 或 bark://…'; form.elements.clear_push_url.checked = false; form.elements.clear_push_url.disabled = !policy?.has_push_url; form.elements.notify_actionable_deadlines.checked = policy?.notify_actionable_deadlines ?? true; form.elements.enabled.checked = policy?.enabled ?? true; form.querySelectorAll('[name="important_event_types"]').forEach((input) => { input.checked = policy ? (policy.important_event_types || []).includes(input.value) : ['ASSESSMENT', 'WRITTEN_TEST', 'INTERVIEW', 'INTERVIEW_CHANGED', 'OFFER'].includes(input.value); }); q('[data-policy-dialog-title]').textContent = policy ? '编辑通知策略' : '新建通知策略'; msg('[data-policy-dialog-feedback]', ''); form.elements.push_url.disabled = false; drafts.accept('policy'); q('[data-dialog="policy"]').showModal();
  }

  function cancelModelProbe() {
    probeSequence += 1;
    probeController?.abort();
    probeController = null;
    buttonBusy(q('[data-action="probe-models"]'), false);
  }
  function invalidateProbe(event) {
    if (!event.target.closest('[data-form="model"]') || !['provider', 'custom_provider', 'api_base', 'api_key', 'clear_api_key', 'use_proxy'].includes(event.target.name)) return;
    cancelModelProbe();
    state.probeModels = [];
    q('[data-probed-model]').innerHTML = '<option value="">连接已修改，请重新获取模型</option>';
    msg('[data-probe-feedback]', '');
  }
  async function modelProbe(form = q('[data-form="model"]'), configId = null) {
    cancelModelProbe();
    const sequence = probeSequence;
    probeController = new AbortController();
    const data = new FormData(form); const providerChoice = data.get('provider'); const provider = providerChoice === 'custom' ? String(data.get('custom_provider') || '').trim() : providerChoice; const savedId = configId || form.elements.id.value; const options = modelProbeRequest({ provider, api_base: data.get('api_base'), use_proxy: form.elements.use_proxy.checked, timeout_seconds: data.get('timeout_seconds'), api_key: data.get('api_key'), config_id: savedId });
    const button = q('[data-action="probe-models"]'); buttonBusy(button, true, '正在探测服务…'); msg('[data-probe-feedback]', '正在请求所填服务的模型列表…');
    try { const result = await req('/llm/models-probe', { ...options, signal: probeController.signal }); if (state.cleaned || sequence !== probeSequence) return; state.probeModels = collection(result?.models).length ? result.models : (Array.isArray(result?.models) ? result.models : []); q('[data-probed-model]').innerHTML = state.probeModels.length ? state.probeModels.map((model) => `<option value="${esc(typeof model === 'string' ? model : model.id)}">${esc(typeof model === 'string' ? model : model.id)}</option>`).join('') : '<option value="">服务未返回模型</option>'; msg('[data-probe-feedback]', state.probeModels.length ? `服务返回 ${state.probeModels.length} 个模型。` : '服务没有返回模型列表。', 'success'); }
    catch (error) { if (!state.cleaned && sequence === probeSequence) msg('[data-probe-feedback]', `模型探测失败：${error.message}`, 'error'); }
    finally { if (!state.cleaned && sequence === probeSequence) buttonBusy(button, false); }
  }
  async function saveModel(event) {
    event.preventDefault(); const form = event.target.closest('form'); if (!form) return; const data = new FormData(form); const id = String(data.get('id') || ''); const selectedProvider = String(data.get('provider')); const values = { display_name: data.get('display_name'), provider: selectedProvider === 'custom' ? data.get('custom_provider') : selectedProvider, model: data.get('model'), api_base: data.get('api_base'), use_proxy: form.elements.use_proxy.checked, temperature: data.get('temperature'), timeout_seconds: data.get('timeout_seconds'), enabled: form.elements.enabled.checked, health_mode: data.get('health_mode'), health_interval: data.get('health_interval'), api_key: data.get('api_key'), clear_api_key: form.elements.clear_api_key.checked }; const request = modelSaveRequest(values, id); modelSaving = true; const submit = form.querySelector('[type="submit"]'); buttonBusy(submit, true, '保存中…');
    try { const saved = await req(request.path, request.options); if (state.cleaned) return; modelSaving = false; drafts.accept('model'); hideDialog(q('[data-dialog="model"]')); await loadModels(); if (state.cleaned) return; q(`[data-model-edit="${saved.id}"]`)?.focus({ preventScroll: true }); msg('[data-model-feedback]', '模型配置已保存。', 'success'); }
    catch (error) { if (!state.cleaned) msg('[data-model-dialog-feedback]', `保存失败：${error.message}`, 'error'); }
    finally { modelSaving = false; if (!state.cleaned) buttonBusy(submit, false); }
  }
  async function saveRouting(event) {
    event.preventDefault(); const form = event.target.closest('form'); if (!form) return; const max = Number(form.elements.max_parallel_calls.value); if (!Number.isInteger(max) || max < 1 || max > 8) { msg('[data-routing-feedback]', '并发数须为 1 到 8。', 'error'); return; }
    const payload = { auto_select_enabled: form.elements.auto_select_enabled.checked, default_config_id: form.elements.default_config_id.value ? Number(form.elements.default_config_id.value) : null, email_config_id: form.elements.email_config_id.value ? Number(form.elements.email_config_id.value) : null, max_parallel_calls: max };
    try { state.routing = await req('/llm/routing', jsonOptions('PUT', payload)); if (state.cleaned) return; drafts.accept('routing'); msg('[data-routing-feedback]', '模型路由已保存。', 'success'); await loadModels(); }
    catch (error) { msg('[data-routing-feedback]', `保存路由失败：${error.message}`, 'error'); }
  }
  async function saveAccount(event) {
    event.preventDefault(); const form = event.target.closest('form'); if (!form) return; const data = new FormData(form); const id = String(data.get('id') || ''); const provider = String(data.get('provider_type')); const payload = { email_address: String(data.get('email_address')).trim(), provider_type: provider, check_interval_minutes: Number(data.get('check_interval_minutes')) };
    const lookback = String(data.get('sync_lookback_days') || '').trim(); payload.sync_lookback_days = lookback ? Number(lookback) : null;
    if (provider === 'custom') { payload.imap_host = String(data.get('imap_host')).trim(); payload.imap_port = Number(data.get('imap_port')); payload.username = String(data.get('username')).trim() || null; payload.use_ssl = form.elements.use_ssl.checked; }
    const secret = String(data.get('authorization_code') || ''); if (secret) payload.authorization_code = secret; if (id && form.elements.clear_authorization_code.checked) payload.clear_authorization_code = true; const submit = form.querySelector('[type="submit"]'); buttonBusy(submit, true, '保存中…');
    try { await req(id ? `/email-accounts/${encodeURIComponent(id)}` : '/email-accounts', jsonOptions(id ? 'PATCH' : 'POST', payload)); if (state.cleaned) return; drafts.accept('account'); hideDialog(q('[data-dialog="account"]')); await loadAccounts(); msg('[data-account-feedback]', '邮箱账户已保存；授权码不会回显。', 'success'); }
    catch (error) { if (!state.cleaned) msg('[data-account-dialog-feedback]', `保存失败：${error.message}`, 'error'); }
    finally { if (!state.cleaned) buttonBusy(submit, false); }
  }
  async function accountAction(button) {
    const id = encodeURIComponent(button.dataset.accountTest || button.dataset.accountSync || button.dataset.accountToggle || button.dataset.accountDelete || ''); const action = button.dataset.accountTest ? 'test' : button.dataset.accountSync ? 'sync' : button.dataset.accountToggle ? 'toggle' : 'delete';
    if (action === 'delete' && !confirm('删除邮箱账户与本地邮件缓存？招聘事件和已建岗位会保留。')) return;
    buttonBusy(button, true, ({ test: '测试连接中…', sync: '同步中…', toggle: '更新中…', delete: '删除中…' })[action]);
    try {
      if (action === 'test') { await req(`/email-accounts/${id}/test-connection`, { method: 'POST' }); if (state.cleaned) return; msg('[data-account-feedback]', '连接成功，IMAP 登录与收件箱读取正常。', 'success'); }
      else if (action === 'sync') { const result = await req(`/email-accounts/${id}/sync`, { method: 'POST' }); if (state.cleaned) return; msg('[data-account-feedback]', `同步完成：新增 ${result.created} 封，生成 ${result.events_created} 个事件${result.processing_errors ? `，${result.processing_errors} 个待重试` : ''}${result.retry_paused ? `，${result.retry_paused} 封已暂停自动重试` : ''}。`, result.processing_errors ? 'error' : 'success'); }
      else if (action === 'toggle') { const account = state.accounts.find((item) => String(item.id) === String(decodeURIComponent(id))); await req(`/email-accounts/${id}`, jsonOptions('PATCH', { enabled: !account?.enabled })); if (state.cleaned) return; }
      else { await req(`/email-accounts/${id}`, { method: 'DELETE' }); if (state.cleaned) return; msg('[data-account-feedback]', '邮箱账户及本地邮件缓存已删除；远端邮件、招聘事件和岗位不会被删除。', 'success'); }
      await loadAccounts();
    } catch (error) { if (!state.cleaned) msg('[data-account-feedback]', `${action === 'test' ? '连接测试' : action === 'sync' ? '同步' : action === 'delete' ? '删除' : '更新'}失败：${error.message}`, 'error'); }
    finally { if (!state.cleaned) buttonBusy(button, false); }
  }
  async function previewFilter() {
    const regex = q('[data-filter-regex]').value.trim(); const sample = q('[data-filter-sample]').value.trim(); if (!sample) { msg('[data-preview-feedback]', '先输入用于预览的邮件文本。', 'error'); return; }
    try { const result = await req('/email-filter/preview', jsonOptions('POST', { sender: '', subject: '', body_text: sample, regex: regex || null })); if (state.cleaned) return; const matched = result?.matched ?? result?.regex_matched ?? result?.passed; msg('[data-preview-feedback]', matched === undefined ? '服务端没有返回命中结果。' : matched ? '命中：邮件会进入后续清洗。' : '未命中：邮件会停留在必要摘要，不进入清洗。', 'success'); }
    catch (error) { if (!state.cleaned) msg('[data-preview-feedback]', `预览失败：${error.message}`, 'error'); }
  }
  async function saveFilter() {
    const regex = q('[data-filter-regex]').value.trim() || null; const keywords = q('[data-filter-keywords]').value.split(/[\n,，]/).map((item) => item.trim()).filter(Boolean);
    const options = emailFilterRequest(regex, keywords);
    try { const result = await req('/email-filter', options); if (state.cleaned) return; q('[data-filter-regex]').value = result?.regex || result?.pattern || ''; q('[data-filter-keywords]').value = Array.isArray(result?.keywords) ? result.keywords.join('\n') : keywords.join('\n'); drafts.accept('filter'); msg('[data-filter-feedback]', '预筛规则已保存。', 'success'); }
    catch (error) { if (!state.cleaned) msg('[data-filter-feedback]', `保存失败：${error.message}`, 'error'); }
  }
  async function savePolicy(event) {
    event.preventDefault(); const form = event.target.closest('form'); if (!form) return; const data = new FormData(form); const id = String(data.get('id') || ''); const important = [...form.querySelectorAll('[name="important_event_types"]:checked')].map((item) => item.value); if (!important.length) { msg('[data-policy-dialog-feedback]', '请至少选择一种即时提醒事件。', 'error'); return; }
    const payload = { name: String(data.get('name')).trim(), enabled: form.elements.enabled.checked, desktop_enabled: form.elements.desktop_enabled.checked, push_enabled: form.elements.push_enabled.checked, push_provider: String(data.get('push_provider')), clear_push_url: form.elements.clear_push_url.checked, important_event_types: important, notify_actionable_deadlines: form.elements.notify_actionable_deadlines.checked }; const target = String(data.get('push_url') || '').trim(); if (target) payload.push_url = target; const submit = form.querySelector('[type="submit"]'); buttonBusy(submit, true, '保存中…');
    try { await req(id ? `/notification-policies/${encodeURIComponent(id)}` : '/notification-policies', jsonOptions(id ? 'PUT' : 'POST', payload)); if (state.cleaned) return; drafts.accept('policy'); hideDialog(q('[data-dialog="policy"]')); await loadPolicies(); msg('[data-policy-feedback]', '通知策略已保存；Push URL 不会回显。', 'success'); }
    catch (error) { if (!state.cleaned) msg('[data-policy-dialog-feedback]', `保存失败：${error.message}`, 'error'); }
    finally { if (!state.cleaned) buttonBusy(submit, false); }
  }
  async function policyTest(button) {
    const policy = state.policies.find((item) => String(item.id) === button.dataset.policyTest); if (!policy) return; const provider = button.dataset.provider; if (!['desktop', 'push'].includes(provider) || (provider === 'desktop' ? !policy.desktop_enabled : !policy.push_enabled)) { msg('[data-policy-feedback]', '该策略没有启用可测试的渠道。', 'error'); return; }
    buttonBusy(button, true, '发送测试通知…'); try { const result = await req(`/notification-policies/${encodeURIComponent(policy.id)}/test`, jsonOptions('POST', { provider })); if (state.cleaned) return; msg('[data-policy-feedback]', result.status === 'sent' ? `测试通知已发送（${provider}）。` : `测试未发送：${result.error || result.status}`, result.status === 'sent' ? 'success' : 'error'); }
    catch (error) { if (!state.cleaned) msg('[data-policy-feedback]', `测试发送失败：${error.message}`, 'error'); }
    finally { if (!state.cleaned) buttonBusy(button, false); }
  }

  function renderUsageMetrics(data) {
    const summary = data?.summary || {}; q('[data-usage-metrics]').innerHTML = METRICS.map(([key, label, unit]) => `<button class="wb-usage-metric" type="button" data-usage-metric="${key}" aria-pressed="${state.usageMetric === key}"><span>${label}</span><strong>${data ? esc(int(summary[key])) : '…'}</strong><small>${key === 'average_duration_ms' ? '所选周期平均' : '所选周期统计'} · ${unit}</small></button>`).join('');
    root.querySelectorAll('[data-usage-metric]').forEach((button) => button.addEventListener('click', () => { state.usageMetric = button.dataset.usageMetric; renderUsageMetrics(state.usage); renderUsageChart(); }));
  }
  function renderUsageChart() {
    const container = q('[data-usage-chart]'); const data = state.usage; if (!data) { container.innerHTML = '<p class="wb-muted">选择日期范围后读取用量。</p>'; return; }
    const points = Array.isArray(data.points) ? data.points : []; const values = points.map((point) => point?.available === false ? null : n(point?.[state.usageMetric])).filter((value) => value !== null && value !== undefined && Number.isFinite(value));
    if (!values.length) { container.innerHTML = `<p class="wb-empty">${data.recorded_from ? '所选日期没有可显示的时间明细。' : '尚无历史时间明细；有新调用记录后才会形成趋势。'}</p>`; return; }
    const max = Math.max(1, ...values); const bars = points.map((point) => { const value = point?.available === false ? null : n(point?.[state.usageMetric]); const title = `${esc(point.timestamp || '')} · ${value == null ? '暂无明细' : int(value)}`; return `<div class="wb-chart-column" title="${title}"><span style="height:${value == null ? 0 : Math.max(2, value / max * 100)}%"></span><small>${esc(String(point.timestamp || '').slice(5, 10))}</small></div>`; }).join('');
    container.innerHTML = `<div class="wb-chart-bars" role="img" aria-label="${esc(METRICS.find(([key]) => key === state.usageMetric)?.[1])}时段趋势">${bars}</div>`;
  }
  async function loadUsage(start = q('[data-form="usage-range"]').elements.start.value, end = q('[data-form="usage-range"]').elements.end.value) {
    if (!state.usageConfig || !start || !end || start > end || end > todayUtc8() || (Date.parse(end) - Date.parse(start)) / 86400000 > 365) { msg('[data-usage-feedback]', '请选择有效日期：开始不晚于结束，最多 366 天，结束不晚于今天。', 'error'); return; }
    state.usageController?.abort(); const controller = new AbortController(); state.usageController = controller; state.usage = null; q('[data-usage-lifetime]').textContent = ''; q('[data-usage-note]').textContent = ''; renderUsageMetrics(null); q('[data-usage-chart]').innerHTML = '<p class="wb-muted">正在读取用量趋势…</p>'; msg('[data-usage-feedback]', '');
    try { const payload = await req(`/llm/configs/${encodeURIComponent(state.usageConfig.id)}/usage?${new URLSearchParams({ start, end })}`, { signal: controller.signal }); if (state.cleaned || controller.signal.aborted || state.usageController !== controller) return; state.usage = payload; renderUsageMetrics(payload); renderUsageChart(); const life = payload.lifetime || state.usageConfig; q('[data-usage-lifetime]').textContent = `历史累计：${int((life.total_prompt_tokens || 0) + (life.total_completion_tokens || 0))} Token · ${int(life.call_count)} 次调用 · 成功 ${int(life.success_count)} · 失败 ${int(life.failure_count)} · 最近使用 ${payload.last_used_at ? fmtTime(payload.last_used_at) : '尚未使用'}`; q('[data-usage-note]').textContent = `本周期业务 ${int(payload.by_purpose?.business?.call_count || 0)} 次 / ${int(payload.by_purpose?.business?.total_tokens || 0)} Token；测试 ${int(payload.by_purpose?.test?.call_count || 0)} 次 / ${int(payload.by_purpose?.test?.total_tokens || 0)} Token（均计入总量）。历史未分类调用沿用业务口径。${payload.recorded_from ? `日期明细始于 ${fmtTime(payload.recorded_from)}。` : '没有历史调用明细；不会用 0 补造趋势。'}${payload.legacy_call_count ? `此前 ${int(payload.legacy_call_count)} 次调用仅计入累计值，不显示在时间曲线中。` : ''}`; msg('[data-usage-feedback]', `${payload.start} 至 ${payload.end}，按${payload.granularity === 'hour' ? '小时' : '天'}统计。`, 'success'); }
    catch (error) { if (!state.cleaned && !controller.signal.aborted) { q('[data-usage-chart]').innerHTML = '<p class="wb-empty">暂时无法显示用量曲线。</p>'; msg('[data-usage-feedback]', `读取失败：${error.message}`, 'error'); } }
  }
  function openUsage(config, navigateToPage = true) {
    if (!config) return;
    state.usageConfig = config; state.usageMetric = 'total_tokens'; state.usage = null;
    q('[data-usage-config]').value = String(config.id);
    q('[data-usage-title]').textContent = `用量统计 · ${config.display_name}`;
    const end = todayUtc8(), form = q('[data-form="usage-range"]');
    form.elements.end.max = end;
    if (!form.elements.start.value) { form.elements.start.value = shiftDay(end, -6); form.elements.end.value = end; }
    if (navigateToPage) setTab('usage', true);
    renderUsageMetrics(null); void loadUsage();
  }
  async function toggleModel(button) { const config = state.configs.find((item) => String(item.id) === button.dataset.modelToggle); if (!config) return; buttonBusy(button, true, '更新中…'); try { await req(`/llm/configs/${encodeURIComponent(config.id)}`, jsonOptions('PUT', { enabled: !config.enabled })); if (state.cleaned) return; await loadModels(); } catch (error) { msg('[data-model-feedback]', `更新模型失败：${error.message}`, 'error'); } finally { if (!state.cleaned) buttonBusy(button, false); } }
  async function testModel(button) {
    if (button.disabled) return;
    buttonBusy(button, true, '测试中…');
    const id = button.dataset.modelTest;
    const feedbackSelector = `[data-model-test-feedback="${id}"]`;
    const feedback = (text, kind = 'info') => {
      if (state.cleaned) return;
      const element = q(feedbackSelector);
      if (element) { element.hidden = false; msg(feedbackSelector, text, kind); }
    };
    feedback('正在测试模型连接…');
    let testError = '';
    try {
      await req(`/llm/configs/${encodeURIComponent(id)}/test`, { method: 'POST' });
      feedback('模型连接测试通过', 'success');
    } catch (error) { testError = `模型连接测试失败：${error.message}`; feedback(testError, 'error'); }
    finally {
      if (!state.cleaned) {
        buttonBusy(button, false);
        try {
          const config = await req(`/llm/configs/${encodeURIComponent(id)}`);
          if (!state.cleaned) {
            state.configs = state.configs.map((item) => String(item.id) === id ? config : item);
            const card = q(`[data-model-card="${id}"]`); if (card) card.outerHTML = modelSummary(config); renderDiagnostics();
            if (testError && !['failed', 'interrupted'].includes(config.health?.status)) feedback(testError, 'error');
            q(`[data-model-test="${id}"]`)?.focus({ preventScroll: true });
          }
        } catch { /* Original test feedback remains visible. */ }
      }
    }
  }
  async function probeSavedModel(button) { const config = state.configs.find((item) => String(item.id) === button.dataset.modelProbe); if (!config) return; buttonBusy(button, true, '探测中…'); try { const result = await req('/llm/models-probe', modelProbeRequest({ provider: config.provider, api_base: config.api_base, config_id: config.id, timeout_seconds: config.timeout_seconds })); if (!state.cleaned) msg('[data-model-feedback]', result.models?.length ? `${config.display_name} 的服务返回 ${result.models.length} 个模型。` : '服务没有返回模型列表。', 'success'); } catch (error) { if (!state.cleaned) msg('[data-model-feedback]', `模型探测失败：${error.message}`, 'error'); } finally { if (!state.cleaned) buttonBusy(button, false); } }
  async function deleteModel(button) { const config = state.configs.find((item) => String(item.id) === button.dataset.modelDelete); if (!config || !confirm(`删除模型配置“${config.display_name}”？`)) return; buttonBusy(button, true, '删除中…'); try { await req(`/llm/configs/${encodeURIComponent(config.id)}`, { method: 'DELETE' }); if (state.cleaned) return; await loadModels(); msg('[data-model-feedback]', '模型配置已删除；服务端会按兼容规则清理相关路由。', 'success'); } catch (error) { msg('[data-model-feedback]', `删除失败：${error.message}`, 'error'); } finally { if (!state.cleaned) buttonBusy(button, false); } }
  async function deletePolicy(button) { const policy = state.policies.find((item) => String(item.id) === button.dataset.policyDelete); if (!policy || !confirm(`删除通知策略“${policy.name}”？历史通知记录会保留。`)) return; try { await req(`/notification-policies/${encodeURIComponent(policy.id)}`, { method: 'DELETE' }); if (state.cleaned) return; await loadPolicies(); msg('[data-policy-feedback]', '通知策略已删除，发送历史保留。', 'success'); } catch (error) { msg('[data-policy-feedback]', `删除失败：${error.message}`, 'error'); } }

  const onClick = async (event) => {
    root.querySelectorAll('.wb-model-more[open]').forEach((menu) => { if (!menu.contains(event.target)) menu.open = false; });
    const result = event.target.closest('[data-settings-result]');
    if (result) {
      setTab(result.dataset.resultSection, true, result.dataset.resultPanel);
      q('[data-settings-search]').value = ''; renderSearch();
      root.querySelectorAll('[data-search-hit]').forEach((item) => item.removeAttribute('data-search-hit'));
      const target = q(result.dataset.resultTarget);
      if (target) { target.setAttribute('data-search-hit', 'true'); target.scrollIntoView({ block: 'center' }); target.focus({ preventScroll: true }); }
      return;
    }
    const panel = event.target.closest('[data-settings-panel]');
    if (panel) { setTab(panel.dataset.settingsSection, true, panel.dataset.settingsPanel); return; }
    if (event.target.closest('[data-action="refresh-diagnostics"]')) { await Promise.all([loadModels(), loadAccounts()]); return; }
    if (event.target.closest('[data-action="toggle-api-key"]')) { const input = q('[data-form="model"]').elements.api_key; const show = input.type === 'password'; input.type = show ? 'text' : 'password'; const button = q('[data-action="toggle-api-key"]'); button.setAttribute('aria-pressed', String(show)); button.setAttribute('aria-label', show ? '隐藏 API Key' : '显示本次输入的 API Key'); return; }
    const tab = event.target.closest('[data-setting-tab]'); if (tab) { setTab(tab.dataset.settingTab, true); return; }
    if (event.target.closest('[data-close]')) { hideDialog(event.target.closest('[data-dialog]')); return; }
    if (event.target.closest('[data-action="reload-all"]')) { if (canLeave()) { state.discarding = true; window.location.reload(); } return; }
    if (event.target.closest('[data-action="new-model"]')) { openModel(); return; }
    if (event.target.closest('[data-action="new-account"]')) { openAccount(); return; }
    if (event.target.closest('[data-action="new-policy"]')) { openPolicy(); return; }
    const editModel = event.target.closest('[data-model-edit]'); if (editModel) { const config = state.configs.find((item) => String(item.id) === editModel.dataset.modelEdit); if (config) openModel(config); return; }
    const toggle = event.target.closest('[data-model-toggle]'); if (toggle) { await toggleModel(toggle); return; }
    const test = event.target.closest('[data-model-test]'); if (test) { await testModel(test); return; }
    const probe = event.target.closest('[data-model-probe]'); if (probe) { await probeSavedModel(probe); return; }
    const usage = event.target.closest('[data-model-usage]'); if (usage) { const config = state.configs.find((item) => String(item.id) === usage.dataset.modelUsage); if (config) openUsage(config); return; }
    const deleteLlm = event.target.closest('[data-model-delete]'); if (deleteLlm) { await deleteModel(deleteLlm); return; }
    const editAccount = event.target.closest('[data-account-edit]'); if (editAccount) { openAccount(state.accounts.find((item) => String(item.id) === editAccount.dataset.accountEdit)); return; }
    const account = event.target.closest('[data-account-test], [data-account-sync], [data-account-toggle], [data-account-delete]'); if (account) { await accountAction(account); return; }
    const editPolicy = event.target.closest('[data-policy-edit]'); if (editPolicy) { openPolicy(state.policies.find((item) => String(item.id) === editPolicy.dataset.policyEdit)); return; }
    const testPolicy = event.target.closest('[data-policy-test]'); if (testPolicy) { await policyTest(testPolicy); return; }
    const deletePol = event.target.closest('[data-policy-delete]'); if (deletePol) { await deletePolicy(deletePol); return; }
    if (event.target.closest('[data-action="validate-regex"]')) { const value = q('[data-filter-regex]').value.trim(); if (!value) { msg('[data-filter-feedback]', '正则为空：将使用已保存的关键词进行筛选。', 'info'); return; } try { new RegExp(value, 'i'); msg('[data-filter-feedback]', '浏览器语法检查通过；最终以服务端 Python 正则校验为准。', 'success'); } catch (error) { msg('[data-filter-feedback]', `浏览器不支持此语法：${error.message}；仍可保存或运行服务端预览，由 Python 校验。`, 'error'); } return; }
    if (event.target.closest('[data-action="save-filter"]')) { await drafts.save('filter', saveFilter); return; }
    if (event.target.closest('[data-action="preview-filter"]')) { await previewFilter(); return; }
    if (event.target.closest('[data-action="probe-models"]')) { await modelProbe(); return; }
    if (event.target.closest('[data-action="use-probed-model"]')) { const value = q('[data-probed-model]').value; if (value) { q('[data-form="model"]').elements.model.value = value; drafts.sync('model'); } return; }
    if (event.target.closest('[data-action="refresh-usage"]')) { await loadUsage(); return; }
    const period = event.target.closest('[data-usage-days]'); if (period) { const end = todayUtc8(); q('[data-form="usage-range"]').elements.start.value = shiftDay(end, 1 - Number(period.dataset.usageDays)); q('[data-form="usage-range"]').elements.end.value = end; await loadUsage(); }
  };
  const onChange = (event) => {
    invalidateProbe(event);
    if (event.target.matches('[data-settings-select]')) { setTab(event.target.value, true); return; }
    if (event.target.matches('[data-usage-config]')) { openUsage(state.configs.find((config) => String(config.id) === event.target.value), false); return; }
    if (event.target.matches('[name="auto_select_enabled"]')) q('[data-fixed-model]').hidden = event.target.checked;
    if (event.target.matches('[name="clear_authorization_code"]')) { const form = q('[data-form="account"]'); if (event.target.checked) form.elements.authorization_code.value = ''; form.elements.authorization_code.disabled = event.target.checked; }
    if (event.target.matches('[name="clear_push_url"]')) { const form = q('[data-form="policy"]'); if (event.target.checked) { form.elements.push_url.value = ''; form.elements.push_enabled.checked = false; } form.elements.push_url.disabled = event.target.checked; }

    if (event.target.name === 'health_mode') {
      q('[data-health-interval]').hidden = event.target.value !== 'custom';
      q('[name="health_interval"]').disabled = event.target.value !== 'custom';
    }
    if (event.target.matches('[data-probed-model]') && event.target.value) q('[data-form="model"]').elements.model.value = event.target.value;
    if (event.target.matches('[name="provider_type"]')) q('[data-imap-fields]').hidden = event.target.value !== 'custom';
    if (event.target.matches('[name="clear_api_key"]')) { const form = q('[data-form="model"]'); if (event.target.checked) form.elements.api_key.value = ''; form.elements.api_key.disabled = event.target.checked; }
    if (event.target.matches('[name="provider"]')) { const provider = event.target.value; q('[data-custom-provider]').hidden = provider !== 'custom'; q('[data-provider-hint]').textContent = provider === 'custom' ? '自定义 LiteLLM provider 前缀；按服务要求填写 API Base。' : `${PROTOCOLS[provider].note} · ${PROTOCOLS[provider].path} · 示例 ${PROTOCOLS[provider].base}`; const base = q('[data-form="model"]').elements.api_base; if (provider !== 'custom' && (!base.value || Object.values(PROTOCOLS).some((item) => item.base === base.value))) base.value = PROTOCOLS[provider].base; }
    const form = event.target.closest('[data-form]');
    if (form && ['model', 'account', 'policy'].includes(form.dataset.form)) drafts.sync(form.dataset.form);
  };
  const onSubmit = (event) => {
    const name = event.target.dataset.form;
    const actions = { model: saveModel, routing: saveRouting, account: saveAccount, policy: savePolicy };
    if (actions[name]) { event.preventDefault(); void drafts.save(name, () => actions[name](event)); }
    else if (name === 'appearance') void saveAppearance(event);
    else if (name === 'usage-range') { event.preventDefault(); void loadUsage(); }
  };
  const onInput = (event) => {
    invalidateProbe(event);
    if (event.target.matches('[data-settings-search]')) renderSearch();
    const secrets = { api_key: ['model', 'clear_api_key'], authorization_code: ['account', 'clear_authorization_code'], push_url: ['policy', 'clear_push_url'] };
    if (secrets[event.target.name] && event.target.value) {
      const [name, clear] = secrets[event.target.name], form = q(`[data-form="${name}"]`);
      form.elements[clear].checked = false; event.target.disabled = false; drafts.sync(name);
    }
  };
  root.addEventListener('click', onClick); root.addEventListener('change', onChange); root.addEventListener('input', onInput); root.addEventListener('submit', onSubmit);
  setTab(route?.params?.get?.('section') || 'appearance', false, route?.params?.get?.('settings_panel'));
  void loadAppearance();
  Promise.all([loadModels(), loadAccounts(), loadFilter(), loadPolicies()]);
  const beforeUnload = (event) => { if (!state.discarding && (drafts.hasDirty || drafts.busy || prompts.dirty || prompts.busy)) { event.preventDefault(); event.returnValue = ''; } };
  window.addEventListener('beforeunload', beforeUnload);
  const onMenuEscape = (event) => { if (event.key === 'Escape') root.querySelectorAll('.wb-model-more[open]').forEach((menu) => { menu.open = false; menu.querySelector('summary').focus(); }); };
  root.addEventListener('keydown', onMenuEscape);
  const cleanup = () => {
    state.cleaned = true; state.seq += 1; controller.abort(); state.usageController?.abort();
    probeController?.abort();
    window.clearInterval(modelPoll); root.removeEventListener('keydown', onMenuEscape);
    behaviors.cleanup(); prompts.cleanup(); proxy.cleanup(); drafts.cleanup();
    window.removeEventListener('beforeunload', beforeUnload);
    root.querySelectorAll('dialog').forEach((dialog) => { dialog.removeEventListener('cancel', onDialogCancel); if (dialog.open) dialog.close(); });
    setNavigationGuard(null); delete shell.dataset.settingsActive;
    root.removeEventListener('click', onClick); root.removeEventListener('change', onChange); root.removeEventListener('input', onInput); root.removeEventListener('submit', onSubmit);
  };
  cleanup.updateRoute = (next) => setTab(next.params.get('section') || 'appearance', false, next.params.get('settings_panel'));
  return cleanup;
}
