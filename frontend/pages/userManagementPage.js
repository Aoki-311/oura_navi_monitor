import {
  createManagedLabel,
  createManagedUser,
  deleteManagedLabel,
  getManagedLabels,
  getManagedUsers,
  getManagementMetadata,
  isCancellation,
  previewManagedUserScope,
  updateManagedLabel,
  updateManagedUser,
} from "../api/client.js";
import {
  managementLabelsModel,
  managementMetadataModel,
  scopePreviewModel,
  managementUsersModel,
} from "../adapters/managementAdapter.js";
import { chips, compareJapaneseNames, escapeHtml, installDialogLifecycle, moduleMessage, setBusy } from "../components/dom.js";
import { bindPagination, bindResponsiveCollection, compareNullable, paginate, paginationMarkup } from "../components/collection.js";
import { displayDateTime } from "../viewModels/formatters.js";
import {
  normalizeCanonicalEmail,
  normalizeCanonicalText,
} from "../contracts/canonicalText.js";

function normalizeManagementText(value) {
  return normalizeCanonicalText(value);
}

function normalizeManagementEmail(value) {
  return normalizeCanonicalEmail(value);
}

export class UserManagementPage {
  constructor(root, { rosterId = "", navigate, toast, state, signal, isCurrent, clearManagementRoster, setLeaveGuard }) {
    this.root = root;
    this.rosterId = rosterId;
    this.navigate = navigate;
    this.toast = toast;
    this.signal = signal;
    this.isCurrent = isCurrent;
    this.clearManagementRoster = clearManagementRoster;
    this.setLeaveGuard = setLeaveGuard;
    this.users = [];
    this.labels = [];
    this.metadata = null;
    this.errors = { users: "", labels: "", metadata: "" };
    this.issues = { users: [], labels: [] };
    this.subtab = state.managementSubtab;
    this.userSearch = state.managementQuery;
    this.userStatus = state.managementStatus;
    this.userDepartment = state.managementDepartment;
    this.userRole = state.managementRole;
    this.userLabel = state.managementLabel;
    this.userSort = state.managementSort;
    this.userPage = state.managementPage;
    this.dialogCleanup = null;
    this.releaseLeaveGuard = null;
    this.labelCatalogComplete = false;
    this.labelCatalogUsable = false;
    this.labelRelationIssueCount = 0;
    this.compactCollection = bindResponsiveCollection(this.signal, () => this.renderUserRows());
  }

  async load() {
    this.errors = { users: "", labels: "", metadata: "" };
    this.issues = { users: [], labels: [] };
    this.metadata = null;
    this.labelCatalogComplete = false;
    this.labelCatalogUsable = false;
    this.labelRelationIssueCount = 0;
    setBusy(this.root, true);
    if (!this.root.querySelector("#managementBody")) this.root.innerHTML = `<div class="pageHeading"><div><h2>ユーザー管理</h2></div></div><div id="managementBody">${moduleMessage("読み込み中…", "loading")}</div><div id="drawerHost"></div>`;
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
    this.users.forEach((user) => {
      user.scopePolicyVerified = Boolean(
        this.metadata
        && user.scopePolicyVersion === this.metadata.scopePolicyVersion
      );
      if (!user.scopePolicyVerified && !user.rosterIssues.includes("分析対象ポリシーを確認できません")) {
        user.rosterIssues.push("分析対象ポリシーを確認できません");
      }
    });
    this.recomputeLabelCatalogCompleteness();
    if (this.userLabel && !this.labels.some((row) => row.labelId === this.userLabel)) {
      this.userLabel = "";
      this.navigate("management", { managementLabel: "" }, { replace: true, render: false });
    }
    this.render();
    if (this.rosterId && !this.errors.users) {
      const target = this.users.find((row) => row.rosterId === this.rosterId);
      if (target) this.openUser(target);
      else {
        this.toast("編集対象のユーザーが見つかりません。", "error");
        this.clearManagementRoster?.();
      }
    }
    setBusy(this.root, false);
  }

  verifyUserScopes(items) {
    items.forEach((user) => {
      user.scopePolicyVerified = Boolean(
        this.metadata
        && user.scopePolicyVersion === this.metadata.scopePolicyVersion
      );
      if (!user.scopePolicyVerified && !user.rosterIssues.includes("分析対象ポリシーを確認できません")) {
        user.rosterIssues.push("分析対象ポリシーを確認できません");
      }
    });
  }

  recomputeLabelCatalogCompleteness() {
    this.labelCatalogUsable = !this.errors.labels && this.issues.labels.length === 0;
    this.labelCatalogComplete = false;
    this.labelRelationIssueCount = 0;
    if (!this.labelCatalogUsable) return;
    const knownLabels = new Set(this.labels.map((row) => row.labelId));
    const unresolved = this.users.flatMap((user) => user.labelIds.filter((labelId) => !knownLabels.has(labelId)));
    this.labelRelationIssueCount = unresolved.length;
    this.labelCatalogComplete = unresolved.length === 0;
  }

  applyUsersReadback(model) {
    this.users = model.items;
    this.issues.users = model.issues;
    this.errors.users = "";
    this.verifyUserScopes(this.users);
    this.recomputeLabelCatalogCompleteness();
  }

  applyLabelsReadback(model) {
    this.labels = model.items;
    this.issues.labels = model.issues;
    this.errors.labels = "";
    this.recomputeLabelCatalogCompleteness();
  }

  assertSameValues(actual, expected, fields, label) {
    for (const field of fields) {
      if (actual[field] !== expected[field]) throw new Error(`${label}の${field}が保存内容と一致しません。`);
    }
    const actualLabels = [...(actual.labelIds || [])].sort();
    const expectedLabels = [...(expected.labelIds || [])].sort();
    if (JSON.stringify(actualLabels) !== JSON.stringify(expectedLabels)) {
      throw new Error(`${label}の分析ラベルが保存内容と一致しません。`);
    }
  }

