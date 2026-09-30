export const WORKBENCH_VIEWS = Object.freeze({
  library: '岗位库',
  pending: '待整理',
  follow: '投递跟进',
  mail: '邮件与提醒',
  settings: '设置',
});

const ALLOWED_PARAMS = new Set([
  'job_id', 'draft_id', 'email_id', 'q', 'company_id', 'locations', 'employment_types',
  'job_category', 'category_review_status', 'organize_status', 'application_status',
  'needs_review', 'sort', 'limit', 'offset', 'draft_offset', 'pending_offset', 'tab', 'status',
  'section', 'settings_panel', 'direction', 'legacy_status',
]);
const POSITIVE_ID_PARAMS = new Set(['job_id', 'draft_id', 'email_id', 'company_id']);
const MULTI_PARAMS = new Set(['locations', 'employment_types']);
const MAX_QUERY_LENGTH = 200;

function validPositiveInteger(value) {
  return /^\d+$/.test(value) && Number.isSafeInteger(Number(value)) && Number(value) > 0;
}

function safeParamValue(key, value) {
  const trimmed = value.trim();
  if (POSITIVE_ID_PARAMS.has(key)) return validPositiveInteger(trimmed) ? String(Number(trimmed)) : null;
  if (key === 'q') return trimmed.length <= MAX_QUERY_LENGTH ? trimmed : trimmed.slice(0, MAX_QUERY_LENGTH);
  if (key === 'limit') return /^\d+$/.test(trimmed) ? String(Math.min(100, Math.max(1, Number(trimmed)))) : null;
  if (key === 'offset') return /^\d+$/.test(trimmed) ? String(Math.min(1_000_000, Number(trimmed))) : null;
  if (key === 'draft_offset') return /^\d+$/.test(trimmed) ? String(Math.min(1_000_000, Number(trimmed))) : null;
  if (key === 'pending_offset') return /^\d+$/.test(trimmed) ? String(Math.min(1_000_000, Number(trimmed))) : null;
  if (key === 'needs_review') return ['true', 'false'].includes(trimmed) ? trimmed : null;
  if (key === 'legacy_status') return trimmed === 'true' ? trimmed : null;
  if (key === 'section') return ['appearance', 'delivery', 'models', 'mailbox', 'processing', 'policies', 'usage'].includes(trimmed) ? trimmed : null;
  if (key === 'settings_panel') return ['list', 'routing', 'health', 'proxy', 'web', 'email'].includes(trimmed) ? trimmed : null;
  if (key === 'tab') return ['inbox', 'notifications', 'events'].includes(trimmed) ? trimmed : null;
  if (key === 'status') return ['draft', 'extracted', 'confirmed'].includes(trimmed) ? trimmed : null;
  if (key === 'organize_status') return ['active', 'pending', 'organized', 'archived', 'all'].includes(trimmed) ? trimmed : null;
  if (key === 'category_review_status') return ['pending', 'confirmed'].includes(trimmed) ? trimmed : null;
  if (key === 'sort') return /^[a-z_]{1,40}$/.test(trimmed) ? trimmed : null;
  return trimmed.length <= MAX_QUERY_LENGTH ? trimmed : trimmed.slice(0, MAX_QUERY_LENGTH);
}

export function sanitizeParams(input) {
  const source = input instanceof URLSearchParams ? input : new URLSearchParams(input || '');
  const result = new URLSearchParams();
  for (const [key, rawValue] of source.entries()) {
    if (!ALLOWED_PARAMS.has(key)) continue;
    if (!MULTI_PARAMS.has(key) && result.has(key)) continue;
    const value = safeParamValue(key, rawValue);
    if (value === null || value === '') continue;
    result.append(key, value);
  }
  return result;
}

export function parseWorkbenchRoute(locationLike) {
  const href = locationLike?.href || String(locationLike || '/workbench');
  const url = new URL(href, 'http://workbench.local');
  const requestedView = url.searchParams.get('view');
  const view = Object.hasOwn(WORKBENCH_VIEWS, requestedView) ? requestedView : 'library';
  return {
    view,
    params: sanitizeParams(url.searchParams),
    href: `${url.pathname}${url.search}`,
  };
}

export function createWorkbenchRouter(onChange, browser = window, canNavigate = () => true) {
  let route = parseWorkbenchRoute(browser.location);

  const notify = (previous = route) => {
    const nextRoute = parseWorkbenchRoute(browser.location);
    if (!canNavigate(previous, nextRoute)) {
      browser.history.pushState({ workbench: true, restore: true }, '', previous.href);
      return route;
    }
    route = nextRoute;
    onChange(route);
  };

  const navigate = ({ view = route.view, params = route.params, replace = false, refresh = true } = {}) => {
    const targetView = Object.hasOwn(WORKBENCH_VIEWS, view) ? view : 'library';
    const query = sanitizeParams(params);
    query.set('view', targetView);
    const nextUrl = `/workbench?${query.toString()}`;
    if (nextUrl === `${browser.location.pathname}${browser.location.search}`) return route;
    const nextRoute = parseWorkbenchRoute({ href: nextUrl });
    if (!canNavigate(route, nextRoute)) return route;
    if (replace) browser.history.replaceState({ workbench: true }, '', nextUrl);
    else browser.history.pushState({ workbench: true }, '', nextUrl);
    if (refresh) {
      route = nextRoute;
      onChange(route);
    } else route = nextRoute;
    return route;
  };

  const onPopState = () => notify(route);
  browser.addEventListener('popstate', onPopState);
  return {
    get route() { return route; },
    navigate,
    dispose() { browser.removeEventListener('popstate', onPopState); },
  };
}
