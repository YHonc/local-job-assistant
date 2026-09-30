from fastapi.testclient import TestClient

from app.main import create_app


def client(tmp_path):
    return TestClient(create_app(f"sqlite:///{tmp_path / 'ui.db'}", create_tables=True))


def test_ui_pages_assets_and_health(tmp_path):
    with client(tmp_path) as test_client:
        for path, content_type in [
            ('/', 'text/html'),
            ('/workbench', 'text/html'),
            ('/legacy/tracker', 'text/html'),
            ('/settings', 'text/html'),
            ('/events', 'text/html'),
            ('/notifications', 'text/html'),
            ('/static/app.css', 'text/css'),
            ('/static/app.js', 'text/javascript'),
            ('/static/js/shared/api.js', 'text/javascript'),
            ('/static/js/shared/dom.js', 'text/javascript'),
            ('/static/js/pages/settings.js', 'text/javascript'),
            ('/static/js/pages/settings-integrations.js', 'text/javascript'),
            ('/static/js/pages/usage-charts.js', 'text/javascript'),
            ('/static/js/pages/events.js', 'text/javascript'),
            ('/static/js/pages/notifications.js', 'text/javascript'),
            ('/static/js/pages/job-detail.js', 'text/javascript'),
            ('/static/js/pages/tracker.js', 'text/javascript'),
        ]:
            response = test_client.get(path)
            assert response.status_code == 200
            assert content_type in response.headers['content-type']
        home = test_client.get('/', follow_redirects=False)
        assert home.status_code == 307
        assert home.headers['location'] == '/workbench'
        assert home.headers['cache-control'] == 'no-store'
        assert 'id="workbenchApp"' in test_client.get('/').text
        assert test_client.get('/?edit=42', follow_redirects=False).headers['location'] == '/legacy/tracker?edit=42'
        assert test_client.get('/?view=mail&email_id=42', follow_redirects=False).headers['location'] == '/workbench?view=mail&email_id=42'
        page = test_client.get('/legacy/tracker').text
        assert '新增岗位' in page and '管理公司' in page and '还没有公司' in page
        for field_id in ('description', 'sourceWebsite', 'originalTitle', 'jobCategory', 'publishedAt', 'companyAliases', 'companyNotes'):
            assert f'id="{field_id}"' in page
        assert 'id="categoryFilter"' in page and '所有岗位类别' in page
        settings = test_client.get('/settings').text
        assert 'theme' in settings and 'density' in settings and '保存偏好' in settings
        assert 'id="llmConfigForm"' in settings
        assert 'id="testLLMConfig"' not in settings
        assert '测试已保存配置（会调用一次）' not in settings
        assert 'SenseNova' in settings and '<code>openai</code>' in settings
        assert 'API 格式（点击选择）' in settings and 'combo-chevron' in settings
        assert 'type="password"' in settings
        assert '操作系统凭据库' in settings
        assert 'href="#"' not in page and 'href="#"' not in settings
        assert 'aria-current="page"' in page and 'aria-current="page"' in settings
        assert 'href="/events"' in page and 'href="/notifications"' in page
        assert 'id="emailAccountForm"' in settings
        assert 'id="manualEmailForm"' in settings and 'id="emailRegex"' in settings
        assert 'id="saveEmailFilter"' in settings
        assert 'id="notificationPolicyForm"' in settings
        assert settings.count('<details class="setting-row">') == 4
        assert 'class="setting-summary"' in settings
        assert '04 个设置组' in settings
        common_javascript = test_client.get('/static/app.js').text
        tracker_javascript = test_client.get('/static/js/pages/tracker.js').text
        settings_javascript = test_client.get('/static/js/pages/settings.js').text
        api_javascript = test_client.get('/static/js/shared/api.js').text
        javascript = '\n'.join((common_javascript, tracker_javascript, settings_javascript, api_javascript))
        assert 'function renderJobs()' in tracker_javascript
        assert 'function loadLLMConfig()' in settings_javascript
        assert 'function renderJobs()' not in common_javascript
        assert 'function loadLLMConfig()' not in common_javascript
        assert 'function loadLLMConfig()' not in tracker_javascript
        assert 'function renderJobs()' not in settings_javascript
        assert 'app.ready = loadSettings();' in common_javascript
        assert 'app.ready.then(loadJobs);' in tracker_javascript
        assert 'app.ready.then(loadLLMConfig);' in settings_javascript
        for document, page_script in ((page, 'tracker.js'), (settings, 'settings.js')):
            assert document.index('/static/js/shared/api.js') < document.index('/static/app.js')
            assert document.index('/static/app.js') < document.index(f'/static/js/pages/{page_script}')
        usage_javascript = test_client.get('/static/js/pages/usage-charts.js').text
        assert "defaultPreferences = { theme: 'light', density: 'comfortable', poetry_preset: 'random' }" in javascript
        assert 'const allowedPreferences' in javascript
        assert 'window.preferences = preferences;' in javascript
        assert "theme: $('#theme').value" in javascript
        assert "density: $('#density').value" in javascript
        assert "if (!$('#settingsForm')) return" not in javascript
        assert "poetry_preset: '#poetryPreset'" in javascript
        assert "api('/llm/configs')" in javascript and "api('/llm/routing')" in javascript
        assert "configs.filter((config) => config.enabled)" in javascript
        assert "data-llm-edit" in javascript and "data-llm-toggle" in javascript and "data-llm-delete" in javascript and "data-llm-test" in javascript
        assert "data-llm-usage" in javascript and "openUsageDialog" in javascript and "formatTokens" in javascript
        assert 'id="usageDialog"' in settings and 'id="usageMetrics"' in settings
        assert 'total_prompt_tokens' in usage_javascript and 'total_completion_tokens' in usage_javascript and 'last_used_at' in usage_javascript
        assert "tag-toggle" in javascript and "点击暂停此模型" in javascript
        assert 'const PROTOCOLS = {' in javascript and 'function providerValue()' in javascript
        assert "combo-check" in javascript and 'data-combo-custom="1"' in javascript
        assert "provider: providerValue()" in javascript and "setProviderField(config?.provider || 'openai', false)" in javascript
        assert "stage-${statusStages[status]}" in javascript and "String(index + 1).padStart(2, '0')" in javascript
        assert 'llm_auth_failed' in javascript
        assert "payload.api_key = apiKey" in javascript
        assert 'function setupSidebar()' in javascript
        assert 'function setupSettingsAccordion()' in javascript
        assert 'class="job-place"' in javascript and 'class="job-type"' in javascript
        assert "job.job_category === category" in javascript
        assert "job_category: $('#jobCategory').value || null" in javascript
        assert "published_at: $('#publishedAt').value || null" in javascript
        assert 'stage-${statusStages[status]}' in javascript
        assert '<h1>岗位轨道</h1>' in page
        assert '安静、清楚的求职资料夹' not in page and '安静、清楚的求职资料夹' not in javascript
        assert "method: 'DELETE'" in javascript and "await loadLLMConfig();" in javascript
        assert "api('/llm/configs', { method: 'DELETE' })" not in javascript
        assert 'localStorage' not in javascript
        assert 'aria-labelledby="dialogTitle"' in page
        assert 'aria-labelledby="companyDialogTitle"' in page
        stylesheet = test_client.get('/static/app.css').text
        assert '--sidebar-bg' in stylesheet and '--accent-on-color' in stylesheet
        assert ':root[data-theme="dark"]' in stylesheet and ':root[data-density="compact"]' in stylesheet
        assert '[hidden]{display:none!important}' in stylesheet
        assert test_client.get('/api/health').json() == {'status': 'ok'}
        events = test_client.get('/events').text
        notifications = test_client.get('/notifications').text
        assert 'id="eventList"' in events and '<h1>招聘事件</h1>' in events
        assert 'id="notificationList"' in notifications and '<h1>通知中心</h1>' in notifications
        for document in (page, settings, events, notifications):
            assert 'id="appSidebar"' in document
            assert 'data-collapsed="false"' in document
            assert 'data-sidebar-toggle' in document
            assert 'aria-controls="primaryNav"' in document
            assert document.count('class="nav-icon"') == 4
        assert '.sidebar[data-collapsed="true"]' in stylesheet
        assert '.setting-row[open]' in stylesheet
        assert 'grid-template-columns:22px minmax(190px,1.5fr)' in stylesheet
        assert '.sidebar[data-collapsed="true"] .brand-name' in stylesheet
        assert 'grid-template-areas:"select primary place type status added"' in stylesheet
        assert '.job-table-head{display:flex;align-items:center;flex-wrap:wrap' in stylesheet
        assert '.job-card{grid-template-columns:22px minmax(0,1fr) minmax(140px,.6fr);grid-template-areas:"select primary status" "place place type" "added added added"' in stylesheet
        assert '.job-table-head .job-title-header{display:none}' in stylesheet
        assert '.location-menu-panel{left:0;right:auto;width:min(280px,calc(100vw - 100px));max-width:calc(100vw - 100px)}' in stylesheet
        assert 'grid-template-columns:22px minmax(0,1.2fr) minmax(120px,1fr)' not in stylesheet
        assert '.card-actions{justify-content:flex-start}' not in stylesheet
        assert 'grid-template-columns:22px minmax(0,1fr) auto' in stylesheet
        assert 'grid-template-areas:"select primary primary" "place place type" "status status added"' in stylesheet
        assert "title: '岗位 / 公司'" in javascript and "status: '当前状态'" in javascript
        assert '当前${current}，点击切换为${target}' in javascript and "direction === 'default'" in javascript
        assert "localeCompare(right, 'zh-CN')" in javascript and "(!left ? 1 : -1)" in javascript
        assert '--primary-bg' in stylesheet and ':active' in stylesheet and ':focus-visible' in stylesheet
        assert 'id="selectAllJobs"' in page and 'id="bulkActions"' in page and 'id="selectedCount"' in page
        assert 'id="bulkStatus"' in page and 'id="applyBulkStatus"' in page and 'id="clearSelection"' in page
        assert 'selectedApplications' not in javascript and '/applications/bulk-status' in javascript
        assert 'selectedJobs' in javascript and 'locationFilterActive' in javascript
        assert 'locationMenu' in page and 'locationOptions' in page
        assert 'class="location-menu-panel"' in page
        assert '.location-menu-panel{left:0;right:auto;width:min(280px,calc(100vw - 100px));max-width:calc(100vw - 100px)}' in stylesheet
        assert 'data-sort="title"' in page
        assert '岗位 / 公司' in page
        assert 'data-sort="location"' in page
        assert '.location-menu-panel' in stylesheet and 'details>div' not in stylesheet
        detail = test_client.get('/jobs/1').text
        assert '岗位发布时间' in detail and '/static/js/pages/job-detail.js' in detail
        assert "api('/jobs/bulk-delete'" in javascript
        assert 'data-edit' not in javascript and 'data-delete' not in javascript
        assert 'data-quick-status' in javascript and 'data-quick-type' in javascript and 'data-status-filter' in javascript
        # 卡片化设置页：模型池 / 邮箱 / 通知策略 + 弹窗 + 加载态 + 事件中文化
        assert 'id="llmCardGrid"' in settings and 'id="emailCardGrid"' in settings and 'id="policyCardGrid"' in settings
        assert 'id="llmConfigDialog"' in settings and 'id="emailAccountDialog"' in settings
        assert 'id="manualEmailDialog"' in settings and 'id="policyDialog"' in settings
        assert 'id="llmCardGrid"' in settings and 'class="routing-bar"' in settings
        assert '同时调用数量' in settings and '保存路由' in settings and '手动添加指定邮件' in settings
        assert 'id="llmEmailModel"' in settings and '邮件解析模型' in settings
        assert 'email_config_id: $(\'#llmEmailModel\').value ? Number($(\'#llmEmailModel\').value) : null' in javascript
        assert 'class="regex-row"' in settings and 'id="validateRegex"' in settings
        assert 'renderLLMCards' in javascript and 'llmTestResults' in javascript and 'test-result' in javascript
        integrations = test_client.get('/static/js/pages/settings-integrations.js').text
        assert 'setButtonLoading' in integrations and 'spinner' in integrations and 'test-result' in integrations
        assert '/notification-policies' in integrations and '/email-messages/manual' in integrations
        assert 'eventTypeLabels' in integrations
        events_page = events
        assert '收到回复' in events_page and '招聘营销' in events_page and 'value="INTERVIEW"' in events_page
        assert 'id="emailDialog"' in events_page and 'id="deleteEmail"' in events_page
        events_js = test_client.get('/static/js/pages/events.js').text
        assert 'openEmailDialog' in events_js and 'data-email-id' in events_js
        assert 'data-view-email' in events_js
        assert '/email-messages/${emailId}' in events_js and "method: 'DELETE'" in events_js
        assert 'eventTypeLabels' in events_js
        for selector in ('.card-grid', '.new-card', '.stack-card', '.test-result.ok', '.test-result.fail',
                         '.routing-bar', '.routing-save', '.spinner', '.is-loading', '.filter-rule-card', '.event-legend',
                         '.workspace>.feedback'):
            assert selector in stylesheet, selector
        for document in (page, settings, events, notifications):
            assert document.count('data-sidebar-toggle') == 1
            assert '<button class="brand"' in document
            assert '工作区 /' not in document
            assert 'side-note' not in document
        for token in ('--canvas:#f7f6f3','--paper:#fff','--ink:#37352f','--muted:#787774','--line:#e9e9e7','--hover:#efedea','--active:#e3e1db','--accent:#2eaadc','150ms','prefers-reduced-motion'):
            assert token in stylesheet
        for forbidden in ('linear-gradient','radial-gradient','translate','scale(','border-left: 3px','box-shadow: inset 3px'):
            assert forbidden not in stylesheet
        for selector in ('.event-card', '.event-card-head', '.event-facts', '.association-controls',
                         '.notification-row', '.notification-title', '.delivery-dot', '.delivery-dot.sent',
                         '.delivery-dot.failed', '.delivery-error', '.account-row', '.account-actions', '.tag'):
            assert selector in stylesheet


def test_company_crud_conflict_is_visible(tmp_path):
    with client(tmp_path) as test_client:
        company = test_client.post('/api/companies', json={'name': '测试公司'}).json()
        job = test_client.post('/api/jobs', json={'company_id': company['id'], 'title': '数据分析实习'}).json()
        response = test_client.delete(f"/api/companies/{company['id']}")
        assert response.status_code == 409
        assert 'associated jobs' in response.json()['detail']
        assert test_client.delete(f"/api/jobs/{job['id']}").status_code == 204
        assert test_client.delete(f"/api/companies/{company['id']}").status_code == 204
