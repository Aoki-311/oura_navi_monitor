import { requestDateRange } from "../components/dateRangeControl.js";

const DEFAULT_TIMEOUT_MS = 18000;

export class ApiError extends Error {
  constructor(message, { status = 0, code = "request_failed" } = {}) {
    super(message);
    this.name = "ApiError";
    this.status = status;
    this.code = code;
  }
}

function detailParts(detail) {
  if (typeof detail === "string") return { code: "", message: detail };
  if (detail && typeof detail === "object" && !Array.isArray(detail)) {
    return {
      code: typeof detail.code === "string" ? detail.code : "",
      message: typeof detail.message === "string" ? detail.message : "",
    };
  }
  return { code: "", message: "" };
}

function localizedError(status, detail) {
  const parsed = detailParts(detail);
  const byCode = {
    user_not_found: "対象ユーザーが見つかりません。",
    update_conflict: "別の管理者が先に更新しました。最新の内容を読み直してください。",
    label_in_use: "使用中のラベルは削除できません。",
    duplicate_email: "同じメールアドレスのユーザーが既に登録されています。",
    duplicate_label: "同じ名前のラベルが既に登録されています。",
    bound_email: "LCSと連携済みのメールアドレスは通常編集できません。",
    invalid_roster_value: "名簿項目の選択内容を確認してください。",
    scope_policy_conflict: "分析対象ポリシーが更新されました。画面を再読込してから、もう一度確認してください。",
    readback_conflict: "変更は受付済みですが、保存結果を確認できません。",
  };
  if (parsed.code && byCode[parsed.code]) return new ApiError(byCode[parsed.code], { status, code: parsed.code });
  if (status === 401 || status === 403) return new ApiError("アクセス権限を確認できませんでした。", { status, code: "unauthorized" });
  if (status === 404) return new ApiError("対象データが見つかりません。", { status, code: "not_found" });
  if (status === 409) return new ApiError(parsed.message || "他の更新と競合しました。再読込してください。", { status, code: parsed.code || "conflict" });
  if (status === 422) return new ApiError(parsed.message || "入力内容を確認してください。", { status, code: parsed.code || "invalid_input" });
  if (status >= 500) return new ApiError("データを読み込めませんでした。時間をおいて再度お試しください。", { status, code: "server_error" });
  return new ApiError("通信に失敗しました。", { status, code: parsed.code || "request_failed" });
}

function requestSignal(externalSignal, timeoutMs) {
  const controller = new AbortController();
  let timedOut = false;
  const abortFromExternal = () => controller.abort(externalSignal?.reason);
  if (externalSignal?.aborted) abortFromExternal();
  else externalSignal?.addEventListener("abort", abortFromExternal, { once: true });
  const timer = window.setTimeout(() => {
    timedOut = true;
    controller.abort();
  }, timeoutMs);
  return {
    signal: controller.signal,
    timedOut: () => timedOut,
    cleanup: () => {
      window.clearTimeout(timer);
      externalSignal?.removeEventListener("abort", abortFromExternal);
    },
  };
}

async function responseDetail(response) {
  const contentType = response.headers.get("content-type") || "";
  if (!contentType.includes("application/json")) return null;
  try {
    return (await response.json())?.detail ?? null;
  } catch (_error) {
    return null;
  }
}

async function requestJson(method, path, { params = {}, body, signal, timeoutMs = DEFAULT_TIMEOUT_MS } = {}) {
  const url = new URL(path, window.location.origin);
  Object.entries(params || {}).forEach(([key, value]) => {
    if (value !== undefined && value !== null && String(value) !== "") url.searchParams.set(key, String(value));
  });
  const request = requestSignal(signal, Number(timeoutMs || DEFAULT_TIMEOUT_MS));
  try {
    const response = await fetch(url.toString(), {
      method,
      cache: "no-store",
      credentials: "same-origin",
      signal: request.signal,
      headers: {
        Accept: "application/json",
        ...(body === undefined ? {} : { "Content-Type": "application/json" }),
      },
      ...(body === undefined ? {} : { body: JSON.stringify(body) }),
    });
    if (!response.ok) throw localizedError(response.status, await responseDetail(response));
    if (response.status === 204) return null;
    return await response.json();
  } catch (error) {
    if (error?.name === "AbortError" && request.timedOut()) {
      throw new ApiError("通信がタイムアウトしました。", { status: 408, code: "timeout" });
    }
    throw error;
  } finally {
    request.cleanup();
  }
}

