import { escapeHtml } from "./dom.js";

const PRESETS = { today: ["今日", 1], last_7d: ["過去7日", 7], last_14d: ["過去14日", 14], last_30d: ["過去30日", 30] };
const ISO_DATE = /^\d{4}-\d{2}-\d{2}$/;
export const refreshIcon = '<svg viewBox="0 0 24 24" width="17" height="17" fill="none" stroke="currentColor" stroke-width="1.8" aria-hidden="true"><path d="M20 7v5h-5M4 17v-5h5"/><path d="M6.1 7a7 7 0 0 1 11.6-1L20 9M4 15l2.3 3A7 7 0 0 0 17.9 17"/></svg>';

export function jstDate(asOf = new Date().toISOString()) {
  const date = new Date(asOf);
  return new Date((Number.isFinite(date.getTime()) ? date.getTime() : Date.now()) + 9 * 60 * 60_000).toISOString().slice(0, 10);
}

function validDate(value) {
  const parsed = new Date(`${value}T00:00:00Z`);
  return ISO_DATE.test(String(value || "")) && Number.isFinite(parsed.getTime()) && parsed.toISOString().slice(0, 10) === value;
}

function shiftDate(value, days) {
  return new Date(Date.parse(`${value}T00:00:00Z`) + days * 86400_000).toISOString().slice(0, 10);
}

export function normalizeDateRange(value = {}, asOf) {
  const range = typeof value === "string" ? { preset: value } : value || {};
  const preset = Object.hasOwn(PRESETS, range.preset) || ["custom", "last_60d", "all"].includes(range.preset) ? range.preset : "last_7d";
  if (validDate(range.start) && validDate(range.end) && range.start <= range.end) return { preset, start: range.start, end: range.end };
  if (preset === "all") return { preset, start: "", end: "" };
  const end = jstDate(asOf);
  const days = PRESETS[preset]?.[1] || (preset === "last_60d" ? 60 : 7);
  return { preset: preset === "custom" ? "last_7d" : preset, start: shiftDate(end, 1 - days), end };
}

export function requestDateRange(range, asOf) {
  const value = normalizeDateRange(range, asOf);
  return {
    preset: value.preset === "custom" ? "" : value.preset,
    ...(value.start ? { start: `${value.start}T00:00:00+09:00`, end: `${shiftDate(value.end, 1)}T00:00:00+09:00` } : {}),
    ...(asOf ? { as_of: asOf } : {}),
  };
}

export function periodLabel(range) {
  const value = normalizeDateRange(range);
  if (!value.start) return "全期間";
  const label = (date) => date.replaceAll("-", "/");
  return value.start === value.end ? label(value.start) : `${label(value.start)} — ${label(value.end)}`;
}

export function renderDateRangeControl(id, range, { compact = false, label = "集計期間", refresh = true } = {}) {
  const value = normalizeDateRange(range);
  const calendarIcon = '<svg viewBox="0 0 24 24" width="17" height="17" fill="none" stroke="currentColor" stroke-width="1.7" aria-hidden="true"><rect x="3" y="5" width="18" height="16" rx="3"/><path d="M7 2v6m10-6v6M3 11h18"/></svg>';
  const popupId = `${id}-popup`;
  return `<form id="${escapeHtml(id)}" class="dateRangeControl ${compact ? "isCompact" : ""}" data-date-range>
    <div class="dateRangeApplied"><small>${escapeHtml(label)}</small><strong data-applied-range>${escapeHtml(periodLabel(value))}</strong></div>
    <div class="dateRangeEditor"><button type="button" class="dateRangeTrigger" data-range-trigger popovertarget="${escapeHtml(popupId)}" aria-controls="${escapeHtml(popupId)}" aria-haspopup="dialog" aria-expanded="false" aria-label="${escapeHtml(label)}を選択">${calendarIcon}<span data-range-trigger-label>${escapeHtml(PRESETS[value.preset]?.[0] || periodLabel(value))}</span><span class="dateRangeChevron" aria-hidden="true">⌄</span></button>${refresh ? `<button type="button" class="iconButton dateRangeRefresh" data-range-refresh aria-label="再読込" title="表示中の期間を再読込">${refreshIcon}</button>` : ""}</div>
    <input type="hidden" name="start" value="${escapeHtml(value.start)}"><input type="hidden" name="end" value="${escapeHtml(value.end)}">
    <div id="${escapeHtml(popupId)}" class="dateRangePopup" data-range-popup popover="auto" role="dialog" aria-label="${escapeHtml(label)}を選択">
      <div class="dateRangePopupHead"><span>${escapeHtml(label)}を選択</span><div class="dateRangePresets" role="group" aria-label="期間の候補">${Object.entries(PRESETS).map(([key, [text]]) => `<button type="button" data-range-preset="${key}" aria-pressed="${value.preset === key}">${text}</button>`).join("")}</div></div>
      <div class="rangeCalendar"><div class="rangeCalendarHeading"><button type="button" data-calendar-prev aria-label="前の月">‹</button><strong data-calendar-month aria-live="polite"></strong><button type="button" data-calendar-next aria-label="次の月">›</button></div><div data-calendar-grid role="grid" aria-label="日付の選択"></div></div>
      <div class="dateRangeSelection" data-range-selection></div><p class="dateRangeFeedback" role="status" data-range-feedback hidden></p>
      <div class="dateRangePopupFoot"><button type="button" class="dateRangeClear" data-range-clear>クリア</button><div><button type="button" class="ghostButton" data-range-cancel>キャンセル</button><button type="submit" class="primaryButton" data-range-apply>反映</button></div></div>
    </div>
  </form>`;
}

