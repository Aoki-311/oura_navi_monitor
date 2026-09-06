export function escapeHtml(value) {
  const node = document.createElement("span");
  node.textContent = String(value ?? "");
  return node.innerHTML;
}

export function moduleMessage(message = "データ取得不可", type = "info") {
  if (type === "loading" || /読み込み中|読込中|読み込んで/.test(message)) return `<div class="moduleMessage moduleLoading" role="status" data-state="loading"><span class="loadingSpinner" aria-hidden="true"></span><span>${escapeHtml(message)}</span><div class="skeletonLines" aria-hidden="true"><i></i><i></i><i></i></div></div>`;
  return `<div class="moduleMessage" role="${type === "error" ? "alert" : "status"}" data-state="${escapeHtml(type)}">${escapeHtml(message)}</div>`;
}

export function chips(values) {
  if (!Array.isArray(values) || !values.length) return "";
  return values.map((item) => `<span class="chip" style="--chip:${escapeHtml(item.color)}">${escapeHtml(item.name)}</span>`).join("");
}

export function setBusy(root, busy) {
  root?.setAttribute("aria-busy", busy ? "true" : "false");
  root?.classList.toggle("isLoading", busy);
}

export function compareJapaneseNames(a, b) {
  const text = (value) => String(value || "").normalize("NFKC").replace(/\s+/g, " ").trim();
  return text(a).localeCompare(text(b), "ja-JP", { numeric: true, sensitivity: "base" });
}

export function installDialogLifecycle(dialog, { onClose, initialFocus } = {}) {
  const previouslyFocused = document.activeElement;
  const focusableSelector = 'button:not([disabled]),input:not([disabled]),select:not([disabled]),textarea:not([disabled]),a[href],[tabindex]:not([tabindex="-1"])';
  const focusables = () => [...dialog.querySelectorAll(focusableSelector)].filter((item) => !item.hidden && item.getAttribute("aria-hidden") !== "true");
  const keydown = (event) => {
    if (event.key === "Escape") {
      event.preventDefault();
      onClose?.();
      return;
    }
    if (event.key !== "Tab") return;
    const items = focusables();
    if (!items.length) return;
    const first = items[0];
    const last = items.at(-1);
    if (event.shiftKey && document.activeElement === first) { event.preventDefault(); last.focus(); }
    else if (!event.shiftKey && document.activeElement === last) { event.preventDefault(); first.focus(); }
  };
  dialog.addEventListener("keydown", keydown);
  window.requestAnimationFrame(() => (initialFocus || focusables()[0] || dialog).focus());
  return () => {
    dialog.removeEventListener("keydown", keydown);
    if (previouslyFocused instanceof HTMLElement && previouslyFocused.isConnected) previouslyFocused.focus();
  };
}
