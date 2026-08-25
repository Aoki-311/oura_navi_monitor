import {
  createManagedLabel,
  createManagedUser,
  deleteManagedLabel,
  getManagedLabels,
  getManagedUsers,
  getManagementMetadata,
  isCancellation,
  updateManagedLabel,
  updateManagedUser,
} from "../api/client.js";
import {
  managementLabelsModel,
  managementMetadataModel,
  managementUsersModel,
} from "../adapters/managementAdapter.js";
import { chips, escapeHtml, installDialogLifecycle, moduleMessage, setBusy } from "../components/dom.js";
import { displayDateTime } from "../viewModels/formatters.js";

export class UserManagementPage {
  constructor(root, { rosterId = "", toast, signal, isCurrent, clearManagementRoster }) {
    this.root = root;
    this.rosterId = rosterId;
    this.toast = toast;
    this.signal = signal;
    this.isCurrent = isCurrent;
    this.clearManagementRoster = clearManagementRoster;
    this.users = [];
    this.labels = [];
    this.metadata = null;
    this.errors = { users: "", labels: "", metadata: "" };
    this.issues = { users: [], labels: [] };
    this.subtab = "users";
    this.userSearch = "";
    this.userStatus = "all";
    this.dialogCleanup = null;
  }

  async load() {
    this.errors = { users: "", labels: "", metadata: "" };
    this.issues = { users: [], labels: [] };
    setBusy(this.root, true);
    this.root.innerHTML = `<div class="pageHeading"><div><p class="eyebrow">Monitor内だけで使用する名簿とラベル</p><h2>ユーザー管理</h2><p>部門が分析対象を決め、ラベルはMonitorの画面表示と分析だけに使用します。</p></div></div><div id="managementBody">${moduleMessage("読み込み中…")}</div><div id="drawerHost"></div>`;
    const results = await Promise.allSettled([
      getManagedUsers({ include_inactive: true }, { signal: this.signal }),
      getManagedLabels({ include_inactive: true }, { signal: this.signal }),
      getManagementMetadata({ signal: this.signal }),
    ]);
    if (!this.isCurrent()) return;
    const [usersResult, labelsResult, metadataResult] = results;
    if (usersResult.status === "fulfilled") {
      try {
        const model = managementUsersModel(usersResult.value);
        this.users = model.items;
        this.issues.users = model.issues;
      } catch (error) { this.errors.users = error.message; }
    } else if (!isCancellation(usersResult.reason)) this.errors.users = usersResult.reason.message;
    if (labelsResult.status === "fulfilled") {
      try {
        const model = managementLabelsModel(labelsResult.value);
        this.labels = model.items;
        this.issues.labels = model.issues;
      } catch (error) { this.errors.labels = error.message; }
    } else if (!isCancellation(labelsResult.reason)) this.errors.labels = labelsResult.reason.message;
    if (metadataResult.status === "fulfilled") {
      try { this.metadata = managementMetadataModel(metadataResult.value); }
      catch (error) { this.errors.metadata = error.message; }
    } else if (!isCancellation(metadataResult.reason)) this.errors.metadata = metadataResult.reason.message;
    this.render();
    if (this.rosterId && !this.errors.users) {
      const target = this.users.find((row) => row.rosterId === this.rosterId);
      if (target) this.openUser(target);
      else this.toast("編集対象のユーザーが見つかりません。", "error");
    }
    setBusy(this.root, false);
  }

  render() {
    const body = this.root.querySelector("#managementBody");
    if (!body) return;
    body.innerHTML = `
      <div class="subtabs" role="tablist" aria-label="管理対象">
        <button role="tab" aria-selected="${this.subtab === "users"}" data-subtab="users" class="${this.subtab === "users" ? "isActive" : ""}">ユーザー管理 <span>${this.users.length}</span></button>
        <button role="tab" aria-selected="${this.subtab === "labels"}" data-subtab="labels" class="${this.subtab === "labels" ? "isActive" : ""}">ラベル管理 <span>${this.labels.length}</span></button>
      </div>
      <section class="panel" id="managementPanel"></section>`;
    body.querySelectorAll("[data-subtab]").forEach((button) => button.addEventListener("click", () => {
      this.subtab = button.dataset.subtab;
      this.render();
    }));
    if (this.subtab === "users") this.renderUsers(); else this.renderLabels();
  }

