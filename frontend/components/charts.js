import { escapeHtml, moduleMessage } from "./dom.js";

const instances = new Map();

const palette = Object.freeze({
  blue: "#4f7cff", cyan: "#24b8ae", green: "#2db77c", amber: "#d99b3d",
  red: "#d96b7a", violet: "#8b7be8", slate: "#64748b",
  grid: "rgba(148, 163, 184, .14)", text: "#cbd5e1", muted: "#7f8da8",
});

const centerTotalPlugin = {
  id: "ouraCenterTotal",
  afterDraw(chartInstance) {
    const { ctx, chartArea } = chartInstance;
    if (!chartArea) return;
    const total = chartInstance.data.datasets[0]?.data.reduce((sum, value) => sum + Number(value || 0), 0) || 0;
    const x = (chartArea.left + chartArea.right) / 2;
    const y = (chartArea.top + chartArea.bottom) / 2;
    ctx.save();
    ctx.textAlign = "center";
    ctx.textBaseline = "middle";
    ctx.fillStyle = palette.text;
    ctx.font = "700 21px system-ui, sans-serif";
    ctx.fillText(new Intl.NumberFormat("ja-JP").format(total), x, y - 7);
    ctx.fillStyle = palette.muted;
    ctx.font = "500 11px system-ui, sans-serif";
    ctx.fillText("合計", x, y + 14);
    ctx.restore();
  },
};

function showChartMessage(canvas, message) {
  if (!canvas?.parentElement) return null;
  canvas.parentElement.innerHTML = moduleMessage(message);
  return null;
}

function dataTable(canvas, headers, rows) {
  const existing = canvas.parentElement?.querySelector(".chartDataTable");
  existing?.remove();
  const table = document.createElement("table");
  table.className = "chartDataTable srOnly";
  table.innerHTML = `<caption>${escapeHtml(canvas.getAttribute("aria-label") || "グラフのデータ")}</caption><thead><tr>${headers.map((value) => `<th>${escapeHtml(value)}</th>`).join("")}</tr></thead><tbody>${rows.map((row) => `<tr>${row.map((value) => `<td>${escapeHtml(value)}</td>`).join("")}</tr>`).join("")}</tbody>`;
  canvas.insertAdjacentElement("afterend", table);
}

function chart(canvas, config, { summary, headers, rows }) {
  if (!canvas) return null;
  if (!window.Chart) return showChartMessage(canvas, "グラフ機能を読み込めませんでした。");
  const old = instances.get(canvas.id);
  if (old) old.destroy();
  canvas.setAttribute("role", "img");
  canvas.setAttribute("aria-label", summary);
  dataTable(canvas, headers, rows);
  const instance = new window.Chart(canvas.getContext("2d"), config);
  instances.set(canvas.id, instance);
  return instance;
}

const reducedMotion = window.matchMedia?.("(prefers-reduced-motion: reduce)")?.matches;
const baseOptions = {
  responsive: true,
  maintainAspectRatio: false,
  animation: { duration: reducedMotion ? 0 : 300 },
  plugins: {
    legend: { labels: { color: palette.text, usePointStyle: true, boxWidth: 8 } },
    tooltip: { backgroundColor: "rgba(8,15,29,.96)", borderColor: "rgba(120,145,195,.35)", borderWidth: 1 },
  },
  scales: {
    x: { ticks: { color: palette.text }, grid: { color: palette.grid } },
    y: { beginAtZero: true, ticks: { color: palette.text }, grid: { color: palette.grid } },
  },
};

function noRows(canvas, rows) {
  if (Array.isArray(rows) && rows.length) return false;
  showChartMessage(canvas, "この期間のデータはありません。");
  return true;
}

export function barChart(canvas, rows, { label = "件数", horizontal = false, color = palette.blue, summary = "件数の棒グラフ" } = {}) {
  if (noRows(canvas, rows)) return null;
  return chart(canvas, {
    type: "bar",
    data: { labels: rows.map((row) => row.label), datasets: [{ label, data: rows.map((row) => row.count), backgroundColor: color, borderRadius: 7 }] },
    options: { ...baseOptions, indexAxis: horizontal ? "y" : "x" },
  }, { summary, headers: ["項目", label], rows: rows.map((row) => [row.label, row.count]) });
}

