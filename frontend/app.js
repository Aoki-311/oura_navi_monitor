import { createExportJob } from "./api/client.js";
import { destroyAllCharts } from "./components/charts.js";
import { OverviewPage } from "./pages/overviewPage.js";
import { UserAnalysisPage } from "./pages/userAnalysisPage.js";
import { UserManagementPage } from "./pages/userManagementPage.js";

const root = document.querySelector("#pageRoot");
const preset = document.querySelector("#analysisPreset");
const presetControl = document.querySelector("#presetControl");
const exportButton = document.querySelector("#exportButton");
const toastElement = document.querySelector("#toast");
let toastTimer = 0;

function stateFromUrl() {
  const params = new URLSearchParams(window.location.search);
  const page = ["overview", "user", "management"].includes(params.get("page")) ? params.get("page") : "overview";
  return { page, roster: params.get("roster") || "" };
}

function navigate(page, values = {}) {
  const params = new URLSearchParams();
  if (page !== "overview") params.set("page", page);
  if (values.roster) params.set("roster", values.roster);
  const query = params.toString();
  window.history.pushState({}, "", `/dashboard${query ? `?${query}` : ""}`);
  render();
}

function toast(message, type = "info") {
  window.clearTimeout(toastTimer);
  toastElement.textContent = String(message || "");
  toastElement.dataset.type = type;
  toastElement.hidden = false;
  toastTimer = window.setTimeout(() => { toastElement.hidden = true; }, 4200);
}

async function render() {
  destroyAllCharts();
  const state = stateFromUrl();
  document.querySelectorAll(".mainNav [data-page]").forEach((button) => button.classList.toggle("isActive", button.dataset.page === state.page));
  const showPeriod = state.page !== "management";
  presetControl.hidden = !showPeriod;
  exportButton.hidden = !showPeriod;
  root.innerHTML = "";
  const shared = { navigate, toast, getPreset: () => preset.value };
  if (state.page === "overview") await new OverviewPage(root, shared).load();
  else if (state.page === "user") await new UserAnalysisPage(root, { ...shared, rosterId: state.roster }).load();
  else await new UserManagementPage(root, { ...shared, rosterId: state.roster }).load();
}

document.querySelectorAll(".mainNav [data-page]").forEach((button) => button.addEventListener("click", () => navigate(button.dataset.page)));
document.querySelector("#refreshButton").addEventListener("click", render);
preset.addEventListener("change", render);
window.addEventListener("popstate", render);

exportButton.addEventListener("click", async () => {
  const state = stateFromUrl();
  if (state.page === "user" && !state.roster) { toast("先にユーザーを選択してください", "error"); return; }
  exportButton.disabled = true;
  try {
    const job = await createExportJob({
      kind: state.page === "user" ? "user_detail" : "users",
      rosterId: state.roster,
      preset: preset.value,
    });
    const link = document.createElement("a");
    link.href = job.downloadUrl;
    link.download = job.filename;
    document.body.appendChild(link);
    link.click();
    link.remove();
    toast("CSVを作成しました", "success");
  } catch (error) { toast(error.message, "error"); }
  finally { exportButton.disabled = false; }
});

render();
