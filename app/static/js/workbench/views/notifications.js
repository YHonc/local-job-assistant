import { buttonBusy, collection, esc, fmtTime, setFeedback } from '../integrations/shared.js';

function markup() {
  return `<section class="wb-notifications-subview" aria-label="通知历史"><div class="wb-section-heading"><div><h2>通知历史</h2><p class="wb-muted">按渠道查看发送回执；失败通知可重试，已发送记录遵守幂等保护。</p></div><button class="wb-button" type="button" data-notification-refresh>刷新</button></div><div class="wb-toolbar"><label>发送状态<select data-notification-filter="status"><option value="">全部状态</option><option value="sent">已发送</option><option value="failed">失败</option><option value="pending">等待发送</option><option value="sending">发送中</option></select></label><label>渠道<select data-notification-filter="provider"><option value="">全部渠道</option><option value="desktop">桌面</option><option value="push">Push</option></select></label></div><p class="wb-feedback" data-notification-feedback role="status" aria-live="polite"></p><div class="wb-notification-list" data-notification-list aria-live="polite"></div></section>`;
}

export function mount({ root, api, notify = () => {}, autoLoad = true }) {
  root.innerHTML = markup();
  const q = (selector) => root.querySelector(selector);
  const state = { seq: 0, controller: null, cleaned: false };
  const feedback = (message, kind = 'info') => { if (!state.cleaned) setFeedback(root, '[data-notification-feedback]', message, kind); };
  function render(items) {
    if (state.cleaned) return;
    q('[data-notification-list]').innerHTML = items.length ? items.map((item) => `<article class="wb-notification-row" data-notification-id="${esc(item.id)}"><div><div class="wb-notification-title"><span class="wb-chip status-${esc(item.status)}">${esc(item.status || '未知')}</span><strong>${esc(item.title || '招聘提醒')}</strong></div><p>${esc(item.content || '')}</p><small>${esc(item.provider || '未知渠道')} · 尝试 ${esc(item.attempt_count ?? 0)} 次 · ${esc(fmtTime(item.sent_at || item.updated_at))}</small>${item.error ? `<p class="wb-error-copy">${esc(item.error)}</p>` : ''}</div>${item.status !== 'sent' ? `<button class="wb-button" type="button" data-notification-retry="${esc(item.id)}">重试</button>` : ''}</article>`).join('') : '<div class="wb-empty"><strong>没有通知记录</strong><p>重要招聘事件的桌面或 Push 回执会保留在这里。</p></div>';
  }
  async function load() {
    const seq = ++state.seq;
    state.controller?.abort();
    const controller = new AbortController(); state.controller = controller;
    const params = new URLSearchParams();
    const status = q('[data-notification-filter="status"]').value; const provider = q('[data-notification-filter="provider"]').value;
    if (status) params.set('status', status); if (provider) params.set('provider', provider);
    feedback('正在读取通知历史…');
    try {
      const payload = await api(`/notifications${params.size ? `?${params}` : ''}`, { signal: controller.signal });
      if (state.cleaned || controller.signal.aborted || seq !== state.seq) return;
      const items = collection(payload, ['items', 'notifications']); render(items); feedback(`读取到 ${items.length} 条通知记录。`, 'success');
    } catch (error) {
      if (!state.cleaned && !controller.signal.aborted && error.name !== 'AbortError' && seq === state.seq) feedback(`读取失败：${error.message}`, 'error');
    }
  }
  async function retry(button) {
    buttonBusy(button, true, '重试中…');
    try {
      const result = await api(`/notifications/${encodeURIComponent(button.dataset.notificationRetry)}/retry`, { method: 'POST' });
      if (state.cleaned) return;
      const message = result?.status === 'sent' ? '通知重试发送成功。' : `重试完成：${result?.status || '已处理'}`;
      feedback(message, result?.status === 'sent' ? 'success' : 'info'); notify(message, result?.status === 'sent' ? 'success' : 'info'); await load();
    } catch (error) { if (!state.cleaned) feedback(`重试失败：${error.message}`, 'error'); }
    finally { if (!state.cleaned) buttonBusy(button, false); }
  }
  const onClick = (event) => {
    if (event.target.closest('[data-notification-refresh]')) { load(); return; }
    const button = event.target.closest('[data-notification-retry]'); if (button) retry(button);
  };
  const onChange = (event) => { if (event.target.matches('[data-notification-filter]')) load(); };
  root.addEventListener('click', onClick); root.addEventListener('change', onChange);
  if (autoLoad) load();
  return { load, cleanup() { state.cleaned = true; state.seq += 1; state.controller?.abort(); root.removeEventListener('click', onClick); root.removeEventListener('change', onChange); } };
}
