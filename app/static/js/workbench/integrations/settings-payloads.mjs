const PROVIDER_BASES = {
  openai: 'https://api.openai.com/v1',
  anthropic: 'https://api.anthropic.com',
  gemini: 'https://generativelanguage.googleapis.com/v1beta',
};

export function normalizeProbeTimeout(value) {
  const parsed = Number(value);
  if (!Number.isFinite(parsed) || parsed === 0) return 15;
  return Math.max(3, Math.min(30, Math.round(parsed)));
}

export function buildModelProbePayload(values) {
  const provider = String(values.provider || '').trim();
  const payload = {
    provider: provider || null,
    api_base: String(values.api_base || '').trim() || PROVIDER_BASES[provider] || null,
    timeout_seconds: normalizeProbeTimeout(values.timeout_seconds),
  };
  const apiKey = String(values.api_key || '').trim();
  const configId = Number(values.config_id);
  if (values.use_proxy != null) payload.use_proxy = Boolean(values.use_proxy);
  if (apiKey) payload.api_key = apiKey;
  else if (Number.isInteger(configId) && configId > 0) payload.config_id = configId;
  return payload;
}

export function modelProbeRequest(values) {
  return { method: 'POST', body: JSON.stringify(buildModelProbePayload(values)) };
}

export function buildModelSavePayload(values) {
  const apiKey = String(values.api_key || '').trim();
  const clearApiKey = Boolean(values.clear_api_key) && !apiKey;
  const payload = {
    display_name: String(values.display_name || '').trim(),
    provider: String(values.provider || '').trim(),
    model: String(values.model || '').trim(),
    api_base: String(values.api_base || '').trim() || null,
    temperature: Number(values.temperature),
    timeout_seconds: Number(values.timeout_seconds),
    enabled: Boolean(values.enabled),
    use_proxy: Boolean(values.use_proxy),
    clear_api_key: clearApiKey,
  };
  if (apiKey) payload.api_key = apiKey;
  if (values.health_mode) payload.health = { mode: values.health_mode, interval_minutes: Number(values.health_interval || 30) };
  return payload;
}

export function modelSaveRequest(values, id = '') {
  const hasId = Boolean(id);
  return {
    path: hasId ? `/llm/configs/${encodeURIComponent(id)}` : '/llm/configs',
    options: { method: hasId ? 'PUT' : 'POST', body: JSON.stringify(buildModelSavePayload(values)) },
  };
}

export function buildEmailFilterPayload(regex, keywords) {
  return {
    regex: String(regex || '').trim() || null,
    keywords: Array.from(keywords || [], (value) => String(value).trim()).filter(Boolean),
  };
}

export function emailFilterRequest(regex, keywords) {
  return { method: 'PUT', body: JSON.stringify(buildEmailFilterPayload(regex, keywords)) };
}
