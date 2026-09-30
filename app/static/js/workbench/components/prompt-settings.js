import { esc, jsonOptions } from '../integrations/shared.js';

const FOCUS = { responsibilities: '岗位职责', requirements: '任职要求', salary: '薪资待遇', location: '工作地点', experience: '学历与经验', dates: '招聘时间' };

export function promptMarkup() {
  return `<details class="wb-prompt-card" data-prompt-card><summary><div><h2>岗位解析提示词</h2><p>控制从招聘网页中提取什么，以及怎样整理。</p></div><span class="wb-prompt-scope">仅用于网页岗位解析 <span aria-hidden="true">⌄</span></span></summary><div class="wb-prompt-body"><p data-prompt-feedback class="wb-feedback" role="status">正在加载…</p><button type="button" class="wb-text-button" data-prompt-reload>重新加载配置</button><fieldset data-prompt-fields disabled><div class="wb-prompt-tabs" role="tablist" aria-label="提示词编辑方式"><button type="button" role="tab" id="prompt-tab-quick" aria-controls="prompt-quick" data-prompt-tab="quick">快速修改</button><button type="button" role="tab" id="prompt-tab-full" aria-controls="prompt-full" data-prompt-tab="full">完整修改</button><button type="button" role="tab" id="prompt-tab-templates" aria-controls="prompt-templates" data-prompt-tab="templates">模板库</button></div>
    <div data-prompt-panel="quick" id="prompt-quick" role="tabpanel" aria-labelledby="prompt-tab-quick"><div class="wb-prompt-columns"><div><label>当前模板<select data-prompt-template></select></label><fieldset class="wb-prompt-focus"><legend>提取重点</legend>${Object.entries(FOCUS).map(([key, title]) => `<label><input type="checkbox" value="${key}" data-prompt-focus><span>${title}</span></label>`).join('')}</fieldset><label>内容整理<select data-prompt-style><option value="original">保留原文要点</option><option value="concise">简洁整理</option></select></label><label>补充要求<textarea rows="4" maxlength="4000" data-prompt-rules placeholder="例如：区分必备条件与加分项；薪资保留范围和单位。"></textarea></label></div><aside><h3>这份配置将影响</h3><p>浏览器扩展的岗位解析<br>网页草稿的 AI 提取</p><p>不修改已保存岗位，不影响邮件提示词。</p><div class="wb-prompt-note">信息缺失时留空，不推测。网页内容作为独立资料传入，输出字段由系统维护。</div></aside></div></div>
    <div data-prompt-panel="full" id="prompt-full" role="tabpanel" aria-labelledby="prompt-tab-full" hidden><div class="wb-section-heading"><div><h3>完整提示词</h3><p>自由编辑解析指令；网页资料与输出字段由系统独立传入。</p></div><button type="button" class="wb-button" data-prompt-generate>从快速配置生成</button></div><label>解析指令<textarea rows="13" maxlength="16000" data-prompt-full spellcheck="false"></textarea></label><p class="wb-hint">切换页签会保留未保存的文本。点击“保存提示词”后，当前编辑方式才会生效。</p></div>
    <div data-prompt-panel="templates" id="prompt-templates" role="tabpanel" aria-labelledby="prompt-tab-templates" hidden><div class="wb-prompt-templates" data-prompt-templates></div><div class="wb-form-grid"><label>个人模板名称<input maxlength="80" data-prompt-name placeholder="例如：我的校招模板"></label><div class="wb-inline-actions"><button type="button" class="wb-button" data-prompt-copy>将当前配置另存为模板</button><button type="button" class="wb-button" data-prompt-update hidden>更新所选个人模板</button></div></div><p class="wb-hint">系统模板保留原版。选择模板会先载入编辑区，保存后才应用；个人模板随配置一同保存。</p></div>
    <div class="wb-prompt-footer"><span data-prompt-mode class="wb-hint"></span><div class="wb-inline-actions"><button type="button" class="wb-button" data-prompt-reset>恢复默认</button><button type="button" class="wb-button wb-primary" data-prompt-save>保存提示词</button></div></div></fieldset></div></details>`;
}

