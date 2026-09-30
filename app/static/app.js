(() => {
  const app = window.JobAssistant;
  const { api, dom: { $, esc } } = app;
  const settingKeys = new Set(['theme', 'density', 'poetry_preset']);
  const poetryPresets = {
    shuidiao: '明月几时有？把酒问青天。', dielianhua: '昨夜西风凋碧树，独上高楼，望尽天涯路。',
    qingpingle: '若有知音见采，不辞遍唱阳春。', yulinling: '今宵酒醒何处？杨柳岸晓风残月。',
    jiangchengzi: '会挽雕弓如满月，西北望，射天狼。', nian: '大江东去，浪淘尽，千古风流人物。'
  };
  const defaultPreferences = { theme: 'light', density: 'comfortable', poetry_preset: 'random' };
  const allowedPreferences = {
    theme: new Set(['light', 'dark']),
    density: new Set(['comfortable', 'compact']),
    poetry_preset: new Set(['random', ...Object.keys(poetryPresets)]),
  };
  function setupSidebar() {
    const sidebar = $('.sidebar');
    const toggle = $('[data-sidebar-toggle]');
    if (!sidebar || !toggle) return;
    const applyState = (collapsed) => {
      sidebar.dataset.collapsed = collapsed ? 'true' : 'false';
      toggle.setAttribute('aria-expanded', collapsed ? 'false' : 'true');
      toggle.setAttribute('aria-label', collapsed ? '展开主导航' : '折叠主导航');
    };
    applyState(sidebar.dataset.collapsed !== 'false');
    toggle.addEventListener('click', () => applyState(sidebar.dataset.collapsed !== 'true'));
  }
  async function loadSettings() {
    const preferences = { ...defaultPreferences };
    try {
      const saved = await api('/settings');
      saved.forEach((item) => {
        if (settingKeys.has(item.key) && allowedPreferences[item.key].has(item.value)) {
          preferences[item.key] = item.value;
        }
      });
    } catch (error) {
      const feedbackElement = $('#settingsFeedback');
      if (feedbackElement) feedbackElement.textContent = `读取失败：${error.message}`;
    }
    window.preferences = preferences;
    const controlSelectors = { theme: '#theme', density: '#density', poetry_preset: '#poetryPreset' };
    Object.entries(controlSelectors).forEach(([key, selector]) => {
      const control = $(selector);
      if (control) control.value = preferences[key];
    });
    applySettings();
    applyPoetry();
  }
  function applySettings() {
    const preferences = window.preferences || defaultPreferences;
    document.documentElement.dataset.theme = preferences.theme;
    document.documentElement.dataset.density = preferences.density;
  }

  function applyPoetry() {
    const element = $('#poetrySubhead');
    if (!element) return;
    const key = window.preferences?.poetry_preset || 'random';
    const keys = Object.keys(poetryPresets);
    const chosen = key === 'random' ? keys[Math.floor(Math.random() * keys.length)] : key;
    element.textContent = poetryPresets[chosen] || poetryPresets.dielianhua;
  }
  app.allowedPreferences = allowedPreferences;
  app.applySettings = applySettings;
  app.applyPoetry = applyPoetry;
  document.addEventListener('click', (event) => {
    event.target.closest('[data-close]')?.closest('dialog')?.close();
  });
  setupSidebar();
  app.ready = loadSettings();
})();
