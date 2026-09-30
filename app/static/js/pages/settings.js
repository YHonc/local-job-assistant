(() => {
  if (!document.querySelector('#settingsForm')) return;
  const app = window.JobAssistant;
  const { api, dom: { $, esc }, allowedPreferences, applySettings, applyPoetry } = app;
  function setupSettingsAccordion() {
    const rows = [...document.querySelectorAll('.setting-row')];
    rows.forEach((row) => {
      row.addEventListener('toggle', () => {
        if (!row.open) return;
        rows.forEach((other) => {
          if (other !== row) other.open = false;
        });
      });
    });
  }
  const llmTestResults = new Map();
  let llmConfigsCache = [];

  // API 格式 = 调用协议，封闭点选；厂商差异由 API Base 地址承担。
  const PROTOCOLS = {
    openai: { label: 'Chat Completions', path: '/chat/completions', note: 'OpenAI 官方及所有兼容服务（DeepSeek、千问、GLM、Kimi、SenseNova、OpenRouter、Ollama 等）', url: 'https://api.openai.com/v1' },
    anthropic: { label: 'Anthropic Messages', path: '/v1/messages', note: 'Anthropic Claude 官方协议', url: 'https://api.anthropic.com' },
    gemini: { label: 'Gemini', path: 'generateContent', note: 'Google Gemini 原生协议', url: 'https://generativelanguage.googleapis.com/v1beta' },
  };
  const LABEL_TO_PROVIDER = Object.fromEntries(Object.entries(PROTOCOLS).map(([value, info]) => [info.label, value]));
  const EXAMPLE_URLS = new Set(Object.values(PROTOCOLS).map((info) => info.url));

  let fetchedModels = [];

  function providerValue() {
    const raw = $('#llmProvider').value.trim();
    return LABEL_TO_PROVIDER[raw] || raw;
  }

  function setProviderField(provider, autoFill = true) {
    const input = $('#llmProvider');
    if (!input) return;
    const info = PROTOCOLS[provider];
    input.value = info ? info.label : provider;
    input.readOnly = !!info;
    input.placeholder = info ? '' : '输入 LiteLLM 前缀，如 sensenova、azure、vllm';
    updateProviderExample(autoFill);
  }

  function enterProviderCustomMode() {
    const input = $('#llmProvider');
    if (!input) return;
    const current = providerValue();
    input.readOnly = false;
    input.value = PROTOCOLS[current] ? '' : current;
    input.placeholder = '输入 LiteLLM 前缀，如 sensenova、azure、vllm';
    updateProviderExample(false);
    input.focus();
  }

  function updateProviderExample(autoFill) {
    const example = $('#providerExample');
    const apiBase = $('#llmApiBase');
    if (!example || !apiBase) return;
    const info = PROTOCOLS[providerValue()];
    if (info) {
      example.textContent = `${info.label}（${info.path}）· ${info.note} · 示例地址：${info.url}`;
      apiBase.placeholder = info.url;
      const replaceable = !apiBase.value || EXAMPLE_URLS.has(apiBase.value) || apiBase.value === info.url;
      if (autoFill && replaceable) apiBase.value = info.url;
    } else {
      example.textContent = '自定义格式：填写服务方 API Base；OpenAI 兼容服务也可以直接改选 Chat Completions。';
      apiBase.placeholder = 'https://api.example.com/v1';
    }
  }

  function closeAllCombos() {
    document.querySelectorAll('.combo-menu.open').forEach((menu) => menu.classList.remove('open'));
  }

  function renderProviderMenu() {
    const menu = $('#providerComboMenu');
    if (!menu) return;
    const current = providerValue();
    const options = Object.entries(PROTOCOLS).map(([value, info]) => {
      const selected = value === current;
      return `<div class="combo-option${selected ? ' selected' : ''}" role="option" aria-selected="${selected}" tabindex="0" data-combo-value="${esc(value)}" data-combo-target="#llmProvider">${selected ? '<span class="combo-check" aria-hidden="true">✓</span>' : ''}<b>${esc(info.label)}</b><small>${esc(info.path)} · 示例：${esc(info.url)}</small></div>`;
    }).join('');
    menu.innerHTML = `<div class="combo-group-title">调用协议</div>${options}<div class="combo-separator" role="separator"></div><div class="combo-option combo-custom-option" role="option" tabindex="0" data-combo-custom="1" data-combo-target="#llmProvider"><b>自定义（高级）</b><small>手动输入 LiteLLM provider 前缀</small></div>`;
  }

  function renderModelMenu(query) {
    const menu = $('#modelComboMenu');
    if (!menu) return;
    const q = (query || '').trim().toLowerCase();
    const items = fetchedModels.filter((id) => !q || id.toLowerCase().includes(q));
    menu.innerHTML = items.length
      ? '<div class="combo-group-title">从服务获取的模型</div>' + items.map((id) => (
        `<div class="combo-option" role="option" tabindex="0" data-combo-value="${esc(id)}" data-combo-target="#llmModel"><b>${esc(id)}</b></div>`
      )).join('')
      : '<div class="combo-empty">点击右侧「获取模型」从服务拉取模型列表</div>';
  }

  function showLlmError(message) {
    const output = $('#llmConfigFeedback');
    if (output) {
      output.textContent = message;
      output.className = 'feedback error';
    }
  }

  function llmTestResultHtml(configId) {
    const result = llmTestResults.get(configId);
    return result ? `<p class="test-result ${result.ok ? 'ok' : 'fail'}">${esc(result.message)}</p>` : '';
  }

  function formatTokens(value) {
    const count = Number(value) || 0;
    return count >= 1000 ? `${(count / 1000).toFixed(1)}k` : String(count);
  }

  function llmCardHtml(config) {
    const totalTokens = (config.total_prompt_tokens || 0) + (config.total_completion_tokens || 0);
    return `<article class="stack-card${config.enabled ? '' : ' is-paused'}">
      <div class="card-title-row"><strong>${esc(config.display_name)}</strong><button class="tag tag-toggle${config.enabled ? ' ok' : ''}" type="button" data-llm-toggle="${config.id}" title="${config.enabled ? '点击暂停此模型' : '点击启用此模型'}" aria-label="${config.enabled ? '已启用，点击暂停' : '已暂停，点击启用'}">${config.enabled ? '已启用' : '已暂停'}</button></div>
      <div class="card-body"><span>${esc(PROTOCOLS[config.provider]?.label || config.provider)} / ${esc(config.model)}</span><span>${esc(config.api_base || '默认 API Base')}${config.has_api_key ? ' · 密钥已保存' : ''}</span></div>
      <p class="card-stats">调用 ${config.call_count} · 成功 ${config.success_count} · 失败 ${config.failure_count} · 平均 ${Math.round(config.average_duration_ms || 0)} ms · Token ${formatTokens(totalTokens)}</p>
      <div class="card-actions"><button class="text-button" data-llm-edit="${config.id}" type="button">编辑</button><button class="text-button" data-llm-toggle="${config.id}" type="button">${config.enabled ? '暂停' : '启用'}</button><button class="text-button" data-llm-test="${config.id}" type="button">测试</button><button class="text-button" data-llm-usage="${config.id}" type="button">用量</button><button class="text-button danger-text" data-llm-delete="${config.id}" type="button">删除</button></div>
      ${llmTestResultHtml(config.id)}
    </article>`;
  }

  function openUsageDialog(config) {
    window.LLMUsage.open(config);
  }

  function renderLLMCards(configs) {
    const grid = $('#llmCardGrid');
    if (!grid) return;
    grid.innerHTML = configs.length || llmTestResults.size
      ? `<div class="new-card" id="newLLMCard" role="button" tabindex="0" aria-label="新建模型配置"><span class="new-copy"><span class="plus" aria-hidden="true">＋</span>新建模型配置<small>在弹窗中填写</small></span></div>${configs.map(llmCardHtml).join('')}`
      : `<div class="new-card wide" id="newLLMCard" role="button" tabindex="0" aria-label="新建模型配置"><span class="new-copy"><span class="plus" aria-hidden="true">＋</span>新建第一个模型配置<small>密钥只写入操作系统凭据库</small></span></div>`;
  }

  function openLLMDialog(config) {
    const dialog = $('#llmConfigDialog');
    if (!dialog) return;
    $('#llmConfigDialogTitle').textContent = config ? '编辑模型配置' : '新建模型配置';
    $('#llmConfigId').value = config?.id || '';
    $('#llmDisplayName').value = config?.display_name || '';
    setProviderField(config?.provider || 'openai', false);
    $('#llmModel').value = config?.model || '';
    $('#llmApiBase').value = config?.api_base || '';
    $('#llmTemperature').value = config?.temperature ?? 0.2;
    $('#llmTimeout').value = config?.timeout_seconds ?? 60;
    $('#llmEnabled').checked = config ? config.enabled : true;
    $('#llmApiKey').value = '';
    $('#llmApiKey').placeholder = config?.has_api_key ? '密钥已保存；留空保持不变' : '本地模型可留空';
    fetchedModels = [];
    renderModelMenu();
    closeAllCombos();
    updateProviderExample(false);
    $('#llmDialogFeedback').textContent = '';
    dialog.showModal();
    $('#llmDisplayName').focus();
  }

  async function loadLLMConfig() {
    const grid = $('#llmCardGrid');
    if (!grid) return;
    try {
      const configs = await api('/llm/configs');
      llmConfigsCache = configs;
      renderLLMCards(configs);
      const badge = $('#llmConfigBadge');
      if (badge) {
        const enabledCount = configs.filter((config) => config.enabled).length;
        badge.textContent = configs.length ? `${configs.length} 个配置 · ${enabledCount} 个启用` : '未配置';
      }
      const routing = await api('/llm/routing');
      $('#llmAutoSelect').checked = routing.auto_select_enabled;
      $('#llmMaxParallel').value = routing.max_parallel_calls;
      const enabledConfigs = configs.filter((config) => config.enabled);
      $('#llmDefaultConfig').innerHTML = '<option value="">不指定</option>' + enabledConfigs.map((config) => `<option value="${config.id}">${esc(config.display_name)}</option>`).join('');
      if (routing.default_config_id && enabledConfigs.some((config) => config.id === routing.default_config_id)) {
        $('#llmDefaultConfig').value = routing.default_config_id;
      }
      $('#llmEmailModel').innerHTML = '<option value="">跟随全局路由</option>' + enabledConfigs.map((config) => `<option value="${config.id}">${esc(config.display_name)}</option>`).join('');
      if (routing.email_config_id && enabledConfigs.some((config) => config.id === routing.email_config_id)) {
        $('#llmEmailModel').value = routing.email_config_id;
      }
    } catch (error) {
      const output = $('#llmConfigFeedback');
      if (output) {
        output.textContent = `读取失败：${error.message}`;
        output.className = 'feedback error';
      }
    }
  }

  document.addEventListener('click', async (event) => {
    const target = event.target.closest('[data-status-filter]') || event.target;
    // 自定义下拉：选项选择与点击外部关闭
    const comboOption = target.closest('[data-combo-value], [data-combo-custom]');
    if (comboOption) {
      if (comboOption.dataset.comboCustom) {
        enterProviderCustomMode();
      } else {
        const input = document.querySelector(comboOption.dataset.comboTarget || '');
        if (input) {
          if (comboOption.dataset.comboTarget === '#llmProvider') setProviderField(comboOption.dataset.comboValue);
          else input.value = comboOption.dataset.comboValue;
        }
      }
      closeAllCombos();
      return;
    }
    if (!target.closest('.combo')) closeAllCombos();
    if (target.closest('#newLLMCard')) {
      openLLMDialog(null);
    }
    if (target.dataset.llmEdit) {
      try {
        const config = await api(`/llm/configs/${target.dataset.llmEdit}`);
        openLLMDialog(config);
      } catch (error) {
        showLlmError(`编辑模型失败：${error.message}`);
      }
    }
    if (target.dataset.llmUsage) {
      const config = llmConfigsCache.find((item) => String(item.id) === target.dataset.llmUsage);
      if (config) openUsageDialog(config);
    }
    if (target.dataset.llmToggle) {
      try {
        const config = await api(`/llm/configs/${target.dataset.llmToggle}`);
        await api(`/llm/configs/${config.id}`, { method: 'PUT', body: JSON.stringify({ enabled: !config.enabled }) });
        await loadLLMConfig();
      } catch (error) {
        showLlmError(`切换模型失败：${error.message}`);
      }
    }
    if (target.dataset.llmDelete && confirm('确认删除此模型配置？')) {
      try {
        await api(`/llm/configs/${target.dataset.llmDelete}`, { method: 'DELETE' });
        llmTestResults.delete(Number(target.dataset.llmDelete));
        await loadLLMConfig();
      } catch (error) {
        showLlmError(`删除模型失败：${error.message}`);
      }
    }
    if (target.dataset.llmTest) {
      const configId = Number(target.dataset.llmTest);
      target.disabled = true;
      const originalText = target.textContent;
      target.textContent = '测试中…';
      try {
        const result = await api(`/llm/configs/${configId}/test`, { method: 'POST' });
        llmTestResults.set(configId, {
          ok: true,
          message: `连接成功：${result.provider} / ${result.model} · ${result.elapsed_ms} ms`,
        });
      } catch (error) {
        llmTestResults.set(configId, { ok: false, message: `测试失败：${error.message}` });
      } finally {
        target.disabled = false;
        target.textContent = originalText;
        renderLLMCards(llmConfigsCache);
      }
    }
  });
  $('#settingsForm')?.addEventListener('submit', async (event) => {
    event.preventDefault();
    const preferences = {
      theme: $('#theme').value,
      density: $('#density').value,
      poetry_preset: $('#poetryPreset').value,
    };
    if (!allowedPreferences.theme.has(preferences.theme) || !allowedPreferences.density.has(preferences.density) || !allowedPreferences.poetry_preset.has(preferences.poetry_preset)) {
      $('#settingsFeedback').textContent = '保存失败：偏好值无效';
      return;
    }
    try {
      await api('/settings/preferences', { method: 'PUT', body: JSON.stringify(preferences) });
      window.preferences = preferences;
      applySettings();
      applyPoetry();
      $('#settingsFeedback').textContent = '偏好已保存';
    } catch (error) {
      $('#settingsFeedback').textContent = `保存失败：${error.message}`;
    }
  });
  $('#llmProvider')?.addEventListener('focus', () => {
    renderProviderMenu();
    closeAllCombos();
    $('#providerComboMenu')?.classList.add('open');
  });
  $('#llmProvider')?.addEventListener('pointerdown', () => {
    if ($('#llmProvider').readOnly) $('#providerComboMenu')?.classList.toggle('open');
  });
  $('#llmProvider')?.addEventListener('input', () => {
    updateProviderExample(false);
  });
  $('#llmModel')?.addEventListener('focus', () => {
    renderModelMenu($('#llmModel').value);
    closeAllCombos();
    $('#modelComboMenu')?.classList.add('open');
  });
  $('#llmModel')?.addEventListener('input', () => {
    renderModelMenu($('#llmModel').value);
    $('#modelComboMenu')?.classList.add('open');
  });
  // combo 打开时 Esc 只关闭下拉，不关闭弹窗
  document.addEventListener('keydown', (event) => {
    if (event.key !== 'Escape') return;
    const openMenu = document.querySelector('.combo-menu.open');
    if (openMenu) {
      event.preventDefault();
      event.stopPropagation();
      openMenu.classList.remove('open');
    }
  }, true);
  $('#fetchModels')?.addEventListener('click', async () => {
    const button = $('#fetchModels');
    const output = $('#llmDialogFeedback');
    button.disabled = true;
    button.innerHTML = '<span class="spinner" aria-hidden="true"></span>获取中…';
    try {
      const payload = {
        provider: providerValue() || null,
        api_base: $('#llmApiBase').value.trim(),
        timeout_seconds: Math.min(30, Math.max(3, Number($('#llmTimeout').value) || 15)),
      };
      const key = $('#llmApiKey').value.trim();
      if (key) payload.api_key = key;
      const configId = $('#llmConfigId').value;
      if (!key && configId) payload.config_id = Number(configId);
      const result = await api('/llm/models-probe', { method: 'POST', body: JSON.stringify(payload) });
      fetchedModels = result.models || [];
      renderModelMenu($('#llmModel').value);
      closeAllCombos();
      $('#modelComboMenu')?.classList.add('open');
      output.textContent = fetchedModels.length ? `已获取 ${fetchedModels.length} 个模型，请选择。` : '服务没有返回模型列表。';
      output.className = 'feedback';
    } catch (error) {
      output.textContent = `获取模型失败：${error.message}`;
      output.className = 'feedback error';
    } finally {
      button.disabled = false;
      button.textContent = '获取模型';
    }
  });
  $('#cancelLLMConfig')?.addEventListener('click', () => {
    $('#llmConfigDialog')?.close();
  });
  $('#llmConfigForm')?.addEventListener('submit', async (event) => {
    event.preventDefault();
    const output = $('#llmDialogFeedback');
    const apiKey = $('#llmApiKey').value.trim();
    const payload = {
      display_name: $('#llmDisplayName').value.trim(),
      provider: providerValue(),
      model: $('#llmModel').value.trim(),
      api_base: $('#llmApiBase').value.trim() || null,
      temperature: Number($('#llmTemperature').value),
      timeout_seconds: Number($('#llmTimeout').value),
      enabled: $('#llmEnabled').checked,
    };
    if (apiKey) payload.api_key = apiKey;
    try {
      output.textContent = '正在保存…';
      const id = $('#llmConfigId').value;
      await api(id ? `/llm/configs/${id}` : '/llm/configs', { method: id ? 'PUT' : 'POST', body: JSON.stringify(payload) });
      $('#llmConfigDialog')?.close();
      await loadLLMConfig();
    } catch (error) {
      output.textContent = `保存失败：${error.message}`;
      output.className = 'feedback error';
    }
  });
  $('#saveLLMRouting')?.addEventListener('click', async () => {
    try {
      await api('/llm/routing', {
        method: 'PUT',
        body: JSON.stringify({
          auto_select_enabled: $('#llmAutoSelect').checked,
          default_config_id: $('#llmDefaultConfig').value ? Number($('#llmDefaultConfig').value) : null,
          email_config_id: $('#llmEmailModel').value ? Number($('#llmEmailModel').value) : null,
          max_parallel_calls: Number($('#llmMaxParallel').value),
        }),
      });
      $('#llmConfigFeedback').textContent = '路由已保存';
    } catch (error) {
      $('#llmConfigFeedback').textContent = `保存失败：${error.message}`;
    }
  });
  setupSettingsAccordion();
  app.ready.then(loadLLMConfig);
})();