  async verifyUserReadback(expected) {
    const raw = await getManagedUsers({ include_inactive: true }, { signal: this.signal });
    if (!raw || !Array.isArray(raw.users)) throw new Error("保存後のユーザー一覧の形式が不正です。");
    let candidates = expected.rosterId
      ? raw.users.filter((row) => row?.rosterId === expected.rosterId)
      : raw.users.filter((row) => normalizeManagementEmail(row?.email) === expected.email);
    if (candidates.length !== 1) throw new Error("保存した対象ユーザーを一意に確認できません。");
    const targetModel = managementUsersModel({ users: candidates });
    if (targetModel.items.length !== 1 || targetModel.issues.length) throw new Error("保存した対象ユーザーの形式が不正です。");
    const target = targetModel.items[0];
    if (expected.rosterId && target.rosterId !== expected.rosterId) throw new Error("保存した対象ユーザーIDが一致しません。");
    if (!target.rosterId || !target.updatedAt) throw new Error("保存した対象ユーザーのIDまたは版を確認できません。");
    if (expected.updatedAt && target.updatedAt !== expected.updatedAt) throw new Error("保存した対象ユーザーの版が一致しません。");
    if (!expected.updatedAt && expected.previousUpdatedAt && target.updatedAt === expected.previousUpdatedAt) {
      throw new Error("保存した対象ユーザーの版が更新されていません。");
    }
    this.assertSameValues(target, expected, [
      "name", "email", "area", "workplace", "role", "department", "mrExperience",
      "isActive", "globalScopeEnabled", "userMapScopeEnabled", "scopePolicyVersion",
    ], "保存したユーザー");
    expected.rosterId = target.rosterId;
    expected.updatedAt = target.updatedAt;
    const allModel = managementUsersModel(raw);
    if (allModel.issues.length) throw new Error("保存後のユーザー一覧に不正な行があり、結果を確定できません。");
    return allModel;
  }

  async verifyLabelReadback(expected) {
    const raw = await getManagedLabels({ include_inactive: true }, { signal: this.signal });
    if (!raw || !Array.isArray(raw.labels)) throw new Error("保存後のラベル一覧の形式が不正です。");
    const candidates = expected.labelId
      ? raw.labels.filter((row) => row?.labelId === expected.labelId)
      : raw.labels.filter((row) => normalizeManagementText(row?.name) === expected.name);
    if (candidates.length !== 1) throw new Error("保存した対象ラベルを一意に確認できません。");
    const targetModel = managementLabelsModel({ labels: candidates });
    if (targetModel.items.length !== 1 || targetModel.issues.length) throw new Error("保存した対象ラベルの形式が不正です。");
    const target = targetModel.items[0];
    if (expected.labelId && target.labelId !== expected.labelId) throw new Error("保存した対象ラベルIDが一致しません。");
    if (!target.labelId || !target.updatedAt) throw new Error("保存した対象ラベルのIDまたは版を確認できません。");
    if (expected.updatedAt && target.updatedAt !== expected.updatedAt) throw new Error("保存した対象ラベルの版が一致しません。");
    if (!expected.updatedAt && expected.previousUpdatedAt && target.updatedAt === expected.previousUpdatedAt) {
      throw new Error("保存した対象ラベルの版が更新されていません。");
    }
    this.assertSameValues(target, expected, ["name", "color", "isActive"], "保存したラベル");
    expected.labelId = target.labelId;
    expected.updatedAt = target.updatedAt;
    const allModel = managementLabelsModel(raw);
    if (allModel.issues.length) throw new Error("保存後のラベル一覧に不正な行があり、結果を確定できません。");
    return allModel;
  }

  async verifyLabelDeletion(labelId) {
    const raw = await getManagedLabels({ include_inactive: true }, { signal: this.signal });
    if (!raw || !Array.isArray(raw.labels)) throw new Error("削除後のラベル一覧の形式が不正です。");
    if (raw.labels.some((row) => row?.labelId === labelId)) throw new Error("削除したラベルが読込結果に残っています。");
    const model = managementLabelsModel(raw);
    if (model.issues.length) throw new Error("削除後のラベル一覧に不正な行があり、結果を確定できません。");
    return model;
  }

  async completeCommittedReadback({ form, lifecycle, errorBox, verify, apply, successMessage }) {
    const retryId = "committedReadbackRetry";
    const setCommittedState = (message) => {
      lifecycle.setPhase("committed_unverified");
      form.removeAttribute("aria-busy");
      [...form.elements].forEach((element) => { element.disabled = true; });
      errorBox.textContent = message;
      errorBox.hidden = false;
      let retry = form.querySelector(`#${retryId}`);
      if (!retry) {
        retry = document.createElement("button");
        retry.id = retryId;
        retry.type = "button";
        retry.className = "ghostButton";
        retry.textContent = "確認を再試行";
        errorBox.insertAdjacentElement("afterend", retry);
      }
      retry.disabled = false;
      retry.onclick = () => { void attempt(); };
    };
    const attempt = async () => {
      lifecycle.setPhase("committed_unverified");
      form.setAttribute("aria-busy", "true");
      const retry = form.querySelector(`#${retryId}`);
      if (retry) retry.disabled = true;
      try {
        const model = await verify();
        if (!form.isConnected || !this.isCurrent()) return false;
        apply(model);
        lifecycle.setPhase("idle");
        this.closeDrawer();
        this.render();
        this.toast(successMessage, "success");
        return true;
      } catch (error) {
        if (!isCancellation(error) && form.isConnected) {
          setCommittedState(`変更は受付済みですが、保存結果を確認できません。${error?.message || "確認を再試行してください。"}`);
          this.toast("変更は受付済みですが、保存結果を確認できません。", "error");
        }
        return false;
      }
    };
    return attempt();
  }

