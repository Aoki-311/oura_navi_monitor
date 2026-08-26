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
  ...[40, 41, 42, 43, 44, 45, 46].map((code) => [code, "九州"]),
]);
const MAP_ZOOM_LEVELS = Object.freeze([1, 1.25, 1.5, 1.75, 2]);

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
  svg.setAttribute("aria-label", "北海道・本州・四国・九州の地域別利用状況");
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
  return svg;
}

function clamp(value, minimum, maximum) {
  return Math.min(maximum, Math.max(minimum, value));
}

function readViewBox(svg) {
  const source = String(svg.getAttribute("viewBox") || "").trim();
  const values = source.split(/[\s,]+/).map(Number);
  if (values.length !== 4 || values.some((value) => !Number.isFinite(value)) || values[2] <= 0 || values[3] <= 0) {
    throw new Error("日本地図の表示範囲を確認できませんでした");
  }
  return { source, x: values[0], y: values[1], width: values[2], height: values[3] };
}

function viewportFor(base, zoom, centerX, centerY) {
  const width = base.width / zoom;
  const height = base.height / zoom;
  return {
    x: clamp(centerX - width / 2, base.x, base.x + base.width - width),
    y: clamp(centerY - height / 2, base.y, base.y + base.height - height),
    width,
    height,
  };
}

function viewBoxValue(viewport) {
  return [viewport.x, viewport.y, viewport.width, viewport.height]
    .map((value) => String(Number(value.toFixed(3))))
    .join(" ");
}

function installMapViewport(container, svg, hideTooltip) {
  const base = readViewBox(svg);
  let zoomIndex = 0;
  let viewport = { x: base.x, y: base.y, width: base.width, height: base.height };
  let drag = null;
  let suppressClick = false;

  const controls = document.createElement("div");
  controls.className = "mapZoomControls";
  controls.setAttribute("role", "group");
  controls.setAttribute("aria-label", "地図の拡大縮小");
  controls.innerHTML = `
    <button type="button" data-map-zoom-out aria-label="地図を縮小">−</button>
    <output data-map-zoom-level aria-label="現在の地図倍率" aria-live="polite">100%</output>
    <button type="button" data-map-zoom-in aria-label="地図を拡大">＋</button>
    <button type="button" class="mapZoomReset" data-map-zoom-reset>全体表示</button>`;
  container.appendChild(controls);

  const panHint = document.createElement("p");
  panHint.className = "mapPanHint";
  panHint.textContent = "拡大中：地図をドラッグして移動";
  panHint.hidden = true;
  container.appendChild(panHint);

  const zoomOut = controls.querySelector("[data-map-zoom-out]");
  const zoomLevel = controls.querySelector("[data-map-zoom-level]");
  const zoomIn = controls.querySelector("[data-map-zoom-in]");
  const zoomReset = controls.querySelector("[data-map-zoom-reset]");

  const updateControls = () => {
    const zoom = MAP_ZOOM_LEVELS[zoomIndex];
    const isZoomed = zoomIndex > 0;
    zoomLevel.textContent = `${Math.round(zoom * 100)}%`;
    zoomOut.disabled = !isZoomed;
    zoomIn.disabled = zoomIndex === MAP_ZOOM_LEVELS.length - 1;
    zoomReset.disabled = !isZoomed;
    panHint.hidden = !isZoomed;
    container.classList.toggle("isZoomed", isZoomed);
    svg.classList.toggle("isPannable", isZoomed);
  };

  const renderViewport = () => {
    svg.setAttribute("viewBox", zoomIndex === 0 ? base.source : viewBoxValue(viewport));
    hideTooltip();
    updateControls();
  };

  const setZoomIndex = (nextIndex) => {
    const centerX = viewport.x + viewport.width / 2;
    const centerY = viewport.y + viewport.height / 2;
    zoomIndex = clamp(nextIndex, 0, MAP_ZOOM_LEVELS.length - 1);
    viewport = zoomIndex === 0
      ? { x: base.x, y: base.y, width: base.width, height: base.height }
      : viewportFor(base, MAP_ZOOM_LEVELS[zoomIndex], centerX, centerY);
    renderViewport();
  };

  zoomOut.addEventListener("click", () => setZoomIndex(zoomIndex - 1));
  zoomIn.addEventListener("click", () => setZoomIndex(zoomIndex + 1));
  zoomReset.addEventListener("click", () => setZoomIndex(0));

  svg.addEventListener("pointerdown", (event) => {
    if (zoomIndex === 0 || event.button !== 0 || event.isPrimary === false) return;
    const bounds = svg.getBoundingClientRect();
    const scale = Math.min(bounds.width / viewport.width, bounds.height / viewport.height);
    drag = {
      pointerId: event.pointerId,
      startX: event.clientX,
      startY: event.clientY,
      viewport: { ...viewport },
      scale,
      moved: false,
    };
    svg.setPointerCapture(event.pointerId);
    container.classList.add("isDragging");
    hideTooltip();
  });

  svg.addEventListener("pointermove", (event) => {
    if (!drag || event.pointerId !== drag.pointerId) return;
    const deltaX = event.clientX - drag.startX;
    const deltaY = event.clientY - drag.startY;
    if (!drag.moved && Math.hypot(deltaX, deltaY) < 5) return;
    drag.moved = true;
    viewport = {
      ...viewport,
      x: clamp(drag.viewport.x - deltaX / drag.scale, base.x, base.x + base.width - drag.viewport.width),
      y: clamp(drag.viewport.y - deltaY / drag.scale, base.y, base.y + base.height - drag.viewport.height),
    };
    svg.setAttribute("viewBox", viewBoxValue(viewport));
    hideTooltip();
    event.preventDefault();
  });

  const finishDrag = (event, cancelled = false) => {
    if (!drag || event.pointerId !== drag.pointerId) return;
    const moved = drag.moved;
    if (svg.hasPointerCapture(event.pointerId)) svg.releasePointerCapture(event.pointerId);
    drag = null;
    container.classList.remove("isDragging");
    if (moved && !cancelled) {
      suppressClick = true;
      window.setTimeout(() => { suppressClick = false; }, 0);
    }
  };
  svg.addEventListener("pointerup", (event) => finishDrag(event));
  svg.addEventListener("pointercancel", (event) => finishDrag(event, true));
  svg.addEventListener("click", (event) => {
    if (!suppressClick) return;
    event.preventDefault();
    event.stopImmediatePropagation();
    suppressClick = false;
  }, true);

  updateControls();
}

export async function renderJapanMap(container, regions, { selectedAreaKey = "", onSelect, signal } = {}) {
  if (!container) return;
  const response = await fetch("/dashboard-assets/assets/japan-regions.svg", { credentials: "same-origin", signal });
  if (!response.ok) throw new Error("日本地図を読み込めませんでした");
  container.innerHTML = await response.text();
  const svg = preparePrefectures(container);
  const byKey = new Map(regions.map((row) => [row.areaKey, row]));
  const tooltip = document.createElement("div");
  tooltip.className = "mapTooltip";
  tooltip.hidden = true;
  container.appendChild(tooltip);
  installMapViewport(container, svg, () => { tooltip.hidden = true; });
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
      if (container.classList.contains("isDragging")) {
        tooltip.hidden = true;
        return;
      }
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
