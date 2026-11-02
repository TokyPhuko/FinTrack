(function () {
  const tbody = document.querySelector("#users-table tbody");
  const detailPanel = document.getElementById("detail-panel");
  const statusEl = document.getElementById("d-status");
  let currentId = null;
  let currentRow = null;

  function fmtMoney(n) {
    return new Intl.NumberFormat("ru-RU", { style: "currency", currency: "RUB", maximumFractionDigits: 0 }).format(n);
  }

  async function loadUsers() {
    const rows = await getJSON("/api/admin/users");
    if (!rows.length) {
      tbody.innerHTML = `<tr><td colspan="10" class="muted">Нет пользователей</td></tr>`;
      return;
    }
    tbody.innerHTML = rows
      .map((u) => {
        const role = u.is_admin ? "👑 админ" : "user";
        const status = u.is_active ? '<span class="positive">активен</span>' : '<span class="negative">блок</span>';
        const tg = u.telegram_id ? "✓" : "—";
        return `<tr data-id="${u.id}">
          <td>${u.id}</td>
          <td><strong>${escapeHtml(u.username)}</strong></td>
          <td>${role}</td>
          <td>${tg}</td>
          <td>${u.accounts}</td>
          <td>${fmtMoney(u.balance)}</td>
          <td>${fmtMoney(u.expense_month)}</td>
          <td>${u.transactions}</td>
          <td>${status}</td>
          <td><button class="btn btn-ghost btn-sm" data-open="${u.id}">Открыть</button></td>
        </tr>`;
      })
      .join("");
  }

  tbody.addEventListener("click", async (e) => {
    const btn = e.target.closest("button[data-open]");
    const tr = e.target.closest("tr[data-id]");
    const id = btn ? Number(btn.dataset.open) : tr ? Number(tr.dataset.id) : null;
    if (!id) return;
    await openDetail(id);
  });

  document.getElementById("detail-close").addEventListener("click", () => {
    detailPanel.style.display = "none";
    currentId = null;
  });

  document.getElementById("adm-refresh").addEventListener("click", () => loadUsers().catch(console.error));

  function setStatus(msg, ok = true) {
    statusEl.textContent = msg;
    statusEl.className = ok ? "muted small positive" : "muted small negative";
  }

  async function openDetail(id) {
    const d = await getJSON(`/api/admin/users/${id}`);
    currentId = id;
    detailPanel.style.display = "block";
    document.getElementById("detail-title").textContent = `Профиль: ${d.user.username} (#${d.user.id})`;
    document.getElementById("d-password").value = "";
    document.getElementById("d-politeness").value = d.user.llm_politeness;
    setStatus("");

    const cards = [
      ["Баланс", fmtMoney(d.total_balance)],
      ["Счета", d.accounts.length],
      ["Операций (посл.)", d.transactions.length],
      ["Вкладов", d.deposits.length],
      ["Планов", d.plans.length],
      ["Telegram", d.user.telegram_id ? "✓ привязан" : "—"],
    ];
    document.getElementById("detail-cards").innerHTML = cards
      .map(
        ([l, v]) =>
          `<div class="card"><div class="card-label">${l}</div><div class="card-value">${v}</div></div>`
      )
      .join("");

    const accUl = document.getElementById("d-accounts");
    accUl.innerHTML = d.accounts.length
      ? d.accounts
          .map(
            (a) =>
              `<li><span>${escapeHtml(a.name)} <span class="muted small">${a.type}${a.archived ? " · архив" : ""}</span></span>
               <span class="num">${fmtMoney(a.balance)}</span></li>`
          )
          .join("")
      : `<li class="muted">Нет счетов</li>`;

    const txUl = document.getElementById("d-transactions");
    txUl.innerHTML = d.transactions.length
      ? d.transactions
          .map((t) => {
            const sign = t.type === "income" ? "+" : t.type === "expense" ? "−" : "⇄";
            const when = t.occurred_at ? t.occurred_at.slice(0, 16).replace("T", " ") : "";
            return `<li><span>#${t.id} ${when} ${sign}${t.amount} ${escapeHtml(t.note || "")}</span>
              <span class="muted small">${t.source}</span></li>`;
          })
          .join("")
      : `<li class="muted">Нет операций</li>`;

    document.getElementById("d-categories").innerHTML = d.categories.length
      ? d.categories
          .map(
            (c) =>
              `<span class="chip">${c.icon || ""} ${escapeHtml(c.name)}${c.monthly_limit ? " · " + c.monthly_limit : ""}</span>`
          )
          .join("")
      : `<span class="muted">Нет</span>`;

    const planUl = document.getElementById("d-plans");
    planUl.innerHTML = d.plans.length
      ? d.plans
          .map(
            (p) =>
              `<li><span>${escapeHtml(p.title)}</span><span>${p.amount} · ${p.due_date} · ${p.status}</span></li>`
          )
          .join("")
      : `<li class="muted">Нет планов</li>`;

    const depUl = document.getElementById("d-deposits");
    depUl.innerHTML = d.deposits.length
      ? d.deposits
          .map(
            (x) =>
              `<li><span>${escapeHtml(x.title)} <span class="muted small">${escapeHtml(x.bank)} · ${x.rate}%</span></span>
               <span class="num">${fmtMoney(x.amount)}</span></li>`
          )
          .join("")
      : `<li class="muted">Нет вкладов</li>`;

    // buttons state
    const activeBtn = document.getElementById("d-toggle-active");
    activeBtn.textContent = d.user.is_active ? "Заблокировать" : "Разблокировать";
    const admBtn = document.getElementById("d-toggle-admin");
    admBtn.textContent = d.user.is_admin ? "Снять админа" : "Сделать админом";

    detailPanel.scrollIntoView({ behavior: "smooth", block: "start" });
  }

  document.getElementById("d-reset-pw").addEventListener("click", async () => {
    const pw = document.getElementById("d-password").value;
    if (pw.length < 3) return setStatus("Пароль мин. 3 символа", false);
    try {
      await sendJSON(`/api/admin/users/${currentId}/password`, "POST", { password: pw });
      setStatus("✓ Пароль изменён");
      document.getElementById("d-password").value = "";
    } catch (e) {
      setStatus(e.message, false);
    }
  });

  document.getElementById("d-unbind-tg").addEventListener("click", async () => {
    try {
      await sendJSON(`/api/admin/users/${currentId}/unbind-telegram`, "POST", {});
      setStatus("✓ Telegram отвязан");
      await openDetail(currentId);
      await loadUsers();
    } catch (e) {
      setStatus(e.message, false);
    }
  });

  document.getElementById("d-impersonate").addEventListener("click", async () => {
    if (!confirm("Войти в этот аккаунт? Вернуться можно кнопкой вверху.")) return;
    try {
      await sendJSON(`/api/admin/users/${currentId}/impersonate`, "POST", {});
      window.location.href = "/";
    } catch (e) {
      setStatus(e.message, false);
    }
  });

  document.getElementById("d-toggle-active").addEventListener("click", async () => {
    try {
      const r = await sendJSON(`/api/admin/users/${currentId}/toggle-active`, "POST", {});
      setStatus(r.is_active ? "✓ Разблокирован" : "✓ Заблокирован");
      await openDetail(currentId);
      await loadUsers();
    } catch (e) {
      setStatus(e.message, false);
    }
  });

  document.getElementById("d-toggle-admin").addEventListener("click", async () => {
    try {
      const r = await sendJSON(`/api/admin/users/${currentId}/toggle-admin`, "POST", {});
      setStatus(r.is_admin ? "✓ Теперь админ" : "✓ Админ снят");
      await openDetail(currentId);
      await loadUsers();
    } catch (e) {
      setStatus(e.message, false);
    }
  });

  document.getElementById("d-save-politeness").addEventListener("click", async () => {
    const level = Number(document.getElementById("d-politeness").value);
    try {
      await sendJSON(`/api/admin/users/${currentId}/politeness`, "POST", { level });
      setStatus("✓ LLM уровень сохранён");
    } catch (e) {
      setStatus(e.message, false);
    }
  });

  if (window.Telegram && window.Telegram.WebApp) window.Telegram.WebApp.ready();
  loadUsers().catch((e) => {
    tbody.innerHTML = `<tr><td colspan="10" class="muted">${escapeHtml(e.message)}</td></tr>`;
  });
})();