  render() {
    const body = this.root.querySelector("#managementBody");
    if (!body) return;
    body.innerHTML = `
      <div class="managementScopeSummary" aria-label="名簿の分析範囲"><span>名簿 ${this.users.length}名</span><span>全体サマリー ${this.users.filter((row) => row.scopePolicyVerified && row.globalScopeEnabled).length}名</span><span>ユーザー分析 ${this.users.filter((row) => row.scopePolicyVerified && row.userMapScopeEnabled).length}名</span><span>要修正 ${this.users.filter((row) => row.rosterIssues.length).length}名</span></div>
      <div class="subtabs" role="tablist" aria-label="管理対象">
        <button role="tab" aria-selected="${this.subtab === "users"}" data-subtab="users" class="${this.subtab === "users" ? "isActive" : ""}">ユーザー管理 <span>${this.users.length}</span></button>
        <button role="tab" aria-selected="${this.subtab === "labels"}" data-subtab="labels" class="${this.subtab === "labels" ? "isActive" : ""}">ラベル管理 <span>${this.labels.length}</span></button>
      </div>
      <section class="panel" id="managementPanel"></section>`;
    body.querySelectorAll("[data-subtab]").forEach((button) => button.addEventListener("click", () => {
      const closeButton = this.root.querySelector("#closeDrawer");
      if (closeButton) {
        closeButton.click();
        if (this.root.querySelector(".drawer")) return;
      }
      this.subtab = button.dataset.subtab;
      this.navigate("management", { managementSubtab: this.subtab }, { replace: true, render: false });
      this.render();
    }));
    if (this.subtab === "users") this.renderUsers(); else this.renderLabels();
  }

  filteredUsers() {
    const query = this.userSearch.trim().toLocaleLowerCase("ja-JP");
    return this.users.filter((row) => {
      const matchesStatus = this.userStatus === "all" || (this.userStatus === "active" ? row.isActive : !row.isActive);
      const matchesDepartment = !this.userDepartment || row.department === this.userDepartment;
      const matchesRole = !this.userRole || row.role === this.userRole;
      const matchesLabel = !this.userLabel || row.labelIds.includes(this.userLabel);
      const haystack = [row.name, row.email, row.area, row.workplace, row.role, row.department].join(" ").toLocaleLowerCase("ja-JP");
      return matchesStatus && matchesDepartment && matchesRole && matchesLabel && (!query || haystack.includes(query));
    });
  }

  renderUsers() {
    const panel = this.root.querySelector("#managementPanel");
    if (this.errors.users) {
      panel.innerHTML = `<div class="panelHead"><h3>登録ユーザー</h3></div>${moduleMessage(this.errors.users, "error")}`;
      return;
    }
    panel.innerHTML = `
      <div class="panelHead"><div><h3>登録ユーザー</h3></div><button id="newUser" class="primaryButton" ${this.metadata && this.labelCatalogUsable ? "" : "disabled"}>ユーザーを追加</button></div>
      ${this.errors.metadata ? moduleMessage(`編集用の選択肢を読み込めません: ${this.errors.metadata}`, "error") : ""}
      ${!this.labelCatalogUsable ? moduleMessage(`分析ラベルの台帳を確認できないため、ラベル関係の編集と新規ユーザー登録を停止しています。${this.errors.labels ? ` ${this.errors.labels}` : ""}`, "error") : ""}
      ${this.labelCatalogUsable && this.labelRelationIssueCount ? moduleMessage(`${this.labelRelationIssueCount}件の未解決ラベル関係があります。該当ユーザーを編集して保存すると、存在しないラベル参照だけを削除できます。`, "error") : ""}
      ${this.users.some((row) => row.rosterIssues.length) ? moduleMessage(`${this.users.filter((row) => row.rosterIssues.length).length}件の名簿行に修正が必要です。行は削除せず表示しています。`, "error") : ""}
      <div class="managementFilters collectionToolbar"><label>ユーザー検索<input id="userSearch" type="search" value="${escapeHtml(this.userSearch)}" placeholder="氏名・メール・地域"></label><label>状態<select id="userStatus"><option value="all">すべて</option><option value="active" ${this.userStatus === "active" ? "selected" : ""}>有効</option><option value="inactive" ${this.userStatus === "inactive" ? "selected" : ""}>停用</option></select></label><label>役割<select id="userRole"><option value="">すべて</option>${(this.metadata?.roles || []).map((value) => `<option value="${escapeHtml(value)}" ${this.userRole === value ? "selected" : ""}>${escapeHtml(value)}</option>`).join("")}</select></label><label>部門<select id="userDepartment"><option value="">すべて</option>${(this.metadata?.departments || []).map((value) => `<option value="${escapeHtml(value)}" ${this.userDepartment === value ? "selected" : ""}>${escapeHtml(value)}</option>`).join("")}</select></label><label>分析ラベル<select id="userLabel"><option value="">すべて</option>${this.labels.map((row) => `<option value="${escapeHtml(row.labelId)}" ${this.userLabel === row.labelId ? "selected" : ""}>${escapeHtml(row.name)}</option>`).join("")}</select></label><label>並び順<select id="userSort"><option value="name_asc" ${this.userSort === "name_asc" ? "selected" : ""}>社員名順</option><option value="updated_desc" ${this.userSort === "updated_desc" ? "selected" : ""}>更新が新しい順</option><option value="area_asc" ${this.userSort === "area_asc" ? "selected" : ""}>地域順</option></select></label></div>
      <div id="managementUserResults"></div>`;
    panel.querySelector("#newUser")?.addEventListener("click", () => this.openUser(null));
    panel.querySelector("#userSearch").addEventListener("input", (event) => this.updateUserCollection({ search: event.target.value, page: 1 }));
    panel.querySelector("#userStatus").addEventListener("change", (event) => this.updateUserCollection({ status: event.target.value, page: 1 }));
    panel.querySelector("#userRole").addEventListener("change", (event) => this.updateUserCollection({ role: event.target.value, page: 1 }));
    panel.querySelector("#userDepartment").addEventListener("change", (event) => this.updateUserCollection({ department: event.target.value, page: 1 }));
    panel.querySelector("#userLabel").addEventListener("change", (event) => this.updateUserCollection({ label: event.target.value, page: 1 }));
    panel.querySelector("#userSort").addEventListener("change", (event) => this.updateUserCollection({ sort: event.target.value, page: 1 }));
    this.renderUserRows();
  }