export function doughnutChart(canvas, rows, { summary = "構成比の円グラフ" } = {}) {
  if (noRows(canvas, rows) || rows.every((row) => Number(row.count) === 0)) return showChartMessage(canvas, "この期間のデータはありません。");
  return chart(canvas, {
    type: "doughnut",
    data: {
      labels: rows.map((row) => row.label),
      datasets: [{ data: rows.map((row) => row.count), backgroundColor: [palette.green, palette.blue, palette.amber, palette.slate, palette.violet, palette.cyan], borderWidth: 0 }],
    },
    options: { responsive: true, maintainAspectRatio: false, cutout: "70%", plugins: baseOptions.plugins, animation: baseOptions.animation },
    plugins: [centerTotalPlugin],
  }, { summary, headers: ["項目", "件数"], rows: rows.map((row) => [row.label, row.count]) });
}

export function trendChart(canvas, rows) {
  if (noRows(canvas, rows)) return null;
  return chart(canvas, {
    data: {
      labels: rows.map((row) => row.date),
      datasets: [
        { type: "bar", label: "質問数", data: rows.map((row) => row.questions), backgroundColor: "rgba(79,124,255,.76)", borderRadius: 6, borderSkipped: false, yAxisID: "y" },
        { type: "line", label: "完全交付率", data: rows.map((row) => row.completeDelivery?.value == null ? null : row.completeDelivery.value * 100), borderColor: palette.cyan, backgroundColor: "rgba(39,217,210,.12)", tension: .34, pointRadius: 3, yAxisID: "y1" },
      ],
    },
    options: {
      ...baseOptions,
      interaction: { intersect: false, mode: "index" },
      scales: {
        y: { beginAtZero: true, ticks: { color: palette.text }, grid: { color: palette.grid } },
        y1: { beginAtZero: true, max: 100, position: "right", ticks: { color: palette.text, callback: (value) => `${value}%` }, grid: { drawOnChartArea: false } },
        x: { ticks: { color: palette.text }, grid: { display: false } },
      },
    },
  }, { summary: "日別の質問数と完全交付率", headers: ["日付", "質問数", "完全交付率"], rows: rows.map((row) => [row.date, row.questions, row.completeDelivery?.value == null ? "未計測" : `${(row.completeDelivery.value * 100).toFixed(1)}%`]) });
}

export function usageTrendChart(canvas, rows) {
  if (noRows(canvas, rows)) return null;
  return chart(canvas, {
    data: {
      labels: rows.map((row) => row.date),
      datasets: [
        { type: "bar", label: "質問数", data: rows.map((row) => row.questions), backgroundColor: "rgba(79,124,255,.72)", borderRadius: 6, borderSkipped: false, yAxisID: "y" },
        { type: "line", label: "利用人数", data: rows.map((row) => row.activeUsers), borderColor: palette.cyan, tension: .3, pointRadius: 3, yAxisID: "y1" },
      ],
    },
    options: { ...baseOptions, interaction: { intersect: false, mode: "index" }, scales: { ...baseOptions.scales, y1: { beginAtZero: true, position: "right", ticks: { color: palette.text }, grid: { drawOnChartArea: false } } } },
  }, { summary: "日別の質問数と利用人数", headers: ["日付", "質問数", "利用人数"], rows: rows.map((row) => [row.date, row.questions, row.activeUsers]) });
}

export function stackedChart(canvas, rows, { summary = "活性度構成の100パーセント積み上げ棒グラフ" } = {}) {
  if (noRows(canvas, rows)) return null;
  const keys = ["high", "middle", "low", "dormant"];
  const labels = { high: "高アクティブ", middle: "中アクティブ", low: "低アクティブ", dormant: "休眠ユーザー" };
  const colors = [palette.green, palette.blue, palette.amber, palette.slate];
  const tableRows = [];
  const datasets = keys.map((key, index) => ({
    label: labels[key],
    data: rows.map((row) => {
      const segment = Array.isArray(row.segments) ? row.segments.find((item) => item.key === key) : null;
      const value = segment?.rate == null ? null : Number(segment.rate) * 100;
      tableRows.push([row.label, labels[key], value == null ? "未計測" : `${value.toFixed(1)}%`]);
      return value;
    }),
    backgroundColor: colors[index],
  }));
  return chart(canvas, {
    type: "bar",
    data: { labels: rows.map((row) => row.label), datasets },
    options: { ...baseOptions, indexAxis: "y", scales: { x: { stacked: true, max: 100, ticks: { color: palette.text, callback: (value) => `${value}%` }, grid: { color: palette.grid } }, y: { stacked: true, ticks: { color: palette.text }, grid: { display: false } } } },
  }, { summary, headers: ["比較対象", "活性度", "構成比"], rows: tableRows });
}

export function destroyAllCharts() {
  instances.forEach((instance) => instance.destroy());
  instances.clear();
}
