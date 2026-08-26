import { escapeHtml } from "./dom.js";

export function positivePage(value) {
  const page = Number.parseInt(String(value || "1"), 10);
  return Number.isInteger(page) && page > 0 ? page : 1;
}

export function paginate(rows, requestedPage, pageSize) {
  const total = rows.length;
  const pageCount = Math.max(1, Math.ceil(total / pageSize));
  const page = Math.min(positivePage(requestedPage), pageCount);
  const start = (page - 1) * pageSize;
  const items = rows.slice(start, start + pageSize);
  return {
    items,
    page,
    pageCount,
    total,
    from: total ? start + 1 : 0,
    to: Math.min(start + pageSize, total),
  };
}

export function paginationMarkup(model, noun = "名") {
  return `<nav class="pagination" aria-label="一覧ページ">
    <span>${model.from.toLocaleString("ja-JP")}–${model.to.toLocaleString("ja-JP")} / ${model.total.toLocaleString("ja-JP")}${escapeHtml(noun)}</span>
    <div>
      <button type="button" class="ghostButton compactButton" data-page-action="previous" ${model.page <= 1 ? "disabled" : ""}>前のページ</button>
      <b>${model.page} / ${model.pageCount}</b>
      <button type="button" class="ghostButton compactButton" data-page-action="next" ${model.page >= model.pageCount ? "disabled" : ""}>次のページ</button>
    </div>
  </nav>`;
}

export function bindPagination(root, model, onPage) {
  root.querySelector('[data-page-action="previous"]')?.addEventListener("click", () => onPage(model.page - 1));
  root.querySelector('[data-page-action="next"]')?.addEventListener("click", () => onPage(model.page + 1));
}

export function compareNullable(left, right, direction = "desc") {
  const missingLeft = left == null || left === "";
  const missingRight = right == null || right === "";
  if (missingLeft !== missingRight) return missingLeft ? 1 : -1;
  if (missingLeft) return 0;
  const result = typeof left === "number" && typeof right === "number"
    ? left - right
    : String(left).localeCompare(String(right), "ja-JP");
  return direction === "asc" ? result : -result;
}

export function bindResponsiveCollection(signal, onChange, breakpoint = 820) {
  const media = window.matchMedia(`(max-width: ${breakpoint}px)`);
  const listener = () => onChange(media.matches);
  media.addEventListener("change", listener);
  const cleanup = () => media.removeEventListener("change", listener);
  if (signal?.aborted) cleanup(); else signal?.addEventListener("abort", cleanup, { once: true });
  return media;
}
