import { esc } from '../integrations/shared.js';

export const SETTINGS_SECTIONS = [
  { id: 'appearance', title: '外观与显示', group: '个人偏好', description: '调整工作台的阅读体验，保留岗位的完整资料。' },
  { id: 'delivery', title: '投递行为', group: '个人偏好', description: '决定查看详情、点击投递后，如何记录投递状态。' },
  { id: 'models', title: 'AI 模型', group: '接入与处理', description: '管理模型连接，并分别配置任务选择、自动测试与代理。' },
  { id: 'mailbox', title: '邮箱账户', group: '接入与处理', description: '管理账户连接与同步计划；授权码不会回显。' },
  { id: 'processing', title: '解析与筛选', group: '接入与处理', description: '网页岗位与招聘邮件使用各自的处理规则。' },
  { id: 'policies', title: '通知提醒', group: '接入与处理', description: '为每条策略设置提醒条件与接收渠道。' },
  { id: 'usage', title: '用量与诊断', group: '使用情况', description: '按模型和日期查看用量，以及最近的测试与同步结果。' },
];
export const SETTINGS_PANELS = { models: ['list', 'routing', 'health', 'proxy'], processing: ['web', 'email'] };
const TARGETS = [
  ['主题与颜色', '外观 深色 浅色 系统 种子色', 'appearance', '', '[name="theme_mode"]'],
  ['密度与动画', '显示 舒适 紧凑 减少动画', 'appearance', '', '[name="density"]'],
  ['岗位字段与诗词', '字段 薪资 地点 宋词 首页', 'appearance', '', '[name="poetry_preset"]'],
  ['投递状态记录', '行为 详情 自动投递 点击 已投递', 'delivery', '', '[name="on_detail"]'],
  ['模型连接与密钥', '模型 api key 密钥 服务 地址', 'models', 'list', '[data-action="new-model"]'],
  ['任务分配与并行度', '路由 自动 默认 并发 并行 邮件模型', 'models', 'routing', '[name="auto_select_enabled"]'],
  ['自动测试', '健康 检测 间隔 测试 周期', 'models', 'health', '[data-health-form] [name="enabled"]'],
  ['网络代理', '代理 proxy socks 网络 端口', 'models', 'proxy', '[name="proxy_url"]'],
  ['邮箱连接与同步计划', '邮箱 账户 qq 163 imap 授权码 密码 同步 回看', 'mailbox', '', '[data-action="new-account"]'],
  ['网页岗位提示词', '提示词 prompt 模板 解析 提取重点 补充要求', 'processing', 'web', '[data-prompt-template]'],
  ['邮件筛选规则', '正则 过滤 预筛 关键词 样例', 'processing', 'email', '[data-filter-regex]'],
  ['提醒条件与渠道', '通知 策略 push ntfy bark 桌面 截止时间', 'policies', '', '[data-action="new-policy"]'],
  ['模型用量与运行结果', '用量 token 调用 统计 诊断 失败 耗时', 'usage', '', '[data-usage-config]'],
];

export function findSettings(query) {
  const words = query.trim().toLowerCase().split(/\s+/).filter(Boolean);
  return words.length ? TARGETS.filter(([title, keywords]) => words.every((word) => `${title} ${keywords}`.toLowerCase().includes(word))) : [];
}

export function settingsShell(contents) {
  let previousGroup = '';
  const navigation = SETTINGS_SECTIONS.map((section) => {
    const group = section.group !== previousGroup ? `<p class="wb-settings-group">${section.group}</p>` : '';
    previousGroup = section.group;
    return `${group}<button type="button" data-setting-tab="${section.id}"><span>${section.title}</span><i data-section-dirty="${section.id}" hidden aria-label="有未保存修改">•</i></button>`;
  }).join('');
  return `<div class="wb-integrations wb-settings" data-settings-view>
    <header class="wb-settings-heading"><div><h1>设置</h1><p>按功能管理，每项单独保存。</p></div><button class="wb-button" type="button" data-action="reload-all">刷新设置</button></header>
    <div class="wb-settings-layout">
      <aside class="wb-settings-sidebar" aria-label="设置目录">
        <label class="wb-settings-search">搜索设置<input type="search" data-settings-search placeholder="代理、提示词、授权码…" autocomplete="off" aria-controls="wb-settings-results"></label>
        <div id="wb-settings-results" class="wb-settings-results" data-settings-results hidden></div>
        <label class="wb-settings-mobile-select">功能板块<select data-settings-select>${SETTINGS_SECTIONS.map((section) => `<option value="${section.id}">${section.title}</option>`).join('')}</select></label>
        <nav class="wb-settings-nav" aria-label="设置功能">${navigation}</nav>
        <p class="wb-settings-draft-note" data-settings-draft-status role="status">切换板块保留草稿；保存后生效。</p>
      </aside>
      <div class="wb-settings-content" data-settings-scroll tabindex="-1">
        <header class="wb-settings-page-head"><p class="wb-eyebrow" data-settings-group></p><h2 data-settings-title></h2><p data-settings-description></p></header>
        ${SETTINGS_SECTIONS.map((section) => `<section data-setting-panel="${section.id}" aria-label="${esc(section.title)}" hidden>${contents[section.id] || ''}</section>`).join('')}
      </div>
    </div>${contents.dialogs || ''}</div>`;
}

export function panelTabs(section, entries) {
  return `<nav class="wb-settings-panel-tabs" aria-label="${section === 'models' ? '模型配置' : '处理规则'}">${entries.map(([id, title]) => `<button class="wb-button" type="button" data-settings-panel="${id}" data-settings-section="${section}">${title}</button>`).join('')}</nav>`;
}
