import { escapeHtml, statusValues } from '../shared.js';

export function mountBehaviorSettings(root, api, onHealthSaved, drafts) {
  const element = document.createElement('section');
  element.className = 'wb-card wb-behavior-settings';
  element.innerHTML = `<h2>投递行为</h2><p>状态：${statusValues().map(escapeHtml).join(' · ')}</p><form data-delivery-form><label class="wb-check"><input type="checkbox" name="on_detail">查看详情后标记为已投递</label><label class="wb-check"><input type="checkbox" name="on_apply">点击去投递后标记为已投递</label><p class="wb-hint">仅从待投递推进；按点击行为记录，不能确认外部网站是否提交成功。</p><button class="wb-button" type="submit">保存投递行为</button><span role="status" data-feedback></span></form>`;
  root.querySelector('[data-delivery-slot]').append(element);
  const health = document.createElement('section');
  health.className = 'wb-card wb-behavior-settings wb-health-settings';
  health.innerHTML = `<form data-health-form>
    <div class="wb-health-heading"><span class="wb-health-icon" aria-hidden="true"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round"><path d="M20 7v5h-5M4 17v-5h5"/><path d="M6.1 7a7 7 0 0 1 11.6-1L20 9M4 15l2.3 3A7 7 0 0 0 18 17"/></svg></span><div><h2>自动测试</h2><p>定期检查模型连接，及时发现异常</p></div></div>
    <label class="wb-health-toggle"><input type="checkbox" name="enabled" role="switch" aria-label="开启后台自动测试"><span class="wb-health-track" aria-hidden="true"></span><span class="wb-health-toggle-text" aria-hidden="true"></span></label>
    <div class="wb-health-controls"><label class="wb-health-interval"><span>测试间隔</span><span class="wb-health-number"><input aria-label="默认间隔（分钟）" name="interval_minutes" type="number" min="1" max="1440" step="1" value="30" list="wbHealthIntervals" required><span>分钟</span></span></label><datalist id="wbHealthIntervals"><option value="5"><option value="15"><option value="30"><option value="60"></datalist><span class="wb-health-scope">单个模型可在编辑中单独设置</span><div class="wb-health-save"><span role="status" data-feedback></span><button class="wb-button" type="submit">保存设置</button></div></div>
    <p class="wb-health-note">服务运行期间持续检查，关闭网页不影响。测试会产生少量用量，失败后自动延长间隔。</p>
  </form>`;
  root.querySelector('[data-health-slot]').append(health);
  let disposed = false;
  const controller = new AbortController();
  for (const [name, container, path] of [['delivery', element, '/workbench/delivery-preferences'], ['health', health, '/llm/health-settings']]) {
    const form = container.querySelector('form');
    const feedback = container.querySelector('[data-feedback]');
    drafts.register(name, form);
    feedback.textContent = '正在读取…';
    api(path, { signal: controller.signal }).then((values) => {
      if (disposed) return;
      for (const [key, value] of Object.entries(values)) {
        const input = form.elements[key];
        if (!input) continue;
        if (input.type === 'checkbox') input.checked = value;
        else input.value = value;
      }
      drafts.accept(name);
      feedback.textContent = '已加载';
    }).catch((error) => { if (!disposed) feedback.textContent = `读取失败：${error.message}；请刷新重试`; });
    form.addEventListener('submit', async (event) => {
      event.preventDefault();
      const values = Object.fromEntries([...form.elements].filter((input) => input.name).map((input) => [input.name, input.type === 'checkbox' ? input.checked : Number(input.value)]));
      await drafts.save(name, async () => { try {
        feedback.textContent = '正在保存…';
        await api(path, { method: 'PUT', body: JSON.stringify(values), signal: controller.signal });
        if (disposed) return;
        drafts.accept(name); feedback.textContent = '已保存';
        if (container === health) await onHealthSaved();
      } catch (error) { if (!disposed) feedback.textContent = `保存失败：${error.message}`; }
      });
    });
  }
  return { cleanup: () => { disposed = true; controller.abort(); } };
}