  updateUserCollection({ search = this.userSearch, status = this.userStatus, role = this.userRole, department = this.userDepartment, label = this.userLabel, sort = this.userSort, page = this.userPage }) {
    this.userSearch = search;
    this.userStatus = status;
    this.userRole = role;
    this.userDepartment = department;
    this.userLabel = label;
    this.userSort = sort;
    this.userPage = page;
    this.navigate("management", { managementQuery: search, managementStatus: status, managementRole: role, managementDepartment: department, managementLabel: label, managementSort: sort, managementPage: page }, { replace: true, render: false });
    this.renderUserRows();
  }

  renderUserRows() {
    const target = this.root.querySelector("#managementUserResults");
    if (!target) return;
    const labelMap = new Map(this.labels.map((row) => [row.labelId, row]));
    const labelFor = (id) => labelMap.get(id) || { labelId: id, name: `未解決: ${id}`, color: "#5f6285" };
    const rows = this.filteredUsers();
    const sorters = {
      name_asc: (a, b) => compareJapaneseNames(a.name, b.name) || compareJapaneseNames(a.email, b.email),
      updated_desc: (a, b) => compareNullable(a.updatedAt, b.updatedAt),
      area_asc: (a, b) => compareNullable(`${a.area} ${a.name}`, `${b.area} ${b.name}`, "asc"),
    };
    rows.sort(sorters[this.userSort] || sorters.name_asc);
    const page = paginate(rows, this.userPage, this.compactCollection.matches ? 8 : 20);
    if (page.page !== this.userPage) {
      this.userPage = page.page;
      this.navigate("management", { managementPage: page.page }, { replace: true, render: false });
    }
    const scopeText = (row) => !row.scopePolicyVerified ? "対象判定未確認" : row.globalScopeEnabled ? "全体サマリー・ユーザー分析" : row.userMapScopeEnabled ? "ユーザー分析のみ" : "管理のみ";
    const issueText = (row) => row.rosterIssues.length ? `<small class="rowIssue">要修正: ${escapeHtml(row.rosterIssues.join(", "))}</small>` : "";
    const tableRows = page.items.map((row) => `<tr class="${row.isActive ? "" : "isInactive"}"><td><strong>${escapeHtml(row.name)}</strong><small>${escapeHtml(row.email)}</small>${issueText(row)}</td><td>${escapeHtml(row.area)}<small>${escapeHtml(row.workplace)}</small></td><td>${escapeHtml(row.role || "未設定")}<small>${escapeHtml(row.department || "未設定")}</small></td><td>${row.labelIds.length ? `<div class="chips">${chips(row.labelIds.map(labelFor))}</div>` : ""}</td><td><span class="scopeBadge">${scopeText(row)}</span></td><td><span class="statusBadge ${row.isActive ? "active" : "inactive"}">${row.isActive ? "有効" : "停用"}</span></td><td>${displayDateTime(row.updatedAt)}</td><td><button class="linkButton" data-edit-user="${escapeHtml(row.rosterId)}" ${row.rosterId && this.metadata ? "" : "disabled"}>編集</button></td></tr>`).join("");
    const cards = page.items.map((row) => `<article class="userCard managementCard"><header><div><strong>${escapeHtml(row.name)}</strong><small>${escapeHtml(row.email)}</small>${issueText(row)}</div><span class="statusBadge ${row.isActive ? "active" : "inactive"}">${row.isActive ? "有効" : "停用"}</span></header><dl><div><dt>地域</dt><dd>${escapeHtml(row.area)}・${escapeHtml(row.workplace)}</dd></div><div><dt>役割・部門</dt><dd>${escapeHtml(row.role || "未設定")}・${escapeHtml(row.department || "未設定")}</dd></div><div><dt>分析範囲</dt><dd>${scopeText(row)}</dd></div><div><dt>分析ラベル</dt><dd>${row.labelIds.length ? `<span class="chips">${chips(row.labelIds.map(labelFor))}</span>` : "なし"}</dd></div></dl><button class="linkButton" data-edit-user="${escapeHtml(row.rosterId)}" ${row.rosterId && this.metadata ? "" : "disabled"}>編集</button></article>`).join("");
    target.innerHTML = page.total ? `<div class="desktopTable"><div class="tableScroll" tabindex="0" aria-label="管理ユーザー一覧"><table><caption>Monitorに登録されたユーザー</caption><thead><tr><th>社員名 / メール</th><th>地域・勤務地</th><th>役割・部門</th><th>ラベル</th><th>分析範囲</th><th>状態</th><th>最終更新</th><th></th></tr></thead><tbody>${tableRows}</tbody></table></div></div><div class="mobileCards">${cards}</div>${paginationMarkup(page)}` : moduleMessage("条件に一致するユーザーはいません。", "empty");
    target.querySelectorAll("[data-edit-user]").forEach((button) => button.addEventListener("click", () => this.openUser(this.users.find((row) => row.rosterId === button.dataset.editUser))));
    bindPagination(target, page, (next) => this.updateUserCollection({ page: next }));
  }

  closeDrawer() {
    this.dialogCleanup?.();
    this.dialogCleanup = null;
    this.releaseLeaveGuard?.();
    this.releaseLeaveGuard = null;
    this.root.querySelector("#drawerHost")?.replaceChildren();
    this.rosterId = "";
    this.clearManagementRoster?.();
  }

