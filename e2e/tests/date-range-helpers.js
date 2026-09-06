const { expect } = require("@playwright/test");

async function openPeriod(form) {
  const popup = form.locator("[data-range-popup]");
  if (!(await popup.isVisible())) await form.locator("[data-range-trigger]").click();
  await expect(popup).toBeVisible();
  return popup;
}

async function showMonth(form, isoDate) {
  await openPeriod(form);
  const target = isoDate.slice(0, 7);
  const month = form.locator("[data-calendar-month]");
  for (let attempt = 0; attempt < 120; attempt += 1) {
    const visible = await month.getAttribute("data-month");
    if (visible === target) return;
    await form.locator(visible > target ? "[data-calendar-prev]" : "[data-calendar-next]").click();
  }
  throw new Error(`Calendar did not reach ${target} within 120 month steps`);
}

async function selectDate(form, isoDate, { keyboard = false } = {}) {
  await showMonth(form, isoDate);
  const day = form.locator(`[data-date="${isoDate}"]`);
  if (keyboard) {
    await day.focus();
    await day.press("Enter");
  } else {
    await day.click();
  }
}

async function draftPeriod(form, start, end) {
  await selectDate(form, start);
  await expect(form.locator('[name="start"]')).toHaveValue(start);
  await expect(form.locator('[name="end"]')).toHaveValue("");
  await selectDate(form, end);
  const ordered = [start, end].sort();
  await expect(form.locator('[name="start"]')).toHaveValue(ordered[0]);
  await expect(form.locator('[name="end"]')).toHaveValue(ordered[1]);
}

async function applyPreset(form, preset) {
  await openPeriod(form);
  await form.locator(`[data-range-preset="${preset}"]`).click();
  await form.locator("[data-range-apply]").click();
}

module.exports = { openPeriod, showMonth, selectDate, draftPeriod, applyPreset };
