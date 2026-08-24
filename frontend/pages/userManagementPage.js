import {
  createManagedLabel,
  createManagedUser,
  deleteManagedLabel,
  getManagedLabels,
  getManagedUsers,
  updateManagedLabel,
  updateManagedUser,
} from "../api/client.js";
import { managementLabelsModel, managementUsersModel } from "../adapters/managementAdapter.js";
import { chips, escapeHtml, moduleMessage, setBusy } from "../components/dom.js";
import { displayDateTime } from "../viewModels/formatters.js";
import { DEPARTMENTS } from "../viewModels/labels.js";

const COLORS = ["#23d28f", "#386dff", "#ffb340", "#ff5b74", "#7c5cff", "#27d9d2", "#5f6285"];

export class UserManagementPage {
  constructor(root, { rosterId = "", toast }) {
    this.root = root;
    this.rosterId = rosterId;
    this.toast = toast;
    this.users = [];
    this.labels = [];
    this.subtab = "users";
  }

  async load() {
    setBusy(this.root, true);
    this.root.innerHTML = `<div class="pageHeading"><div><p class="eyebrow">Monitor内だけで使用する名簿とラベル</p><h2>ユーザー管理</h2><p>部門が分析範囲を決めます。ここでIAP権限は変更されません。</p></div></div><div id="managementBody">${moduleMessage("読み込み中…")}</div><div id="drawerHost"></div>`;
    try {
      const [users, labels] = await Promise.all([getManagedUsers({ include_inactive: true }), getManagedLabels({ include_inactive: true })]);
      this.users = managementUsersModel(users);
      this.labels = managementLabelsModel(labels);
      this.render();
      if (this.rosterId) this.openUser(this.users.find((row) => row.rosterId === this.rosterId));
    } catch (error) {
      this.root.querySelector("#managementBody").innerHTML = moduleMessage(error.message);
      this.toast(error.message, "error");
    }
    setBusy(this.root, false);
  }

  render() {
    this.root.querySelector("#managementBody").innerHTML = `
      <div class="subtabs"><button data-subtab="users" class="${this.subtab === "users" ? "isActive" : ""}">ユーザー管理 <span>${this.users.length}</span></button><button data-subtab="labels" class="${this.subtab === "labels" ? "isActive" : ""}">ラベル管理 <span>${this.labels.length}</span></button></div>
      <section class="panel" id="managementPanel"></section>`;
    this.root.querySelectorAll("[data-subtab]").forEach((button) => button.addEventListener("click", () => { this.subtab = button.dataset.subtab; this.render(); }));
    if (this.subtab === "users") this.renderUsers(); else this.renderLabels();
  }

  renderUsers() {
    const labelMap = new Map(this.labels.map((row) => [row.labelId, row]));
    const panel = this.root.querySelector("#managementPanel");
    panel.innerHTML = `<div class="panelHead"><div><h3>登録ユーザー</h3><small>初回83名。停用ユーザーも履歴として残ります。</small></div><button id="newUser" class="primaryButton">ユーザーを追加</button></div><div class="tableScroll"><table><thead><tr><th>社員名 / メール</th><th>地域・勤務地</th><th>役割・部門</th><th>ラベル</th><th>状態</th><th>最終更新</th><th></th></tr></thead><tbody>${this.users.map((row) => `<tr class="${row.isActive ? "" : "isInactive"}"><td><strong>${escapeHtml(row.name)}</strong><small>${escapeHtml(row.email)}</small></td><td>${escapeHtml(row.area)}<small>${escapeHtml(row.workplace)}</small></td><td>${escapeHtml(row.role)}<small>${escapeHtml(row.department)}</small></td><td><div class="chips">${chips(row.labelIds.map((id) => labelMap.get(id)).filter(Boolean))}</div></td><td><span class="statusBadge ${row.isActive ? "active" : "inactive"}">${row.isActive ? "有効" : "停用"}</span></td><td>${displayDateTime(row.updatedAt)}<small>${escapeHtml(row.updatedBy || "-")}</small></td><td><button class="linkButton" data-edit-user="${escapeHtml(row.rosterId)}">編集</button></td></tr>`).join("")}</tbody></table></div>`;
    panel.querySelector("#newUser").addEventListener("click", () => this.openUser(null));
    panel.querySelectorAll("[data-edit-user]").forEach((button) => button.addEventListener("click", () => this.openUser(this.users.find((row) => row.rosterId === button.dataset.editUser))));
  }

