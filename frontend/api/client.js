const DEFAULT_TIMEOUT_MS = 18000;

export async function getJson(path, params = {}, options = {}) {
  const url = new URL(path, window.location.origin);
  Object.entries(params || {}).forEach(([key, value]) => {
    if (value !== undefined && value !== null && String(value) !== "") {
      url.searchParams.set(key, String(value));
    }
  });

  const controller = new AbortController();
  const timeoutMs = Number(options.timeoutMs || DEFAULT_TIMEOUT_MS);
  const timer = window.setTimeout(() => controller.abort(), timeoutMs);

  try {
    const response = await fetch(url.toString(), {
      credentials: "same-origin",
      signal: controller.signal,
      headers: { Accept: "application/json" },
    });
    if (!response.ok) {
      const body = await response.text().catch(() => "");
      throw new Error(`HTTP ${response.status}${body ? `: ${body.slice(0, 180)}` : ""}`);
    }
    return await response.json();
  } finally {
    window.clearTimeout(timer);
  }
}

export async function postJson(path, body = {}, options = {}) {
  const url = new URL(path, window.location.origin);
  const controller = new AbortController();
  const timeoutMs = Number(options.timeoutMs || DEFAULT_TIMEOUT_MS);
  const timer = window.setTimeout(() => controller.abort(), timeoutMs);

  try {
    const response = await fetch(url.toString(), {
      method: "POST",
      credentials: "same-origin",
      signal: controller.signal,
      headers: {
        Accept: "application/json",
        "Content-Type": "application/json",
      },
      body: JSON.stringify(body || {}),
    });
    if (!response.ok) {
      const text = await response.text().catch(() => "");
      throw new Error(`HTTP ${response.status}${text ? `: ${text.slice(0, 180)}` : ""}`);
    }
    return await response.json();
  } finally {
    window.clearTimeout(timer);
  }
}

async function sendJson(method, path, body = {}, options = {}) {
  const controller = new AbortController();
  const timer = window.setTimeout(() => controller.abort(), Number(options.timeoutMs || DEFAULT_TIMEOUT_MS));
  try {
    const response = await fetch(new URL(path, window.location.origin).toString(), {
      method,
      credentials: "same-origin",
      signal: controller.signal,
      headers: { Accept: "application/json", "Content-Type": "application/json" },
      body: JSON.stringify(body || {}),
    });
    if (!response.ok) {
      const raw = await response.text().catch(() => "");
      let detail = raw;
      try { detail = JSON.parse(raw)?.detail || raw; } catch (_error) { /* use response text */ }
      throw new Error(detail || `HTTP ${response.status}`);
    }
    return response.status === 204 ? null : response.json();
  } finally {
    window.clearTimeout(timer);
  }
}

export function timeRangeQuery(preset) {
  return { preset: preset || "today" };
}

export function getOverview(params = {}, options = {}) {
  return getJson("/api/analytics/overview", params, options);
}

export function getUsers(params = {}, options = {}) {
  return getJson("/api/analytics/users", params, options);
}

export function getRegions(params = {}, options = {}) {
  return getJson("/api/analytics/regions", params, options);
}

export function getUserDetail(rosterId, params = {}, options = {}) {
  return getJson(`/api/analytics/users/${encodeURIComponent(rosterId)}`, params, options);
}

export function getTraceMessages(params = {}, options = {}) {
  return getJson("/api/trace/messages", params, options);
}

export function createExportJob(body = {}, options = {}) {
  return postJson("/api/export/jobs", body, options);
}

export function getManagedUsers(params = {}, options = {}) {
  return getJson("/api/admin/users", params, options);
}

export function createManagedUser(body, options = {}) {
  return sendJson("POST", "/api/admin/users", body, options);
}

export function updateManagedUser(rosterId, body, options = {}) {
  return sendJson("PATCH", `/api/admin/users/${encodeURIComponent(rosterId)}`, body, options);
}

export function getManagedLabels(params = {}, options = {}) {
  return getJson("/api/admin/labels", params, options);
}

export function createManagedLabel(body, options = {}) {
  return sendJson("POST", "/api/admin/labels", body, options);
}

export function updateManagedLabel(labelId, body, options = {}) {
  return sendJson("PATCH", `/api/admin/labels/${encodeURIComponent(labelId)}`, body, options);
}

export function deleteManagedLabel(labelId, options = {}) {
  return sendJson("DELETE", `/api/admin/labels/${encodeURIComponent(labelId)}`, {}, options);
}