export function bindDateRangeControl(root, { range, onApply, onRefresh, signal } = {}) {
  const form = root?.matches?.("[data-date-range]") ? root : root?.querySelector?.("[data-date-range]");
  if (!form) return null;
  const controller = new AbortController();
  const abort = () => controller.abort();
  if (signal?.aborted) abort(); else signal?.addEventListener("abort", abort, { once: true });
  let applied = normalizeDateRange(range);
  const popup = form.querySelector("[data-range-popup]");
  const trigger = form.querySelector("[data-range-trigger]");
  let selectedPreset = applied.preset;
  let focusedDate = applied.end || jstDate();
  let month = focusedDate.slice(0, 7);
  let previewDate = "";
  let restoringPopover = false;
  const grid = form.querySelector("[data-calendar-grid]");
  const feedback = form.querySelector("[data-range-feedback]");
  const short = (date) => date.replaceAll("-", "/");
  const position = () => {
    if (!popup.matches(":popover-open")) return;
    const anchor = trigger.getBoundingClientRect();
    const box = popup.getBoundingClientRect();
    const below = anchor.bottom + 8;
    const top = below + box.height <= window.innerHeight - 12 ? below : anchor.top - box.height - 8;
    popup.style.left = `${Math.max(12, Math.min(anchor.right - box.width, window.innerWidth - box.width - 12))}px`;
    popup.style.top = `${Math.max(12, Math.min(top, window.innerHeight - box.height - 12))}px`;
  };
  const paintRange = () => {
    const start = form.elements.start.value;
    const end = form.elements.end.value;
    const bounds = start ? [start, end || previewDate || start].sort() : [];
    grid.querySelectorAll("[data-date]").forEach((button) => {
      const date = button.dataset.date;
      const selected = Boolean(start && date >= start && date <= (end || start));
      button.setAttribute("aria-pressed", String(selected));
      button.parentElement.setAttribute("aria-selected", String(selected));
      button.parentElement.classList.toggle("inRange", Boolean(bounds.length && date >= bounds[0] && date <= bounds[1]));
      button.parentElement.classList.toggle("rangeStart", date === bounds[0]);
      button.parentElement.classList.toggle("rangeEnd", date === bounds[1]);
      button.parentElement.classList.toggle("rangePreview", Boolean(!end && previewDate && date >= bounds[0] && date <= bounds[1]));
    });
  };
  const renderMonth = () => {
    const [year, number] = month.split("-").map(Number);
    const caption = form.querySelector("[data-calendar-month]");
    caption.textContent = `${year}年${number}月`;
    caption.dataset.month = month;
    form.querySelector("[data-calendar-next]").disabled = month >= jstDate().slice(0, 7);
    const first = new Date(`${month}-01T00:00:00Z`).getUTCDay();
    const days = new Date(Date.UTC(year, number, 0)).getUTCDate();
    const cells = Array.from({ length: Math.ceil((first + days) / 7) * 7 }, (_, index) => {
      const day = index - first + 1;
      if (day < 1 || day > days) return '<span class="rangeCalendarBlank" role="gridcell" aria-disabled="true"></span>';
      const date = `${month}-${String(day).padStart(2, "0")}`;
      return `<div role="gridcell"><button type="button" data-date="${date}" aria-label="${year}年${number}月${day}日" ${date === jstDate() ? 'aria-current="date"' : ""} tabindex="${date === focusedDate ? "0" : "-1"}" ${date > jstDate() ? "disabled" : ""}>${day}</button></div>`;
    });
    grid.innerHTML = `<div class="rangeCalendarWeek" role="row">${["日", "月", "火", "水", "木", "金", "土"].map((day) => `<span role="columnheader">${day}</span>`).join("")}</div>${Array.from({ length: cells.length / 7 }, (_, row) => `<div class="rangeCalendarWeek" role="row">${cells.slice(row * 7, row * 7 + 7).join("")}</div>`).join("")}`;
    if (!grid.querySelector('[tabindex="0"]')) grid.querySelector("[data-date]:not(:disabled)")?.setAttribute("tabindex", "0");
    paintRange();
  };
  const updateDraft = () => {
    const start = form.elements.start.value;
    const end = form.elements.end.value;
    const complete = validDate(start) && validDate(end) && start <= end && end <= jstDate();
    const changed = start !== applied.start || end !== applied.end;
    form.querySelector("[data-range-apply]").disabled = !complete;
    form.querySelector("[data-range-selection]").textContent = start ? `${short(start)} 〜 ${end ? short(end) : "終了日を選択"}` : "開始日と終了日を選択";
    feedback.textContent = complete && changed ? "「反映」で表示を更新します。" : "";
    feedback.hidden = !feedback.textContent;
    form.querySelectorAll("[data-range-preset]").forEach((button) => button.setAttribute("aria-pressed", String(button.dataset.rangePreset === selectedPreset)));
    paintRange();
    position();
  };
  const reset = () => {
    selectedPreset = applied.preset;
    form.elements.start.value = applied.start;
    form.elements.end.value = applied.end;
    focusedDate = applied.end || jstDate();
    month = focusedDate.slice(0, 7);
    previewDate = "";
    renderMonth();
    updateDraft();
  };
  popup.addEventListener("beforetoggle", (event) => {
    const open = event.newState === "open";
    trigger.setAttribute("aria-expanded", String(open));
    if (!restoringPopover) reset();
    if (open) window.requestAnimationFrame(() => {
      if (!controller.signal.aborted && popup.matches(":popover-open")) {
        position();
        grid.querySelector('[tabindex="0"]')?.focus({ preventScroll: true });
      }
    });
  }, { signal: controller.signal });
  window.addEventListener("resize", position, { signal: controller.signal });
  window.addEventListener("scroll", position, { capture: true, signal: controller.signal });
  form.querySelectorAll("[data-range-preset]").forEach((button) => button.addEventListener("click", () => {
    selectedPreset = button.dataset.rangePreset;
    const next = normalizeDateRange({ preset: selectedPreset });
    form.elements.start.value = next.start;
    form.elements.end.value = next.end;
    focusedDate = next.end;
    month = next.end.slice(0, 7);
    previewDate = "";
    renderMonth();
    updateDraft();
  }, { signal: controller.signal }));
  grid.addEventListener("click", (event) => {
    const button = event.target.closest("[data-date]");
    if (!button || button.disabled) return;
    const date = button.dataset.date;
    if (!form.elements.start.value || form.elements.end.value) {
      form.elements.start.value = date;
      form.elements.end.value = "";
    } else {
      const [start, end] = [form.elements.start.value, date].sort();
      form.elements.start.value = start;
      form.elements.end.value = end;
    }
    selectedPreset = "custom";
    focusedDate = date;
    previewDate = "";
    grid.querySelectorAll("[data-date]").forEach((item) => item.tabIndex = item === button ? 0 : -1);
    updateDraft();
  }, { signal: controller.signal });
  const moveMonth = (offset) => {
    const [year, number] = month.split("-").map(Number);
    month = new Date(Date.UTC(year, number - 1 + offset, 1)).toISOString().slice(0, 7);
    focusedDate = `${month}-01`;
    previewDate = "";
    renderMonth();
    position();
  };
  form.querySelector("[data-calendar-prev]").addEventListener("click", () => moveMonth(-1), { signal: controller.signal });
  form.querySelector("[data-calendar-next]").addEventListener("click", () => moveMonth(1), { signal: controller.signal });
  grid.addEventListener("keydown", (event) => {
    const button = event.target.closest("[data-date]");
    if (!button) return;
    const date = button.dataset.date;
    const weekday = new Date(`${date}T00:00:00Z`).getUTCDay();
    const offsets = { ArrowLeft: -1, ArrowRight: 1, ArrowUp: -7, ArrowDown: 7, Home: -weekday, End: 6 - weekday };
    let next;
    if (Object.hasOwn(offsets, event.key)) next = shiftDate(date, offsets[event.key]);
    else if (["PageUp", "PageDown"].includes(event.key)) {
      const [year, number, day] = date.split("-").map(Number);
      const target = new Date(Date.UTC(year, number - 1 + (event.key === "PageUp" ? -1 : 1), 1));
      const last = new Date(Date.UTC(target.getUTCFullYear(), target.getUTCMonth() + 1, 0)).getUTCDate();
      target.setUTCDate(Math.min(day, last));
      next = target.toISOString().slice(0, 10);
    } else return;
    event.preventDefault();
    focusedDate = next > jstDate() ? jstDate() : next;
    month = focusedDate.slice(0, 7);
    renderMonth();
    grid.querySelector(`[data-date="${focusedDate}"]`)?.focus({ preventScroll: true });
    position();
  }, { signal: controller.signal });
  const preview = (event) => {
    const button = event.target.closest("[data-date]");
    if (button && !button.disabled && form.elements.start.value && !form.elements.end.value) {
      previewDate = button.dataset.date;
      paintRange();
    }
  };
  grid.addEventListener("pointerover", preview, { signal: controller.signal });
  grid.addEventListener("focusin", preview, { signal: controller.signal });
  grid.addEventListener("pointerleave", () => { previewDate = ""; paintRange(); }, { signal: controller.signal });
  form.querySelector("[data-range-clear]").addEventListener("click", () => {
    selectedPreset = "custom";
    form.elements.start.value = "";
    form.elements.end.value = "";
    previewDate = "";
    updateDraft();
  }, { signal: controller.signal });
  form.querySelector("[data-range-cancel]").addEventListener("click", () => {
    popup.hidePopover();
    trigger.focus({ preventScroll: true });
  }, { signal: controller.signal });
  form.addEventListener("submit", (event) => {
    event.preventDefault();
    const start = form.elements.start.value;
    const end = form.elements.end.value;
    if (!validDate(start) || !validDate(end) || start > end || end > jstDate()) {
      feedback.textContent = "開始日と終了日を確認してください。今日以前の日付を指定してください。";
      feedback.hidden = false;
      return;
    }
    const selection = { preset: selectedPreset, start, end };
    popup.hidePopover();
    onApply?.(selection);
  }, { signal: controller.signal });
  form.querySelector("[data-range-refresh]")?.addEventListener("click", () => onRefresh?.(), { signal: controller.signal });
  reset();
  const close = () => { if (popup.matches(":popover-open")) popup.hidePopover(); };
  controller.signal.addEventListener("abort", close, { once: true });
  return {
    setAppliedRange(range) {
      const next = normalizeDateRange(range);
      if (next.preset === applied.preset && next.start === applied.start && next.end === applied.end) return;
      applied = next;
      form.querySelector("[data-applied-range]").textContent = periodLabel(applied);
      trigger.querySelector("[data-range-trigger-label]").textContent = PRESETS[applied.preset]?.[0] || periodLabel(applied);
      if (popup.matches(":popover-open")) updateDraft();
      else reset();
    },
    destroy() { close(); controller.abort(); signal?.removeEventListener("abort", abort); },
    preserveOpenPopover() {
      const wasOpen = popup.matches(":popover-open");
      return () => {
        if (!wasOpen || controller.signal.aborted || !popup.isConnected || popup.matches(":popover-open")) return;
        restoringPopover = true;
        try { popup.showPopover(); } finally { restoringPopover = false; }
      };
    },
  };
}