  installDrawer(form, initialFocus) {
    let cleanValue = JSON.stringify([...new FormData(form).entries()]);
    let phase = "idle";
    const dirty = () => cleanValue !== JSON.stringify([...new FormData(form).entries()]);
    this.releaseLeaveGuard?.();
    this.releaseLeaveGuard = this.setLeaveGuard?.(() => ({ dirty: dirty(), phase })) || null;
    const close = () => {
      if (phase === "saving" || phase === "deleting" || phase === "committed_unverified") {
        this.toast(
          phase === "committed_unverified"
            ? "変更は受付済みですが、保存結果を確認できていません。確認が完了するまで編集画面を閉じられません。"
            : phase === "deleting"
            ? "削除結果を確認中です。完了するまで編集画面を閉じられません。"
            : "保存結果を確認中です。完了するまで編集画面を閉じられません。",
          "error",
        );
        return;
      }
      if (dirty() && !window.confirm("保存していない変更を破棄しますか？")) return;
      cleanValue = JSON.stringify([...new FormData(form).entries()]);
      this.closeDrawer();
    };
    this.dialogCleanup = installDialogLifecycle(form.closest("[role=dialog]"), { onClose: close, initialFocus });
    return {
      close,
      markClean: () => { cleanValue = JSON.stringify([...new FormData(form).entries()]); },
      setPhase: (nextPhase) => { phase = nextPhase; },
      getPhase: () => phase,
    };
  }

