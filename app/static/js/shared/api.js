(() => {
  const apiErrorMessages = {
    llm_not_configured: '尚未保存模型配置。',
    llm_configuration_error: '模型配置或系统凭据不可用，请重新保存配置。',
    llm_auth_failed: '模型服务认证失败，请重新保存有效的 API Key。',
    llm_model_not_found: '未找到指定模型或接口，请核对 Model 与 API Base。',
    llm_rate_limited: '模型服务触发限流，请稍后重试。',
    llm_request_rejected: '模型服务拒绝了请求，请核对模型名称和兼容设置。',
    llm_connection_failed: '无法连接模型服务，请检查 API Base 与网络。',
    llm_timeout: '模型服务响应超时，请稍后重试或增加超时时间。',
    llm_invalid_output: '模型已响应，但没有返回可用的结构化 JSON。',
    llm_provider_error: '模型服务请求失败，请先使用“测试已保存配置”定位问题。',
  };
  function readableError(detail, fallback) {
    if (Array.isArray(detail)) return detail.map((item) => item.msg || item.message || JSON.stringify(item)).join('；');
    if (detail && typeof detail === 'object') {
      return apiErrorMessages[detail.code] || (typeof detail.message === 'string' ? detail.message : fallback);
    }
    return detail || fallback;
  }

  async function api(path, options = {}) {
    const response = await fetch(`/api${path}`, { headers: { 'Content-Type': 'application/json' }, ...options });
    if (!response.ok) {
      let payload = {};
      try {
        payload = await response.json();
      } catch (error) {
        // non-JSON error
      }
      const error = new Error(readableError(payload?.detail, `请求失败（${response.status}）`));
      error.status = response.status;
      error.detail = payload?.detail ?? null;
      throw error;
    }
    return response.status === 204 ? null : response.json();
  }
  window.JobAssistant = window.JobAssistant || {};
  window.JobAssistant.api = api;
  window.JobAssistant.readableError = readableError;
})();
