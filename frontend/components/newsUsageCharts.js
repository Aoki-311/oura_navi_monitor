import { managedChart, chartOptions, chartPalette, doughnutChart } from "./charts.js";

const number = (value) => new Intl.NumberFormat("ja-JP").format(value);

export function newsDashboardTrendChart(canvas, rows) {
  return managedChart(canvas, {
    data: {
      labels: rows.map((row) => row.date),
      datasets: [
        { type: "bar", label: "Tab訪問", data: rows.map((row) => row.tabViews), backgroundColor: "rgba(79,124,255,.72)", borderRadius: 6, borderSkipped: false },
        { type: "line", label: "コンテンツクリック", data: rows.map((row) => row.contentClicks), borderColor: chartPalette.cyan, pointBackgroundColor: chartPalette.cyan, borderWidth: 2, tension: .25, pointRadius: 4 },
      ],
    },
    options: {
      ...chartOptions, interaction: { intersect: false, mode: "index" },
      scales: { ...chartOptions.scales, y: { ...chartOptions.scales.y, grace: "10%" } },
      plugins: { ...chartOptions.plugins, tooltip: { ...chartOptions.plugins.tooltip, callbacks: {
        afterLabel(context) {
          const row = rows[context.dataIndex];
          return context.datasetIndex === 0
            ? [`ニュース ${number(row.newsTabViews)}回`, `学会 ${number(row.societyTabViews)}回`]
            : [`ニュース ${number(row.newsContentClicks)}回`, `学会 ${number(row.societyContentClicks)}回`];
        },
      } } },
    },
  }, { summary: "日別のニュース・学会のTab訪問とコンテンツクリック", headers: ["日付", "Tab訪問", "ニュース訪問", "学会訪問", "コンテンツクリック", "ニュースクリック", "学会クリック"], rows: rows.map((row) => [row.date, row.tabViews, row.newsTabViews, row.societyTabViews, row.contentClicks, row.newsContentClicks, row.societyContentClicks]) });
}

export function newsDashboardShareChart(canvas, totals) {
  return doughnutChart(canvas, [{ label: "ニュース", count: totals.newsContentClicks }, { label: "学会", count: totals.societyContentClicks }], { summary: "ニュースと学会のコンテンツクリック割合" });
}

export function newsCategoryRankingChart(canvas, sourceRows, { society = false } = {}) {
  const rows = [...sourceRows].sort((a, b) => b.clicks - a.clicks || a.label.localeCompare(b.label, "ja-JP"));
  return managedChart(canvas, {
    type: "bar",
    data: { labels: rows.map((row) => row.label), datasets: [{ label: "コンテンツクリック", data: rows.map((row) => row.clicks), backgroundColor: society ? chartPalette.violet : chartPalette.blue, borderRadius: 6 }] },
    options: { ...chartOptions, indexAxis: "y", plugins: { ...chartOptions.plugins, legend: { display: false }, tooltip: { ...chartOptions.plugins.tooltip, callbacks: {
      afterLabel(context) {
        const row = rows[context.dataIndex];
        return society
          ? [...row.sources].sort((a, b) => b.clicks - a.clicks || a.label.localeCompare(b.label, "ja-JP")).map((item) => `${item.label} ${number(item.clicks)}回`)
          : [`国内 ${number(row.domesticClicks)}回`, `海外 ${number(row.overseasClicks)}回`, ...(row.unknownGeographyClicks > 0 ? [`未分類 ${number(row.unknownGeographyClicks)}回`] : [])];
      },
    } } } },
  }, { summary: society ? "学会カテゴリ別コンテンツクリックと学会内訳" : "ニュース分類別コンテンツクリックと国内・海外内訳", headers: society ? ["カテゴリ", "クリック", "学会内訳"] : ["ニュース分類", "クリック", "国内", "海外"], rows: rows.map((row) => society ? [row.label, row.clicks, row.sources.map((source) => `${source.label}: ${source.clicks}`).join(" / ")] : [row.label, row.clicks, row.domesticClicks, row.overseasClicks]) });
}
