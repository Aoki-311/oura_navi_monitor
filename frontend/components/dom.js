export function escapeHtml(value) {
  const node = document.createElement("span");
  node.textContent = String(value ?? "");
  return node.innerHTML;
}

export function moduleMessage(message = "データ取得不可", type = "info") {
  return `<div class="moduleMessage" role="${type === "error" ? "alert" : "status"}" data-state="${escapeHtml(type)}">${escapeHtml(message)}</div>`;
}

export function chips(values) {
  if (!Array.isArray(values)) return '<span class="muted">-</span>';
  return values.length ? values.map((item) => `<span class="chip" style="--chip:${escapeHtml(item.color)}">${escapeHtml(item.name)}</span>`).join("") : '<span class="muted">-</span>';
}

export function setBusy(root, busy) {
  root?.setAttribute("aria-busy", busy ? "true" : "false");
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