  filteredUsers() {
    const query = this.userSearch.trim().toLocaleLowerCase("ja-JP");
    return this.users.filter((row) => {
      const matchesStatus = this.userStatus === "all" || (this.userStatus === "active" ? row.isActive : !row.isActive);
      const haystack = [row.name, row.email, row.area, row.workplace, row.role, row.department].join(" ").toLocaleLowerCase("ja-JP");
      return matchesStatus && (!query || haystack.includes(query));
    });
  }

  renderUsers() {
    const panel = this.root.querySelector("#managementPanel");
    if (this.errors.users) {
      panel.innerHTML = `<div class="panelHead"><h3>登録ユーザー</h3></div>${moduleMessage(this.errors.users, "error")}`;
      return;
    }
    const labelMap = new Map(this.labels.map((row) => [row.labelId, row]));
    const rows = this.filteredUsers();
    panel.innerHTML = `
      <div class="panelHead"><div><h3>登録ユーザー</h3><small>現在の名簿と停用済みユーザーを同じ履歴として管理します。</small></div><button id="newUser" class="primaryButton" ${this.metadata ? "" : "disabled"}>ユーザーを追加</button></div>
      ${this.errors.metadata ? moduleMessage(`編集用の選択肢を読み込めません: ${this.errors.metadata}`, "error") : ""}
      ${this.errors.labels ? moduleMessage(`ラベル情報を読み込めません: ${this.errors.labels}`, "error") : ""}
      ${this.issues.users.length ? moduleMessage(`${this.issues.users.length}件の不正な名簿行を表示対象から外しました。`, "error") : ""}
      <div class="managementFilters"><label>ユーザー検索<input id="userSearch" type="search" value="${escapeHtml(this.userSearch)}" placeholder="氏名・メール・地域"></label><label>状態<select id="userStatus"><option value="all">すべて</option><option value="active" ${this.userStatus === "active" ? "selected" : ""}>有効</option><option value="inactive" ${this.userStatus === "inactive" ? "selected" : ""}>停用</option></select></label></div>
      <div class="tableScroll" tabindex="0" aria-label="管理ユーザー一覧"><table><thead><tr><th>社員名 / メール</th><th>地域・勤務地</th><th>役割・部門</th><th>ラベル</th><th>状態</th><th>最終更新</th><th></th></tr></thead><tbody>${rows.map((row) => `<tr class="${row.isActive ? "" : "isInactive"}"><td><strong>${escapeHtml(row.name)}</strong><small>${escapeHtml(row.email)}</small></td><td>${escapeHtml(row.area)}<small>${escapeHtml(row.workplace)}</small></td><td>${escapeHtml(row.role)}<small>${escapeHtml(row.department)}</small></td><td><div class="chips">${chips(row.labelIds.map((id) => labelMap.get(id)).filter(Boolean))}</div></td><td><span class="statusBadge ${row.isActive ? "active" : "inactive"}">${row.isActive ? "有効" : "停用"}</span></td><td>${displayDateTime(row.updatedAt)}<small>${escapeHtml(row.updatedBy || "-")}</small></td><td><button class="linkButton" data-edit-user="${escapeHtml(row.rosterId)}">編集</button></td></tr>`).join("") || `<tr><td colspan="7">該当するユーザーはいません。</td></tr>`}</tbody></table></div>`;
    panel.querySelector("#newUser")?.addEventListener("click", () => this.openUser(null));
    panel.querySelector("#userSearch").addEventListener("input", (event) => { this.userSearch = event.target.value; this.renderUsers(); });
    panel.querySelector("#userStatus").addEventListener("change", (event) => { this.userStatus = event.target.value; this.renderUsers(); });
    panel.querySelectorAll("[data-edit-user]").forEach((button) => button.addEventListener("click", () => this.openUser(this.users.find((row) => row.rosterId === button.dataset.editUser))));
  }

