(function () {
  const form = document.getElementById("dep-form");
  const listEl = document.getElementById("dep-list");
  const totalEl = document.getElementById("dep-total");
  const errorEl = document.getElementById("dep-error");
  const searchBtn = document.getElementById("search-btn");
  const resultsEl = document.getElementById("search-results");
  const sourceEl = document.getElementById("search-source");
  const noteEl = document.getElementById("search-note");

  document.getElementById("dep-start").value = todayISO();
  const end = new Date();
  end.setMonth(end.getMonth() + 6);
  const pad = (n) => String(n).padStart(2, "0");
  document.getElementById("dep-end").value = `${end.getFullYear()}-${pad(end.getMonth() + 1)}-${pad(end.getDate())}`;

  async function loadList() {
    const rows = await getJSON("/api/deposits");
    const total = rows.reduce((s, d) => s + d.amount, 0);
    const income = rows.reduce((s, d) => s + d.projected_income, 0);
    totalEl.textContent = `Всего ${fmtMoney(total)} · доход ~${fmtMoney(income)}`;
    if (!rows.length) {
      listEl.innerHTML = `<li class="muted">Вкладов нет</li>`;
      return;
    }
    listEl.innerHTML = rows
      .map((d) => {
        const daysCls = d.days_left <= 3 ? "negative" : d.days_left <= 14 ? "" : "positive";
        return `<li data-id="${d.id}">
          <div>
            <strong>${escapeHtml(d.title)}</strong>
            <div class="muted small">${escapeHtml(d.bank || "—")} · ${d.rate}% · до ${d.end_date} · <span class="${daysCls}">${d.days_left} дн.</span>${d.auto_renew ? " · продл." : ""}</div>
          </div>
          <div class="row-actions">
            <span class="num">${fmtMoney(d.amount)} → ${fmtMoney(d.maturity_amount)}</span>
            <button class="btn btn-ghost btn-sm" data-action="del" data-id="${d.id}">✕</button>
          </div>
        </li>`;
      })
      .join("");
  }

  listEl.addEventListener("click", async (e) => {
    const btn = e.target.closest("button[data-action=del]");
    if (!btn) return;
    if (!confirm("Удалить вклад?")) return;
    await sendJSON(`/api/deposits/${btn.dataset.id}`, "DELETE");
    await loadList();
  });

  form.addEventListener("submit", async (e) => {
    e.preventDefault();
    errorEl.textContent = "";
    try {
      await sendJSON("/api/deposits", "POST", {
        title: document.getElementById("dep-title").value.trim(),
        bank: document.getElementById("dep-bank").value.trim(),
        amount: Number(document.getElementById("dep-amount").value),
        rate: Number(document.getElementById("dep-rate").value),
        start_date: document.getElementById("dep-start").value,
        end_date: document.getElementById("dep-end").value,
        auto_renew: document.getElementById("dep-renew").checked,
      });
      form.reset();
      document.getElementById("dep-start").value = todayISO();
      await loadList();
    } catch (err) {
      errorEl.textContent = err.message;
    }
  });

  searchBtn.addEventListener("click", async () => {
    searchBtn.disabled = true;
    searchBtn.textContent = "Ищу…";
    resultsEl.innerHTML = `<li class="muted">Загрузка…</li>`;
    try {
      const data = await sendJSON("/api/llm/deposits/search", "POST", {
        amount: Number(document.getElementById("search-amount").value),
        term_days: Number(document.getElementById("search-days").value),
      });
      sourceEl.textContent = data.stale ? `⚠ ${data.source}` : `✓ ${data.source}`;
      noteEl.textContent = data.note || "";
      resultsEl.innerHTML = (data.offers || [])
        .map(
          (o) => `<li>
            <span><strong>${escapeHtml(o.bank || o.title || "?")}</strong>
            <span class="muted small"> ${escapeHtml(o.term || "")} ${escapeHtml(o.note || "")}</span></span>
            <span class="num positive">${o.rate}%</span>
          </li>`
        )
        .join("") || `<li class="muted">Ничего не нашлось</li>`;
    } catch (e) {
      resultsEl.innerHTML = `<li class="muted">${escapeHtml(e.message)}</li>`;
    } finally {
      searchBtn.disabled = false;
      searchBtn.textContent = "Найти";
    }
  });

  const discussBtn = document.getElementById("discuss-btn");
  const discussText = document.getElementById("discuss-text");
  if (discussBtn) {
    discussBtn.addEventListener("click", async () => {
      discussBtn.disabled = true;
      discussBtn.textContent = "Анализирую…";
      discussText.style.display = "block";
      discussText.textContent = "Собираю актуальные предложения…";
      try {
        const data = await sendJSON("/api/llm/deposits/discuss", "POST", {
          amount: Number(document.getElementById("search-amount").value),
          term_days: Number(document.getElementById("search-days").value),
        });
        discussText.textContent = data.text;
      } catch (e) {
        discussText.textContent = "Ошибка: " + e.message;
      } finally {
        discussBtn.disabled = false;
        discussBtn.textContent = "💬 Обсудить предложения";
      }
    });
  }

  if (window.Telegram && window.Telegram.WebApp) window.Telegram.WebApp.ready();
  loadList().catch(console.error);
})();
