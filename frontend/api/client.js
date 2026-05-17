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

export function timeRangeQuery(preset) {
  return { preset: preset || "today" };
}

export function getSystemDashboard(params = {}, options = {}) {
  return getJson("/api/metrics/system-dashboard", params, options);
}

export function getUsers(params = {}, options = {}) {
  return getJson("/api/metrics/users", params, options);
}

export function getUserDetail(userId, params = {}, options = {}) {
  return getJson(`/api/metrics/users/${encodeURIComponent(userId)}`, params, options);
}

export function getTraceMessages(params = {}, options = {}) {
  return getJson("/api/trace/messages", params, options);
}

export function createExportJob(body = {}, options = {}) {
  return postJson("/api/export/jobs", body, options);
}
