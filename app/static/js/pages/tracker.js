(() => {
  if (!document.querySelector('#jobList')) return;
  const app = window.JobAssistant;
  const { api, dom: { $, esc } } = app;
  const statuses = JSON.parse(document.querySelector('#application-status-config')?.textContent || '[]');
  const statusStages = {
    收藏: 'saved',
    待投递: 'pending',
    已投递: 'applied',
    待面试: 'active', '1面': 'active', '2面': 'active', '3面': 'active',
    测评: 'active',
    笔试: 'active',
    一面: 'active',
    二面: 'active',
    HR面: 'active',
    Offer: 'success',
    拒绝: 'closed',
    放弃: 'closed',
  };
  const selectedJobs = new Set();
  let sortState = { key: '', direction: 'default' };
  const selectedLocations = new Set();
  let locationFilterActive = false;
  let pendingEditId = Number(new URLSearchParams(location.search).get('edit')) || null;
  let pendingTrackerView = document.querySelector('#jobList') ? history.state?.trackerView : null;
  const trackerFilterIds = ['search', 'categoryFilter', 'companyFilter', 'typeFilter', 'statusFilter'];

  function saveTrackerView() {
    if (!document.querySelector('#jobList') || !window.jobs) return;
    history.replaceState({ ...history.state, trackerView: {
      filters: Object.fromEntries(trackerFilterIds.map((id) => [id, document.getElementById(id).value])),
      sort: { ...sortState },
      selectedJobs: [...selectedJobs],
      selectedLocations: [...selectedLocations],
      locationFilterActive,
      scrollY: window.scrollY,
      railScrollLeft: document.querySelector('.rail-items')?.scrollLeft || 0,
    } }, '');
  }

  function restoreTrackerView() {
    const saved = pendingTrackerView;
    if (!saved) return null;
    pendingTrackerView = null;
    trackerFilterIds.forEach((id) => {
      const control = document.getElementById(id);
      control.value = saved.filters[id] || '';
      if (control.selectedIndex === -1) control.value = '';
    });
    sortState = { ...saved.sort };
    selectedJobs.clear();
    saved.selectedJobs.forEach((id) => selectedJobs.add(id));
    selectedLocations.clear();
    saved.selectedLocations.forEach((value) => selectedLocations.add(value));
    locationFilterActive = saved.locationFilterActive;
    return saved;
  }

  document.addEventListener('click', (event) => {
    if (event.target.closest('.job-detail-link')) saveTrackerView();
  }, true);
  window.addEventListener('pagehide', saveTrackerView);
  function feedback(text, isError = false) {
    const element = $('#feedback');
    if (!element) return;
    element.textContent = text;
    element.className = `feedback${isError ? ' error' : ''}`;
  }

  function renderFilters() {
    const values = Object.fromEntries(['categoryFilter', 'companyFilter', 'typeFilter', 'statusFilter'].map((id) => [id, $(`#${id}`)?.value || '']));
    const types = [...new Set(window.jobs.map((job) => job.employment_type).filter(Boolean))];
    $('#companyFilter').innerHTML = '<option value="">所有公司</option>' + window.companies.map((company) => `<option value="${company.id}">${esc(company.name)}</option>`).join('');
    $('#typeFilter').innerHTML = '<option value="">所有类型</option>' + types.map((type) => `<option>${esc(type)}</option>`).join('');
    $('#statusFilter').innerHTML = '<option value="">所有状态</option>' + statuses.map((status) => `<option>${status}</option>`).join('');
    $('#company').innerHTML = window.companies.map((company) => `<option value="${company.id}">${esc(company.name)}</option>`).join('');
    $('#companyEmpty').hidden = window.companies.length > 0;
    ['categoryFilter', 'companyFilter', 'typeFilter', 'statusFilter'].forEach((id) => { $(`#${id}`).value = values[id]; });
  }

  function formatAdded(value) {
    if (!value) return '时间待补';
    const date = new Date(value);
    return Number.isNaN(date.getTime())
      ? esc(value)
      : date.toLocaleString('zh-CN', { year: 'numeric', month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit' });
  }

  function safeUrl(value) {
    try {
      const url = new URL(value);
      return ['http:', 'https:'].includes(url.protocol) ? url.href : '';
    } catch {
      return '';
    }
  }
  function renderLocationOptions() {
    const locations = [...new Set(window.jobs.map((job) => (job.location || '').trim() || '待补'))].sort((a, b) => a.localeCompare(b, 'zh-CN'));
    $('#locationOptions').innerHTML = locations.map((location) => `<label class="location-option"><input type="checkbox" value="${esc(location)}" ${(locationFilterActive ? selectedLocations.has(location) : true) ? 'checked' : ''}>${esc(location)}</label>`).join('');
    $('#locationSummary').textContent = `${locationFilterActive ? selectedLocations.size : locations.length}/${locations.length} 已选`;
  }
  function updateSortButtons() {
    const labels = { title: '岗位 / 公司', location: '地点', employment_type: '类型', status: '当前状态', created_at: '添加时间' };
    document.querySelectorAll('[data-sort]').forEach((button) => {
      const key = button.dataset.sort;
      const label = labels[key] || button.textContent.replace(/[↑↓↕]/g, '').trim();
      const active = key === sortState.key && sortState.direction !== 'default';
      const current = sortState.direction === 'asc' ? '正序' : '倒序';
      const target = sortState.direction === 'asc' ? '倒序' : '默认';
      const header = button.closest('[role="columnheader"]') || button;
      header.setAttribute('aria-sort', active ? (sortState.direction === 'asc' ? 'ascending' : 'descending') : 'none');
      button.setAttribute('aria-label', active ? `${label}，当前${current}，点击切换为${target}` : `${label}，当前默认顺序，点击切换为正序`);
      const arrow = button.querySelector('span');
      if (arrow) arrow.textContent = active ? (sortState.direction === 'asc' ? '↑' : '↓') : '↕';
    });
    const summary = $('#sortSummary');
    if (summary) summary.textContent = sortState.direction === 'default' ? '默认顺序' : `按${labels[sortState.key]}${sortState.direction === 'asc' ? '正序' : '倒序'}`;
  }

  function renderRail() {
    const counts = Object.fromEntries(statuses.map((status) => [status, 0]));
    window.apps.forEach((application) => { counts[application.status] += 1; });
    $('#totalCount').textContent = `${window.jobs.length} 个岗位`;
    $('#statusRail').innerHTML = statuses.map((status, index) => {
      const count = counts[status];
      const active = $('#statusFilter')?.value === status;
      return `<button type="button" class="rail-item stage-${statusStages[status]} ${count ? 'has-count' : 'is-empty'} ${active ? 'active' : ''}" data-status-filter="${esc(status)}" aria-pressed="${active}" aria-label="${esc(status)}，${count} 个岗位"><span class="rail-sequence">${String(index + 1).padStart(2, '0')}</span><b>${count}</b><span>${esc(status)}</span></button>`;
    }).join('');
  }

  function employmentTypeTone(value) {
    const type = (value || '').trim();
    if (type.includes('日常实习')) return 'daily-intern';
    if (type.includes('实习')) return 'intern';
    if (/校园|校招|应届/.test(type)) return 'campus';
    if (/社会|社招/.test(type)) return 'experienced';
    if (type.includes('兼职')) return 'part-time';
    if (/全职|^full[ -]?time$/i.test(type)) return 'full-time';
    return 'other';
  }

  function renderJobs() {
    const query = ($('#search')?.value || '').toLowerCase();
    const companyId = $('#companyFilter')?.value || '';
    const category = $('#categoryFilter')?.value || '';
    const type = $('#typeFilter')?.value || '';
    const status = $('#statusFilter')?.value || '';
    let jobs = window.jobs.filter((job) => {
      const company = window.companies.find((item) => item.id === job.company_id);
      const application = window.apps.find((item) => item.job_id === job.id);
      const location = (job.location || '').trim() || '待补';
      const matchesQuery = !query || `${job.title} ${job.location} ${job.job_category} ${company?.name}`.toLowerCase().includes(query);
      const matchesCompany = !companyId || String(job.company_id) === companyId;
      const matchesCategory = !category || job.job_category === category;
      const matchesType = !type || job.employment_type === type;
      const matchesStatus = !status || (application?.status || '收藏') === status;
      const matchesLocation = !locationFilterActive || selectedLocations.has(location);
      return matchesQuery && matchesCompany && matchesCategory && matchesType && matchesStatus && matchesLocation;
    });
    jobs.sort((a, b) => {
      let result;
      if (sortState.direction === 'default') {
        result = new Date(a.created_at || 0).getTime() - new Date(b.created_at || 0).getTime();
        return result ? -result : b.id - a.id;
      }
      if (sortState.key === 'title') {
        const companyName = (job) => window.companies.find((item) => item.id === job.company_id)?.name || '';
        result = `${a.title} ${companyName(a)}`.localeCompare(`${b.title} ${companyName(b)}`, 'zh-CN');
      } else if (sortState.key === 'status') {
        const statusOf = (job) => window.apps.find((item) => item.job_id === job.id)?.status || '收藏';
        result = statuses.indexOf(statusOf(a)) - statuses.indexOf(statusOf(b));
      } else if (sortState.key === 'location' || sortState.key === 'employment_type') {
        const field = sortState.key;
        const left = (a[field] || '').trim();
        const right = (b[field] || '').trim();
        if (!left || !right) result = !left && !right ? 0 : (!left ? 1 : -1);
        else result = left.localeCompare(right, 'zh-CN');
      } else {
        result = new Date(a.created_at || 0).getTime() - new Date(b.created_at || 0).getTime();
      }
      const textKey = sortState.key === 'location' || sortState.key === 'employment_type';
      const missingText = textKey && (!(a[sortState.key] || '').trim() || !(b[sortState.key] || '').trim());
      return missingText ? result : (sortState.direction === 'asc' ? result : -result);
    });
    $('#jobList').innerHTML = jobs.length ? jobs.map((job, index) => {
      const company = window.companies.find((item) => item.id === job.company_id);
      const application = window.apps.find((item) => item.job_id === job.id);
      const applicationStatus = application?.status || '收藏';
      const checked = selectedJobs.has(job.id);
      const source = safeUrl(job.source_url);
      const applyLink = source
        ? `<a class="job-apply" href="${esc(source)}" target="_blank" rel="noopener noreferrer" aria-label="打开 ${esc(job.title)} 简历投递页面（新标签页）" title="打开投递页面（新标签页）">投递 <span aria-hidden="true">↗</span></a>`
        : '<button class="job-apply" type="button" disabled title="暂无有效投递链接，请在岗位详情中补充来源链接" aria-label="暂无有效投递链接">投递 <span aria-hidden="true">↗</span></button>';
      const types = [...new Set(['实习', '日常实习', '校园招聘', '社会招聘', '兼职', '全职', job.employment_type].filter(Boolean))];
      return `<article class="job-card" data-status="${esc(applicationStatus)}"><input class="job-select" type="checkbox" data-select-job="${job.id}" ${checked ? 'checked' : ''} aria-label="选择 ${esc(job.title)}"><a class="job-primary job-detail-link" href="/jobs/${job.id}" aria-label="查看 ${esc(job.title)} 详情"><h3>${esc(job.title)}</h3><p class="job-company">${esc(company?.name || '公司待补')} / ${esc(job.job_category || '其他')}</p></a><span class="job-place">${esc(job.location || '待补')}</span><div class="job-type"><select class="quick-type" data-type-tone="${employmentTypeTone(job.employment_type)}" data-quick-type="${job.id}" aria-label="更新 ${esc(job.title)} 招聘类型">${!job.employment_type ? '<option value="" selected>未填写</option>' : ''}${types.map((item) => `<option ${item === job.employment_type ? 'selected' : ''}>${esc(item)}</option>`).join('')}</select></div><div class="job-status"><select class="quick-status" data-status="${esc(applicationStatus)}" data-quick-status="${job.id}" aria-label="更新 ${esc(job.title)} 状态">${statuses.map((item) => `<option ${item === applicationStatus ? 'selected' : ''}>${item}</option>`).join('')}</select></div><div class="job-added"><time datetime="${esc(job.created_at || '')}">${formatAdded(job.created_at)}</time>${applyLink}</div></article>`;
    }).join('') : '<div class="empty"><strong>这里还没有对应岗位</strong><p>调整筛选，或先把下一份机会放进资料夹。</p><button class="button primary" id="emptyAdd" type="button">新增岗位</button></div>';
    const visible = [...document.querySelectorAll('#jobList [data-select-job]')];
    const selectAll = $('#selectAllJobs');
    if (selectAll) {
      const allSelected = visible.length > 0 && visible.every((item) => selectedJobs.has(Number(item.dataset.selectJob)));
      const someSelected = visible.some((item) => selectedJobs.has(Number(item.dataset.selectJob)));
      selectAll.checked = allSelected;
      selectAll.indeterminate = someSelected && !allSelected;
    }
    const count = $('#selectedCount');
    if (count) count.textContent = String(selectedJobs.size);
    const scope = $('#selectionScope');
    if (scope) {
      const visibleIds = new Set(visible.map((item) => Number(item.dataset.selectJob)));
      const hidden = [...selectedJobs].filter((id) => !visibleIds.has(id)).length;
      scope.textContent = hidden ? `另有 ${hidden} 项不在当前筛选中` : '当前筛选';
    }
    const bulk = $('#bulkActions');
    if (bulk) bulk.hidden = selectedJobs.size === 0;
    const visibleCount = $('#visibleCount');
    if (visibleCount) visibleCount.textContent = `显示 ${jobs.length} 个岗位`;
    updateSortButtons();
  }
  function openJob(job) {
    $('#jobDialog').showModal();
    $('#jobId').value = job?.id || '';
    $('#dialogTitle').textContent = job ? '编辑岗位' : '新增岗位';
    $('#company').value = job?.company_id || window.companies[0]?.id || '';
    $('#title').value = job?.title || '';
    $('#location').value = job?.location || '';
    $('#employmentType').value = job?.employment_type || '';
    $('#jobCategory').value = job?.job_category || '';
    $('#publishedAt').value = job?.published_at || '';
    $('#sourceWebsite').value = job?.source_website || '';
    $('#originalTitle').value = job?.original_title || '';
    $('#sourceUrl').value = job?.source_url || '';
    $('#description').value = job?.description || '';
    const application = window.apps.find((item) => item.job_id === job?.id);
    $('#status').innerHTML = statuses.map((status) => `<option ${status === (application?.status || '收藏') ? 'selected' : ''}>${status}</option>`).join('');
    $('#notes').value = application?.notes || '';
    $('#title').focus();
  }
  async function saveJob(event) {
    event.preventDefault();
    if (!window.companies.length) { feedback('请先新增公司，再保存岗位。', true); return; }
    try {
      const id = $('#jobId').value;
      const payload = {
        company_id: Number($('#company').value),
        title: $('#title').value,
        location: $('#location').value || null,
        employment_type: $('#employmentType').value || null,
        job_category: $('#jobCategory').value || null,
        published_at: $('#publishedAt').value || null,
        source_website: $('#sourceWebsite').value || null,
        original_title: $('#originalTitle').value || null,
        source_url: $('#sourceUrl').value || null,
        description: $('#description').value || null,
      };
      const job = id ? await api(`/jobs/${id}`, { method: 'PATCH', body: JSON.stringify(payload) }) : await api('/jobs', { method: 'POST', body: JSON.stringify(payload) });
      const application = window.apps.find((item) => item.job_id === job.id);
      try {
        if (application) await api(`/applications/${application.id}`, { method: 'PATCH', body: JSON.stringify({ status: $('#status').value, notes: $('#notes').value || null }) });
        else await api('/applications', { method: 'POST', body: JSON.stringify({ job_id: job.id, status: $('#status').value, notes: $('#notes').value || null }) });
      } catch (error) {
        $('#jobDialog').close(); feedback('岗位已保存但状态保存失败：可重新编辑岗位后重试。', true); await loadJobs(); return;
      }
      $('#jobDialog').close(); feedback('岗位已保存'); await loadJobs();
    } catch (error) { feedback(`保存失败：${error.message}`, true); }
  }

  function renderCompanies() {
    $('#companyList').innerHTML = window.companies.length ? window.companies.map((company) => {
      const aliases = company.aliases?.length ? `<small>${esc(company.aliases.join(' / '))}</small>` : '';
      return `<div class="company-row"><span><strong>${esc(company.name)}</strong>${aliases}</span><span><button type="button" class="text-button" data-company-edit="${company.id}">编辑</button><button type="button" class="text-button" data-company-delete="${company.id}">删除</button></span></div>`;
    }).join('') : '<p class="empty-copy">还没有公司，先新增第一家公司。</p>';
  }

  function resetCompanyEditor() {
    $('#companyId').value = '';
    $('#companyName').value = '';
    $('#companyAliases').value = '';
    $('#companyNotes').value = '';
    $('#companySubmit').textContent = '新增公司';
    $('#companyCancelEdit').hidden = true;
  }

  async function saveCompany(event) {
    event.preventDefault();
    const aliases = $('#companyAliases').value.split(/[,，\n]/).map((item) => item.trim()).filter(Boolean);
    const payload = { name: $('#companyName').value, aliases: aliases.length ? aliases : null, notes: $('#companyNotes').value || null };
    const id = $('#companyId').value;
    try {
      await api(id ? `/companies/${id}` : '/companies', { method: id ? 'PATCH' : 'POST', body: JSON.stringify(payload) });
      resetCompanyEditor();
      await loadJobs();
      renderCompanies();
    } catch (error) {
      $('#companyList').insertAdjacentHTML('afterbegin', `<p class="feedback error">保存失败：${esc(error.message)}</p>`);
    }
  }

  async function loadJobs() {
    if (!$('#jobList')) return;
    try {
      window.jobs = await api('/jobs');
      window.companies = await api('/companies');
      window.apps = await api('/applications');
      renderFilters();
      const savedView = restoreTrackerView();
      const ids = new Set(window.jobs.map((item) => item.id));
      selectedJobs.forEach((id) => {
        if (!ids.has(id)) selectedJobs.delete(id);
      });
      const locations = new Set(window.jobs.map((job) => (job.location || '').trim() || '待补'));
      selectedLocations.forEach((location) => {
        if (!locations.has(location)) selectedLocations.delete(location);
      });
      renderRail();
      renderLocationOptions();
      renderJobs();
      if (savedView) requestAnimationFrame(() => {
        const rail = document.querySelector('.rail-items');
        if (rail) rail.scrollLeft = savedView.railScrollLeft;
        window.scrollTo({ top: savedView.scrollY, behavior: 'instant' });
      });
      if (pendingEditId) {
        const job = window.jobs.find((item) => item.id === pendingEditId);
        pendingEditId = null;
        history.replaceState(history.state, '', location.pathname);
        if (job) openJob(job);
      }
    } catch (error) {
      feedback(`加载失败：${error.message}`, true);
    }
  }
  document.addEventListener('click', async (event) => {
    const target = event.target.closest('[data-status-filter]') || event.target;
    if (target.dataset.statusFilter) {
      const filter = $('#statusFilter');
      filter.value = filter.value === target.dataset.statusFilter ? '' : target.dataset.statusFilter;
      renderRail(); renderJobs();
    }
    if (target.id === 'newJob' || target.id === 'emptyAdd') openJob();
    if (target.dataset.sort) {
      const sameKey = sortState.key === target.dataset.sort;
      const direction = !sameKey || sortState.direction === 'default' ? 'asc' : sortState.direction === 'asc' ? 'desc' : 'default';
      sortState = { key: direction === 'default' ? '' : target.dataset.sort, direction };
      renderJobs();
    }
    if (target.id === 'manageCompanies') {
      $('#jobDialog').close();
      resetCompanyEditor();
      $('#companyDialog').showModal();
      renderCompanies();
    }
    if (target.dataset.companyDelete) {
      try {
        await api(`/companies/${target.dataset.companyDelete}`, { method: 'DELETE' });
        await loadJobs();
        renderCompanies();
      } catch (error) {
        $('#companyList').insertAdjacentHTML('afterbegin', `<p class="feedback error">删除失败：${esc(error.message)}（请先处理关联岗位）</p>`);
      }
    }
    if (target.dataset.companyEdit) {
      const company = window.companies.find((item) => String(item.id) === target.dataset.companyEdit);
      $('#companyId').value = company.id;
      $('#companyName').value = company.name;
      $('#companyAliases').value = (company.aliases || []).join('，');
      $('#companyNotes').value = company.notes || '';
      $('#companySubmit').textContent = '保存公司';
      $('#companyCancelEdit').hidden = false;
      $('#companyName').focus();
    }
    if (target.id === 'companyCancelEdit') resetCompanyEditor();
  });
  document.addEventListener('input', (event) => {
    if (['search', 'categoryFilter', 'companyFilter', 'typeFilter', 'statusFilter'].includes(event.target.id)) {
      renderJobs();
    }
  });
  document.addEventListener('change', async (event) => {
    if (event.target.id === 'selectAllJobs') {
      document.querySelectorAll('#jobList [data-select-job]').forEach((item) => {
        const id = Number(item.dataset.selectJob);
        if (event.target.checked) selectedJobs.add(id);
        else selectedJobs.delete(id);
      });
      renderJobs();
      return;
    }
    if (event.target.matches('[data-select-job]')) {
      const id = Number(event.target.dataset.selectJob);
      if (event.target.checked) selectedJobs.add(id);
      else selectedJobs.delete(id);
      renderJobs();
      return;
    }
    if (event.target.closest('#locationOptions')) {
      locationFilterActive = true;
      selectedLocations.clear();
      document.querySelectorAll('#locationOptions input:checked').forEach((item) => selectedLocations.add(item.value));
      if (selectedLocations.size === document.querySelectorAll('#locationOptions input').length) locationFilterActive = false;
      renderLocationOptions();
      renderJobs();
      return;
    }
    if (event.target.id === 'statusFilter') {
      renderRail();
      renderJobs();
      return;
    }
    const typeControl = event.target.closest('[data-quick-type]');
    if (typeControl) {
      const job = window.jobs.find((item) => String(item.id) === typeControl.dataset.quickType);
      const previousType = job?.employment_type || '';
      typeControl.disabled = true;
      try {
        await api(`/jobs/${job.id}`, { method: 'PATCH', body: JSON.stringify({ employment_type: typeControl.value }) });
        feedback('招聘类型已更新');
        await loadJobs();
      } catch (error) {
        typeControl.value = previousType;
        feedback(`招聘类型更新失败：${error.message}`, true);
      } finally {
        typeControl.disabled = false;
      }
      return;
    }
    const control = event.target.closest('[data-quick-status]');
    if (!control) return;
    const job = window.jobs.find((item) => String(item.id) === control.dataset.quickStatus);
    const application = window.apps.find((item) => item.job_id === job?.id);
    control.disabled = true;
    const previousStatus = application?.status || '收藏';
    try {
      if (application) await api(`/applications/${application.id}`, { method: 'PATCH', body: JSON.stringify({ status: control.value }) });
      else await api('/applications', { method: 'POST', body: JSON.stringify({ job_id: job.id, status: control.value }) });
      feedback('状态已更新');
      await loadJobs();
    } catch (error) {
      control.value = previousStatus;
      feedback(`状态更新失败：${error.message}`, true);
    } finally {
      control.disabled = false;
    }
  });
  $('#applyBulkStatus')?.addEventListener('click', async () => {
    const button = $('#applyBulkStatus');
    const status = $('#bulkStatus').value;
    if (!selectedJobs.size) return;
    if (!status) { feedback('请先选择目标状态。', true); return; }
    button.disabled = true;
    button.setAttribute('aria-busy', 'true');
    try {
      const application_ids = [...selectedJobs]
        .map((jobId) => window.apps.find((item) => item.job_id === jobId)?.id)
        .filter(Boolean);
      const skipped = selectedJobs.size - application_ids.length;
      if (!application_ids.length) {
        feedback(`更新 0 项，跳过 ${skipped} 项：所选岗位均没有求职记录。`, true);
        return;
      }
      await api('/applications/bulk-status', { method: 'PATCH', body: JSON.stringify({ application_ids, status }) });
      selectedJobs.clear();
      feedback(`更新 ${application_ids.length} 项，跳过 ${skipped} 项`);
      await loadJobs();
    } catch (error) {
      feedback(`批量更新失败：${error.message}`, true);
      await loadJobs();
    } finally {
      button.disabled = false;
      button.setAttribute('aria-busy', 'false');
    }
  });
  $('#clearSelection')?.addEventListener('click', () => { selectedJobs.clear(); renderJobs(); });
  $('#deleteSelected')?.addEventListener('click', async () => {
    const ids = [...selectedJobs];
    if (!ids.length || !confirm(`确认删除所选 ${ids.length} 个岗位？关联记录也会移除。`)) return;
    try {
      await api('/jobs/bulk-delete', { method: 'POST', body: JSON.stringify({ job_ids: ids }) });
      selectedJobs.clear();
      feedback('所选岗位已删除');
      await loadJobs();
    } catch (error) {
      feedback(`删除失败：${error.message}`, true);
    }
  });
  $('#selectAllLocations')?.addEventListener('click', () => { locationFilterActive = false; selectedLocations.clear(); renderLocationOptions(); renderJobs(); });
  $('#clearLocations')?.addEventListener('click', () => { locationFilterActive = true; selectedLocations.clear(); renderLocationOptions(); renderJobs(); });
  $('#applyLocations')?.addEventListener('click', () => { $('#locationMenu').open = false; });
  $('#jobForm')?.addEventListener('submit', saveJob);
  $('#companyForm')?.addEventListener('submit', saveCompany);
  app.ready.then(loadJobs);
})();
