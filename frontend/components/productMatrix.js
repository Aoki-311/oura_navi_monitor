import { displayCount } from "../viewModels/formatters.js";

export function renderProductMatrix(container, rows) {
  if (!container) return;
  const products = [...new Set(rows.map((row) => row.product))];
  const categories = [...new Set(rows.map((row) => row.categoryLabel))];
  const lookup = new Map(rows.map((row) => [`${row.product}\u0000${row.categoryLabel}`, Number(row.count)]));
  const max = Math.max(1, ...lookup.values());
  container.style.gridTemplateColumns = `minmax(130px,1.25fr) repeat(${categories.length},minmax(74px,1fr))`;
  container.innerHTML = [
    '<div class="matrixCorner">製品 × 質問タイプ</div>',
    ...categories.map((label) => `<div class="matrixHeader">${escapeHtml(label)}</div>`),
    ...products.flatMap((product) => [
      `<div class="matrixProduct">${escapeHtml(product)}</div>`,
      ...categories.map((category) => {
        const value = lookup.get(`${product}\u0000${category}`) ?? 0;
        const alpha = value ? .14 + .76 * value / max : .035;
        return `<div class="matrixCell" style="--heat:${alpha}" title="${escapeHtml(product)} / ${escapeHtml(category)}: ${displayCount(value)}">${value || "-"}</div>`;
      }),
    ]),
  ].join("");
}

function escapeHtml(value) {
  const node = document.createElement("span");
  node.textContent = String(value ?? "");
  return node.innerHTML;
}