export function mountPromptSettings(root, api, onChange = () => {}) {
  const host = root.querySelector('[data-prompt-card]');
  const q = (selector) => host.querySelector(selector);
  let config, builtins = [], dirty = false, cleaned = false, busy = false, tab = 'quick';
  const feedback = (text, error = false) => { if (!cleaned) { q('[data-prompt-feedback]').textContent = text; q('[data-prompt-feedback]').dataset.kind = error ? 'error' : 'success'; } };
  const allTemplates = () => [...builtins, ...config.templates];
  function read() {
    return { ...config, focus: [...host.querySelectorAll('[data-prompt-focus]:checked')].map((input) => input.value), template_id: q('[data-prompt-template]').value, style: q('[data-prompt-style]').value, additional_rules: q('[data-prompt-rules]').value, full_text: q('[data-prompt-full]').value };
  }
  function selectTab(name) {
    tab = name;
    if (name !== 'templates') config.mode = name;
    host.querySelectorAll('[data-prompt-tab]').forEach((button) => button.setAttribute('aria-selected', String(button.dataset.promptTab === name)));
    host.querySelectorAll('[data-prompt-panel]').forEach((panel) => { panel.hidden = panel.dataset.promptPanel !== name; });
    q('[data-prompt-mode]').textContent = `保存后使用：${config.mode === 'full' ? '完整提示词' : '快速配置'}${dirty ? ' · 有未保存修改' : ''}`;
    q('[data-prompt-save]').disabled = !dirty || busy;
    onChange();
  }
  function templates() {
    q('[data-prompt-template]').innerHTML = allTemplates().map((item) => `<option value="${esc(item.id)}">${esc(item.name)}${item.id.startsWith('custom-') ? ' · 个人' : ''}</option>`).join('');
    q('[data-prompt-template]').value = config.template_id;
    q('[data-prompt-templates]').innerHTML = allTemplates().map((item) => `<button type="button" class="wb-template-choice" data-prompt-use="${esc(item.id)}" aria-pressed="${item.id === config.template_id}"><strong>${esc(item.name)}</strong><span>${esc(item.text.slice(0, 85))}</span><small>${item.id.startsWith('custom-') ? '个人模板 · 载入编辑' : '系统模板 · 使用副本'}</small></button>`).join('');
    q('[data-prompt-update]').hidden = !config.template_id.startsWith('custom-');
  }
  function render() {
    templates();
    host.querySelectorAll('[data-prompt-focus]').forEach((input) => { input.checked = config.focus.includes(input.value); });
    q('[data-prompt-style]').value = config.style;
    q('[data-prompt-rules]').value = config.additional_rules;
    q('[data-prompt-full]').value = config.full_text;
    selectTab(config.mode);
  }
  async function load() {
    if (dirty && !confirm('重新加载会丢弃未保存的提示词修改，继续？')) return;
    const data = await api('/job-extraction-prompt');
    if (cleaned) return;
    config = data.config; builtins = data.builtins; dirty = false;
    render(); feedback('配置已加载。');
  }
  async function compose() {
    const result = await api('/job-extraction-prompt/preview', jsonOptions('POST', { ...read(), mode: 'quick' }));
    return result.effective_text;
  }
  async function action(event) {
    const button = event.target.closest('button');
    if (!button || busy) return;
    if (!config && !button.matches('[data-prompt-reload]')) return;
    busy = true; q('[data-prompt-fields]').disabled = true; onChange();
    try {
      if (button.matches('[data-prompt-reload]')) await load();
      else if (button.dataset.promptTab) {
        config = read();
        if (button.dataset.promptTab === 'full' && !config.full_text.trim()) { const text = await compose(); if (cleaned) return; q('[data-prompt-full]').value = text; }
        if (button.dataset.promptTab !== 'templates' && button.dataset.promptTab !== config.mode) dirty = true;
        selectTab(button.dataset.promptTab);
      } else if (button.matches('[data-prompt-generate]')) {
        if (q('[data-prompt-full]').value.trim() && !confirm('用快速配置重新生成完整提示词？当前手写内容将被替换。')) return;
        const text = await compose(); if (cleaned) return; q('[data-prompt-full]').value = text; dirty = true;
      } else if (button.dataset.promptUse) {
        if (dirty && !confirm('载入模板会替换当前编辑内容，继续？')) return;
        config = read(); config.template_id = button.dataset.promptUse;
        config.full_text = allTemplates().find((item) => item.id === config.template_id).text;
        config.mode = 'full'; dirty = true; render();
        q('[data-prompt-name]').value = config.templates.find((item) => item.id === config.template_id)?.name || '';
      } else if (button.matches('[data-prompt-copy], [data-prompt-update]')) {
        const name = q('[data-prompt-name]').value.trim();
        if (!name) throw new Error('请输入个人模板名称。');
        config = read(); const text = config.mode === 'full' ? config.full_text.trim() : await compose(); if (cleaned) return;
        if (!text) throw new Error('提示词不能为空。');
        if (button.matches('[data-prompt-update]')) {
          const item = config.templates.find((item) => item.id === config.template_id);
          if (!item) throw new Error('请选择个人模板。');
          item.name = name; item.text = text;
        } else {
          if (config.templates.length >= 20) throw new Error('最多保存 20 个个人模板。');
          const item = { id: `custom-${crypto.randomUUID()}`, name, text }; config.templates.push(item); config.template_id = item.id;
        }
        config.full_text = text; q('[data-prompt-full]').value = text; config.mode = 'full'; dirty = true; templates(); selectTab('templates'); feedback('模板已加入草稿，点击“保存提示词”持久保存。');
      } else if (button.matches('[data-prompt-reset]')) {
        if (!confirm('恢复通用解析配置？个人模板保留，点击保存后生效。')) return;
        config = { ...config, mode: 'quick', template_id: 'general', focus: ['responsibilities', 'requirements', 'salary', 'location'], style: 'original', additional_rules: '', full_text: '' }; dirty = true; render();
      } else if (button.matches('[data-prompt-save]')) {
        const data = await api('/job-extraction-prompt', jsonOptions('PUT', read())); if (cleaned) return;
        config = data.config; dirty = false; render(); feedback('提示词与个人模板已保存，下一次网页解析生效。');
      }
      if (config && !cleaned) selectTab(tab);
    } catch (error) { feedback(error.message || '操作失败，未保存内容仍保留。', true); }
    finally { busy = false; if (!cleaned) { q('[data-prompt-fields]').disabled = !config; q('[data-prompt-save]').disabled = !dirty; onChange(); } }
  }
  function input(event) {
    if (!config) return;
    dirty = true;
    if (event.target.matches('[data-prompt-template]')) {
      config = read(); templates();
      q('[data-prompt-name]').value = config.templates.find((item) => item.id === config.template_id)?.name || '';
    }
    selectTab(tab);
  }
  function beforeUnload(event) { if (dirty || busy) { event.preventDefault(); event.returnValue = ''; } }
  host.addEventListener('click', action); host.addEventListener('input', input); window.addEventListener('beforeunload', beforeUnload);
  load().catch((error) => feedback(`提示词加载失败：${error.message}`, true)).finally(() => { if (!cleaned) q('[data-prompt-fields]').disabled = !config; });
  return { get dirty() { return dirty; }, get busy() { return busy; }, canLeave: () => !busy && (!dirty || confirm('离开会丢弃未保存的提示词修改，继续？')), cleanup() { cleaned = true; host.removeEventListener('click', action); host.removeEventListener('input', input); window.removeEventListener('beforeunload', beforeUnload); } };
}
