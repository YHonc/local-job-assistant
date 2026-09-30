import { createWorkbenchRouter, WORKBENCH_VIEWS } from './router.js?v=20260929-settings';
import { mount as mountLibrary } from './views/library.js?v=20260927-cutover';
import { mount as mountPending } from './views/pending.js?v=20260927-cutover';
import { mount as mountFollow } from './views/follow.js?v=20260927-cutover';
import { escapeHtml, entryCard } from './shared.js';
import { createPreferencesManager } from './components/preferences.js?v=20260929-settings';

const root = document.querySelector('#workbench-view');
const crumb = document.querySelector('#workbenchCrumb');
const toastRegion = document.querySelector('#workbenchToast');
const registrations = new Map([
  ['library', { mount: mountLibrary }],
  ['pending', { mount: mountPending }],
  ['follow', { mount: mountFollow }],
]);
let activeCleanup = null;
let activeController = null;
let activeRoute = null;
let router;
let renderSequence = 0;
let navigationGuard = null;
const moduleErrors = new Map();

function notify(message, kind = 'info', action = null) {
  const toast = document.createElement('div');
  toast.className = 'wb-toast';
  toast.dataset.kind = ['success', 'error', 'info'].includes(kind) ? kind : 'info';
  toast.setAttribute('role', 'status');
  toast.textContent = String(message ?? '');
  if (action) {
    const button = document.createElement('button');
    button.className = 'wb-text-button'; button.textContent = action.label || '撤销';
    button.addEventListener('click', async () => {
      button.disabled = true;
      try { await action.run(); toast.remove(); }
      catch (error) { notify(error?.message || '操作失败，请刷新后重试', 'error'); button.disabled = false; }
    });
    toast.append(button);
  }
  toastRegion.append(toast);
  window.setTimeout(() => toast.remove(), action ? 12000 : 4200);
}

function fallbackModule(view) {
  const loadError = moduleErrors.get(view);
  if (loadError) {
    return `<section class="wb-page-heading"><div><h1>${escapeHtml(WORKBENCH_VIEWS[view])}</h1></div></section><section class="wb-state-message" data-kind="error" role="alert"><strong>页面暂时无法打开</strong><p>${escapeHtml(loadError.message || '页面资源加载失败。')}</p><button class="wb-button wb-button-outline" type="button" data-retry-view="${escapeHtml(view)}">重试加载</button></section>`;
  }
  if (view === 'mail') {
    return `<section class="wb-page-heading"><div><h1>邮件与提醒</h1><p class="wb-page-subtitle">邮件内容、关联岗位和通知记录使用现有工作流页面。</p></div></section><div class="wb-entry-grid">${entryCard('邮件收件箱与招聘事件', '查看本地已保存邮件、招聘事件和岗位关联；打开详情后再执行处理动作。', '/events', '打开邮件与事件')}${entryCard('通知发送记录', '查看通知记录、发送结果和可用的重试入口。', '/notifications', '打开通知中心')}</div>`;
  }
  return `<section class="wb-page-heading"><div><h1>设置</h1><p class="wb-page-subtitle">外观、模型、邮箱、通知和用量使用现有设置页面。</p></div></section><div class="wb-entry-grid">${entryCard('基础设置', '管理模型配置与路由、邮箱同步、预筛规则、通知策略和外观偏好。', '/settings', '打开基础设置')}</div>`;
}

async function showRoute(route) {
  if (!root) return;
  if (activeRoute?.view === 'settings' && route.view === 'settings' && activeCleanup?.updateRoute) {
    activeRoute = route;
    activeCleanup.updateRoute(route);
    return;
  }
  const sequence = ++renderSequence;
  activeController?.abort();
  activeController = new AbortController();
  activeCleanup?.();
  activeCleanup = null;
  activeRoute = route;
  crumb.textContent = WORKBENCH_VIEWS[route.view];
  document.querySelectorAll('[data-workbench-view]').forEach((link) => {
    const selected = link.dataset.workbenchView === route.view;
    link.classList.toggle('is-active', selected);
    if (selected) link.setAttribute('aria-current', 'page');
    else link.removeAttribute('aria-current');
  });

  root.replaceChildren();
  const module = registrations.get(route.view);
  if (!module) {
    root.innerHTML = fallbackModule(route.view);
    preferencesManager.apply();
    preferencesManager.bindPageHints(root, route.view);
    return;
  }
  try {
    const cleanup = await module.mount({
      root,
      route,
      navigate: router.navigate,
      api: window.JobAssistant?.api,
      notify,
      views: registrations,
      signal: activeController.signal,
      setNavigationGuard: (guard) => { navigationGuard = typeof guard === 'function' ? guard : null; },
      preferences: preferencesManager,
    });
    if (sequence !== renderSequence) {
      if (typeof cleanup === 'function') cleanup();
      return;
    }
    if (typeof cleanup === 'function') activeCleanup = cleanup;
    preferencesManager.apply();
    preferencesManager.bindPageHints(root, route.view);
  } catch (error) {
    if (sequence !== renderSequence || activeController.signal.aborted) return;
    root.innerHTML = `<section class="wb-page-heading"><div><h1>${escapeHtml(WORKBENCH_VIEWS[route.view])}</h1></div></section><section class="wb-state-message" data-kind="error" role="alert"><strong>视图暂时无法打开</strong><p>${escapeHtml(error?.message || '工作台视图加载失败。')}</p></section>`;
  }
}

