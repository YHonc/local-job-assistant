(() => {
  const app = window.JobAssistant || {};
  const sharedDom = app.dom || {};
  const byId = (id) => document.getElementById(id);
  if (!byId('emailAccountForm')) return;

  const eventTypeLabels = {
    APPLICATION_RECEIVED: '收到回复',
    ASSESSMENT: '测评',
    WRITTEN_TEST: '笔试',
    INTERVIEW: '面试',
    INTERVIEW_CHANGED: '面试变更',
    OFFER: 'Offer',
    REJECTED: '拒绝',
    RECRUITMENT_MARKETING: '招聘营销',
    OTHER: '其他',
  };
  const eventLabel = (value) => eventTypeLabels[value] || value;

  const esc = sharedDom.esc || ((value) => String(value ?? '').replace(/[&<>"']/g, (char) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[char])));
  const accountTestResults = new Map();
  let accountsCache = [];

  function detailText(detail, fallback) {
    if (typeof detail === 'string') return detail;
    if (Array.isArray(detail)) return detail.map((item) => item.msg || '输入无效').join('；');
    if (detail && typeof detail === 'object') return detail.message || fallback;
    return fallback;
  }

  async function request(path, options = {}) {
    if (typeof app.api === 'function') return app.api(path, options);
    const response = await fetch(`/api${path}`, {
      headers: { 'Content-Type': 'application/json' },
      ...options,
    });
    const data = response.status === 204 ? null : await response.json().catch(() => ({}));
    if (!response.ok) throw new Error(detailText(data?.detail, `请求失败（${response.status}）`));
    return data;
  }

  function setFeedback(id, text, error = false) {
    const element = byId(id);
    if (!element) return;
    element.textContent = text;
    element.className = `feedback${error ? ' error' : ''}`;
  }

  // 需求 5：测试连接 / 立即同步点击后转圈并禁用，防止重复触发。
  function setButtonLoading(button, loading, loadingText) {
    if (loading) {
      button.dataset.label = button.textContent;
      button.disabled = true;
      button.classList.add('is-loading');
      button.innerHTML = `<span class="spinner" aria-hidden="true"></span>${esc(loadingText)}`;
    } else {
      button.disabled = false;
      button.classList.remove('is-loading');
      button.textContent = button.dataset.label || button.textContent;
    }
  }

  function formatTime(value) {
    return value ? new Date(value).toLocaleString('zh-CN') : '尚未同步';
  }

  const syncStatusLabels = { never: '尚未同步', running: '同步中', success: '成功', partial: '部分成功', error: '失败' };
  const syncStatusTag = (account) => {
    const label = syncStatusLabels[account.last_sync_status] || account.last_sync_status;
    const kind = account.last_sync_status === 'success' ? ' ok' : (account.last_sync_status === 'error' ? ' danger' : (account.last_sync_status === 'partial' ? ' warn' : ''));
    return `<span class="tag${kind}">${esc(label)}${account.last_sync_error ? ` · ${esc(account.last_sync_error)}` : ''}</span>`;
  };

  function renderAccounts(accounts) {
    accountsCache = accounts;
    const grid = byId('emailCardGrid');
    const newCard = `<div class="new-card" id="newEmailCard" role="button" tabindex="0" aria-label="添加邮箱账户"><span class="new-copy"><span class="plus" aria-hidden="true">＋</span>添加邮箱账户<small>QQ / 163 / IMAP</small></span></div>`;
    if (!accounts.length) {
      grid.innerHTML = `<div class="new-card wide" id="newEmailCard" role="button" tabindex="0" aria-label="添加邮箱账户"><span class="new-copy"><span class="plus" aria-hidden="true">＋</span>添加第一个邮箱账户<small>先测试连接，再手动同步</small></span></div>`;
      return;
    }
    grid.innerHTML = newCard + accounts.map((account) => {
      const result = accountTestResults.get(account.id);
      return `<article class="stack-card${account.enabled ? '' : ' is-paused'}" data-account-card="${account.id}">
        <div class="card-title-row"><strong>${esc(account.email_address)}</strong><span class="tag" style="margin-left:auto">${esc(account.provider_type.toUpperCase())}${account.enabled ? '' : ' · 已停用'}</span></div>
        <div class="card-body"><span>每 ${account.check_interval_minutes} 分钟增量同步${account.sync_lookback_days ? ` · 回看 ${account.sync_lookback_days} 天` : ''} · 上次 ${esc(formatTime(account.last_sync_at))}</span>${syncStatusTag(account)}</div>
        <div class="card-actions">
          <button class="text-button" data-email-action="test" data-account-id="${account.id}" type="button">测试连接</button>
          <button class="text-button" data-email-action="sync" data-account-id="${account.id}" type="button">立即同步</button>
          <button class="text-button" data-email-action="edit" data-account-id="${account.id}" type="button">编辑</button>
          <button class="text-button" data-email-action="toggle" data-account-id="${account.id}" data-enabled="${account.enabled}" type="button">${account.enabled ? '停用' : '启用'}</button>
          <button class="text-button danger-text" data-email-action="delete" data-account-id="${account.id}" type="button">删除</button>
        </div>
        ${result ? `<p class="test-result ${result.ok ? 'ok' : 'fail'}">${esc(result.message)}</p>` : ''}
      </article>`;
    }).join('');
  }

  async function loadAccounts() {
    try {
      renderAccounts(await request('/email-accounts'));
      const select = byId('manualEmailAccount');
      select.innerHTML = accountsCache.map((account) => `<option value="${account.id}">${esc(account.email_address)}</option>`).join('');
    } catch (error) {
      setFeedback('emailAccountFeedback', `读取账户失败：${error.message}`, true);
    }
  }

  function openAccountDialog(account) {
    const dialog = byId('emailAccountDialog');
    byId('emailAccountId').value = account?.id || '';
    byId('emailProvider').value = account?.provider_type || 'qq';
    byId('emailAddress').value = account?.email_address || '';
    byId('emailAddress').readOnly = Boolean(account);
    byId('emailInterval').value = account?.check_interval_minutes ?? 15;
    byId('emailLookback').value = account?.sync_lookback_days ?? '';
    byId('emailAuthorizationCode').value = '';
    byId('emailAuthorizationCode').required = !account;
    byId('emailAuthorizationCode').placeholder = account?.has_authorization_code ? '已保存；留空保持不变' : 'IMAP 授权码';
    const custom = byId('emailProvider').value === 'custom';
    byId('customImapFields').hidden = !custom;
    byId('imapHost').value = account?.imap_host || '';
    byId('imapPort').value = account?.imap_port ?? 993;
    byId('imapUsername').value = account?.username || '';
    byId('imapSsl').checked = account?.use_ssl ?? true;
    byId('emailAccountDialogTitle').textContent = account ? '编辑邮箱账户' : '添加邮箱账户';
    byId('emailAccountFeedback').textContent = '';
    toggleCustomFields();
    dialog.showModal();
  }

  function toggleCustomFields() {
    const custom = byId('emailProvider').value === 'custom';
    byId('customImapFields').hidden = !custom;
    byId('imapHost').required = custom;
    byId('imapPort').required = custom;
  }

  byId('emailProvider').addEventListener('change', toggleCustomFields);

  byId('emailCardGrid').addEventListener('click', (event) => {
    if (event.target.closest('#newEmailCard')) openAccountDialog(null);
  });

  byId('emailAccountForm').addEventListener('submit', async (event) => {
    event.preventDefault();
    const id = byId('emailAccountId').value;
    const provider = byId('emailProvider').value;
    const payload = {
      email_address: byId('emailAddress').value.trim(),
      provider_type: provider,
      check_interval_minutes: Number(byId('emailInterval').value),
    };
    const lookback = byId('emailLookback').value.trim();
    if (lookback) payload.sync_lookback_days = Number(lookback);
    else if (id) payload.sync_lookback_days = null;
    if (provider === 'custom') {
      payload.imap_host = byId('imapHost').value.trim();
      payload.imap_port = Number(byId('imapPort').value);
      payload.use_ssl = byId('imapSsl').checked;
      payload.username = byId('imapUsername').value.trim() || null;
    }
    const authorizationCode = byId('emailAuthorizationCode').value;
    if (authorizationCode) payload.authorization_code = authorizationCode;
    try {
      setFeedback('emailAccountFeedback', id ? '正在保存…' : '正在添加…');
      await request(id ? `/email-accounts/${id}` : '/email-accounts', {
        method: id ? 'PATCH' : 'POST',
        body: JSON.stringify(payload),
      });
      byId('emailAccountDialog').close();
      setFeedback('emailAccountFeedback', id ? '邮箱账户已更新；授权码不会回显。' : '邮箱账户已添加；授权码不会回显。');
      await loadAccounts();
    } catch (error) {
      setFeedback('emailAccountFeedback', id ? `更新失败：${error.message}` : `添加失败：${error.message}`, true);
    }
  });

  byId('emailCardGrid').addEventListener('click', async (event) => {
    const button = event.target.closest('[data-email-action]');
    if (!button) return;
    const id = button.dataset.accountId;
    const action = button.dataset.emailAction;
    if (action === 'edit') {
      openAccountDialog(accountsCache.find((account) => account.id === Number(id)));
      return;
    }
    if (action === 'delete' && !confirm('删除这个邮箱账户及其本地邮件缓存？招聘事件会保留。')) return;
    const usesLoadingState = action === 'test' || action === 'sync';
    if (usesLoadingState) setButtonLoading(button, true, action === 'test' ? '正在连接…' : '同步中…');
    else button.disabled = true;
    try {
      if (action === 'test') {
        await request(`/email-accounts/${id}/test-connection`, { method: 'POST' });
        accountTestResults.set(Number(id), { ok: true, message: '连接成功 · IMAP 登录与收件箱读取正常' });
      } else if (action === 'sync') {
        const result = await request(`/email-accounts/${id}/sync`, { method: 'POST' });
        accountTestResults.set(Number(id), {
          ok: result.processing_errors === 0,
          message: `同步完成：新增 ${result.created} 封，生成 ${result.events_created} 个事件`
            + `${result.skipped_by_regex ? `，${result.skipped_by_regex} 封未命中正则仅存摘要` : ''}`
            + `${result.retry_paused ? `，${result.retry_paused} 封已暂停自动重试` : ''}`
            + `${result.processing_errors ? `，${result.processing_errors} 个待重试` : ''}`,
        });
      } else if (action === 'toggle') {
        await request(`/email-accounts/${id}`, {
          method: 'PATCH',
          body: JSON.stringify({ enabled: button.dataset.enabled !== 'true' }),
        });
      } else if (action === 'delete') {
        await request(`/email-accounts/${id}`, { method: 'DELETE' });
        accountTestResults.delete(Number(id));
      }
      await loadAccounts();
    } catch (error) {
      if (action === 'test') accountTestResults.set(Number(id), { ok: false, message: `测试失败：${error.message}` });
      setFeedback('emailAccountFeedback', `操作失败：${error.message}`, true);
      if (usesLoadingState) setButtonLoading(button, false);
      else button.disabled = false;
      renderAccounts(accountsCache);
    }
  });

  // ---- 预筛规则：正则 + 关键词 ----

  async function loadFilter() {
    try {
      const config = await request('/email-filter');
      byId('emailKeywords').value = Array.isArray(config?.keywords) ? config.keywords.join('\n') : '';
      byId('emailRegex').value = config?.regex || config?.pattern || '';
    } catch (error) {
      setFeedback('emailFilterFeedback', `读取失败：${error.message}`, true);
    }
  }

  byId('validateRegex').addEventListener('click', () => {
    const value = byId('emailRegex').value.trim();
    if (!value) {
      setFeedback('emailFilterFeedback', '正则为空：将不启用正则预筛。');
      return;
    }
    try {
      new RegExp(value, 'i');
      setFeedback('emailFilterFeedback', '正则语法有效。');
    } catch {
      setFeedback('emailFilterFeedback', '正则语法无效，请修改后再保存。', true);
    }
  });

  byId('previewEmailRegex')?.addEventListener('click', async () => {
    const regex = byId('emailRegex').value.trim();
    const sample = byId('emailRegexPreviewText').value.trim();
    if (!sample) {
      setFeedback('emailRegexPreviewResult', '请先输入一段发件人、主题或正文文本。', true);
      return;
    }
    if (!regex) {
      setFeedback('emailRegexPreviewResult', '当前未启用正则筛选；这段文本会通过正则阶段。');
      return;
    }
    try {
      const result = await request('/email-filter/preview', {
        method: 'POST',
        body: JSON.stringify({
          sender: '',
          subject: '',
          body_text: sample,
          regex,
        }),
      });
      const matched = result?.matched ?? result?.regex_matched ?? result?.passed;
      if (matched === undefined) {
        setFeedback('emailRegexPreviewResult', '预览已返回，但服务端未提供命中结果。');
      } else {
        setFeedback('emailRegexPreviewResult', matched ? '命中：这段文本会进入 AI 清洗。' : '未命中：这段文本会停留在摘要，不进入 AI 清洗。');
      }
    } catch (error) {
      // 预览接口需要把文本作为 POST body 发送，失败时仍给出本地语法提示。
      setFeedback('emailRegexPreviewResult', `预览失败：${error.message}`, true);
    }
  });

  byId('saveEmailFilter').addEventListener('click', async () => {
    const keywords = byId('emailKeywords').value.split(/[\n,，]/).map((item) => item.trim()).filter(Boolean);
    const regex = byId('emailRegex').value.trim() || null;
    if (regex) {
      try {
        new RegExp(regex, 'i');
      } catch {
        setFeedback('emailFilterFeedback', '正则语法无效，未保存。', true);
        return;
      }
    }
    try {
      const payload = { regex };
      if (keywords.length) payload.keywords = keywords;
      let saved;
      try {
        saved = await request('/email-filter', { method: 'PUT', body: JSON.stringify(payload) });
      } catch (error) {
        // Old installations require a non-empty keywords field. Retry only for
        // an empty compatibility field; the new server ignores it for matching.
        if (keywords.length) throw error;
        saved = await request('/email-filter', { method: 'PUT', body: JSON.stringify({ keywords: ['招聘'], regex }) });
      }
      byId('emailKeywords').value = Array.isArray(saved?.keywords) ? saved.keywords.join('\n') : keywords.join('\n');
      byId('emailRegex').value = saved?.regex || saved?.pattern || '';
      setFeedback('emailFilterFeedback', '预筛规则已保存。');
    } catch (error) {
      setFeedback('emailFilterFeedback', `保存失败：${error.message}`, true);
    }
  });

  // ---- 手动添加指定邮件 ----

  byId('openManualEmail').addEventListener('click', () => {
    if (!accountsCache.length) {
      setFeedback('emailAccountFeedback', '请先添加一个邮箱账户。', true);
      return;
    }
    byId('manualEmailForm').reset();
    byId('manualEmailAccount').value = accountsCache[0].id;
    byId('manualEmailFeedback').textContent = '';
    byId('manualEmailDialog').showModal();
  });

  byId('manualEmailForm').addEventListener('submit', async (event) => {
    event.preventDefault();
    try {
      await request('/email-messages/manual', {
        method: 'POST',
        body: JSON.stringify({
          account_id: Number(byId('manualEmailAccount').value),
          sender: byId('manualEmailSender').value.trim(),
          subject: byId('manualEmailSubject').value.trim(),
          body_text: byId('manualEmailBody').value.trim() || null,
          message_id: byId('manualEmailMessageId').value.trim() || null,
        }),
      });
      byId('manualEmailDialog').close();
      setFeedback('emailAccountFeedback', '邮件已加入识别队列，将在下一次同步或手动处理时分类。');
    } catch (error) {
      setFeedback('manualEmailFeedback', `添加失败：${error.message}`, true);
    }
  });

  // ---- 通知策略：多策略卡片 ----

  let policiesCache = [];

  function policyCardHtml(policy) {
    const channels = [policy.desktop_enabled ? '桌面通知' : null, policy.push_enabled ? `Push（${policy.push_provider || 'ntfy'}）` : null].filter(Boolean).join(' + ') || '未启用渠道';
    const typeTags = policy.important_event_types.map((value) => `<span class="tag">${esc(eventLabel(value))}</span>`).join('');
    return `<article class="stack-card${policy.enabled ? '' : ' is-paused'}" data-policy-card="${policy.id}">
      <div class="card-title-row"><strong>${esc(policy.name)}</strong><span class="tag${policy.enabled ? ' ok' : ''}" style="margin-left:auto">${policy.enabled ? '启用中' : '已停用'}</span></div>
      <div class="card-body"><span>渠道：${esc(channels)}</span><span class="event-legend">${typeTags}</span>${policy.notify_actionable_deadlines ? '<span>有明确截止时间且需要行动时提醒</span>' : ''}</div>
      <div class="card-actions">
        <button class="text-button" data-policy-edit="${policy.id}" type="button">编辑</button>
        <button class="text-button" data-policy-test="${policy.id}" type="button">测试发送</button>
        <button class="text-button danger-text" data-policy-delete="${policy.id}" type="button">删除</button>
      </div>
    </article>`;
  }

  async function loadPolicies() {
    try {
      const policies = await request('/notification-policies');
      policiesCache = policies;
      const newCard = `<div class="new-card" id="newPolicyCard" role="button" tabindex="0" aria-label="新建通知策略"><span class="new-copy"><span class="plus" aria-hidden="true">＋</span>新建通知策略<small>渠道与提醒范围</small></span></div>`;
      byId('policyCardGrid').innerHTML = newCard + policies.map(policyCardHtml).join('');
      const badge = byId('policyBadge');
      if (badge) badge.textContent = `${policies.length} 条策略`;
    } catch (error) {
      setFeedback('notificationPolicyFeedback', `读取失败：${error.message}`, true);
    }
  }

  function openPolicyDialog(policy) {
    const dialog = byId('policyDialog');
    byId('policyId').value = policy?.id || '';
    byId('policyName').value = policy?.name || '';
    byId('desktopEnabled').checked = policy ? policy.desktop_enabled : true;
    byId('pushEnabled').checked = policy ? policy.push_enabled : false;
    byId('pushProvider').value = policy?.push_provider || 'ntfy';
    byId('pushUrl').value = '';
    byId('pushUrl').placeholder = policy?.has_push_url ? '已安全保存；留空保持不变' : 'ntfys://… 或 bark://…';
    byId('clearPushUrl').checked = false;
    byId('clearPushUrl').disabled = policy ? !policy.has_push_url : true;
    byId('notifyDeadline').checked = policy ? policy.notify_actionable_deadlines : true;
    byId('policyEnabled').checked = policy ? policy.enabled : true;
    document.querySelectorAll('[name="importantEvent"]').forEach((control) => {
      control.checked = policy ? policy.important_event_types.includes(control.value) : ['ASSESSMENT', 'WRITTEN_TEST', 'INTERVIEW', 'INTERVIEW_CHANGED', 'OFFER'].includes(control.value);
    });
    byId('policyDialogTitle').textContent = policy ? '编辑通知策略' : '新建通知策略';
    byId('notificationPolicyFeedback').textContent = '';
    dialog.showModal();
  }

  byId('policyCardGrid').addEventListener('click', (event) => {
    if (event.target.closest('#newPolicyCard')) openPolicyDialog(null);
  });

  byId('pushUrl').addEventListener('input', (event) => {
    if (event.target.value) byId('clearPushUrl').checked = false;
  });
  byId('clearPushUrl').addEventListener('change', (event) => {
    if (event.target.checked) byId('pushUrl').value = '';
  });

  byId('notificationPolicyForm').addEventListener('submit', async (event) => {
    event.preventDefault();
    const important = [...document.querySelectorAll('[name="importantEvent"]:checked')].map((item) => item.value);
    if (!important.length) {
      setFeedback('notificationPolicyFeedback', '请至少选择一种即时提醒事件。', true);
      return;
    }
    const target = byId('pushUrl').value.trim();
    const payload = {
      name: byId('policyName').value.trim(),
      enabled: byId('policyEnabled').checked,
      desktop_enabled: byId('desktopEnabled').checked,
      push_enabled: byId('pushEnabled').checked,
      push_provider: byId('pushProvider').value,
      clear_push_url: byId('clearPushUrl').checked,
      important_event_types: important,
      notify_actionable_deadlines: byId('notifyDeadline').checked,
    };
    if (target) payload.push_url = target;
    const id = byId('policyId').value;
    try {
      setFeedback('notificationPolicyFeedback', '正在保存…');
      await request(id ? `/notification-policies/${id}` : '/notification-policies', {
        method: id ? 'PUT' : 'POST',
        body: JSON.stringify(payload),
      });
      byId('policyDialog').close();
      await loadPolicies();
      setFeedback('notificationPolicyFeedback', '通知策略已保存；Push URL 不会回显。');
    } catch (error) {
      setFeedback('notificationPolicyFeedback', `保存失败：${error.message}`, true);
    }
  });

  byId('policyCardGrid').addEventListener('click', async (event) => {
    const editTarget = event.target.closest('[data-policy-edit]');
    if (editTarget) {
      openPolicyDialog(policiesCache.find((policy) => policy.id === Number(editTarget.dataset.policyEdit)));
      return;
    }
    const deleteTarget = event.target.closest('[data-policy-delete]');
    if (deleteTarget) {
      if (!confirm('删除这条通知策略？已发送的通知历史会保留。')) return;
      try {
        await request(`/notification-policies/${deleteTarget.dataset.policyDelete}`, { method: 'DELETE' });
        await loadPolicies();
        setFeedback('notificationPolicyFeedback', '通知策略已删除。');
      } catch (error) {
        setFeedback('notificationPolicyFeedback', `删除失败：${error.message}`, true);
      }
      return;
    }
    const testTarget = event.target.closest('[data-policy-test]');
    if (testTarget) {
      const policy = policiesCache.find((item) => item.id === Number(testTarget.dataset.policyTest));
      const provider = policy?.desktop_enabled ? 'desktop' : 'push';
      setButtonLoading(testTarget, true, '发送中…');
      try {
        const result = await request(`/notification-policies/${testTarget.dataset.policyTest}/test`, {
          method: 'POST',
          body: JSON.stringify({ provider }),
        });
        setFeedback('notificationPolicyFeedback', result.status === 'sent' ? '测试通知已发送。' : `测试失败：${result.error || result.status}`, result.status !== 'sent');
      } catch (error) {
        setFeedback('notificationPolicyFeedback', `测试失败：${error.message}`, true);
      } finally {
        setButtonLoading(testTarget, false);
      }
    }
  });

  toggleCustomFields();
  loadAccounts();
  loadFilter();
  loadPolicies();
})();
