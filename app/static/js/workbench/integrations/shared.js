export const esc = (value) => String(value ?? '').replace(/[&<>"']/g, (char) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[char]));
export const text = (value) => value === null || value === undefined ? '' : (['string', 'number', 'boolean'].includes(typeof value) ? String(value) : '');
export const first = (...values) => values.find((value) => value !== undefined && value !== null && value !== '');
export const arr = (value) => Array.isArray(value) ? value : value ? [value] : [];
export const object = (value) => value && typeof value === 'object' ? value : {};
export const collection = (payload, keys = []) => Array.isArray(payload) ? payload : keys.map((key) => payload?.[key]).find(Array.isArray) || [];
export const fmtTime = (value) => {
  if (!value) return '—';
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? text(value) : date.toLocaleString('zh-CN');
};
export const positiveId = (value) => {
  const id = Number(value);
  return Number.isInteger(id) && id > 0 ? id : null;
};
export const labelFor = (map, value, fallback = '—') => map[text(value)] || text(value) || fallback;
export const setFeedback = (root, selector, message, kind = 'info') => {
  const target = root.querySelector(selector);
  if (!target) return;
  target.textContent = message || '';
  target.className = `wb-feedback${kind === 'error' ? ' is-error' : kind === 'success' ? ' is-success' : ''}`;
};
export const buttonBusy = (button, busy, label) => {
  if (!button) return;
  if (busy) {
    button.dataset.wbOriginal = button.textContent;
    button.disabled = true;
    if (label) button.textContent = label;
  } else {
    button.disabled = false;
    if (button.dataset.wbOriginal) button.textContent = button.dataset.wbOriginal;
    delete button.dataset.wbOriginal;
  }
};
export const jsonOptions = (method, payload) => ({ method, body: JSON.stringify(payload) });
export const selectOptions = (items, value, label, selected = '') => items.map((item) => {
  const v = value(item);
  return `<option value="${esc(v)}"${String(v) === String(selected) ? ' selected' : ''}>${esc(label(item))}</option>`;
}).join('');
export const htmlRows = (rows) => rows.filter(([, value]) => value !== null && value !== undefined && value !== '').map(([label, value]) => `<div><dt>${esc(label)}</dt><dd>${esc(value)}</dd></div>`).join('');