async function requestBlob(path, { params = {}, signal, timeoutMs = DEFAULT_TIMEOUT_MS } = {}) {
  const url = new URL(path, window.location.origin);
  Object.entries(params || {}).forEach(([key, value]) => {
    if (value !== undefined && value !== null && String(value) !== "") url.searchParams.set(key, String(value));
  });
  const allowedPath = /^\/api\/export\/jobs\/[^/]+\/download$/.test(url.pathname);
  if (url.origin !== window.location.origin || !allowedPath) {
    throw new ApiError("CSVのダウンロード先が不正です。", { code: "invalid_download_url" });
  }
  const request = requestSignal(signal, Number(timeoutMs || DEFAULT_TIMEOUT_MS));
  try {
    const response = await fetch(url.toString(), {
      method: "GET",
      cache: "no-store",
      credentials: "same-origin",
      signal: request.signal,
      headers: { Accept: "text/csv" },
    });
    if (!response.ok) throw localizedError(response.status, await responseDetail(response));
    const contentType = response.headers.get("content-type") || "";
    if (!contentType.toLowerCase().includes("text/csv")) {
      throw new ApiError("CSVではない応答を受信しました。", { code: "invalid_csv_response" });
    }
    const blob = await response.blob();
    if (blob.size < 3) throw new ApiError("CSVが空でした。", { code: "empty_csv" });
    return blob;
  } catch (error) {
    if (error?.name === "AbortError" && request.timedOut()) {
      throw new ApiError("通信がタイムアウトしました。", { status: 408, code: "timeout" });
    }
    throw error;
  } finally {
    request.cleanup();
  }
}

export function isCancellation(error) {
  return error?.name === "AbortError";
}

export function timeRangeQuery(preset, asOf) {
  if (preset && typeof preset === "object") return requestDateRange(preset, asOf);
  return {
    preset: preset || "last_7d",
    ...(asOf ? { as_of: asOf } : {}),
  };
}

export const getOverview = (params = {}, options = {}) => requestJson("GET", "/api/analytics/overview", { params, ...options });
export const getEnvironment = (params = {}, options = {}) => requestJson("GET", "/api/analytics/environment", { params, ...options });
export const getUsageTrend = (params = {}, options = {}) => requestJson("GET", "/api/analytics/trend", { params, ...options });
export const getNewsUsageOverview = (params = {}, options = {}) => requestJson("GET", "/api/news-usage/overview", { params, ...options });
export const getNewsUsageUser = (rosterId, params = {}, options = {}) => requestJson("GET", `/api/news-usage/users/${encodeURIComponent(rosterId)}`, { params, ...options });
export const getOverviewUsers = (params = {}, options = {}) => requestJson("GET", "/api/analytics/overview/users", { params, ...options });
export const getUsers = (params = {}, options = {}) => requestJson("GET", "/api/analytics/users", { params, ...options });
export const getRegions = (params = {}, options = {}) => requestJson("GET", "/api/analytics/regions", { params, ...options });
export const getUserDetail = (rosterId, params = {}, options = {}) => requestJson("GET", `/api/analytics/users/${encodeURIComponent(rosterId)}`, { params, ...options });
export const getUserConversations = (params = {}, options = {}) => requestJson("GET", "/api/trace/conversations", { params, ...options });
export const getTraceMessages = (params = {}, options = {}) => requestJson("GET", "/api/trace/messages", { params, ...options });
export const createExportJob = (body = {}, options = {}) => requestJson("POST", "/api/export/jobs", { body, ...options });
export const downloadExportJob = (downloadUrl, options = {}) => requestBlob(downloadUrl, options);
export const deleteExportJob = (jobId, options = {}) => requestJson("DELETE", `/api/export/jobs/${encodeURIComponent(jobId)}`, options);
export const getManagedUsers = (params = {}, options = {}) => requestJson("GET", "/api/admin/users", { params, ...options });
export const getManagementMetadata = (options = {}) => requestJson("GET", "/api/admin/metadata", options);
export const previewManagedUserScope = (body, options = {}) => requestJson("POST", "/api/admin/scope-preview", { body, ...options });
export const createManagedUser = (body, options = {}) => requestJson("POST", "/api/admin/users", { body, ...options });
export const updateManagedUser = (rosterId, body, options = {}) => requestJson("PATCH", `/api/admin/users/${encodeURIComponent(rosterId)}`, { body, ...options });
export const getManagedLabels = (params = {}, options = {}) => requestJson("GET", "/api/admin/labels", { params, ...options });
export const createManagedLabel = (body, options = {}) => requestJson("POST", "/api/admin/labels", { body, ...options });
export const updateManagedLabel = (labelId, body, options = {}) => requestJson("PATCH", `/api/admin/labels/${encodeURIComponent(labelId)}`, { body, ...options });
export const deleteManagedLabel = (labelId, body, options = {}) => requestJson("DELETE", `/api/admin/labels/${encodeURIComponent(labelId)}`, { body, ...options });
