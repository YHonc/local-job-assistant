(() => {
  const workspace = document.querySelector('.detail-workspace');
  if (!workspace) return;

  const jobId = Number(workspace.dataset.jobId);
  let statuses = [];
  const typeChoices = ['实习', '日常实习', '校园招聘', '社会招聘', '兼职', '全职'];
  const $ = (selector) => document.querySelector(selector);
  let job;
  let application;

  function returnToTracker() {
    const previous = document.referrer ? new URL(document.referrer) : null;
    if (previous?.origin === location.origin && ['/', '/legacy/tracker', '/workbench'].includes(previous.pathname) && history.length > 1) {
      history.back();
    } else {
      window.location.assign('/legacy/tracker');
    }
  }

  document.querySelector('.back-link').addEventListener('click', (event) => {
    if (event.button !== 0 || event.ctrlKey || event.metaKey || event.shiftKey || event.altKey) return;
    event.preventDefault();
    returnToTracker();
  });

  window.addEventListener('keydown', (event) => {
    if (event.key !== 'Escape' || event.defaultPrevented || event.isComposing || event.repeat) return;
    if (document.querySelector('dialog[open]')) return;
    event.preventDefault();
    returnToTracker();
  });

  async function api(path, options = {}) {
    const response = await fetch(`/api${path}`, {
      headers: { 'Content-Type': 'application/json' },
      ...options,
    });
    if (!response.ok) throw new Error(`请求失败（${response.status}）`);
    return response.status === 204 ? null : response.json();
  }

  function setText(selector, value, fallback = '待补') {
    $(selector).textContent = value || fallback;
  }

  function formatDate(value, withTime = false) {
    if (!value) return '待补';
    const date = new Date(value);
    if (Number.isNaN(date.getTime())) return value;
    return date.toLocaleString('zh-CN', withTime
      ? { year: 'numeric', month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit' }
      : { year: 'numeric', month: '2-digit', day: '2-digit' });
  }

  function safeUrl(value) {
    try {
      const url = new URL(value);
      return ['http:', 'https:'].includes(url.protocol) ? url.href : '';
    } catch {
      return '';
    }
  }

  function fillSelect(select, values, current) {
    const choices = current && !values.includes(current) ? [current, ...values] : values;
    select.replaceChildren(...choices.map((value) => {
      const option = document.createElement('option');
      option.value = value;
      option.textContent = value;
      option.selected = value === current;
      return option;
    }));
  }

  function renderProgress(status) {
    const core = statuses.filter((value) => value !== '拒绝');
    const current = core.indexOf(status);
    const list = $('#detailProgress');
    list.replaceChildren(...core.map((label, index) => {
      const item = document.createElement('li');
      item.className = index === current ? 'current' : '';
      const marker = document.createElement('span');
      marker.textContent = String(index + 1);
      const text = document.createElement('strong');
      text.textContent = label;
      item.append(marker, text);
      return item;
    }));
    if (['拒绝', '放弃'].includes(status)) {
      const item = document.createElement('li');
      item.className = 'closed current';
      const marker = document.createElement('span');
      marker.textContent = '×';
      const text = document.createElement('strong');
      text.textContent = status;
      item.append(marker, text);
      list.append(item);
    }
  }

  function render(company) {
    const status = application?.status || '待投递';
    setText('#detailCompany', `${company?.name || '公司待补'} / ${job.job_category || '其他'}`);
    setText('#detailTitle', job.title);
    setText('#detailSummary', job.original_title || job.description?.split('\n')[0], '岗位资料');
    setText('#detailLocation', job.location);
    setText('#detailCategory', job.job_category, '其他');
    setText('#detailSourceName', job.source_website);
    setText('#detailPublished', formatDate(job.published_at));
    setText('#detailAdded', formatDate(job.created_at, true));
    setText('#detailDescription', job.description, '暂无岗位描述');
    setText('#detailNotes', application?.notes, '暂无个人记录');
    fillSelect($('#detailType'), typeChoices, job.employment_type || '待补');
    fillSelect($('#detailStatus'), statuses, status);
    renderProgress(status);
    const source = safeUrl(job.source_url);
    if (source) {
      $('#detailSource').href = source;
      $('#detailSource').hidden = false;
    }
    $('#detailEdit').href = `/legacy/tracker?edit=${job.id}`;
    $('#detailFeedback').hidden = true;
    $('#jobDetail').hidden = false;
  }

  async function load() {
    try {
      const [jobResult, metadata] = await Promise.all([api(`/workbench/jobs/${jobId}`), api('/workbench/statuses')]);
      job = jobResult;
      application = job.application;
      statuses = metadata.values;
      render(job.company);
      await recordClick('detail');
    } catch (error) {
      $('#detailFeedback').textContent = `岗位资料加载失败：${error.message}`;
      $('#detailFeedback').classList.add('error');
    }
  }

  $('#detailType').addEventListener('change', async (event) => {
    const previous = job.employment_type || '待补';
    event.target.disabled = true;
    try {
      job = await api(`/jobs/${job.id}`, {
        method: 'PATCH',
        body: JSON.stringify({ employment_type: event.target.value === '待补' ? null : event.target.value }),
      });
    } catch (error) {
      event.target.value = previous;
      $('#detailFeedback').textContent = `招聘类型更新失败：${error.message}`;
      $('#detailFeedback').hidden = false;
      $('#detailFeedback').classList.add('error');
    } finally {
      event.target.disabled = false;
    }
  });

  async function recordClick(action) {
    if (!job) return;
    try {
      const result = await api('/workbench/jobs/status-action', { method: 'POST', body: JSON.stringify({ job_id: job.id, expected_application_version: application?.version ?? null, action }) });
      application = result.items[0].application;
      fillSelect($('#detailStatus'), statuses, application?.status || '待投递');
      renderProgress(application?.status || '待投递');
    } catch (error) { $('#detailFeedback').textContent = `自动记录未保存：${error.message}`; $('#detailFeedback').hidden = false; }
  }
  $('#detailSource').addEventListener('click', () => { void recordClick('apply'); });

  $('#detailStatus').addEventListener('change', async (event) => {
    const previous = application?.status || '待投递';
    event.target.disabled = true;
    try {
      const result = await api('/workbench/jobs/status', { method: 'POST', body: JSON.stringify({ items: [{ job_id: job.id, expected_application_version: application?.version ?? null }], status: event.target.value }) });
      application = result.items[0].application;
      renderProgress(application.status);
    } catch (error) {
      event.target.value = previous;
      $('#detailFeedback').textContent = `状态更新失败：${error.message}`;
      $('#detailFeedback').hidden = false;
      $('#detailFeedback').classList.add('error');
    } finally {
      event.target.disabled = false;
    }
  });

  load();
})();
