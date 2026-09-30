(() => {
  const list = document.getElementById('notificationList');
  if (!list) return;
  const esc = (value) => String(value ?? '').replace(/[&<>"']/g, (char) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[char]));
  const formatTime = (value) => value ? new Date(value).toLocaleString('zh-CN') : '—';

  async function request(path, options = {}) {
    const response = await fetch(`/api${path}`, { headers: { 'Content-Type': 'application/json' }, ...options });
    const data = await response.json().catch(() => ({}));
    if (!response.ok) throw new Error(typeof data.detail === 'string' ? data.detail : `请求失败（${response.status}）`);
    return data;
  }

  function feedback(text, error = false) {
    const output = document.getElementById('notificationFeedback');
    output.textContent = text;
    output.className = `feedback${error ? ' error' : ''}`;
  }

  function render(items) {
    if (!items.length) {
      list.innerHTML = '<div class="empty"><strong>还没有通知记录</strong><p>重要招聘事件触发通知后，成功与失败回执都会留在这里。</p></div>';
      return;
    }
    list.innerHTML = items.map((item) => `<article class="notification-row" data-notification-id="${item.id}">
      <div><div class="notification-title"><span class="delivery-dot ${esc(item.status)}"></span><strong>${esc(item.title)}</strong></div><p>${esc(item.content)}</p><small>${esc(item.provider)} · 尝试 ${item.attempt_count} 次 · ${esc(formatTime(item.sent_at || item.updated_at))}</small>${item.error ? `<p class="delivery-error">${esc(item.error)}</p>` : ''}</div>
      <div><span class="tag">${esc(item.status)}</span>${item.status !== 'sent' ? '<button class="text-button" type="button" data-retry>重试</button>' : ''}</div>
    </article>`).join('');
  }

  async function load() {
    const params = new URLSearchParams();
    const status = document.getElementById('notificationStatusFilter').value;
    const provider = document.getElementById('notificationProviderFilter').value;
    if (status) params.set('status', status);
    if (provider) params.set('provider', provider);
    feedback('正在读取通知历史…');
    try {
      const items = await request(`/notifications${params.size ? `?${params}` : ''}`);
      render(items);
      feedback(`共 ${items.length} 条通知记录。`);
    } catch (error) {
      feedback(`读取失败：${error.message}`, true);
    }
  }

  list.addEventListener('click', async (event) => {
    const retry = event.target.closest('[data-retry]');
    if (!retry) return;
    const row = retry.closest('[data-notification-id]');
    retry.disabled = true;
    try {
      const result = await request(`/notifications/${row.dataset.notificationId}/retry`, { method: 'POST' });
      feedback(result.status === 'sent' ? '重试发送成功。' : `重试完成：${result.status}`);
      await load();
    } catch (error) {
      feedback(`重试失败：${error.message}`, true);
      retry.disabled = false;
    }
  });
  document.getElementById('refreshNotifications').addEventListener('click', load);
  document.getElementById('notificationStatusFilter').addEventListener('change', load);
  document.getElementById('notificationProviderFilter').addEventListener('change', load);
  load();
})();
