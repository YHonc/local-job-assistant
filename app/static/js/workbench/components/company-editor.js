function text(value) {
  return String(value ?? '');
}

function escapeHtml(value) {
  return text(value).replace(/[&<>"']/g, (character) => ({
    '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;',
  })[character]);
}

function apiOptions(method, body) {
  return { method, ...(body === undefined ? {} : { body: JSON.stringify(body) }) };
}

function aliasesFromText(value) {
  const seen = new Set();
  return text(value).split(/[\n,，;；]/).map((part) => part.trim()).filter((part) => {
    if (!part) return false;
    const key = part.toLocaleLowerCase();
    if (seen.has(key)) return false;
    seen.add(key);
    return true;
  });
}

function formFingerprint(form) {
  return JSON.stringify([...new FormData(form).entries()]);
}

export function openCompanyManager({ root, api, notify = () => {}, onChange = () => {}, onClosed = () => {} } = {}) {
  if (!root || typeof api !== 'function') throw new TypeError('root and api are required');
  const returnFocus = document.activeElement;
  const dialog = document.createElement('dialog');
  dialog.className = 'wb-dialog wb-company-dialog';
  dialog.setAttribute('aria-labelledby', 'wbCompanyTitle');
  dialog.innerHTML = `<div class="wb-dialog-panel wb-company-panel">
    <header class="wb-dialog-heading"><div><p class="wb-eyebrow">公司资料</p><h2 id="wbCompanyTitle">公司管理</h2><p class="wb-dialog-subtitle">维护公司名称、别名和公司备注；岗位备注与投递备注独立保存。</p></div><button class="wb-button wb-button-quiet" type="button" data-company-close aria-label="关闭公司管理">关闭</button></header>
    <div class="wb-company-layout"><section class="wb-company-list-panel" aria-label="公司列表"><div class="wb-company-list-actions"><label class="wb-field">搜索公司<input type="search" data-company-search placeholder="公司名或别名" autocomplete="off"></label><button class="wb-button wb-button-primary" type="button" data-company-new>＋ 新建公司</button></div><div class="wb-company-list" data-company-list aria-live="polite"><p class="wb-muted-label">正在读取公司资料…</p></div></section><section class="wb-company-form-panel" data-company-form-panel aria-live="polite"><div class="wb-empty-state"><strong>选择一家公司</strong><p>查看、编辑或删除公司资料，也可以新建公司。</p></div></section></div>
    <p class="wb-dialog-status" data-company-status role="status"></p>
  </div>`;
  root.append(dialog);

  const list = dialog.querySelector('[data-company-list]');
  const formPanel = dialog.querySelector('[data-company-form-panel]');
  const status = dialog.querySelector('[data-company-status]');
  const search = dialog.querySelector('[data-company-search]');
  let companies = [];
  let activeCompany = null;
  let formBaseline = '';
  let closed = false;
  let loading = false;

  const setStatus = (message, kind = 'info') => {
    status.textContent = message;
    status.dataset.kind = kind;
  };
  const hasUnsavedChanges = () => {
    const form = formPanel.querySelector('form');
    return Boolean(form && formFingerprint(form) !== formBaseline);
  };
  const canLeaveForm = () => !hasUnsavedChanges() || window.confirm('公司资料尚未保存，确定放弃这些修改吗？');
  const onBeforeUnload = (event) => {
    if (!hasUnsavedChanges()) return;
    event.preventDefault();
    event.returnValue = '';
  };
  window.addEventListener('beforeunload', onBeforeUnload);

  function renderList() {
    const query = search.value.trim().toLocaleLowerCase();
    const filtered = companies.filter((company) => {
      const values = [company.name, ...(Array.isArray(company.aliases) ? company.aliases : [])];
      return !query || values.some((value) => text(value).toLocaleLowerCase().includes(query));
    });
    if (!filtered.length) {
      list.innerHTML = `<div class="wb-empty-state"><strong>${query ? '没有匹配公司' : '暂无公司'}</strong><p>${query ? '可调整搜索词，或清空搜索后新建公司。' : '创建公司后即可在添加岗位时选择。'}</p></div>`;
      return;
    }
    list.innerHTML = filtered.map((company) => `<article class="wb-company-row ${activeCompany?.id === company.id ? 'is-selected' : ''}" data-company-row="${Number(company.id)}">
      <div class="wb-company-row-copy"><strong>${escapeHtml(company.name)}</strong><span>${escapeHtml((company.aliases || []).join('、') || '暂无别名')}</span></div>
      <button class="wb-button wb-button-quiet" type="button" data-company-edit="${Number(company.id)}" aria-label="编辑 ${escapeHtml(company.name)}">编辑</button>
    </article>`).join('');
  }

  function renderForm(company = null) {
    activeCompany = company ? { ...company } : null;
    const aliases = Array.isArray(company?.aliases) ? company.aliases.join('\n') : '';
    formPanel.innerHTML = `<form class="wb-company-form" data-company-form novalidate>
      <div class="wb-form-section-head"><div><h3>${company ? '编辑公司' : '新建公司'}</h3><p>${company ? `公司编号 #${Number(company.id)}` : '别名可填写简称、曾用名或常见写法。'}</p></div>${company ? `<button class="wb-button wb-button-danger" type="button" data-company-delete="${Number(company.id)}">删除公司</button>` : ''}</div>
      <label class="wb-field">公司名称 <span aria-hidden="true">*</span><input name="name" maxlength="200" required value="${escapeHtml(company?.name || '')}" autocomplete="organization" placeholder="例如：某某科技"></label>
      <label class="wb-field">公司别名<textarea name="aliases" rows="4" placeholder="每行一个，也可用逗号分隔">${escapeHtml(aliases)}</textarea></label>
      <label class="wb-field">公司备注<textarea name="notes" rows="5" placeholder="记录公司层面的信息">${escapeHtml(company?.notes || '')}</textarea></label>
      <p class="wb-form-error" data-company-form-error role="alert"></p>
      <div class="wb-dialog-actions"><button class="wb-button wb-button-quiet" type="button" data-company-cancel>取消</button><button class="wb-button wb-button-primary" type="submit" data-company-save>${company ? '保存公司' : '创建公司'}</button></div>
    </form>`;
    const form = formPanel.querySelector('form');
    formBaseline = formFingerprint(form);
    form.querySelector('[name="name"]').focus();
  }

  async function loadCompanies({ keepSelection = true } = {}) {
    if (loading) return;
    loading = true;
    list.innerHTML = '<p class="wb-muted-label">正在读取公司资料…</p>';
    try {
      const selectedId = keepSelection ? activeCompany?.id : null;
      const response = await api('/companies');
      if (closed) return;
      companies = Array.isArray(response) ? response : [];
      activeCompany = selectedId ? companies.find((company) => company.id === selectedId) || null : activeCompany;
      renderList();
    } catch (error) {
      if (closed) return;
      list.innerHTML = `<div class="wb-state-message" data-kind="error" role="alert"><strong>公司列表读取失败</strong><p>${escapeHtml(error?.message || '请求未成功。')}</p><button class="wb-button wb-button-outline" type="button" data-company-retry>重试</button></div>`;
    } finally {
      loading = false;
    }
  }

  function close() {
    if (closed || !canLeaveForm()) return;
    dialog.close();
  }

  function cleanup() {
    if (closed) return;
    closed = true;
    window.removeEventListener('beforeunload', onBeforeUnload);
    dialog.remove();
    if (returnFocus?.isConnected) returnFocus.focus();
    onClosed();
  }

  dialog.addEventListener('cancel', (event) => {
    if (!canLeaveForm()) event.preventDefault();
  });
  dialog.addEventListener('close', cleanup, { once: true });
  dialog.querySelector('[data-company-close]').addEventListener('click', close);
  dialog.querySelector('[data-company-new]').addEventListener('click', () => {
    if (!canLeaveForm()) return;
    renderForm();
    setStatus('填写公司资料后保存。');
  });
  search.addEventListener('input', renderList);

  dialog.addEventListener('click', async (event) => {
    const retry = event.target.closest('[data-company-retry]');
    if (retry) {
      await loadCompanies({ keepSelection: false });
      return;
    }
    const edit = event.target.closest('[data-company-edit]');
    if (edit) {
      if (!canLeaveForm()) return;
      const company = companies.find((item) => item.id === Number(edit.dataset.companyEdit));
      if (company) {
        renderForm(company);
        setStatus('公司备注属于公司资料，不会写入岗位或投递备注。');
      }
      return;
    }
    if (event.target.closest('[data-company-cancel]')) {
      if (!canLeaveForm()) return;
      activeCompany = null;
      formBaseline = '';
      formPanel.innerHTML = '<div class="wb-empty-state"><strong>选择一家公司</strong><p>查看、编辑或删除公司资料，也可以新建公司。</p></div>';
      setStatus('');
      renderList();
      return;
    }
    const remove = event.target.closest('[data-company-delete]');
    if (remove) {
      if (hasUnsavedChanges() && !window.confirm('公司资料有未保存修改。仍要继续删除吗？')) return;
      const target = companies.find((company) => company.id === Number(remove.dataset.companyDelete));
      if (!target || !window.confirm(`确定删除公司“${target.name}”吗？`)) return;
      remove.disabled = true;
      setStatus('正在删除公司…');
      try {
        await api(`/companies/${target.id}`, apiOptions('DELETE'));
        if (closed) return;
        companies = companies.filter((company) => company.id !== target.id);
        activeCompany = null;
        formBaseline = '';
        formPanel.innerHTML = '<div class="wb-empty-state"><strong>选择一家公司</strong><p>查看、编辑或删除公司资料，也可以新建公司。</p></div>';
        renderList();
        setStatus('公司已删除。', 'success');
        notify('公司资料已删除。', 'success');
        onChange({ type: 'delete', companyId: target.id });
      } catch (error) {
        remove.disabled = false;
        if (error?.status === 409) {
          setStatus('该公司仍有关联岗位，无法删除。请先在岗位编辑中更换公司或删除相关岗位。', 'error');
        } else {
          setStatus(error?.message || '删除公司失败，修改仍保留。', 'error');
        }
      }
    }
  });

  dialog.addEventListener('submit', async (event) => {
    const form = event.target.closest('[data-company-form]');
    if (!form) return;
    event.preventDefault();
    const data = new FormData(form);
    const name = text(data.get('name')).trim();
    if (!name) {
      form.querySelector('[name="name"]').focus();
      form.querySelector('[data-company-form-error]').textContent = '请填写公司名称。';
      return;
    }
    const payload = {
      name,
      aliases: aliasesFromText(data.get('aliases')),
      notes: text(data.get('notes')).trim() || null,
    };
    const save = form.querySelector('[data-company-save]');
    const creating = !activeCompany;
    const errorNode = form.querySelector('[data-company-form-error]');
    save.disabled = true;
    save.textContent = activeCompany ? '正在保存…' : '正在创建…';
    errorNode.textContent = '';
    try {
      const company = activeCompany
        ? await api(`/companies/${activeCompany.id}`, apiOptions('PATCH', payload))
        : await api('/companies', apiOptions('POST', payload));
      if (closed) return;
      activeCompany = company;
      await loadCompanies();
      if (closed) return;
      renderForm(company);
      setStatus('公司资料已由服务端保存。', 'success');
      notify('公司资料已保存。', 'success');
      onChange({ type: creating ? 'create' : 'save', company });
    } catch (error) {
      if (closed) return;
      save.disabled = false;
      save.textContent = activeCompany ? '保存公司' : '创建公司';
      if (error?.status === 409) {
        errorNode.textContent = '公司名称发生冲突。请检查已有公司并选择或调整名称。';
      } else {
        errorNode.textContent = error?.message || '保存失败，表单内容仍保留。';
      }
      setStatus('保存未成功，草稿仍在当前表单中。', 'error');
    }
  });

  dialog.showModal();
  void loadCompanies({ keepSelection: false });
  cleanup.canLeave = canLeaveForm;
  return cleanup;
}
