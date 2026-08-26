import { displayCount, displayRate } from "../viewModels/formatters.js";
import { escapeHtml } from "./dom.js";

// This is presentation topology, not a second user-location authority.  The
// roster's areaKey remains the only reporting dimension; these codes only tell
// the SVG which prefecture shapes visually represent each roster area.
const AREA_BY_PREFECTURE_CODE = new Map([
  ...[1, 2, 3, 4, 5, 6, 7].map((code) => [code, "北海道東北"]),
  ...[9, 11].map((code) => [code, "関東A"]),
  ...[8, 12].map((code) => [code, "関東B"]),
  ...[13, 20].map((code) => [code, "首都圏A"]),
  [14, "首都圏B"],
  ...[17, 22, 23].map((code) => [code, "東海北陸"]),
  ...[26, 27, 28].map((code) => [code, "関西"]),
  ...[33, 34, 36, 37, 38, 39].map((code) => [code, "中四国"]),
  ...[40, 41, 42, 43, 44, 45, 46, 47].map((code) => [code, "九州"]),
]);

function addHeadquartersMarker(svg) {
  const namespace = "http://www.w3.org/2000/svg";
  const parent = svg.querySelector(".svg-map") || svg;
  const marker = document.createElementNS(namespace, "g");
  marker.classList.add("mapMarker");
  marker.dataset.areaKey = "本社・虎ノ門";
  marker.innerHTML = '<circle class="mapMarkerHalo" cx="610" cy="654" r="18"></circle><circle class="mapMarkerCore" cx="610" cy="654" r="8"></circle><text x="627" y="649">本社</text><text x="627" y="664">虎ノ門</text>';
  parent.appendChild(marker);
}

function preparePrefectures(container) {
  const svg = container.querySelector("svg");
  if (!svg) throw new Error("日本地図の形式を確認できませんでした");
  svg.classList.add("japanMapSvg");
  svg.setAttribute("aria-label", "日本地域別利用状況");
  svg.querySelectorAll(".prefecture[data-code]").forEach((prefecture) => {
    const title = prefecture.querySelector("title");
    prefecture.dataset.prefectureName = title?.textContent?.split("/")[0]?.trim() || "";
    title?.remove();
    const areaKey = AREA_BY_PREFECTURE_CODE.get(Number(prefecture.dataset.code));
    prefecture.classList.add("mapPrefecture");
    if (areaKey) {
      prefecture.classList.add("mapRegion");
      prefecture.dataset.areaKey = areaKey;
    } else {
      prefecture.classList.add("mapNeutral");
    }
  });
  svg.querySelectorAll("title, desc").forEach((node) => node.remove());
  addHeadquartersMarker(svg);
}

export async function renderJapanMap(container, regions, { selectedAreaKey = "", onSelect, signal } = {}) {
  if (!container) return;
  const response = await fetch("/dashboard-assets/assets/japan-regions.svg", { credentials: "same-origin", signal });
  if (!response.ok) throw new Error("日本地図を読み込めませんでした");
  container.innerHTML = await response.text();
  preparePrefectures(container);
  const byKey = new Map(regions.map((row) => [row.areaKey, row]));
  const tooltip = document.createElement("div");
  tooltip.className = "mapTooltip";
  tooltip.hidden = true;
  container.appendChild(tooltip);
  container.querySelectorAll("[data-area-key]").forEach((shape) => {
    const key = shape.dataset.areaKey;
    const row = byKey.get(key);
    const rate = row?.adoptionRate == null ? 0 : Math.max(0, Math.min(1, Number(row.adoptionRate)));
    const intensity = row ? .12 + .88 * rate : .06;
    const prefecture = shape.dataset.prefectureName || "";
    shape.style.setProperty("--heat", String(intensity));
    shape.classList.toggle("isSelected", key === selectedAreaKey);
    shape.setAttribute("tabindex", "0");
    shape.setAttribute("role", "button");
    shape.setAttribute("aria-pressed", String(key === selectedAreaKey));
    shape.setAttribute("aria-label", row
      ? `${prefecture ? `${prefecture}、` : ""}${row.area}、アクティブ${displayCount(row.activeUsers)}人、質問${displayCount(row.questions)}件、利用率${displayRate(row.adoptionRate)}、再訪率${displayRate(row.returnRate)}`
      : `${key}、データなし`);
    const show = (event) => {
      tooltip.hidden = false;
      tooltip.innerHTML = row
        ? `<strong>${escapeHtml(row.area)}</strong>${prefecture ? `<small>${escapeHtml(prefecture)}</small>` : ""}<span>アクティブ ${displayCount(row.activeUsers)} / ${displayCount(row.rosterUsers)}人</span><span>質問 ${displayCount(row.questions)}件</span><span>利用率 ${displayRate(row.adoptionRate)}</span><span>再訪率 ${displayRate(row.returnRate)}</span>`
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
