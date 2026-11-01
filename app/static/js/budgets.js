(function () {
  const monthEl = document.getElementById("budget-month");
  const progressEl = document.getElementById("budget-progress");
  const warningsEl = document.getElementById("warnings-list");
  const catEl = document.getElementById("budget-category");
  const amountEl = document.getElementById("budget-amount");
  const form = document.getElementById("budget-form");
  const errorEl = document.getElementById("budget-error");
  const listEl = document.getElementById("budgets-list");
  const refreshBtn = document.getElementById("warn-refresh");

  const now = new Date();
  const thisMonth = `${now.getFullYear()}-${String(now.getMonth() + 1).padStart(2, "0")}`;
  monthEl.value = thisMonth;

  const warnPriority = { high: "badge-overdue", medium: "badge-pending", low: "" };

  async function loadWarnings() {
    warningsEl.innerHTML = `<li class="muted">Загрузка…</li>`;
    try {
      const rows = await getJSON("/api/warnings");
      if (!rows.length) {
        warningsEl.innerHTML = `<li class="muted">Предупреждений нет 🎉</li>`;
        return;
      }
      warningsEl.innerHTML = rows
        .map(
          (w) => `<li>
            <div>
              <span class="badge ${warnPriority[w.priority] || ""}">${escapeHtml(w.kind)}</span>
              ${escapeHtml(w.title)}
            </div>
            <span class="num">${w.amount != null ? fmtMoney(w.amount) : ""}</span>
          </li>`
        )
        .join("");
    } catch (e) {
      warningsEl.innerHTML = `<li class="muted">${escapeHtml(e.message)}</li>`;
    }
  }

  async function loadProgress() {
    const month = monthEl.value || thisMonth;
    progressEl.innerHTML = `<p class="muted">Загрузка…</p>`;
    try {
      const data = await getJSON(`/api/budgets/progress?month=${month}`);
      if (!data.items.length) {
        progressEl.innerHTML = `<p class="muted">Нет данных по бюджетам. Задай суммы ниже.</p>`;
        return;
      }
      progressEl.innerHTML =
        `<p class="muted small">Итого: ${fmtMoney(data.total_spent)} из ${fmtMoney(data.total_limit)}</p>` +
        data.items
          .map((it) => {
            const pct = it.pct ?? (it.spent > 0 ? 100 : 0);
            const cls = pct >= 100 ? "bar-over" : pct >= 80 ? "bar-warn" : "bar-ok";
            return `<div class="budget-row">
              <div class="budget-label">
                <span>${escapeHtml(it.category_name)}</span>
                <span class="num small">${fmtMoney(it.spent)}${it.limit != null ? " / " + fmtMoney(it.limit) : ""}</span>
              </div>
              <div class="bar"><div class="bar-fill ${cls}" style="width:${Math.min(pct, 100)}%"></div></div>
            </div>`;
          })
          .join("");
    } catch (e) {
      progressEl.innerHTML = `<p class="muted">${escapeHtml(e.message)}</p>`;
    }
  }

  async function loadBudgets() {
    const month = monthEl.value || thisMonth;
    try {
      const rows = await getJSON(`/api/budgets?month=${month}`);
      listEl.innerHTML = rows.length
        ? rows
            .map(
              (b) => `<li>
                <span>${escapeHtml(b.category_name)} · ${fmtMoney(b.amount)}</span>
                <button class="btn btn-ghost btn-sm" data-id="${b.id}">✕</button>
              </li>`
            )
            .join("")
        : `<li class="muted">Бюджетов на этот месяц нет</li>`;
    } catch (e) {
      listEl.innerHTML = `<li class="muted">${escapeHtml(e.message)}</li>`;
    }
  }

  listEl.addEventListener("click", async (e) => {
    const btn = e.target.closest("button[data-id]");
    if (!btn) return;
    if (!confirm("Удалить бюджет?")) return;
    await sendJSON(`/api/budgets/${btn.dataset.id}`, "DELETE");
    await Promise.all([loadBudgets(), loadProgress(), loadWarnings()]);
  });

  async function fillCats() {
    const cats = await getJSON("/api/categories");
    catEl.innerHTML = cats
      .filter((c) => c.type === "expense")
      .map((c) => `<option value="${c.id}">${escapeHtml(c.icon || "")} ${escapeHtml(c.name)}</option>`)
      .join("");
  }

  form.addEventListener("submit", async (e) => {
    e.preventDefault();
    errorEl.textContent = "";
    try {
      await sendJSON("/api/budgets", "POST", {
        category_id: Number(catEl.value),
        month: monthEl.value || thisMonth,
        amount: Number(amountEl.value),
      });
      amountEl.value = "";
      await Promise.all([loadBudgets(), loadProgress(), loadWarnings()]);
    } catch (err) {
      errorEl.textContent = err.message;
    }
  });

  monthEl.addEventListener("change", () => {
    loadBudgets();
    loadProgress();
  });
  refreshBtn.addEventListener("click", loadWarnings);

  if (window.Telegram && window.Telegram.WebApp) window.Telegram.WebApp.ready();
  fillCats()
    .then(() => Promise.all([loadWarnings(), loadProgress(), loadBudgets()]))
    .catch(console.error);
})();
