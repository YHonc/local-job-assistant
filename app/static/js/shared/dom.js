/* Shared DOM helpers. Kept namespaced to avoid colliding with legacy globals. */
window.JobAssistant = window.JobAssistant || {};
window.JobAssistant.dom = window.JobAssistant.dom || {
  $: (selector, root = document) => root.querySelector(selector),
  esc: (value) => String(value ?? '').replace(/[&<>"']/g, (char) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[char])),
};
