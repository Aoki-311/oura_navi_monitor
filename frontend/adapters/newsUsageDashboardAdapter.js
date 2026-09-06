const TOTAL_FIELDS = ["tabViews", "newsTabViews", "societyTabViews", "contentClicks", "newsContentClicks", "societyContentClicks", "newsDomesticClicks", "newsOverseasClicks"];

function count(value) {
  if (!Number.isInteger(value) || value < 0) throw new Error("利用状況を取得できませんでした。");
  return value;
}

function text(value) {
  if (typeof value !== "string") throw new Error("利用状況を取得できませんでした。");
  return value;
}

function totals(value, { daily = false } = {}) {
  const fields = daily ? TOTAL_FIELDS.slice(0, 6) : TOTAL_FIELDS;
  const result = Object.fromEntries(fields.map((key) => [key, count(value?.[key])]));
  if (!daily) result.newsUnknownGeographyClicks = count(value.newsUnknownGeographyClicks ?? 0);
  if (result.tabViews !== result.newsTabViews + result.societyTabViews
      || result.contentClicks !== result.newsContentClicks + result.societyContentClicks) {
    throw new Error("利用状況を取得できませんでした。");
  }
  return result;
}

function rows(value, transform) {
  if (!Array.isArray(value)) throw new Error("利用状況を取得できませんでした。");
  return value.map(transform);
}

export function newsUsageDashboardModel(raw, { scope = "global", rosterId = "" } = {}) {
  if (raw?.contractVersion !== "news_usage_dashboard_v1" || raw.scope !== scope
      || (rosterId && raw.rosterId !== rosterId)
      || !["available", "before_measurement", "not_enabled", "unavailable"].includes(raw?.state?.availability)) {
    throw new Error("利用状況を取得できませんでした。");
  }
  const available = raw.state.availability === "available";
  const model = { ...raw, available, state: { ...raw.state } };
  if (!available) return model;
  for (const key of ["windowStart", "windowEnd", "publishedRunId", "rosterFingerprint"]) {
    if (!text(raw[key])) throw new Error("利用状況を取得できませんでした。");
  }
  model.totals = totals(raw.totals);
  model.trend = rows(raw.trend, (row) => ({ date: text(row.date), ...totals(row, { daily: true }) }));
  model.newsCategories = rows(raw.newsCategories, (row) => ({ key: text(row.key), label: text(row.label), clicks: count(row.clicks), domesticClicks: count(row.domesticClicks), overseasClicks: count(row.overseasClicks), unknownGeographyClicks: count(row.unknownGeographyClicks ?? 0) }));
  model.societyCategories = rows(raw.societyCategories, (row) => ({ key: text(row.key), label: text(row.label), clicks: count(row.clicks), sources: rows(row.sources, (source) => ({ key: text(source.key), label: text(source.label), clicks: count(source.clicks) })) }));
  return model;
}