  openUser(user) {
    if (user === undefined || !this.metadata) return;
    this.dialogCleanup?.();
    this.releaseLeaveGuard?.();
    this.releaseLeaveGuard = null;
    const host = this.root.querySelector("#drawerHost");
    const isNew = !user;
    const selectedLabels = new Set(user?.labelIds || []);
    const knownLabelIds = new Set(this.labels.map((row) => row.labelId));
    const danglingLabelIds = [...selectedLabels].filter((labelId) => !knownLabelIds.has(labelId));
    const labelChoices = this.labels.filter((row) => row.isActive || selectedLabels.has(row.labelId));
    const roleChoices = [...new Set([...(this.metadata.roles || []), ...(user?.role ? [user.role] : [])])];
    const labelEditingEnabled = this.labelCatalogUsable;
    const areaNeedsRepair = !isNew && !this.metadata.areas.includes(user?.area);
    const roleNeedsRepair = !isNew && !String(user?.role || "").trim();
    const departmentNeedsRepair = !isNew && !this.metadata.departments.includes(user?.department);
    const repairOption = (field, value) => `<option value="" selected disabled>要修正: ${field}${value ? `（現在値: ${escapeHtml(value)}）` : "（未設定）"}</option>`;
    const areaOptions = `${areaNeedsRepair ? repairOption("エリア", user?.area) : ""}${this.metadata.areas.map((value) => `<option value="${escapeHtml(value)}" ${user?.area === value ? "selected" : ""}>${escapeHtml(value)}</option>`).join("")}`;
    const roleOptions = `${roleNeedsRepair ? repairOption("役割", user?.role) : ""}${roleChoices.map((value) => `<option value="${escapeHtml(value)}" ${user?.role === value ? "selected" : ""}>${escapeHtml(value)}</option>`).join("")}`;
    const departmentOptions = `${departmentNeedsRepair ? repairOption("部門", user?.department) : ""}${this.metadata.departments.map((value) => `<option value="${escapeHtml(value)}" ${user?.department === value ? "selected" : ""}>${escapeHtml(value)}</option>`).join("")}`;
    host.innerHTML = `<div class="drawerBackdrop"><aside class="drawer" role="dialog" aria-modal="true" aria-labelledby="userDrawerTitle"><div class="drawerHead"><div><p class="eyebrow">${isNew ? "新規登録" : "名簿編集"}</p><h3 id="userDrawerTitle">${isNew ? "ユーザーを追加" : escapeHtml(user.name)}</h3></div><button id="closeDrawer" type="button" class="iconButton" aria-label="編集画面を閉じる">×</button></div><form id="userForm" class="formGrid">
      <label>社員名<input name="name" required maxlength="120" value="${escapeHtml(user?.name || "")}"></label>
      <label>メール<input name="email" type="email" required ${user?.identityBound ? "readonly aria-describedby=boundEmailNote" : ""} value="${escapeHtml(user?.email || "")}"></label>
      ${user?.identityBound ? '<p id="boundEmailNote" class="fieldNote">LCS利用履歴と連携済みのため、メールは変更できません。</p>' : ""}
      <label>エリア<select name="area" required>${areaOptions}</select></label>
      <label>勤務地<input name="workplace" list="workplaceOptions" required maxlength="80" value="${escapeHtml(user?.workplace || "")}"><datalist id="workplaceOptions">${this.metadata.workplaces.map((value) => `<option value="${escapeHtml(value)}"></option>`).join("")}</datalist></label>
      <label>役割<select name="role" required>${roleOptions}</select></label>
      <label>部門<select name="department" required>${departmentOptions}</select></label>
      <label>MR経験<input name="mr_experience" maxlength="80" value="${escapeHtml(user?.mrExperience || "-")}"></label>
      <fieldset ${labelEditingEnabled ? "" : "disabled"}><legend>分析ラベル</legend><div class="labelChoices">${labelChoices.map((row) => `<label><input type="checkbox" name="label" value="${escapeHtml(row.labelId)}" ${selectedLabels.has(row.labelId) ? "checked" : ""} ${row.isActive ? "" : "disabled"}><span style="--chip:${escapeHtml(row.color)}">${escapeHtml(row.name)}${row.isActive ? "" : "（停用・保持）"}</span></label>`).join("") || '<span class="muted">利用可能な分析ラベルはありません</span>'}</div>${!labelEditingEnabled ? '<p class="fieldNote">ラベル台帳を確認できないため、現在の関係を変更せず保存します。</p>' : danglingLabelIds.length ? `<p class="fieldNote">存在しないラベル参照（${escapeHtml(danglingLabelIds.join("、"))}）は、このユーザーを保存すると削除されます。</p>` : ""}</fieldset>
      ${isNew ? '<label class="switchRow"><input name="is_active" type="checkbox" checked>登録時から有効</label>' : `<label class="switchRow"><input name="is_active" type="checkbox" ${user.isActive ? "checked" : ""}>このユーザーを有効にする</label>`}
      <p class="scopeImpact" id="scopeImpact" role="status"></p><p class="formError" id="userFormError" role="alert" hidden></p>
      <div class="formActions"><button type="button" class="ghostButton" id="cancelUser">キャンセル</button><button type="submit" class="primaryButton" disabled>保存</button></div>
    </form></aside></div>`;
    const form = host.querySelector("#userForm");
    const lifecycle = this.installDrawer(form, form.elements.name);
    const submitButton = form.querySelector('[type="submit"]');
    const updateDepartmentFields = () => {
      const department = form.elements.department.value;
      const isMr = department === "DM専任";
      form.elements.mr_experience.disabled = !isMr;
      if (this.metadata.departments.includes(department) && !isMr) form.elements.mr_experience.value = "-";
    };
    let previewGeneration = 0;
    let scopePreviewVerified = false;
    let verifiedScopePreview = null;
    let verifiedScopeInput = "";
    const scopeInput = () => ({
      role: form.elements.role.value,
      department: form.elements.department.value,
      is_active: form.elements.is_active.checked,
    });
    const scopeInputKey = () => JSON.stringify(scopeInput());
    const repairFields = () => [
      this.metadata.areas.includes(form.elements.area.value) ? "" : "エリア",
      String(form.elements.role.value || "").trim() ? "" : "役割",
      this.metadata.departments.includes(form.elements.department.value) ? "" : "部門",
    ].filter(Boolean);
    const updateScopeImpact = async () => {
      const generation = ++previewGeneration;
      const target = form.querySelector("#scopeImpact");
      scopePreviewVerified = false;
      verifiedScopePreview = null;
      verifiedScopeInput = "";
      submitButton.disabled = true;
      const unresolvedFields = repairFields();
      if (unresolvedFields.length) {
        target.textContent = `要修正: ${unresolvedFields.join("・")}を選択してください。選択するまで保存できません。`;
        return;
      }
      target.textContent = "保存後の分析対象を確認中です。";
      try {
        const input = scopeInput();
        const inputKey = JSON.stringify(input);
        const preview = scopePreviewModel(
          await previewManagedUserScope(input, { signal: this.signal }),
          this.metadata.scopePolicyVersion,
        );
        if (!form.isConnected || generation !== previewGeneration) return;
        scopePreviewVerified = true;
        verifiedScopePreview = preview;
        verifiedScopeInput = inputKey;
        submitButton.disabled = false;
        target.textContent = preview.globalScopeEnabled
          ? "保存後: 全体サマリーとユーザー分析の両方に含まれます。"
          : preview.userMapScopeEnabled
            ? "保存後: ユーザー分析に含まれ、全体サマリーには含まれません。"
            : "保存後: ユーザー管理だけに表示され、分析対象には含まれません。";
      } catch (error) {
        if (!isCancellation(error) && form.isConnected && generation === previewGeneration) {
          scopePreviewVerified = false;
          submitButton.disabled = true;
          target.textContent = error?.message || "分析対象を確認できません。";
        }
      }
    };
    const updateHeadquarters = () => {
      if (form.elements.area.value === "本社") form.elements.workplace.value = "虎ノ門";
    };
    updateDepartmentFields();
    if (isNew) updateHeadquarters();
    lifecycle.markClean();
    updateScopeImpact();
    form.elements.role.addEventListener("change", updateScopeImpact);
    form.elements.department.addEventListener("change", () => { updateDepartmentFields(); updateScopeImpact(); });
    form.elements.area.addEventListener("change", () => { updateHeadquarters(); updateScopeImpact(); });
    form.elements.is_active.addEventListener("change", updateScopeImpact);
    host.querySelector("#closeDrawer").addEventListener("click", lifecycle.close);
    host.querySelector("#cancelUser").addEventListener("click", lifecycle.close);
    form.addEventListener("submit", async (event) => {
      event.preventDefault();
      if (lifecycle.getPhase() !== "idle") return;
      const submit = submitButton;
      const errorBox = form.querySelector("#userFormError");
      if (!scopePreviewVerified || !verifiedScopePreview || verifiedScopeInput !== scopeInputKey() || repairFields().length) {
        errorBox.textContent = repairFields().length
          ? "要修正の名簿項目を選択するまで保存できません。"
          : "分析対象の確認が完了していないため保存できません。";
        errorBox.hidden = false;
        submit.disabled = true;
        return;
      }
      submit.disabled = true;
      form.setAttribute("aria-busy", "true");
      lifecycle.setPhase("saving");
      errorBox.hidden = true;
      const data = new FormData(form);
      const fields = {
        name: data.get("name"), email: data.get("email"), area: data.get("area"), workplace: data.get("workplace"),
        role: data.get("role"), department: data.get("department"), mr_experience: data.get("mr_experience") || "-",
        is_active: data.get("is_active") === "on",
        expected_scope_policy_version: verifiedScopePreview.scopePolicyVersion,
      };
      if (labelEditingEnabled) {
        const preservedInactiveLabelIds = labelChoices
          .filter((row) => !row.isActive && selectedLabels.has(row.labelId))
          .map((row) => row.labelId);
        fields.label_ids = [
          ...new Set([
            ...data.getAll("label"),
            ...preservedInactiveLabelIds,
          ]),
        ];
      }
      const expected = {
        rosterId: isNew ? "" : user.rosterId,
        previousUpdatedAt: user?.updatedAt || "",
        updatedAt: "",
        name: normalizeManagementText(fields.name),
        email: normalizeManagementEmail(fields.email),
        area: normalizeManagementText(fields.area),
        workplace: normalizeManagementText(fields.workplace),
        role: normalizeManagementText(fields.role),
        department: normalizeManagementText(fields.department),
        mrExperience: normalizeManagementText(fields.mr_experience) || "-",
        labelIds: Object.hasOwn(fields, "label_ids") ? [...fields.label_ids] : [...(user?.labelIds || [])],
        isActive: fields.is_active,
        globalScopeEnabled: verifiedScopePreview.globalScopeEnabled,
        userMapScopeEnabled: verifiedScopePreview.userMapScopeEnabled,
        scopePolicyVersion: verifiedScopePreview.scopePolicyVersion,
      };
      let writeSucceeded = false;
      const verifyCommitted = () => this.completeCommittedReadback({
        form,
        lifecycle,
        errorBox,
        verify: () => this.verifyUserReadback(expected),
        apply: (model) => this.applyUsersReadback(model),
        successMessage: "ユーザー情報を保存しました",
      });
      try {
        const saved = isNew
          ? await createManagedUser(fields, { signal: this.signal })
          : await updateManagedUser(user.rosterId, { ...fields, expected_updated_at: user.updatedAt }, { signal: this.signal });
        writeSucceeded = true;
        lifecycle.markClean();
        lifecycle.setPhase("committed_unverified");
        try {
          const responseModel = managementUsersModel({ users: [saved] });
          const responseUser = responseModel.items[0];
          if (!responseUser || responseModel.issues.length) throw new Error("保存応答のユーザー形式が不正です。");
          if (!isNew && responseUser.rosterId !== user.rosterId) throw new Error("保存応答のユーザーIDが一致しません。");
          this.assertSameValues(responseUser, expected, [
            "name", "email", "area", "workplace", "role", "department", "mrExperience",
            "isActive", "globalScopeEnabled", "userMapScopeEnabled", "scopePolicyVersion",
          ], "保存応答");
          expected.rosterId = responseUser.rosterId;
          expected.updatedAt = responseUser.updatedAt;
        } catch (_responseError) {
          // The write may already be committed. Only the canonical GET readback
          // can settle it; never repeat the mutation from this point onward.
        }
        await verifyCommitted();
      } catch (error) {
        if (error?.code === "readback_conflict") {
          writeSucceeded = true;
          lifecycle.markClean();
          lifecycle.setPhase("committed_unverified");
          await verifyCommitted();
        } else if (!isCancellation(error)) {
          if (error?.code === "scope_policy_conflict") {
            scopePreviewVerified = false;
            verifiedScopePreview = null;
            form.querySelector("#scopeImpact").textContent = error.message;
          }
          errorBox.textContent = error.message;
          errorBox.hidden = false;
          this.toast(error.message, "error");
        }
      } finally {
        if (!writeSucceeded) {
          lifecycle.setPhase("idle");
          if (submit.isConnected) submit.disabled = !scopePreviewVerified;
          if (form.isConnected) form.removeAttribute("aria-busy");
        }
      }
    });
  }

