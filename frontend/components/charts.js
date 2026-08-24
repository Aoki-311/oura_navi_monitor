const instances = new Map();

const palette = Object.freeze({
  blue: "#5b7cff",
  cyan: "#27d9d2",
  green: "#23d28f",
  amber: "#ffb340",
  red: "#ff5b74",
  violet: "#8f72ff",
  slate: "#667596",
  grid: "rgba(143, 165, 210, .15)",
  text: "#dce8ff",
});

function chart(canvas, config) {
  if (!canvas || !window.Chart) return null;
  const old = instances.get(canvas.id);
  if (old) old.destroy();
  const instance = new window.Chart(canvas.getContext("2d"), config);
  instances.set(canvas.id, instance);
  return instance;
}

const baseOptions = {
  responsive: true,
  maintainAspectRatio: false,
  animation: { duration: 300 },
  plugins: {
    legend: { labels: { color: palette.text, usePointStyle: true, boxWidth: 8 } },
    tooltip: { backgroundColor: "rgba(8,15,29,.96)", borderColor: "rgba(120,145,195,.35)", borderWidth: 1 },
  },
  scales: {
    x: { ticks: { color: palette.text }, grid: { color: palette.grid } },
    y: { beginAtZero: true, ticks: { color: palette.text }, grid: { color: palette.grid } },
  },
};

export function barChart(canvas, rows, { label = "件数", horizontal = false, color = palette.blue } = {}) {
  return chart(canvas, {
    type: "bar",
    data: { labels: rows.map((row) => row.label), datasets: [{ label, data: rows.map((row) => Number(row.count)), backgroundColor: color, borderRadius: 7 }] },
    options: { ...baseOptions, indexAxis: horizontal ? "y" : "x" },
  });
}

export function doughnutChart(canvas, rows) {
  return chart(canvas, {
    type: "doughnut",
    data: {
      labels: rows.map((row) => row.label),
      datasets: [{ data: rows.map((row) => Number(row.count)), backgroundColor: [palette.green, palette.blue, palette.amber, palette.slate, palette.violet, palette.cyan], borderWidth: 0 }],
    },
    options: { responsive: true, maintainAspectRatio: false, cutout: "68%", plugins: baseOptions.plugins },
  });
}

export function trendChart(canvas, rows, { rateKey = "completeDeliveryRate" } = {}) {
  return chart(canvas, {
    data: {
      labels: rows.map((row) => row.date),
      datasets: [
        { type: "bar", label: "質問数", data: rows.map((row) => Number(row.questions)), backgroundColor: "rgba(91,124,255,.78)", borderRadius: 6, yAxisID: "y" },
        { type: "line", label: "完全交付率", data: rows.map((row) => row[rateKey] == null ? null : Number(row[rateKey]) * 100), borderColor: palette.cyan, backgroundColor: "rgba(39,217,210,.12)", tension: .34, pointRadius: 3, yAxisID: "y1" },
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
  });
}

export function usageTrendChart(canvas, rows) {
  return chart(canvas, {
    data: {
      labels: rows.map((row) => row.date),
      datasets: [
        { type: "bar", label: "質問数", data: rows.map((row) => Number(row.questions)), backgroundColor: "rgba(91,124,255,.75)", borderRadius: 6, yAxisID: "y" },
        { type: "line", label: "利用人数", data: rows.map((row) => Number(row.activeUsers)), borderColor: palette.cyan, tension: .3, pointRadius: 3, yAxisID: "y1" },
      ],
    },
    options: { ...baseOptions, interaction: { intersect: false, mode: "index" }, scales: { ...baseOptions.scales, y1: { beginAtZero: true, position: "right", ticks: { color: palette.text }, grid: { drawOnChartArea: false } } } },
  });
}

export function stackedChart(canvas, rows) {
  const keys = ["high", "middle", "low", "dormant"];
  const labels = { high: "高アクティブ", middle: "中アクティブ", low: "低アクティブ", dormant: "休眠ユーザー" };
  const colors = [palette.green, palette.blue, palette.amber, palette.slate];
  return chart(canvas, {
    type: "bar",
    data: {
      labels: rows.map((row) => row.label),
      datasets: keys.map((key, index) => ({
        label: labels[key],
        data: rows.map((row) => {
          const segment = row.segments.find((item) => item.key === key);
          if (!segment || segment.rate == null) throw new Error(`活性度データの${key}が不正です`);
          return Number(segment.rate) * 100;
        }),
        backgroundColor: colors[index],
      })),
    },
    options: { ...baseOptions, indexAxis: "y", scales: { x: { stacked: true, max: 100, ticks: { color: palette.text, callback: (value) => `${value}%` }, grid: { color: palette.grid } }, y: { stacked: true, ticks: { color: palette.text }, grid: { display: false } } } },
  });
}

export function destroyAllCharts() {
  instances.forEach((instance) => instance.destroy());
  instances.clear();
}