  closeDrawer() {
    this.dialogCleanup?.();
    this.dialogCleanup = null;
    this.root.querySelector("#drawerHost")?.replaceChildren();
    this.rosterId = "";
    this.clearManagementRoster?.();
  }

  installDrawer(form, initialFocus) {
    let cleanValue = JSON.stringify([...new FormData(form).entries()]);
    const close = () => {
      const dirty = cleanValue !== JSON.stringify([...new FormData(form).entries()]);
      if (dirty && !window.confirm("保存していない変更を破棄しますか？")) return;
      cleanValue = JSON.stringify([...new FormData(form).entries()]);
      this.closeDrawer();
    };
    this.dialogCleanup = installDialogLifecycle(form.closest("[role=dialog]"), { onClose: close, initialFocus });
    return { close, markClean: () => { cleanValue = JSON.stringify([...new FormData(form).entries()]); } };
  }

  openUser(user) {
    if (user === undefined || !this.metadata) return;
    this.dialogCleanup?.();
    const host = this.root.querySelector("#drawerHost");
    const isNew = !user;
    const selectedLabels = new Set(user?.labelIds || []);
    const labelChoices = this.labels.filter((row) => row.isActive || selectedLabels.has(row.labelId));
    host.innerHTML = `<div class="drawerBackdrop"><aside class="drawer" role="dialog" aria-modal="true" aria-labelledby="userDrawerTitle"><div class="drawerHead"><div><p class="eyebrow">${isNew ? "新規登録" : "名簿編集"}</p><h3 id="userDrawerTitle">${isNew ? "ユーザーを追加" : escapeHtml(user.name)}</h3></div><button id="closeDrawer" type="button" class="iconButton" aria-label="編集画面を閉じる">×</button></div><form id="userForm" class="formGrid">
      <label>社員名<input name="name" required maxlength="120" value="${escapeHtml(user?.name || "")}"></label>
      <label>メール<input name="email" type="email" required ${user?.identityBound ? "readonly aria-describedby=boundEmailNote" : ""} value="${escapeHtml(user?.email || "")}"></label>
      ${user?.identityBound ? '<p id="boundEmailNote" class="fieldNote">LCS利用履歴と連携済みのため、メールは変更できません。</p>' : ""}
      <label>エリア<select name="area" required>${this.metadata.areas.map((value) => `<option value="${escapeHtml(value)}" ${user?.area === value ? "selected" : ""}>${escapeHtml(value)}</option>`).join("")}</select></label>
      <label>勤務地<input name="workplace" list="workplaceOptions" required maxlength="80" value="${escapeHtml(user?.workplace || "")}"><datalist id="workplaceOptions">${this.metadata.workplaces.map((value) => `<option value="${escapeHtml(value)}"></option>`).join("")}</datalist></label>
      <label>役割<input name="role" list="roleOptions" required maxlength="80" value="${escapeHtml(user?.role || "")}"><datalist id="roleOptions">${this.metadata.roles.map((value) => `<option value="${escapeHtml(value)}"></option>`).join("")}</datalist></label>
      <label>部門<select name="department">${this.metadata.departments.map((value) => `<option value="${escapeHtml(value)}" ${user?.department === value ? "selected" : ""}>${escapeHtml(value)}</option>`).join("")}</select></label>
      <label>MR経験<input name="mr_experience" maxlength="80" value="${escapeHtml(user?.mrExperience || "-")}"></label>
      <fieldset><legend>ラベル</legend><div class="labelChoices">${labelChoices.map((row) => `<label><input type="checkbox" name="label" value="${escapeHtml(row.labelId)}" ${selectedLabels.has(row.labelId) ? "checked" : ""} ${row.isActive ? "" : "disabled"}><span style="--chip:${escapeHtml(row.color)}">${escapeHtml(row.name)}${row.isActive ? "" : "（停用・保持）"}</span></label>`).join("") || '<span class="muted">利用可能なラベルはありません</span>'}</div></fieldset>
      ${isNew ? '<label class="switchRow"><input name="is_active" type="checkbox" checked>登録時から有効</label>' : `<label class="switchRow"><input name="is_active" type="checkbox" ${user.isActive ? "checked" : ""}>このユーザーを有効にする</label>`}
      <p class="formError" id="userFormError" role="alert" hidden></p>
      <div class="formActions"><button type="button" class="ghostButton" id="cancelUser">キャンセル</button><button type="submit" class="primaryButton">保存</button></div>
    </form><p class="drawerNote">分析対象は部門から自動決定されます。ラベルはMonitor内の表示・分析だけに使用します。</p></aside></div>`;
    const form = host.querySelector("#userForm");
    const lifecycle = this.installDrawer(form, form.elements.name);
    const updateDepartmentFields = () => {
      const isMr = form.elements.department.value === "DM専任";
      form.elements.mr_experience.disabled = !isMr;
      if (!isMr) form.elements.mr_experience.value = "-";
    };
    const updateHeadquarters = () => {
      if (form.elements.area.value === "本社") form.elements.workplace.value = "虎ノ門";
    };
    updateDepartmentFields();
    updateHeadquarters();
    form.elements.department.addEventListener("change", updateDepartmentFields);
    form.elements.area.addEventListener("change", updateHeadquarters);
    host.querySelector("#closeDrawer").addEventListener("click", lifecycle.close);
    host.querySelector("#cancelUser").addEventListener("click", lifecycle.close);
    form.addEventListener("submit", async (event) => {
      event.preventDefault();
      const submit = form.querySelector('[type="submit"]');
      const errorBox = form.querySelector("#userFormError");
      submit.disabled = true;
      form.setAttribute("aria-busy", "true");
      errorBox.hidden = true;
      const data = new FormData(form);
      const fields = {
        name: data.get("name"), email: data.get("email"), area: data.get("area"), workplace: data.get("workplace"),
        role: data.get("role"), department: data.get("department"), mr_experience: data.get("mr_experience") || "-",
        label_ids: data.getAll("label"), is_active: data.get("is_active") === "on",
      };
      try {
        if (isNew) await createManagedUser(fields, { signal: this.signal });
        else await updateManagedUser(user.rosterId, { ...fields, expected_updated_at: user.updatedAt }, { signal: this.signal });
        lifecycle.markClean();
        this.closeDrawer();
        this.toast("ユーザー情報を保存しました", "success");
        await this.load();
      } catch (error) {
        if (!isCancellation(error)) {
          errorBox.textContent = error.message;
          errorBox.hidden = false;
          this.toast(error.message, "error");
        }
      } finally {
        if (submit.isConnected) submit.disabled = false;
        if (form.isConnected) form.removeAttribute("aria-busy");
      }
    });
  }

