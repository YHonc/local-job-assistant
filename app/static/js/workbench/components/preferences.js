const DEFAULT_PREFERENCES = Object.freeze({
  theme_mode: 'light',
  seed_color: '#3659b8',
  density: 'comfortable',
  reduce_motion: false,
  visible_job_fields: ['category', 'tags', 'application', 'organization', 'location', 'salary'],
  dismissed_page_hints: { pending: false, follow: false, mail: false },
  poetry_preset: 'random',
});

const FIELD_LABELS = Object.freeze({
  category: '职位类别', tags: '标签', application: '投递状态',
  organization: '公司', location: '工作地点', salary: '薪资',
});
const POETRY = Object.freeze({
  shuidiao: '明月几时有？把酒问青天。',
  dielianhua: '昨夜西风凋碧树，独上高楼，望尽天涯路。',
  qingpingle: '若有知音见采，不辞遍唱阳春。',
  yulinling: '今宵酒醒何处？杨柳岸晓风残月。',
  jiangchengzi: '会挽雕弓如满月，西北望，射天狼。',
  nian: '大江东去，浪淘尽，千古风流人物。',
});

function normalizedPreferences(value = {}) {
  const merged = { ...DEFAULT_PREFERENCES, ...value };
  merged.dismissed_page_hints = {
    ...DEFAULT_PREFERENCES.dismissed_page_hints,
    ...(value.dismissed_page_hints || {}),
  };
  merged.visible_job_fields = Array.isArray(value.visible_job_fields)
    ? [...new Set(value.visible_job_fields.filter((field) => Object.hasOwn(FIELD_LABELS, field)))]
    : [...DEFAULT_PREFERENCES.visible_job_fields];
  if (!['light', 'dark', 'system'].includes(merged.theme_mode)) merged.theme_mode = 'light';
  if (!['comfortable', 'compact'].includes(merged.density)) merged.density = 'comfortable';
  if (!/^#[0-9a-f]{6}$/i.test(merged.seed_color)) merged.seed_color = DEFAULT_PREFERENCES.seed_color;
  if (!Object.hasOwn(POETRY, merged.poetry_preset) && merged.poetry_preset !== 'random') merged.poetry_preset = 'random';
  merged.reduce_motion = Boolean(merged.reduce_motion);
  return merged;
}

function randomPoetryKey() {
  const keys = Object.keys(POETRY);
  return keys[Math.floor(Math.random() * keys.length)];
}

function checkboxList(selected) {
  return Object.entries(FIELD_LABELS).map(([value, label]) => `<label class="wb-check-field"><input type="checkbox" name="visible_job_fields" value="${value}" ${selected.includes(value) ? 'checked' : ''}><span>${label}</span></label>`).join('');
}

export function createPreferencesManager({ root, api, notify = () => {} } = {}) {
  if (!root || typeof api !== 'function') throw new TypeError('root and api are required');
  let preferences = normalizedPreferences();
  let disposed = false;
  let returnFocus = null;
  let resolvedPoetryKey = randomPoetryKey();

  const dialog = document.createElement('dialog');
  dialog.className = 'wb-dialog wb-preferences-dialog';
  dialog.setAttribute('aria-labelledby', 'wbPreferencesTitle');
  dialog.innerHTML = `<form class="wb-preferences-form" data-preferences-form>
    <header class="wb-dialog-heading"><div><p class="wb-eyebrow">工作台偏好</p><h2 id="wbPreferencesTitle">显示与阅读</h2><p class="wb-dialog-subtitle">偏好保存在本地工作台设置中；岗位正文和来源资料始终保留。</p></div><button class="wb-button wb-button-quiet" type="button" data-preferences-close aria-label="关闭显示偏好">关闭</button></header>
    <div class="wb-editor-section"><h3>外观</h3><div class="wb-editor-grid">
      <label class="wb-field">主题<select name="theme_mode"><option value="system">跟随系统</option><option value="light">浅色</option><option value="dark">深色</option></select></label>
      <label class="wb-field">主题种子色<input name="seed_color" type="color" value="#3659b8"></label>
      <label class="wb-field">密度<select name="density"><option value="comfortable">舒适</option><option value="compact">紧凑</option></select></label>
      <label class="wb-check-field"><input type="checkbox" name="reduce_motion"><span>减少动画</span></label>
    </div></div>
    <div class="wb-editor-section"><h3>岗位卡片字段</h3><p class="wb-help-text">隐藏字段只影响列表卡片，岗位详情仍可查看完整资料。</p><div class="wb-visible-fields" data-visible-fields></div></div>
    <div class="wb-editor-section"><h3>诗词展示</h3><label class="wb-field">首页诗词<select name="poetry_preset"><option value="random">随机宋词</option><option value="shuidiao">水调歌头</option><option value="dielianhua">蝶恋花</option><option value="qingpingle">清平乐</option><option value="yulinling">雨霖铃</option><option value="jiangchengzi">江城子</option><option value="nian">念奴娇</option></select></label><p class="wb-help-text">诗句显示在岗位库标题下；此偏好不改变岗位内容。</p></div>
    <p class="wb-dialog-status" data-preferences-status role="status" aria-live="polite"></p>
    <footer class="wb-dialog-footer"><span></span><div class="wb-dialog-actions"><button class="wb-button wb-button-quiet" type="button" data-preferences-cancel>取消</button><button class="wb-button wb-button-primary" type="submit">保存偏好</button></div></footer>
  </form>`;
  root.append(dialog);

  const form = dialog.querySelector('[data-preferences-form]');
  const status = dialog.querySelector('[data-preferences-status]');
  const statusMessage = (message, kind = 'info') => {
    status.textContent = message;
    status.dataset.kind = kind;
  };

  function activeTheme() {
    return preferences.theme_mode === 'system'
      ? (window.matchMedia?.('(prefers-color-scheme: dark)').matches ? 'dark' : 'light')
      : preferences.theme_mode;
  }

  function apply() {
    if (disposed) return;
    root.dataset.theme = activeTheme();
    root.dataset.themeMode = preferences.theme_mode;
    root.dataset.density = preferences.density;
    root.dataset.reduceMotion = preferences.reduce_motion ? 'true' : 'false';
    root.style.setProperty('--wb-seed-color', preferences.seed_color);
    const poem = POETRY[preferences.poetry_preset === 'random' ? resolvedPoetryKey : preferences.poetry_preset];
    root.querySelectorAll('[data-workbench-poetry]').forEach((node) => {
      node.textContent = poem || POETRY.dielianhua;
    });
    root.querySelectorAll('[data-job-field]').forEach((node) => {
      node.hidden = !preferences.visible_job_fields.includes(node.dataset.jobField);
    });
  }

  function fillForm() {
    form.elements.theme_mode.value = preferences.theme_mode;
    form.elements.seed_color.value = preferences.seed_color;
    form.elements.density.value = preferences.density;
    form.elements.reduce_motion.checked = preferences.reduce_motion;
    form.elements.poetry_preset.value = preferences.poetry_preset;
    dialog.querySelector('[data-visible-fields]').innerHTML = checkboxList(preferences.visible_job_fields);
  }

  async function load() {
    try {
      preferences = normalizedPreferences(await api('/workbench/preferences'));
      resolvedPoetryKey = preferences.poetry_preset === 'random' ? randomPoetryKey() : preferences.poetry_preset;
      apply();
      return preferences;
    } catch (error) {
      apply();
      notify(`显示偏好暂不可用，当前使用默认外观：${error?.message || '读取失败'}`, 'error');
      return preferences;
    }
  }

  async function savePatch(patch, { quiet = false } = {}) {
    try {
      const oldPoetry = preferences.poetry_preset;
      preferences = normalizedPreferences(await api('/workbench/preferences', {
        method: 'PUT', body: JSON.stringify(patch),
      }));
      if (preferences.poetry_preset !== oldPoetry) {
        resolvedPoetryKey = preferences.poetry_preset === 'random' ? randomPoetryKey() : preferences.poetry_preset;
      }
      apply();
      if (!quiet) notify('显示偏好已保存。', 'success');
      return true;
    } catch (error) {
      if (!quiet) statusMessage(`保存失败，当前偏好没有更改：${error?.message || '请求未成功。'}`, 'error');
      return false;
    }
  }

  function open() {
    returnFocus = document.activeElement;
    fillForm();
    statusMessage('外观偏好会影响工作台卡片和颜色；岗位完整资料仍保留。');
    dialog.showModal();
    form.elements.theme_mode.focus();
  }

  function close() {
    if (dialog.open) dialog.close();
  }

  async function setHintDismissed(view, dismissed) {
    const succeeded = await savePatch({ dismissed_page_hints: { [view]: dismissed } }, { quiet: true });
    if (succeeded) return true;
    notify('说明状态没有保存，请稍后重试。', 'error');
    return false;
  }

  function bindPageHints(viewRoot, view) {
    if (!viewRoot) return;
    if (!['pending', 'follow', 'mail'].includes(view)) return;
    let hint = viewRoot.querySelector('[data-workbench-page-hint]');
    if (!hint && view === 'mail') {
      hint = document.createElement('aside');
      hint.className = 'wb-callout wb-page-hint';
      hint.dataset.workbenchPageHint = 'mail';
      hint.innerHTML = '<span aria-hidden="true">i</span><div><strong>邮件操作保持分开</strong><p>查看邮件、关联岗位和确认进度建议是独立操作；打开详情不会更改投递状态。</p><button class="wb-button wb-button-quiet wb-hint-dismiss" type="button" data-dismiss-workbench-hint>关闭本页说明</button></div>';
      viewRoot.prepend(hint);
    }
    if (!hint) return;
    hint.dataset.workbenchPageHint = view;
    if (!hint.querySelector('[data-dismiss-workbench-hint]')) {
      const button = document.createElement('button');
      button.className = 'wb-button wb-button-quiet wb-hint-dismiss';
      button.type = 'button';
      button.dataset.dismissWorkbenchHint = 'true';
      button.textContent = '关闭本页说明';
      hint.append(button);
    }
    let restore = viewRoot.querySelector(`[data-restore-workbench-hint="${view}"]`);
    if (!restore) {
      restore = document.createElement('button');
      restore.className = 'wb-hint-restore';
      restore.type = 'button';
      restore.dataset.restoreWorkbenchHint = view;
      restore.textContent = '显示本页说明';
      hint.after(restore);
    }
    const dismissed = Boolean(preferences.dismissed_page_hints?.[view]);
    hint.hidden = dismissed;
    restore.hidden = !dismissed;
  }

  dialog.addEventListener('click', (event) => {
    if (event.target.closest('[data-preferences-close], [data-preferences-cancel]')) close();
  });
  dialog.addEventListener('close', () => {
    if (returnFocus?.isConnected) returnFocus.focus();
    returnFocus = null;
  });
  form.addEventListener('submit', async (event) => {
    event.preventDefault();
    const visible = [...form.querySelectorAll('[name="visible_job_fields"]:checked')].map((input) => input.value);
    const patch = {
      theme_mode: form.elements.theme_mode.value,
      seed_color: form.elements.seed_color.value,
      density: form.elements.density.value,
      reduce_motion: form.elements.reduce_motion.checked,
      visible_job_fields: visible,
      poetry_preset: form.elements.poetry_preset.value,
    };
    const saveButton = form.querySelector('[type="submit"]');
    saveButton.disabled = true;
    statusMessage('正在保存偏好…');
    try {
      const saved = await savePatch(patch);
      if (saved) close();
    } finally {
      saveButton.disabled = false;
    }
  });

  const onSystemThemeChange = () => { if (preferences.theme_mode === 'system') apply(); };
  const media = window.matchMedia?.('(prefers-color-scheme: dark)');
  media?.addEventListener?.('change', onSystemThemeChange);
  const onDocumentClick = async (event) => {
    if (event.target.closest('[data-open-workbench-preferences]')) open();
    const dismiss = event.target.closest('[data-dismiss-workbench-hint]');
    const restore = event.target.closest('[data-restore-workbench-hint]');
    const action = dismiss ? 'dismiss' : restore ? 'restore' : '';
    if (!action) return;
    const view = action === 'dismiss'
      ? dismiss.closest('[data-workbench-page-hint]')?.dataset.workbenchPageHint
      : restore.dataset.restoreWorkbenchHint;
    if (!view) return;
    const button = dismiss || restore;
    button.disabled = true;
    if (await setHintDismissed(view, action === 'dismiss')) {
      const activeRoot = document.querySelector('#workbench-view');
      bindPageHints(activeRoot, view);
    }
    button.disabled = false;
  };
  document.addEventListener('click', onDocumentClick);

  const ready = load();
  return {
    get value() { return preferences; },
    ready,
    load,
    apply,
    save: savePatch,
    open,
    bindPageHints,
    dispose() {
      disposed = true;
      media?.removeEventListener?.('change', onSystemThemeChange);
      document.removeEventListener('click', onDocumentClick);
      dialog.remove();
    },
  };
}
