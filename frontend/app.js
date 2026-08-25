import { createExportJob, isCancellation } from "./api/client.js";
import { destroyAllCharts } from "./components/charts.js";
import { OverviewPage } from "./pages/overviewPage.js";
import { UserAnalysisPage } from "./pages/userAnalysisPage.js";
import { UserManagementPage } from "./pages/userManagementPage.js";

const root = document.querySelector("#pageRoot");
const preset = document.querySelector("#analysisPreset");
const presetControl = document.querySelector("#presetControl");
const exportButton = document.querySelector("#exportButton");
const toastElement = document.querySelector("#toast");
const validPresets = new Set(["today", "last_7d", "last_14d", "last_30d", "last_60d", "all"]);
let toastTimer = 0;
let renderGeneration = 0;
let renderController = null;
let exportController = null;

function stateFromUrl() {
  const params = new URLSearchParams(window.location.search);
  const page = ["overview", "user", "management"].includes(params.get("page")) ? params.get("page") : "overview";
  const selectedPreset = validPresets.has(params.get("preset")) ? params.get("preset") : "last_7d";
  return {
    page,
    roster: page === "user" || page === "management" ? (params.get("roster") || "") : "",
    area: page === "overview" ? (params.get("area") || "") : "",
    preset: selectedPreset,
  };
}

function dashboardUrl(state) {
  const params = new URLSearchParams();
  if (state.page !== "overview") params.set("page", state.page);
  if (state.roster && (state.page === "user" || state.page === "management")) params.set("roster", state.roster);
  if (state.area && state.page === "overview") params.set("area", state.area);
  if (state.preset !== "last_7d") params.set("preset", state.preset);
  const query = params.toString();
  return `/dashboard${query ? `?${query}` : ""}`;
}

function writeState(state, { replace = false } = {}) {
  window.history[replace ? "replaceState" : "pushState"]({}, "", dashboardUrl(state));
}

function navigate(page, values = {}, options = {}) {
  const current = stateFromUrl();
  const next = {
    page,
    preset: values.preset ?? current.preset,
    roster: values.roster ?? ((page === current.page && (page === "user" || page === "management")) ? current.roster : ""),
    area: values.area ?? ((page === current.page && page === "overview") ? current.area : ""),
  };
  writeState(next, options);
  if (options.render !== false) render({ focusMain: true });
}

function clearManagementRoster() {
  const state = stateFromUrl();
  if (state.page === "management" && state.roster) writeState({ ...state, roster: "" }, { replace: true });
}

function toast(message, type = "info") {
  window.clearTimeout(toastTimer);
  toastElement.textContent = String(message || "");
  toastElement.dataset.type = type;
  toastElement.setAttribute("role", type === "error" ? "alert" : "status");
  toastElement.hidden = false;
  toastTimer = window.setTimeout(() => { toastElement.hidden = true; }, 4200);
}

function pageTitle(page) {
  return { overview: "全体サマリー", user: "ユーザー分析", management: "ユーザー管理" }[page];
}

async function render({ focusMain = false } = {}) {
  renderController?.abort();
  exportController?.abort();
  exportController = null;
  exportButton.disabled = false;
  exportButton.removeAttribute("aria-busy");
  const controller = new AbortController();
  renderController = controller;
  const generation = ++renderGeneration;
  const state = stateFromUrl();
  const isCurrent = () => generation === renderGeneration && !controller.signal.aborted;
  preset.value = state.preset;
  document.title = `${pageTitle(state.page)} | OurA Navi User Analytics`;
  document.querySelectorAll(".mainNav [data-page]").forEach((button) => {
    const active = button.dataset.page === state.page;
    button.classList.toggle("isActive", active);
    if (active) button.setAttribute("aria-current", "page"); else button.removeAttribute("aria-current");
  });
  const showPeriod = state.page !== "management";
  presetControl.hidden = !showPeriod;
  exportButton.hidden = !showPeriod;
  destroyAllCharts();
  root.innerHTML = "";
  const shared = {
    navigate,
    toast,
    state,
    getPreset: () => state.preset,
    signal: controller.signal,
    isCurrent,
    setArea: (area) => navigate("overview", { preset: state.preset, area }),
    clearManagementRoster,
  };
  try {
    if (state.page === "overview") await new OverviewPage(root, shared).load();
    else if (state.page === "user") await new UserAnalysisPage(root, { ...shared, rosterId: state.roster }).load();
    else await new UserManagementPage(root, { ...shared, rosterId: state.roster }).load();
  } catch (error) {
    if (!isCancellation(error) && isCurrent()) toast(error?.message || "画面を表示できませんでした。", "error");
  }
  if (focusMain && isCurrent()) root.focus({ preventScroll: true });
}

document.querySelectorAll(".mainNav [data-page]").forEach((button) => button.addEventListener("click", () => navigate(button.dataset.page, { roster: "", area: "" })));
document.querySelector("#refreshButton").addEventListener("click", () => render());
preset.addEventListener("change", () => {
  const state = stateFromUrl();
  navigate(state.page, { roster: state.roster, area: state.area, preset: preset.value }, { replace: true });
});
window.addEventListener("popstate", () => render({ focusMain: true }));

exportButton.addEventListener("click", async () => {
  const state = stateFromUrl();
  if (state.page === "user" && !state.roster) { toast("先にユーザーを選択してください。", "error"); return; }
  exportController?.abort();
  const controller = new AbortController();
  exportController = controller;
  exportButton.disabled = true;
  exportButton.setAttribute("aria-busy", "true");
  try {
    const job = await createExportJob({
      kind: state.page === "user" ? "user_detail" : "users",
      rosterId: state.roster,
      preset: state.preset,
      areaKey: state.area,
    }, { signal: controller.signal });
    if (exportController !== controller || controller.signal.aborted) return;
    const link = document.createElement("a");
    link.href = job.downloadUrl;
    link.download = job.filename;
    document.body.appendChild(link);
    link.click();
    link.remove();
    toast("CSVを作成しました。", "success");
  } catch (error) {
    if (!isCancellation(error)) toast(error.message, "error");
  } finally {
    if (exportController === controller) {
      exportController = null;
      exportButton.disabled = false;
      exportButton.removeAttribute("aria-busy");
    }
  }
});

render();
