import { displayCount, displayRate } from "../viewModels/formatters.js";
import { escapeHtml } from "./dom.js";

export async function renderJapanMap(container, regions, { selectedAreaKey = "", onSelect, signal } = {}) {
  if (!container) return;
  const response = await fetch("/dashboard-assets/assets/japan-regions.svg", { credentials: "same-origin", signal });
  if (!response.ok) throw new Error("日本地図を読み込めませんでした");
  container.innerHTML = await response.text();
  const byKey = new Map(regions.map((row) => [row.areaKey, row]));
  const max = Math.max(1, ...regions.map((row) => Number(row.activeUsers)));
  const tooltip = document.createElement("div");
  tooltip.className = "mapTooltip";
  tooltip.hidden = true;
  container.appendChild(tooltip);
  container.querySelectorAll("[data-area-key]").forEach((shape) => {
    const key = shape.dataset.areaKey;
    const row = byKey.get(key);
    const intensity = row ? .18 + .82 * Number(row.activeUsers) / max : .08;
    shape.style.setProperty("--heat", String(intensity));
    shape.classList.toggle("isSelected", key === selectedAreaKey);
    shape.setAttribute("tabindex", "0");
    shape.setAttribute("role", "button");
    shape.setAttribute("aria-label", row
      ? `${row.area}、アクティブ${displayCount(row.activeUsers)}人、質問${displayCount(row.questions)}件、利用率${displayRate(row.adoptionRate)}、再訪率${displayRate(row.returnRate)}`
      : `${key}、データなし`);
    const show = (event) => {
      tooltip.hidden = false;
      tooltip.innerHTML = row
        ? `<strong>${escapeHtml(row.area)}</strong><span>アクティブ ${displayCount(row.activeUsers)}人</span><span>質問 ${displayCount(row.questions)}件</span><span>利用率 ${displayRate(row.adoptionRate)}</span><span>再訪率 ${displayRate(row.returnRate)}</span>`
        : `<strong>${escapeHtml(key)}</strong><span>データなし</span>`;
      const bounds = container.getBoundingClientRect();
      const shapeBounds = shape.getBoundingClientRect();
      const pointerX = Number.isFinite(event?.clientX) && event.clientX > 0 ? event.clientX : shapeBounds.left + shapeBounds.width / 2;
      const pointerY = Number.isFinite(event?.clientY) && event.clientY > 0 ? event.clientY : shapeBounds.top;
      tooltip.style.left = `${Math.min(bounds.width - 190, Math.max(8, pointerX - bounds.left + 12))}px`;
      tooltip.style.top = `${Math.max(8, pointerY - bounds.top - 30)}px`;
    };
    shape.addEventListener("mousemove", show);
    shape.addEventListener("mouseenter", show);
    shape.addEventListener("focus", show);
    shape.addEventListener("mouseleave", () => { tooltip.hidden = true; });
    shape.addEventListener("blur", () => { tooltip.hidden = true; });
    shape.addEventListener("click", () => onSelect?.(key));
    shape.addEventListener("keydown", (event) => {
      if (event.key === "Enter" || event.key === " ") {
        event.preventDefault();
        onSelect?.(key);
      }
    });
  });
}