  renderLabels() {
    const panel = this.root.querySelector("#managementPanel");
    if (this.errors.labels) {
      panel.innerHTML = `<div class="panelHead"><h3>分析ラベル</h3></div>${moduleMessage(this.errors.labels, "error")}`;
      return;
    }
    const hasRepairableConflict = this.labels.some((row) => this.isRepairableLabel(row));
    const catalogIssue = this.issues.labels.length
      ? hasRepairableConflict
        ? moduleMessage("同名として判定されたラベルがあります。対象行は名称の修復だけ可能です。新規追加・削除・色や状態・ユーザーとの関係変更は停止しています。", "error")
        : moduleMessage(`${this.issues.labels.length}件のラベル異常を検出したため、ラベル編集を停止しています。`, "error")
      : "";
    panel.innerHTML = `<div class="panelHead"><div><h3>分析ラベル</h3></div><button id="newLabel" class="primaryButton" ${this.metadata && this.labelCatalogUsable ? "" : "disabled"}>ラベルを追加</button></div>${this.errors.metadata ? moduleMessage(`色の選択肢を読み込めません: ${this.errors.metadata}`, "error") : ""}${catalogIssue}<div class="labelCards">${this.labels.map((row) => `<article class="labelCard ${row.isActive ? "" : "isInactive"}"><span class="labelSwatch" style="--chip:${escapeHtml(row.color)}"></span><div><strong>${escapeHtml(row.name)}</strong><small>${row.usageCount}名で使用 · ${row.isActive ? "有効" : "停用"}</small>${row.labelIssues.length ? `<small>${escapeHtml(row.labelIssues.join(" / "))}</small>` : ""}</div><button data-edit-label="${escapeHtml(row.labelId)}" class="linkButton" ${this.labelCatalogUsable || this.isRepairableLabel(row) ? "" : "disabled"}>${this.isRepairableLabel(row) ? "名称を修復" : "編集"}</button></article>`).join("") || moduleMessage("ラベルはありません")}</div>`;
    panel.querySelector("#newLabel")?.addEventListener("click", () => this.openLabel(null));
    panel.querySelectorAll("[data-edit-label]").forEach((button) => button.addEventListener("click", () => this.openLabel(this.labels.find((row) => row.labelId === button.dataset.editLabel))));
  }

  isRepairableLabel(label) {
    return Boolean(
      label?.labelId
      && Array.isArray(label.labelIssues)
      && label.labelIssues.length === 1
      && label.labelIssues[0] === "duplicate_label_name"
    );
  }

