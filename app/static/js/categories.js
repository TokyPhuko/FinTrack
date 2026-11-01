(function () {
  const form = document.getElementById("cat-form");
  const expList = document.getElementById("cat-expense-list");
  const incList = document.getElementById("cat-income-list");
  const errorEl = document.getElementById("cat-error");

  async function loadList() {
    const rows = await getJSON("/api/categories");
    const render = (el, items) => {
      if (!items.length) {
        el.innerHTML = `<li class="muted">Пусто</li>`;
        return;
      }
      el.innerHTML = items
        .map(
          (c) => `<li data-id="${c.id}">
            <div>
              <strong>${c.icon || ""} ${escapeHtml(c.name)}</strong>
              ${c.monthly_limit ? `<span class="muted small"> · лимит ${fmtMoney(c.monthly_limit)}</span>` : ""}
            </div>
            <div class="row-actions">
              <button class="btn btn-ghost btn-sm" data-action="edit" data-id="${c.id}">✎</button>
              <button class="btn btn-ghost btn-sm" data-action="archive" data-id="${c.id}">📦</button>
            </div>
          </li>`
        )
        .join("");
    };
    render(expList, rows.filter((c) => c.type === "expense"));
    render(incList, rows.filter((c) => c.type === "income"));
  }

  async function handleListClick(e) {
    const btn = e.target.closest("button[data-action]");
    if (!btn) return;
    const id = Number(btn.dataset.id);
    try {
      if (btn.dataset.action === "archive") {
        if (!confirm("Архивировать категорию? Существующие операции сохранятся.")) return;
        await sendJSON(`/api/categories/${id}/archive`, "POST", {});
      } else if (btn.dataset.action === "edit") {
        const name = prompt("Название:", btn.parentElement.querySelector("strong").textContent.trim());
        if (name === null) return;
        const limitStr = prompt("Лимит в месяц (пусто = без лимита):", "");
        if (limitStr === null) return;
        const payload = { name: name.trim() };
        if (limitStr.trim() === "") {
          payload.monthly_limit = null;
        } else {
          payload.monthly_limit = Number(limitStr.replace(",", "."));
        }
        await sendJSON(`/api/categories/${id}`, "PUT", payload);
      }
      await loadList();
    } catch (err) {
      alert(err.message);
    }
  }

  expList.addEventListener("click", handleListClick);
  incList.addEventListener("click", handleListClick);

  form.addEventListener("submit", async (e) => {
    e.preventDefault();
    errorEl.textContent = "";
    const limitRaw = document.getElementById("cat-limit").value;
    try {
      await sendJSON("/api/categories", "POST", {
        name: document.getElementById("cat-name").value.trim(),
        type: document.getElementById("cat-type").value,
        monthly_limit: limitRaw === "" ? null : Number(limitRaw),
        icon: document.getElementById("cat-icon").value.trim(),
      });
      form.reset();
      await loadList();
    } catch (err) {
      errorEl.textContent = err.message;
    }
  });

  if (window.Telegram && window.Telegram.WebApp) window.Telegram.WebApp.ready();
  loadList().catch((err) => {
    expList.innerHTML = `<li class="muted">${escapeHtml(err.message)}</li>`;
  });
})();