  renderLabels() {
    const panel = this.root.querySelector("#managementPanel");
    if (this.errors.labels) {
      panel.innerHTML = `<div class="panelHead"><h3>分析ラベル</h3></div>${moduleMessage(this.errors.labels, "error")}`;
      return;
    }
    panel.innerHTML = `<div class="panelHead"><div><h3>分析ラベル</h3><small>Monitor画面内だけで使用します。権限や分析対象には影響しません。</small></div><button id="newLabel" class="primaryButton" ${this.metadata ? "" : "disabled"}>ラベルを追加</button></div>${this.errors.metadata ? moduleMessage(`色の選択肢を読み込めません: ${this.errors.metadata}`, "error") : ""}${this.issues.labels.length ? moduleMessage(`${this.issues.labels.length}件の不正なラベル行を表示対象から外しました。`, "error") : ""}<div class="labelCards">${this.labels.map((row) => `<article class="labelCard ${row.isActive ? "" : "isInactive"}"><span class="labelSwatch" style="--chip:${escapeHtml(row.color)}"></span><div><strong>${escapeHtml(row.name)}</strong><small>${row.usageCount}名で使用 · ${row.isActive ? "有効" : "停用"}</small></div><button data-edit-label="${escapeHtml(row.labelId)}" class="linkButton">編集</button></article>`).join("") || moduleMessage("ラベルはありません")}</div>`;
    panel.querySelector("#newLabel")?.addEventListener("click", () => this.openLabel(null));
    panel.querySelectorAll("[data-edit-label]").forEach((button) => button.addEventListener("click", () => this.openLabel(this.labels.find((row) => row.labelId === button.dataset.editLabel))));
  }