router = createWorkbenchRouter((route) => { void showRoute(route); }, window, (previous, next) => {
  if (previous.view === 'settings' && next.view === 'settings' && activeCleanup?.updateRoute) return true;
  try { return navigationGuard ? navigationGuard() !== false : true; }
  catch { return false; }
});

function registerView(name, implementation) {
  if (!Object.hasOwn(WORKBENCH_VIEWS, name) || !implementation || typeof implementation.mount !== 'function') {
    throw new TypeError('工作台视图注册参数无效');
  }
  registrations.set(name, implementation);
  if (activeRoute?.view === name) void showRoute(activeRoute);
}

window.JobWorkbench = {
  get route() { return activeRoute; },
  get views() { return registrations; },
  registerView,
  navigate: (...args) => router.navigate(...args),
  notify,
  api: window.JobAssistant?.api,
  components: {},
};

const preferencesManager = createPreferencesManager({
  root: document.querySelector('#workbenchApp'),
  api: window.JobAssistant?.api,
  notify,
});
window.JobWorkbench.preferences = preferencesManager;

const appShell = document.querySelector('#workbenchApp');
const sidebar = document.querySelector('#workbenchSidebar');
const navToggle = document.querySelector('[data-workbench-nav-toggle]');
const mobileNavigation = window.matchMedia('(max-width: 800px)');
function syncMobileNavigation() {
  const open = mobileNavigation.matches && appShell.dataset.navOpen === 'true';
  if (!mobileNavigation.matches) appShell.dataset.navOpen = 'false';
  sidebar.inert = mobileNavigation.matches && !open;
  navToggle.setAttribute('aria-expanded', String(open));
}
function setMobileNavigation(open, returnFocus = false) {
  appShell.dataset.navOpen = mobileNavigation.matches && open ? 'true' : 'false';
  syncMobileNavigation();
  if (open && mobileNavigation.matches) sidebar.querySelector('[data-workbench-nav-close]')?.focus();
  else if (returnFocus) navToggle.focus();
}
mobileNavigation.addEventListener('change', syncMobileNavigation);
syncMobileNavigation();

document.addEventListener('click', (event) => {
  if (event.target.closest?.('[data-workbench-nav-toggle]')) {
    setMobileNavigation(appShell.dataset.navOpen !== 'true');
    return;
  }
  if (event.target.closest?.('[data-workbench-nav-close]')) {
    setMobileNavigation(false, true);
    return;
  }
  if (appShell.dataset.navOpen === 'true' && event.target.closest?.('#workbenchSidebar a')) {
    setMobileNavigation(false);
  }
  const retry = event.target.closest?.('[data-retry-view]');
  if (retry) {
    event.preventDefault();
    const view = retry.dataset.retryView;
    void loadOptionalView(view);
    return;
  }
  const anchor = event.target.closest?.('a[data-workbench-view]');
  if (!anchor || event.defaultPrevented || event.button !== 0 || event.metaKey || event.ctrlKey || event.shiftKey || event.altKey) return;
  const url = new URL(anchor.href, window.location.href);
  if (url.origin !== window.location.origin || url.pathname !== '/workbench') return;
  event.preventDefault();
  const view = anchor.dataset.workbenchView;
  router.navigate({ view, params: url.searchParams });
});

document.addEventListener('keydown', (event) => {
  if (event.key === 'Escape' && appShell.dataset.navOpen === 'true') setMobileNavigation(false, true);
});

window.addEventListener('pagehide', () => {
  activeCleanup?.();
  router.dispose();
}, { once: true });

const OPTIONAL_MODULES = { mail: './views/mail.js?v=20260926-visual4', settings: './views/settings.js?v=20260929-settings' };
async function loadOptionalView(view) {
  const modulePath = OPTIONAL_MODULES[view];
  if (!modulePath) return;
  moduleErrors.delete(view);
  try {
    const module = await import(modulePath);
    const name = module.view || view;
    if (typeof module.mount !== 'function') throw new TypeError('页面没有提供可用入口。');
    registerView(name, module);
  } catch (error) {
    moduleErrors.set(view, error);
    if (activeRoute?.view === view) void showRoute(activeRoute);
  }
}

for (const view of Object.keys(OPTIONAL_MODULES)) void loadOptionalView(view);
void preferencesManager.ready.then(() => showRoute(router.route));
