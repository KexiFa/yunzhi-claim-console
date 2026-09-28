(() => {
  const toastEl = document.getElementById("toast");
  let boot = window.__BOOT__ || { accounts: [], stats: {}, settings: {} };
  let reLoginId = "";

  function toast(msg, isErr) {
    toastEl.hidden = false;
    toastEl.textContent = msg;
    toastEl.classList.toggle("err", !!isErr);
    clearTimeout(toastEl._t);
    toastEl._t = setTimeout(() => { toastEl.hidden = true; }, 2800);
  }

  async function api(url, opts = {}) {
    const res = await fetch(url, {
      credentials: "same-origin",
      headers: { "Content-Type": "application/json", ...(opts.headers || {}) },
      ...opts,
    });
    if (res.status === 401) {
      location.href = "/login";
      throw new Error("未登录");
    }
    const data = await res.json().catch(() => ({}));
    if (!res.ok || data.ok === false) throw new Error(data.error || "请求失败");
    return data;
  }

  function escapeHtml(s) {
    return String(s)
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;");
  }

  function setText(id, v) {
    const el = document.getElementById(id);
    if (el) el.textContent = v == null ? "-" : String(v);
  }

  function applyStats(stats) {
    if (!stats) return;
    setText("stat-total", stats.total);
    setText("stat-enabled", stats.enabled);
    setText("stat-token", stats.token_ok);
    setText("stat-today", stats.today_ok);
  }

  function statusTag(a) {
    if (!a.has_token) return `<span class="tag warn">未配置</span>`;
    if (a.token_status === "已失效") return `<span class="tag warn">已失效</span>`;
    return `<span class="tag ok">${escapeHtml(a.token_status || "已配置")}</span>`;
  }

  function cardHtml(a) {
    return `
      <article class="account-card" data-id="${a.id}">
        <div class="acc-head">
          <div class="acc-who">
            <span class="acc-avatar">${escapeHtml(a.avatar || "?")}</span>
            <div>
              <div class="acc-name">${escapeHtml(a.name || "云智账号")} <span class="phone">${escapeHtml(a.phone_mask || "")}</span></div>
              <div class="tags">
                ${statusTag(a)}
                <span class="tag ${a.enabled ? "ok" : "mute"}">${a.enabled ? "定时开启" : "已暂停"}</span>
                ${a.last_status ? `<span class="tag">${escapeHtml(a.last_status)}</span>` : ""}
              </div>
            </div>
          </div>
          <div class="acc-ops">
            <button type="button" class="icon-btn btn-edit" title="改名">编辑</button>
            <button type="button" class="icon-btn danger btn-del" title="删除">删除</button>
          </div>
        </div>
        <div class="box">
          <div class="box-title">领取状态</div>
          <div class="pulse-meta">
            <span>倒计时: <b class="countdown">${Number(a.countdown) || 0}</b>s</span>
            <span class="muted small">userId ${escapeHtml(a.user_id || "-")}</span>
          </div>
          <div class="action-line">当前: <span class="last-action">${escapeHtml(a.last_action || "待机")}</span></div>
          <div class="muted small">上次成功: <span class="last-ok">${escapeHtml(a.last_ok || "-")}</span>
            · Token 获取: ${escapeHtml(a.token_get_time || "-")}</div>
          ${a.last_message ? `<div class="muted small">详情: ${escapeHtml(a.last_message)}</div>` : ""}
        </div>
        <div class="acc-foot">
          <label class="switch"><input type="checkbox" class="enabled-toggle" ${a.enabled ? "checked" : ""} /> 启用定时领取</label>
          <div class="row">
            <button type="button" class="btn sm btn-relogin">短信续期</button>
            <button type="button" class="btn sm primary btn-claim">立即领取</button>
          </div>
        </div>
      </article>`;
  }

  function renderAccounts(accounts) {
    const grid = document.getElementById("account-grid");
    if (!accounts || !accounts.length) {
      grid.innerHTML = `<div class="empty">还没有账号，点击右上角「添加账号」通过短信登录添加。</div>`;
      return;
    }
    grid.innerHTML = accounts.map(cardHtml).join("");
    bindCardEvents();
  }

  function applyDashboard(data) {
    if (data.stats) applyStats(data.stats);
    if (data.accounts) {
      boot.accounts = data.accounts;
      renderAccounts(data.accounts);
    }
    if (data.settings) boot.settings = data.settings;
  }

  function openModal(id) {
    document.getElementById(id).hidden = false;
  }
  function closeModal(id) {
    document.getElementById(id).hidden = true;
  }

  document.querySelectorAll("[data-close]").forEach((btn) => {
    btn.addEventListener("click", () => closeModal(btn.dataset.close));
  });

  function bindCardEvents() {
    document.querySelectorAll(".account-card").forEach((card) => {
      const id = card.dataset.id;

      card.querySelector(".btn-claim")?.addEventListener("click", async (ev) => {
        const btn = ev.currentTarget;
        btn.disabled = true;
        btn.textContent = "领取中…";
        try {
          const data = await api(`/api/accounts/${id}/claim`, { method: "POST", body: "{}" });
          applyDashboard(data.dashboard || {});
          toast(`${data.result?.status || "ok"}: ${data.result?.message || "完成"}`);
        } catch (e) {
          toast(e.message || "领取失败", true);
          refreshLite();
        } finally {
          btn.disabled = false;
          btn.textContent = "立即领取";
        }
      });

      card.querySelector(".enabled-toggle")?.addEventListener("change", async (ev) => {
        try {
          const data = await api(`/api/accounts/${id}`, {
            method: "PATCH",
            body: JSON.stringify({ enabled: !!ev.target.checked }),
          });
          applyDashboard(data.dashboard || {});
          toast(ev.target.checked ? "已开启定时领取" : "已暂停");
        } catch (e) {
          toast(e.message || "更新失败", true);
          ev.target.checked = !ev.target.checked;
        }
      });

      card.querySelector(".btn-edit")?.addEventListener("click", async () => {
        const name = prompt("备注名", card.querySelector(".acc-name")?.childNodes[0]?.textContent?.trim() || "");
        if (name == null) return;
        try {
          const data = await api(`/api/accounts/${id}`, {
            method: "PATCH",
            body: JSON.stringify({ name }),
          });
          applyDashboard(data.dashboard || {});
          toast("已更新名称");
        } catch (e) {
          toast(e.message || "更新失败", true);
        }
      });

      card.querySelector(".btn-del")?.addEventListener("click", async () => {
        if (!confirm("确定删除该账号？")) return;
        try {
          const data = await api(`/api/accounts/${id}`, { method: "DELETE" });
          applyDashboard(data.dashboard || {});
          toast("已删除");
        } catch (e) {
          toast(e.message || "删除失败", true);
        }
      });

      card.querySelector(".btn-relogin")?.addEventListener("click", () => {
        reLoginId = id;
        const acc = (boot.accounts || []).find((x) => x.id === id);
        document.getElementById("modal-add-title").textContent = "短信续期 Token";
        document.getElementById("add-name").value = acc?.name || "";
        document.getElementById("add-phone").value = acc?.phone || "";
        document.getElementById("add-code").value = "";
        openModal("modal-add");
      });
    });
  }

  async function refreshLite() {
    try {
      const data = await api("/api/dashboard");
      applyDashboard(data);
    } catch (_) {}
  }

  document.getElementById("btn-add")?.addEventListener("click", () => {
    reLoginId = "";
    document.getElementById("modal-add-title").textContent = "添加账号";
    document.getElementById("add-name").value = "";
    document.getElementById("add-phone").value = "";
    document.getElementById("add-code").value = "";
    openModal("modal-add");
  });

  document.getElementById("btn-settings")?.addEventListener("click", () => {
    const s = boot.settings || {};
    document.getElementById("set-pushplus").value = s.pushplus_token || "";
    document.getElementById("set-notify").value = s.notify || "important";
    document.getElementById("set-interval").value = s.claim_interval_min || 120;
    if (document.getElementById("set-claim-mode")) {
      document.getElementById("set-claim-mode").value = s.claim_mode || "api";
    }
    openModal("modal-settings");
  });

  document.getElementById("btn-send-sms")?.addEventListener("click", async (ev) => {
    const btn = ev.currentTarget;
    const phone = document.getElementById("add-phone").value.trim();
    btn.disabled = true;
    try {
      const body = { phone, name: document.getElementById("add-name").value.trim() };
      if (reLoginId) body.account_id = reLoginId;
      const data = await api("/api/sms/send", { method: "POST", body: JSON.stringify(body) });
      toast(data.message || `已发送到 ${data.phone_mask}`);
    } catch (e) {
      toast(e.message || "发送失败", true);
    } finally {
      btn.disabled = false;
    }
  });

  document.getElementById("btn-sms-login")?.addEventListener("click", async () => {
    const code = document.getElementById("add-code").value.trim();
    try {
      const data = await api("/api/sms/login", {
        method: "POST",
        body: JSON.stringify({ code }),
      });
      applyDashboard(data.dashboard || {});
      closeModal("modal-add");
      toast("登录成功，Token 已保存");
    } catch (e) {
      toast(e.message || "登录失败", true);
    }
  });

  document.getElementById("btn-save-settings")?.addEventListener("click", async () => {
    try {
      const data = await api("/api/settings", {
        method: "POST",
        body: JSON.stringify({
          pushplus_token: document.getElementById("set-pushplus").value.trim(),
          notify: document.getElementById("set-notify").value,
          claim_interval_min: Number(document.getElementById("set-interval").value) || 120,
          claim_mode: document.getElementById("set-claim-mode")?.value || "api",
        }),
      });
      boot.settings = data.settings;
      closeModal("modal-settings");
      toast("设置已保存");
      refreshLite();
    } catch (e) {
      toast(e.message || "保存失败", true);
    }
  });

  document.getElementById("btn-test-push")?.addEventListener("click", async () => {
    try {
      await api("/api/settings/pushplus/test", { method: "POST", body: "{}" });
      toast("测试推送已发送");
    } catch (e) {
      toast(e.message || "推送失败", true);
    }
  });

  document.getElementById("btn-claim-all")?.addEventListener("click", async () => {
    if (!confirm("对所有账号立即执行一次领取？")) return;
    try {
      toast("批量领取中，请稍候…");
      const data = await api("/api/claim-all", { method: "POST", body: "{}" });
      applyDashboard(data.dashboard || {});
      toast(`完成 ${data.results?.length || 0} 个账号`);
    } catch (e) {
      toast(e.message || "批量领取失败", true);
    }
  });

  renderAccounts(boot.accounts || []);
  applyStats(boot.stats || {});
  setInterval(refreshLite, 5000);
})();