  openLabel(label) {
    if (!this.metadata) return;
    this.dialogCleanup?.();
    const host = this.root.querySelector("#drawerHost");
    const isNew = !label;
    const colors = this.metadata.labelColors;
    host.innerHTML = `<div class="drawerBackdrop"><aside class="drawer compact" role="dialog" aria-modal="true" aria-labelledby="labelDrawerTitle"><div class="drawerHead"><h3 id="labelDrawerTitle">${isNew ? "ラベルを追加" : "ラベルを編集"}</h3><button id="closeDrawer" type="button" class="iconButton" aria-label="ラベル編集画面を閉じる">×</button></div><form id="labelForm" class="formGrid"><label>名称<input name="name" required maxlength="40" value="${escapeHtml(label?.name || "")}"></label><fieldset><legend>色</legend><div class="colorChoices">${colors.map((color, index) => `<label><input type="radio" name="color" value="${escapeHtml(color)}" ${(label?.color || colors[0]) === color ? "checked" : ""}><span style="--chip:${escapeHtml(color)}" aria-label="色${index + 1}"></span></label>`).join("")}</div></fieldset>${isNew ? "" : `<label class="switchRow"><input type="checkbox" name="is_active" ${label.isActive ? "checked" : ""}>有効</label>`}<p class="formError" id="labelFormError" role="alert" hidden></p><div class="formActions">${!isNew ? `<button type="button" id="deleteLabel" class="dangerButton" ${label.usageCount > 0 ? "disabled aria-describedby=deleteLabelNote" : ""}>削除</button>` : ""}<button type="button" id="cancelLabel" class="ghostButton">キャンセル</button><button type="submit" class="primaryButton">保存</button></div>${!isNew && label.usageCount > 0 ? `<p id="deleteLabelNote" class="fieldNote">${label.usageCount}名に割り当て中のため削除できません。停用は可能です。</p>` : ""}</form></aside></div>`;
    const form = host.querySelector("#labelForm");
    const lifecycle = this.installDrawer(form, form.elements.name);
    host.querySelector("#closeDrawer").addEventListener("click", lifecycle.close);
    host.querySelector("#cancelLabel").addEventListener("click", lifecycle.close);
    form.addEventListener("submit", async (event) => {
      event.preventDefault();
      const submit = form.querySelector('[type="submit"]');
      const errorBox = form.querySelector("#labelFormError");
      const data = new FormData(form);
      submit.disabled = true;
      form.setAttribute("aria-busy", "true");
      errorBox.hidden = true;
      try {
        if (isNew) await createManagedLabel({ name: data.get("name"), color: data.get("color") }, { signal: this.signal });
        else await updateManagedLabel(label.labelId, { name: data.get("name"), color: data.get("color"), is_active: data.get("is_active") === "on", expected_updated_at: label.updatedAt }, { signal: this.signal });
        lifecycle.markClean();
        this.closeDrawer();
        this.toast("ラベルを保存しました", "success");
        await this.load();
      } catch (error) {
        if (!isCancellation(error)) {
          errorBox.textContent = error.message;
          errorBox.hidden = false;
          this.toast(error.message, "error");
        }
      } finally {
        if (submit.isConnected) submit.disabled = false;
        if (form.isConnected) form.removeAttribute("aria-busy");
      }
    });
    host.querySelector("#deleteLabel")?.addEventListener("click", async (event) => {
      if (!window.confirm("この未使用ラベルを削除しますか？")) return;
      event.currentTarget.disabled = true;
      try {
        await deleteManagedLabel(label.labelId, { expected_updated_at: label.updatedAt }, { signal: this.signal });
        lifecycle.markClean();
        this.closeDrawer();
        this.toast("ラベルを削除しました", "success");
        await this.load();
      } catch (error) {
        if (!isCancellation(error)) this.toast(error.message, "error");
        if (event.currentTarget.isConnected) event.currentTarget.disabled = false;
      }
    });
  }
}
