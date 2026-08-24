export function escapeHtml(value) {
  const node = document.createElement("span");
  node.textContent = String(value ?? "");
  return node.innerHTML;
}

export function moduleMessage(message = "データ取得不可") {
  return `<div class="moduleMessage" role="status">${escapeHtml(message)}</div>`;
}

export function chips(values) {
  if (!Array.isArray(values)) throw new Error("ラベルデータが不正です");
  return values.length ? values.map((item) => `<span class="chip" style="--chip:${escapeHtml(item.color)}">${escapeHtml(item.name)}</span>`).join("") : '<span class="muted">-</span>';
}

export function setBusy(root, busy) {
  root?.setAttribute("aria-busy", busy ? "true" : "false");
}