  openUser(user) {
    if (user === undefined) return;
    const host = this.root.querySelector("#drawerHost");
    const isNew = !user;
    host.innerHTML = `<div class="drawerBackdrop" id="drawerBackdrop"><aside class="drawer" role="dialog" aria-modal="true"><div class="drawerHead"><div><p class="eyebrow">${isNew ? "新規登録" : "名簿編集"}</p><h3>${isNew ? "ユーザーを追加" : escapeHtml(user.name)}</h3></div><button id="closeDrawer" class="iconButton" aria-label="閉じる">×</button></div><form id="userForm" class="formGrid">
      <label>社員名<input name="name" required maxlength="120" value="${escapeHtml(user?.name || "")}"></label>
      <label>メール<input name="email" type="email" required value="${escapeHtml(user?.email || "")}"></label>
      <label>エリア<input name="area" required maxlength="80" value="${escapeHtml(user?.area || "")}"></label>
      <label>勤務地<input name="workplace" required maxlength="80" value="${escapeHtml(user?.workplace || "")}"></label>
      <label>役割<input name="role" required maxlength="80" value="${escapeHtml(user?.role || "")}"></label>
      <label>部門<select name="department">${DEPARTMENTS.map((value) => `<option ${user?.department === value ? "selected" : ""}>${value}</option>`).join("")}</select></label>
      <label>MR経験<input name="mr_experience" maxlength="80" value="${escapeHtml(user?.mrExperience || "-")}"></label>
      <fieldset><legend>ラベル</legend><div class="labelChoices">${this.labels.filter((row) => row.isActive).map((row) => `<label><input type="checkbox" name="label" value="${escapeHtml(row.labelId)}" ${user?.labelIds?.includes(row.labelId) ? "checked" : ""}><span style="--chip:${escapeHtml(row.color)}">${escapeHtml(row.name)}</span></label>`).join("") || '<span class="muted">利用可能なラベルはありません</span>'}</div></fieldset>
      ${isNew ? '<label class="switchRow"><input name="is_active" type="checkbox" checked>登録時から有効</label>' : `<label class="switchRow"><input name="is_active" type="checkbox" ${user.isActive ? "checked" : ""}>このユーザーを有効にする</label>`}
      <div class="formActions"><button type="button" class="ghostButton" id="cancelUser">キャンセル</button><button type="submit" class="primaryButton">保存</button></div>
    </form><p class="drawerNote">分析範囲は部門から自動決定されます。ラベルは表示・絞り込み専用です。</p></aside></div>`;
    const close = () => { host.innerHTML = ""; };
    host.querySelector("#closeDrawer").addEventListener("click", close);
    host.querySelector("#cancelUser").addEventListener("click", close);
    host.querySelector("#drawerBackdrop").addEventListener("click", (event) => { if (event.target.id === "drawerBackdrop") close(); });
    host.querySelector("#userForm").addEventListener("submit", async (event) => {
      event.preventDefault();
      const data = new FormData(event.currentTarget);
      const fields = {
        name: data.get("name"), email: data.get("email"), area: data.get("area"), workplace: data.get("workplace"),
        role: data.get("role"), department: data.get("department"), mr_experience: data.get("mr_experience") || "-",
      };
      const labelIds = data.getAll("label");
      const active = data.get("is_active") === "on";
      try {
        if (isNew) await createManagedUser({ ...fields, label_ids: labelIds, is_active: active });
        else {
          await updateManagedUser(user.rosterId, { ...fields, label_ids: labelIds, is_active: active });
        }
        close();
        this.toast("ユーザー情報を保存しました", "success");
        await this.load();
      } catch (error) { this.toast(error.message, "error"); }
    });
  }

  renderLabels() {
    const panel = this.root.querySelector("#managementPanel");
    panel.innerHTML = `<div class="panelHead"><div><h3>分析ラベル</h3><small>Monitor画面内だけで使用します。権限や分析範囲には影響しません。</small></div><button id="newLabel" class="primaryButton">ラベルを追加</button></div><div class="labelCards">${this.labels.map((row) => `<article class="labelCard ${row.isActive ? "" : "isInactive"}"><span class="labelSwatch" style="--chip:${escapeHtml(row.color)}"></span><div><strong>${escapeHtml(row.name)}</strong><small>${row.usageCount}名で使用 · ${row.isActive ? "有効" : "停用"}</small></div><button data-edit-label="${escapeHtml(row.labelId)}" class="linkButton">編集</button></article>`).join("") || moduleMessage("ラベルはありません")}</div>`;
    panel.querySelector("#newLabel").addEventListener("click", () => this.openLabel(null));
    panel.querySelectorAll("[data-edit-label]").forEach((button) => button.addEventListener("click", () => this.openLabel(this.labels.find((row) => row.labelId === button.dataset.editLabel))));
  }

  openLabel(label) {
    const host = this.root.querySelector("#drawerHost");
    const isNew = !label;
    host.innerHTML = `<div class="drawerBackdrop" id="drawerBackdrop"><aside class="drawer compact" role="dialog" aria-modal="true"><div class="drawerHead"><h3>${isNew ? "ラベルを追加" : "ラベルを編集"}</h3><button id="closeDrawer" class="iconButton">×</button></div><form id="labelForm" class="formGrid"><label>名称<input name="name" required maxlength="40" value="${escapeHtml(label?.name || "")}"></label><fieldset><legend>色</legend><div class="colorChoices">${COLORS.map((color, index) => `<label><input type="radio" name="color" value="${color}" ${(label?.color || COLORS[0]) === color ? "checked" : ""}><span style="--chip:${color}" title="色${index + 1}"></span></label>`).join("")}</div></fieldset>${isNew ? "" : `<label class="switchRow"><input type="checkbox" name="is_active" ${label.isActive ? "checked" : ""}>有効</label>`}<div class="formActions">${!isNew ? '<button type="button" id="deleteLabel" class="dangerButton">削除</button>' : ""}<button type="submit" class="primaryButton">保存</button></div></form></aside></div>`;
    const close = () => { host.innerHTML = ""; };
    host.querySelector("#closeDrawer").addEventListener("click", close);
    host.querySelector("#labelForm").addEventListener("submit", async (event) => {
      event.preventDefault(); const data = new FormData(event.currentTarget);
      try {
        if (isNew) await createManagedLabel({ name: data.get("name"), color: data.get("color") });
        else await updateManagedLabel(label.labelId, { name: data.get("name"), color: data.get("color"), is_active: data.get("is_active") === "on" });
        close(); this.toast("ラベルを保存しました", "success"); await this.load();
      } catch (error) { this.toast(error.message, "error"); }
    });
    host.querySelector("#deleteLabel")?.addEventListener("click", async () => {
      if (!window.confirm("このラベルを削除しますか？")) return;
      try { await deleteManagedLabel(label.labelId); close(); this.toast("ラベルを削除しました", "success"); await this.load(); }
      catch (error) { this.toast(error.message, "error"); }
    });
  }
}
