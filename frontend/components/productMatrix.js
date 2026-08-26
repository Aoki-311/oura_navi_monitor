import { displayCount } from "../viewModels/formatters.js";
import { moduleMessage } from "./dom.js";

export function renderProductMatrix(container, rows) {
  if (!container) return;
  if (!Array.isArray(rows) || rows.length === 0) {
    container.style.gridTemplateColumns = "";
    container.innerHTML = moduleMessage("この期間の製品データはありません。");
    return;
  }
  const products = [...new Set(rows.map((row) => row.product))];
  const tasks = [...new Set(rows.map((row) => row.taskLabel))];
  const lookup = new Map();
  for (const row of rows) {
    const key = `${row.product}\u0000${row.taskLabel}`;
    if (lookup.has(key)) {
      container.innerHTML = moduleMessage("製品マトリクスの重複データを確認してください。");
      return;
    }
    lookup.set(key, Number(row.count));
  }
  const max = Math.max(1, ...lookup.values());
  container.style.gridTemplateColumns = `minmax(130px,1.25fr) repeat(${tasks.length},minmax(74px,1fr))`;
  container.setAttribute("role", "table");
  container.setAttribute("aria-label", "製品と依頼タスクの件数マトリクス");
  container.innerHTML = [
    '<div class="matrixRow" role="row"><div class="matrixCorner" role="columnheader">製品 × 依頼タスク</div>',
    ...tasks.map((label) => `<div class="matrixHeader" role="columnheader">${escapeHtml(label)}</div>`),
    '</div>',
    ...products.map((product) => [
      `<div class="matrixRow" role="row"><div class="matrixProduct" role="rowheader">${escapeHtml(product)}</div>`,
      ...tasks.map((category) => {
        const value = lookup.get(`${product}\u0000${category}`) ?? 0;
        const alpha = value ? .14 + .76 * value / max : .035;
        return `<div class="matrixCell" role="cell" style="--heat:${alpha}" title="${escapeHtml(product)} / ${escapeHtml(category)}: ${displayCount(value)}" aria-label="${escapeHtml(product)}、${escapeHtml(category)}、${displayCount(value)}件">${value || "-"}</div>`;
      }),
      '</div>',
    ].join("")),
  ].join("");
}

function escapeHtml(value) {
  const node = document.createElement("span");
  node.textContent = String(value ?? "");
  return node.innerHTML;
}
