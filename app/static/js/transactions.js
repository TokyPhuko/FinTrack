(function () {
  const typeSwitch = document.getElementById("tx-type-switch");
  const typeInput = document.getElementById("tx-type");
  const idInput = document.getElementById("tx-id");
  const form = document.getElementById("tx-form");
  const amountEl = document.getElementById("tx-amount");
  const categoryField = document.getElementById("tx-category-field");
  const categoryEl = document.getElementById("tx-category");
  const accountEl = document.getElementById("tx-account");
  const toField = document.getElementById("tx-to-field");
  const toAccountEl = document.getElementById("tx-to-account");
  const dateEl = document.getElementById("tx-date");
  const noteEl = document.getElementById("tx-note");
  const submitBtn = document.getElementById("tx-submit");
  const cancelBtn = document.getElementById("tx-cancel");
  const errorEl = document.getElementById("tx-error");
  const monthEl = document.getElementById("tx-month");
  const filterTypeEl = document.getElementById("tx-filter-type");
  const tbody = document.getElementById("tx-tbody");
  const transferHint = document.getElementById("transfer-hint");

  let categories = [];
  let accounts = [];

  function setType(type) {
    typeInput.value = type;
    typeSwitch.querySelectorAll(".type-btn").forEach((btn) => {
      btn.classList.toggle("active", btn.dataset.type === type);
    });
    const isTransfer = type === "transfer";
    categoryField.hidden = isTransfer;
    toField.hidden = !isTransfer;
    transferHint.hidden = !isTransfer;
    categoryEl.required = !isTransfer;
    toAccountEl.required = isTransfer;
  }

  typeSwitch.addEventListener("click", (e) => {
    const btn = e.target.closest(".type-btn");
    if (btn) setType(btn.dataset.type);
  });

  function fillSelects() {
    const type = typeInput.value;
    const expCats = categories.filter((c) => c.type === "expense");
    const incCats = categories.filter((c) => c.type === "income");
    const cats = type === "income" ? incCats : expCats;
    categoryEl.innerHTML = cats
      .map((c) => `<option value="${c.id}">${escapeHtml(c.icon || "")} ${escapeHtml(c.name)}</option>`)
      .join("");
    const accOpts = accounts
      .map((a) => `<option value="${a.id}">${escapeHtml(a.name)} (${fmtMoney(a.balance)})</option>`)
      .join("");
    accountEl.innerHTML = accOpts;
    toAccountEl.innerHTML = accOpts;
  }

  typeSwitch.addEventListener("click", () => setTimeout(fillSelects, 0));

  async function loadFilters() {
    categories = await getJSON("/api/categories");
    accounts = await getJSON("/api/accounts");
    fillSelects();
  }

  async function loadList() {
    const params = new URLSearchParams();
    if (monthEl.value) params.set("month", monthEl.value);
    if (filterTypeEl.value) params.set("tx_type", filterTypeEl.value);
    const rows = await getJSON("/api/transactions?" + params.toString());
    if (!rows.length) {
      tbody.innerHTML = `<tr><td colspan="7" class="muted">Пока пусто</td></tr>`;
      return;
    }
    tbody.innerHTML = rows
      .map((t) => {
        const d = new Date(t.occurred_at);
        const dateStr = d.toLocaleDateString("ru-RU");
        const typeLabel =
          t.type === "expense" ? "Расход" : t.type === "income" ? "Доход" : "Перевод";
        const amount =
          t.type === "income" ? "+" + fmtMoney(t.amount) : t.type === "expense" ? "−" + fmtMoney(t.amount) : fmtMoney(t.amount);
        const amountClass =
          t.type === "income" ? "positive" : t.type === "expense" ? "negative" : "muted";
        const cat = t.category_name ? escapeHtml(t.category_name) : "—";
        let acc = escapeHtml(t.account_name);
        if (t.type === "transfer" && t.transfer_account_name) {
          acc += ` → ${escapeHtml(t.transfer_account_name)}`;
        }
        return `<tr data-id="${t.id}">
          <td>${dateStr}</td>
          <td><span class="badge badge-${t.type}">${typeLabel}</span></td>
          <td>${cat}</td>
          <td>${acc}</td>
          <td class="num ${amountClass}">${amount}</td>
          <td>${escapeHtml(t.note || "")}</td>
          <td class="row-actions">
            <button class="btn btn-ghost btn-sm" data-action="edit">✎</button>
            <button class="btn btn-ghost btn-sm" data-action="delete">✕</button>
          </td>
        </tr>`;
      })
      .join("");
  }

  tbody.addEventListener("click", async (e) => {
    const btn = e.target.closest("button[data-action]");
    if (!btn) return;
    const tr = btn.closest("tr");
    const id = Number(tr.dataset.id);
    if (btn.dataset.action === "delete") {
      if (!confirm("Удалить операцию? Баланс будет пересчитан.")) return;
      await sendJSON(`/api/transactions/${id}`, "DELETE");
      await loadList();
      await loadFilters();
      await loadList();
    } else if (btn.dataset.action === "edit") {
      await editFromRow(id);
    }
  });

  async function editFromRow(id) {
    // get from last loaded list via API filter is heavy; use data attributes
    // Reload list once and find
    const params = new URLSearchParams();
    if (monthEl.value) params.set("month", monthEl.value);
    if (filterTypeEl.value) params.set("tx_type", filterTypeEl.value);
    const rows = await getJSON("/api/transactions?" + params.toString());
    const t = rows.find((r) => r.id === id);
    if (!t) return;
    idInput.value = t.id;
    setType(t.type);
    fillSelects();
    amountEl.value = t.amount;
    if (t.category_id) categoryEl.value = t.category_id;
    accountEl.value = t.account_id;
    if (t.transfer_account_id) toAccountEl.value = t.transfer_account_id;
    dateEl.value = t.occurred_at.slice(0, 10);
    noteEl.value = t.note || "";
    submitBtn.textContent = "Сохранить";
    cancelBtn.hidden = false;
    form.scrollIntoView({ behavior: "smooth" });
  }

  cancelBtn.addEventListener("click", () => {
    idInput.value = "";
    form.reset();
    setType("expense");
    fillSelects();
    dateEl.value = todayISO();
    submitBtn.textContent = "Добавить";
    cancelBtn.hidden = true;
    errorEl.textContent = "";
  });

  form.addEventListener("submit", async (e) => {
    e.preventDefault();
    errorEl.textContent = "";
    const type = typeInput.value;
    const payload = {
      type,
      amount: Number(amountEl.value),
      account_id: Number(accountEl.value),
      note: noteEl.value,
      occurred_at: dateEl.value + "T12:00:00",
    };
    if (type === "transfer") {
      payload.transfer_account_id = Number(toAccountEl.value);
      payload.category_id = null;
    } else {
      payload.category_id = Number(categoryEl.value);
    }
    try {
      if (idInput.value) {
        await sendJSON(`/api/transactions/${idInput.value}`, "PUT", payload);
      } else {
        await sendJSON("/api/transactions", "POST", payload);
      }
      idInput.value = "";
      form.reset();
      setType(type);
      fillSelects();
      dateEl.value = todayISO();
      submitBtn.textContent = "Добавить";
      cancelBtn.hidden = true;
      await loadList();
      await loadFilters();
      await loadList();
    } catch (err) {
      errorEl.textContent = err.message;
    }
  });

  monthEl.addEventListener("change", loadList);
  filterTypeEl.addEventListener("change", loadList);

  const now = new Date();
  monthEl.value = `${now.getFullYear()}-${String(now.getMonth() + 1).padStart(2, "0")}`;
  dateEl.value = todayISO();

  if (window.Telegram && window.Telegram.WebApp) window.Telegram.WebApp.ready();
  loadFilters()
    .then(loadList)
    .catch((err) => {
      tbody.innerHTML = `<tr><td colspan="7" class="muted">${escapeHtml(err.message)}</td></tr>`;
    });
})();
