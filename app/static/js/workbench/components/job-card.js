import { escapeHtml as esc, safeHttpUrl, normalizeStatus, statusOptions } from '../shared.js';

function addedTime(value) {
  if (!value) return '<span class="wb-job-added">添加时间 · 未记录</span>';
  // SQLite may return a UTC timestamp without an offset.
  const raw = String(value);
  const date = new Date(/(?:Z|[+-]\d{2}:\d{2})$/i.test(raw) ? raw : `${raw}Z`);
  if (Number.isNaN(date.valueOf())) return '<span class="wb-job-added">添加时间 · 未记录</span>';
  const label = new Intl.DateTimeFormat('zh-CN', { year: 'numeric', month: '2-digit', day: '2-digit' }).format(date);
  const full = new Intl.DateTimeFormat('zh-CN', { dateStyle: 'medium', timeStyle: 'short' }).format(date);
  return `<span class="wb-job-added" title="${esc(full)}">添加时间 · <time datetime="${date.toISOString()}">${esc(label)}</time></span>`;
}

export function renderJobCard(job, selected) {
  const company = job.company?.name || '公司资料缺失';
  const status = normalizeStatus(job.application?.status);
  const source = safeHttpUrl(job.source_url);
  const tags = (job.tags || []).filter((tag) => !tag.startsWith('方向:')).slice(0, 3);
  return `<article class="wb-job-card wb-progress-card ${selected ? 'is-selected' : ''}" data-job-id="${Number(job.id)}">
    <label class="wb-job-select" aria-label="选择 ${esc(job.title)}，${esc(company)}"><input type="checkbox" data-select-job="${Number(job.id)}" ${selected ? 'checked' : ''}><span class="wb-sr-label">选择此岗位</span></label>
    <div class="wb-job-main"><span class="wb-company-mark" aria-hidden="true">${esc([...company][0] || '职')}</span><div class="wb-job-copy"><a href="/workbench?view=library&job_id=${Number(job.id)}" data-open-workbench-job="${Number(job.id)}"><strong>${esc(job.title)}</strong></a><span data-job-field="organization">${esc(company)}</span><span data-job-field="location">${esc(job.location || '地点未填写')} · ${esc(job.employment_type || '类型未填写')}</span></div></div>
    <div class="wb-job-meta"><span class="wb-badge" data-job-field="category">${esc(job.job_category)}${job.direction ? ' · ' + esc(job.direction) : ''}</span>${tags.map((tag) => `<span class="wb-badge" data-job-field="tags">${esc(tag)}</span>`).join('')}${job.salary_text ? `<strong class="wb-job-salary" data-job-field="salary">${esc(job.salary_text)}</strong>` : ''}${addedTime(job.created_at)}</div>
    <div class="wb-job-controls"><label class="wb-quick-status" data-status="${esc(status)}"><span class="wb-sr-label">修改 ${esc(job.title)} 的投递状态</span><select data-quick-status="${Number(job.id)}" aria-label="${esc(job.title)} 的投递状态">${statusOptions(status)}</select></label>
    <div class="wb-job-actions"><a class="wb-text-button" href="/workbench?view=library&job_id=${Number(job.id)}" data-open-workbench-job="${Number(job.id)}">查看详情</a>${source ? `<a class="wb-text-button" href="${esc(source)}" target="_blank" rel="noopener noreferrer" data-apply-job="${Number(job.id)}">去投递 ↗</a>` : ''}<button class="wb-text-button" type="button" data-edit-job="${Number(job.id)}">编辑</button></div>
    </div>
  </article>`;
}
