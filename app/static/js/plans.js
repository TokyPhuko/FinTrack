(function () {
  const form = document.getElementById("plan-form");
  const listEl = document.getElementById("plans-list");
  const statusFilter = document.getElementById("plan-status-filter");
  const errorEl = document.getElementById("plan-error");

  const titleEl = document.getElementById("plan-title");
  const typeEl = document.getElementById("plan-type");
  const amountEl = document.getElementById("plan-amount");
  const categoryEl = document.getElementById("plan-category");
  const accountEl = document.getElementById("plan-account");
  const dueEl = document.getElementById("plan-due");
  const recEl = document.getElementById("plan-recurrence");

  let categories = [];
  let accounts = [];

  function fillSelects() {
    const type = typeEl.value;
    const cats = categories.filter((c) => c.type === type);
    categoryEl.innerHTML =
      `<option value="">— нет —</option>` +
      cats
        .map((c) => `<option value="${c.id}">${escapeHtml(c.icon || "")} ${escapeHtml(c.name)}</option>`)
        .join("");
    accountEl.innerHTML =
      `<option value="">— нет —</option>` +
      accounts
        .map((a) => `<option value="${a.id}">${escapeHtml(a.name)} (${fmtMoney(a.balance)})</option>`)
        .join("");
  }

  typeEl.addEventListener("change", fillSelects);
  recEl.addEventListener("change", () => {
    // nothing special; recurrence only if not none
  });

  async function loadMeta() {
    categories = await getJSON("/api/categories");
    accounts = await getJSON("/api/accounts");
    fillSelects();
  }

  function statusLabel(p) {
    if (p.status === "overdue") return '<span class="badge badge-overdue">Просрочен</span>';
    if (p.status === "done") return '<span class="badge badge-done">Выполнен</span>';
    if (p.status === "skipped") return '<span class="badge badge-skipped">Пропущен</span>';
    return '<span class="badge badge-pending">Ожидает</span>';
  }

  async function loadList() {
    const st = statusFilter.value;
    const q = st === "all" ? "" : `?status=${encodeURIComponent(st)}`;
    const rows = await getJSON("/api/plans" + q);
    if (!rows.length) {
      listEl.innerHTML = `<li class="muted">Нет планов</li>`;
      return;
    }
    listEl.innerHTML = rows
      .map((p) => {
        const typeLabel = p.type === "income" ? "Доход" : "Расход";
        const cat = p.category_name ? escapeHtml(p.category_name) : "—";
        const rec = p.is_recurring ? ` · ${escapeHtml(p.recurrence)}` : "";
        const actions =
          p.status === "pending" || p.status === "overdue"
            ? `<button class="btn btn-sm btn-primary" data-action="complete" data-id="${p.id}">Выполнено</button>
               <button class="btn btn-sm" data-action="skip" data-id="${p.id}">Пропустить</button>
               <button class="btn btn-ghost btn-sm" data-action="delete" data-id="${p.id}">✕</button>`
            : `<button class="btn btn-ghost btn-sm" data-action="restore" data-id="${p.id}">↩︎</button>
               <button class="btn btn-ghost btn-sm" data-action="delete" data-id="${p.id}">✕</button>`;
        return `<li>
          <div class="plan-main">
            <div class="plan-title">${escapeHtml(p.title)} ${statusLabel(p)}</div>
            <div class="muted small">${p.due_date} · ${typeLabel} · ${cat}${rec}</div>
          </div>
          <div class="plan-side">
            <span class="num">${fmtMoney(p.amount)}</span>
            <span class="row-actions">${actions}</span>
          </div>
        </li>`;
      })
      .join("");
  }

  listEl.addEventListener("click", async (e) => {
    const btn = e.target.closest("button[data-action]");
    if (!btn) return;
    const id = btn.dataset.id;
    const action = btn.dataset.action;
    try {
      if (action === "complete") {
        await sendJSON(`/api/plans/${id}/complete`, "POST", {});
      } else if (action === "skip") {
        await sendJSON(`/api/plans/${id}/skip`, "POST", {});
      } else if (action === "restore") {
        await sendJSON(`/api/plans/${id}/restore`, "POST", {});
      } else if (action === "delete") {
        if (!confirm("Удалить план?")) return;
        await sendJSON(`/api/plans/${id}`, "DELETE");
      }
      await loadList();
    } catch (err) {
      alert(err.message);
    }
  });

  form.addEventListener("submit", async (e) => {
    e.preventDefault();
    errorEl.textContent = "";
    const rec = recEl.value;
    const payload = {
      type: typeEl.value,
      amount: Number(amountEl.value),
      title: titleEl.value.trim(),
      category_id: categoryEl.value ? Number(categoryEl.value) : null,
      account_id: accountEl.value ? Number(accountEl.value) : null,
      due_date: dueEl.value,
      is_recurring: rec !== "none",
      recurrence: rec,
    };
    try {
      await sendJSON("/api/plans", "POST", payload);
      form.reset();
      dueEl.value = todayISO();
      fillSelects();
      await loadList();
    } catch (err) {
      errorEl.textContent = err.message;
    }
  });

  statusFilter.addEventListener("change", loadList);
  dueEl.value = todayISO();

  if (window.Telegram && window.Telegram.WebApp) window.Telegram.WebApp.ready();
  loadMeta()
    .then(loadList)
    .catch((err) => {
      listEl.innerHTML = `<li class="muted">${escapeHtml(err.message)}</li>`;
    });
})();