  openLabel(label) {
    const repairMode = !this.labelCatalogUsable && this.isRepairableLabel(label);
    if (!this.metadata || (!this.labelCatalogUsable && !repairMode)) return;
    this.dialogCleanup?.();
    this.releaseLeaveGuard?.();
    this.releaseLeaveGuard = null;
    const host = this.root.querySelector("#drawerHost");
    const isNew = !label;
    const colors = this.metadata.labelColors;
    host.innerHTML = `<div class="drawerBackdrop"><aside class="drawer compact" role="dialog" aria-modal="true" aria-labelledby="labelDrawerTitle"><div class="drawerHead"><h3 id="labelDrawerTitle">${repairMode ? "重複したラベル名を修復" : isNew ? "ラベルを追加" : "ラベルを編集"}</h3><button id="closeDrawer" type="button" class="iconButton" aria-label="ラベル編集画面を閉じる">×</button></div><form id="labelForm" class="formGrid">${repairMode ? '<p class="fieldNote">この行の名称だけ変更できます。保存後にラベル全体を再確認し、競合が解消した場合だけ完了します。</p>' : ""}<label>名称<input name="name" required maxlength="40" value="${escapeHtml(label?.name || "")}"></label><fieldset ${repairMode ? "disabled" : ""}><legend>色</legend><div class="colorChoices">${colors.map((color, index) => `<label><input type="radio" name="color" value="${escapeHtml(color)}" ${(label?.color || colors[0]) === color ? "checked" : ""}><span style="--chip:${escapeHtml(color)}" aria-label="色${index + 1}"></span></label>`).join("")}</div></fieldset>${isNew ? "" : `<label class="switchRow"><input type="checkbox" name="is_active" ${label.isActive ? "checked" : ""} ${repairMode ? "disabled" : ""}>有効</label>`}<p class="formError" id="labelFormError" role="alert" hidden></p><div class="formActions">${!isNew ? `<button type="button" id="deleteLabel" class="dangerButton" ${repairMode || label.usageCount > 0 ? "disabled aria-describedby=deleteLabelNote" : ""}>削除</button>` : ""}<button type="button" id="cancelLabel" class="ghostButton">キャンセル</button><button type="submit" class="primaryButton">${repairMode ? "名称を修復" : "保存"}</button></div>${!isNew && (repairMode || label.usageCount > 0) ? `<p id="deleteLabelNote" class="fieldNote">${repairMode ? "修復中は削除・色・有効状態を変更できません。" : `${label.usageCount}名に割り当て中のため削除できません。停用は可能です。`}</p>` : ""}</form></aside></div>`;
    const form = host.querySelector("#labelForm");
    const lifecycle = this.installDrawer(form, form.elements.name);
    host.querySelector("#closeDrawer").addEventListener("click", lifecycle.close);
    host.querySelector("#cancelLabel").addEventListener("click", lifecycle.close);
    form.addEventListener("submit", async (event) => {
      event.preventDefault();
      if (lifecycle.getPhase() !== "idle") return;
      const submit = form.querySelector('[type="submit"]');
      const errorBox = form.querySelector("#labelFormError");
      const data = new FormData(form);
      const requestedColor = String(data.get("color") || label?.color || "").trim();
      const requestedActive = repairMode ? Boolean(label?.isActive) : isNew ? true : data.get("is_active") === "on";
      submit.disabled = true;
      form.setAttribute("aria-busy", "true");
      lifecycle.setPhase("saving");
      errorBox.hidden = true;
      const expected = {
        labelId: isNew ? "" : label.labelId,
        previousUpdatedAt: label?.updatedAt || "",
        updatedAt: "",
        name: normalizeManagementText(data.get("name")),
        color: requestedColor.toLocaleLowerCase("und"),
        isActive: requestedActive,
      };
      let writeSucceeded = false;
      const verifyCommitted = () => this.completeCommittedReadback({
        form,
        lifecycle,
        errorBox,
        verify: () => this.verifyLabelReadback(expected),
        apply: (model) => this.applyLabelsReadback(model),
        successMessage: "ラベルを保存しました",
      });
      try {
        const saved = isNew
          ? await createManagedLabel({ name: data.get("name"), color: requestedColor }, { signal: this.signal })
          : await updateManagedLabel(label.labelId, { name: data.get("name"), color: requestedColor, is_active: requestedActive, expected_updated_at: label.updatedAt }, { signal: this.signal });
        writeSucceeded = true;
        lifecycle.markClean();
        lifecycle.setPhase("committed_unverified");
        try {
          const responseModel = managementLabelsModel({ labels: [saved] });
          const responseLabel = responseModel.items[0];
          if (!responseLabel || responseModel.issues.length) throw new Error("保存応答のラベル形式が不正です。");
          if (!isNew && responseLabel.labelId !== label.labelId) throw new Error("保存応答のラベルIDが一致しません。");
          this.assertSameValues(responseLabel, expected, ["name", "color", "isActive"], "保存応答");
          expected.labelId = responseLabel.labelId;
          expected.updatedAt = responseLabel.updatedAt;
        } catch (_responseError) {
          // A successful mutation response is irreversible here. The retry
          // owner below is read-only even when the response contract is bad.
        }
        await verifyCommitted();
      } catch (error) {
        if (error?.code === "readback_conflict") {
          writeSucceeded = true;
          lifecycle.markClean();
          lifecycle.setPhase("committed_unverified");
          await verifyCommitted();
        } else if (!isCancellation(error)) {
          errorBox.textContent = error.message;
          errorBox.hidden = false;
          this.toast(error.message, "error");
        }
      } finally {
        if (!writeSucceeded) {
          lifecycle.setPhase("idle");
          if (submit.isConnected) submit.disabled = false;
          if (form.isConnected) form.removeAttribute("aria-busy");
        }
      }
    });
    host.querySelector("#deleteLabel")?.addEventListener("click", async (event) => {
      if (lifecycle.getPhase() !== "idle") return;
      if (!window.confirm("この未使用ラベルを削除しますか？")) return;
      event.currentTarget.disabled = true;
      lifecycle.setPhase("deleting");
      const errorBox = form.querySelector("#labelFormError");
      errorBox.hidden = true;
      let writeSucceeded = false;
      const verifyCommitted = () => this.completeCommittedReadback({
        form,
        lifecycle,
        errorBox,
        verify: () => this.verifyLabelDeletion(label.labelId),
        apply: (model) => this.applyLabelsReadback(model),
        successMessage: "ラベルを削除しました",
      });
      try {
        await deleteManagedLabel(label.labelId, { expected_updated_at: label.updatedAt }, { signal: this.signal });
        writeSucceeded = true;
        lifecycle.markClean();
        lifecycle.setPhase("committed_unverified");
        await verifyCommitted();
      } catch (error) {
        if (error?.code === "readback_conflict") {
          writeSucceeded = true;
          lifecycle.markClean();
          lifecycle.setPhase("committed_unverified");
          await verifyCommitted();
        } else if (!isCancellation(error)) {
          errorBox.textContent = error.message;
          errorBox.hidden = false;
          this.toast(error.message, "error");
        }
      } finally {
        if (!writeSucceeded) {
          lifecycle.setPhase("idle");
          if (event.currentTarget.isConnected) event.currentTarget.disabled = false;
        }
      }
    });
  }
}
