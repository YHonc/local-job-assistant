import { buttonBusy, jsonOptions, setFeedback } from '../integrations/shared.js';

export function proxyMarkup() {
  return `<form class="wb-route-card" data-form="proxy"><h3>统一本地代理</h3>
    <label>代理地址<input name="proxy_url" type="url" maxlength="1000" placeholder="http://127.0.0.1:7890" autocomplete="off" disabled></label>
    <p class="wb-hint">填写本机代理软件的 HTTP / 混合端口（例如 http://127.0.0.1:7890），或 SOCKS5 地址（例如 socks5://127.0.0.1:1080）。保存后，在需要的模型中勾选「使用本地代理」。地址不支持账号密码。</p>
    <div class="wb-inline-actions"><button class="wb-button wb-primary" type="submit" disabled>保存代理</button><span class="wb-feedback" data-proxy-feedback role="status"></span></div></form>`;
}

export function mountProxySettings(root, api, drafts) {
  const form = root.querySelector('[data-form="proxy"]');
  const input = form.elements.proxy_url;
  const submit = form.querySelector('[type="submit"]');
  let cleaned = false;
  drafts.register('proxy', form);
  const feedback = (text, kind = 'info') => { if (!cleaned) setFeedback(root, '[data-proxy-feedback]', text, kind); };
  async function load() {
    if (drafts.dirty('proxy') || drafts.saving('proxy')) return;
    try {
      const result = await api('/llm/proxy');
      if (cleaned || drafts.dirty('proxy') || drafts.saving('proxy')) return;
      input.value = result.proxy_url || ''; input.disabled = false; drafts.accept('proxy');
      feedback(result.proxy_url ? '已保存；仅勾选代理的模型使用此地址。' : '尚未配置代理。');
    } catch (error) { feedback(`读取代理设置失败：${error.message}`, 'error'); }
  }
  const onSubmit = async (event) => {
    event.preventDefault();
    await drafts.save('proxy', async () => { try {
      feedback('正在保存…');
      const result = await api('/llm/proxy', jsonOptions('PUT', { proxy_url: input.value.trim() || null }));
      if (cleaned) return;
      input.value = result.proxy_url || ''; drafts.accept('proxy');
      feedback('代理设置已保存，将用于后续请求。', 'success');
    } catch (error) { feedback(`保存失败：${error.message}`, 'error'); }
    });
  };
  form.addEventListener('submit', onSubmit);
  load();
  return { load, cleanup() { cleaned = true; form.removeEventListener('submit', onSubmit); } };
}
