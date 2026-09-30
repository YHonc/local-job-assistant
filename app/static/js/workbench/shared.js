export function escapeHtml(value) {
  return String(value ?? '').replace(/[&<>"']/g, (character) => ({
    '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;',
  })[character]);
}

export function safeHttpUrl(value) {
  try {
    const url = new URL(String(value));
    return ['http:', 'https:'].includes(url.protocol) ? url.href : '';
  } catch {
    return '';
  }
}

export function routeWith(route, changes = {}) {
  const params = new URLSearchParams(route.params);
  for (const [key, value] of Object.entries(changes)) {
    params.delete(key);
    if (Array.isArray(value)) value.forEach((item) => item !== '' && params.append(key, String(item)));
    else if (value !== undefined && value !== null && value !== '') params.set(key, String(value));
  }
  return params;
}

export function pageHeading(title, description, actions = '') {
  return `<header class="wb-page-heading"><div><h1>${escapeHtml(title)}</h1><p class="wb-page-subtitle">${escapeHtml(description)}</p></div>${actions ? `<div class="wb-heading-actions">${actions}</div>` : ''}</header>`;
}

export function entryCard(title, description, href, action = '打开现有页面') {
  return `<a class="wb-card wb-entry-card" href="${escapeHtml(href)}"><strong>${escapeHtml(title)}</strong><p>${escapeHtml(description)}</p><span class="wb-inline-link">${escapeHtml(action)} ↗</span></a>`;
}

export function stateMessage(title, message, { kind = 'info', link = '', linkLabel = '' } = {}) {
  return `<section class="wb-state-message" data-kind="${escapeHtml(kind)}" role="status"><strong>${escapeHtml(title)}</strong><p>${escapeHtml(message)}</p>${link ? `<a class="wb-inline-link" href="${escapeHtml(link)}">${escapeHtml(linkLabel || '打开现有页面')} ↗</a>` : ''}</section>`;
}

export function categoriesFromConfig() {
  const node = document.getElementById('workbench-config');
  try { return JSON.parse(node?.textContent || '{}').jobCategories || []; }
  catch { return []; }
}

export function workbenchConfig() {
  try { return JSON.parse(document.getElementById('workbench-config')?.textContent || '{}'); }
  catch { return {}; }
}

export function statusValues() { return workbenchConfig().statuses || []; }
export function normalizeStatus(value) { return workbenchConfig().statusAliases?.[value] || value || '待投递'; }
export function statusOptions(value) {
  const selected = normalizeStatus(value);
  const values = statusValues();
  const legacy = values.includes(selected) ? '' : `<option value="${escapeHtml(selected)}" selected>历史：${escapeHtml(selected)}（请核对）</option>`;
  return legacy + values.map((status) => `<option value="${escapeHtml(status)}" ${selected === status ? 'selected' : ''}>${escapeHtml(status)}</option>`).join('');
}
