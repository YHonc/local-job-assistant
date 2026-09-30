(() => {
  const dialog = document.getElementById('usageDialog');
  if (!dialog) return;
  const $ = (id) => document.getElementById(id);
  const metrics = [
    { key: 'total_tokens', label: 'Token 合计', unit: 'Token' },
    { key: 'total_prompt_tokens', label: '输入 Token', unit: 'Token' },
    { key: 'total_completion_tokens', label: '输出 Token', unit: 'Token' },
    { key: 'average_duration_ms', label: '平均响应', unit: 'ms' },
    { key: 'call_count', label: '调用次数', unit: '次' },
    { key: 'success_count', label: '成功调用', unit: '次' },
    { key: 'failure_count', label: '失败调用', unit: '次' },
  ];
  const number = (value) => value == null ? '暂无' : Math.round(value).toLocaleString('zh-CN');
  const compact = (value) => value >= 1e6 ? `${+(value / 1e6).toFixed(1)}m` : value >= 1e3 ? `${+(value / 1e3).toFixed(1)}k` : String(Math.round(value));
  const today = () => new Date(Date.now() + 8 * 3600000).toISOString().slice(0, 10);
  const shiftDate = (value, days) => new Date(Date.parse(`${value}T00:00:00Z`) + days * 86400000).toISOString().slice(0, 10);
  const stamp = (value) => new Date(value).toLocaleString('zh-CN', { timeZone: 'Asia/Hong_Kong', hour12: false });
  let config = null;
  let metric = metrics[0];
  let data = null;
  let controller = null;
  let selectedIndex = null;
  let chartGeometry = null;

  function selectPeriod(days) {
    const end = today();
    $('usageStart').value = shiftDate(end, 1 - days);
    $('usageEnd').value = end;
    document.querySelectorAll('[data-usage-days]').forEach((button) => button.setAttribute('aria-pressed', String(Number(button.dataset.usageDays) === days)));
  }

  function showMetrics() {
    $('usageMetrics').replaceChildren(...metrics.map((item) => {
      const button = document.createElement('button');
      button.type = 'button';
      button.className = 'usage-metric';
      button.dataset.usageMetric = item.key;
      button.setAttribute('aria-pressed', String(item.key === metric.key));
      const label = document.createElement('span');
      label.textContent = item.label;
      const value = document.createElement('strong');
      value.textContent = data ? number(data.summary[item.key]) : '…';
      const scope = document.createElement('small');
      scope.textContent = item.key === 'average_duration_ms' ? '所选周期平均 / ms' : `所选周期合计 / ${item.unit}`;
      button.append(label, value, scope);
      return button;
    }));
    $('usageChartTitle').textContent = metric.label;
    const grain = data?.granularity === 'hour' ? '小时' : '天';
    $('usageChartUnit').textContent = `${metric.unit} / 每${grain}${metric.key === 'average_duration_ms' ? '平均' : '合计'}`;
    const totals = data?.lifetime || { ...config, total_tokens: (config.total_prompt_tokens || 0) + (config.total_completion_tokens || 0) };
    const lastUsed = data ? data.last_used_at : config.last_used_at;
    const lifetimeLabel = metric.key === 'average_duration_ms' ? '历史平均响应' : `历史累计${metric.label}`;
    $('usageLifetime').textContent = `${lifetimeLabel}：${number(totals[metric.key])} ${metric.unit}。最近使用：${lastUsed ? stamp(lastUsed) : '尚未使用'}。`;
  }

  function valueAt(point) {
    return point.available ? point[metric.key] : null;
  }

  function dateLabel(point, full = false) {
    const local = new Date(Date.parse(point.timestamp) + 8 * 3600000).toISOString();
    if (data.granularity === 'hour') return full ? `${local.slice(0, 10)} ${local.slice(11, 16)}` : local.slice(11, 16);
    return full ? local.slice(0, 10) : local.slice(5, 10).replace('-', '/');
  }

  function showPoint(index) {
    if (!data || !chartGeometry) return;
    selectedIndex = Math.max(0, Math.min(data.points.length - 1, index));
    const point = data.points[selectedIndex];
    const value = valueAt(point);
    const detail = !point.available ? '此时段无历史明细' : value == null ? '此时段无调用' : `${number(value)} ${metric.unit}`;
    $('usagePointReadout').textContent = `${dateLabel(point, true)}　${metric.label}：${detail}`;
    const cursor = $('usageChart').querySelector('.usage-chart-cursor');
    const dot = $('usageChart').querySelector('.usage-chart-selected');
    const { x, y, top, bottom } = chartGeometry;
    cursor.setAttribute('x1', x(selectedIndex));
    cursor.setAttribute('x2', x(selectedIndex));
    cursor.setAttribute('y1', top);
    cursor.setAttribute('y2', bottom);
    cursor.style.display = '';
    dot.style.display = value == null ? 'none' : '';
    if (value != null) {
      dot.setAttribute('cx', x(selectedIndex));
      dot.setAttribute('cy', y(value));
    }
  }

  function renderChart() {
    const container = $('usageChart');
    chartGeometry = null;
    if (!data) return;
    const points = data.points;
    const values = points.map(valueAt).filter((value) => value != null);
    if (!values.length) {
      container.replaceChildren();
      const empty = document.createElement('p');
      empty.className = 'usage-chart-empty';
      empty.textContent = data.recorded_from ? '所选日期没有可显示的调用明细，请切换日期范围。' : '尚无时间明细。启用记录后的调用将在这里形成曲线。';
      container.append(empty);
      $('usagePointReadout').textContent = '没有明细的日期不会绘制为 0。';
      return;
    }
    const width = Math.max(240, container.clientWidth);
    const height = container.clientHeight;
    const left = 60, right = width - 22, top = 22, bottom = height - 44;
    const peak = Math.max(4, ...values);
    const power = 10 ** Math.floor(Math.log10(peak));
    const ceiling = Math.ceil([1, 2, 5, 10].find((step) => step * power >= peak) * power / 4) * 4;
    const x = (index) => left + index * (right - left) / Math.max(1, points.length - 1);
    const y = (value) => bottom - value / ceiling * (bottom - top);
    let svg = `<svg viewBox="0 0 ${width} ${height}" role="img" tabindex="0" aria-label="${metric.label}折线图，左右方向键查看日期与数值"><title>${metric.label}，${data.start} 至 ${data.end}</title>`;
    for (let i = 0; i <= 4; i++) {
      const value = ceiling * i / 4;
      svg += `<line class="usage-chart-grid" x1="${left}" x2="${right}" y1="${y(value)}" y2="${y(value)}"/><text class="usage-chart-label" x="${left - 10}" y="${y(value) + 4}" text-anchor="end">${compact(value)}</text>`;
    }
    const tickCount = width < 500 ? 3 : 6;
    const ticks = [...new Set(Array.from({ length: tickCount }, (_, i) => Math.round(i * (points.length - 1) / (tickCount - 1))))];
    ticks.forEach((index) => {
      svg += `<text class="usage-chart-label" x="${x(index)}" y="${height - 14}" text-anchor="${index === 0 ? 'start' : index === points.length - 1 ? 'end' : 'middle'}">${dateLabel(points[index])}</text>`;
    });
    let path = '', connected = false;
    points.forEach((point, index) => {
      const value = valueAt(point);
      if (value == null) { connected = false; return; }
      path += `${connected ? 'L' : 'M'}${x(index)},${y(value)} `;
      connected = true;
    });
    svg += `<path class="usage-chart-line" d="${path}"/>`;
    points.forEach((point, index) => {
      const value = valueAt(point);
      if (value != null) svg += `<circle class="usage-chart-dot" cx="${x(index)}" cy="${y(value)}" r="${points.length > 40 ? 2 : 3}"/>`;
    });
    svg += '<line class="usage-chart-cursor" style="display:none"/><circle class="usage-chart-selected" r="5" style="display:none"/></svg>';
    container.innerHTML = svg;
    chartGeometry = { x, y, left, right, top, bottom };
    if (selectedIndex == null) selectedIndex = points.findLastIndex((point) => valueAt(point) != null);
    showPoint(selectedIndex);
  }

  function feedback(message, error = false) {
    $('usageFeedback').textContent = message;
    $('usageFeedback').classList.toggle('error', error);
  }

  async function load() {
    const start = $('usageStart').value, end = $('usageEnd').value;
    if (!start || !end || start > end || end > today() || (Date.parse(end) - Date.parse(start)) / 86400000 > 365) {
      feedback('请选择有效日期：开始不晚于结束，最多 366 天，结束不晚于今天。', true);
      return;
    }
    controller?.abort();
    const request = new AbortController();
    controller = request;
    data = null;
    selectedIndex = null;
    chartGeometry = null;
    showMetrics();
    $('usageChart').innerHTML = '<p class="usage-chart-empty">正在读取用量趋势…</p>';
    $('usageChart').setAttribute('aria-busy', 'true');
    $('usagePointReadout').textContent = '';
    $('usageHistoryNote').textContent = '';
    feedback('');
    try {
      const response = await fetch(`/api/llm/configs/${config.id}/usage?${new URLSearchParams({ start, end })}`, { signal: request.signal });
      const body = await response.json().catch(() => ({}));
      if (!response.ok) throw new Error(response.status === 404 ? '用量接口尚未启用或模型已被删除，请确认已重启程序并完成数据库升级。' : typeof body.detail === 'string' ? body.detail : `读取失败（${response.status}）`);
      if (controller !== request || !dialog.open) return;
      data = body;
      showMetrics();
      renderChart();
      $('usageHistoryNote').textContent = `${data.recorded_from ? `时间明细始于 ${stamp(data.recorded_from)}。` : '时间明细将从启用后的首次调用开始记录。'}${data.legacy_call_count ? `此前 ${number(data.legacy_call_count)} 次调用仅保留累计值，不计入日期曲线。` : ''}`;
      feedback(`${data.start} 至 ${data.end}，按${data.granularity === 'hour' ? '小时' : '天'}统计。`);
    } catch (error) {
      if (request.signal.aborted || controller !== request) return;
      $('usageChart').innerHTML = '<p class="usage-chart-empty">暂时无法显示曲线</p>';
      feedback(error.message, true);
    } finally {
      if (controller === request) $('usageChart').setAttribute('aria-busy', 'false');
    }
  }

  $('usageMetrics').addEventListener('click', (event) => {
    const key = event.target.closest('[data-usage-metric]')?.dataset.usageMetric;
    if (!key) return;
    metric = metrics.find((item) => item.key === key);
    selectedIndex = null;
    showMetrics();
    renderChart();
    $('usageMetrics').querySelector(`[data-usage-metric="${key}"]`).focus();
  });
  $('usagePeriods').addEventListener('click', (event) => {
    const days = event.target.closest('[data-usage-days]')?.dataset.usageDays;
    if (!days) return;
    selectPeriod(Number(days));
    load();
  });
  $('usageDateForm').addEventListener('submit', (event) => {
    event.preventDefault();
    document.querySelectorAll('[data-usage-days]').forEach((button) => button.setAttribute('aria-pressed', 'false'));
    load();
  });
  $('usageRefresh').addEventListener('click', load);
  function pointFromPointer(event) {
    if (!chartGeometry || !data) return;
    const position = event.clientX - $('usageChart').getBoundingClientRect().left;
    showPoint(Math.round((position - chartGeometry.left) / (chartGeometry.right - chartGeometry.left) * (data.points.length - 1)));
  }
  $('usageChart').addEventListener('pointermove', pointFromPointer);
  $('usageChart').addEventListener('click', pointFromPointer);
  $('usageChart').addEventListener('keydown', (event) => {
    if (!['ArrowLeft', 'ArrowRight', 'Home', 'End'].includes(event.key) || !data || !chartGeometry) return;
    event.preventDefault();
    showPoint(event.key === 'Home' ? 0 : event.key === 'End' ? data.points.length - 1 : (selectedIndex ?? 0) + (event.key === 'ArrowRight' ? 1 : -1));
  });
  new ResizeObserver(() => { if (dialog.open && data) renderChart(); }).observe($('usageChart'));
  dialog.addEventListener('close', () => { controller?.abort(); });
  window.LLMUsage = {
    open(selectedConfig) {
      config = selectedConfig;
      metric = metrics[0];
      data = null;
      $('usageDialogTitle').textContent = `用量统计 · ${config.display_name}`;
      $('usageStart').max = $('usageEnd').max = today();
      selectPeriod(7);
      if (!dialog.open) dialog.showModal();
      load();
    },
  };
})();
