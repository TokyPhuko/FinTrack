(function () {
  const monthEl = document.getElementById("export-month");
  const exportBtn = document.getElementById("export-btn");
  const weeklyBtn = document.getElementById("weekly-btn");
  const weeklyText = document.getElementById("weekly-text");
  const llmStatus = document.getElementById("llm-status");
  const now = new Date();
  const thisMonth = `${now.getFullYear()}-${String(now.getMonth() + 1).padStart(2, "0")}`;
  monthEl.value = thisMonth;

  function syncExport() {
    exportBtn.href = `/api/export/excel?month=${monthEl.value || thisMonth}`;
  }
  monthEl.addEventListener("change", syncExport);
  syncExport();

  const tgBtn = document.getElementById("export-tg-btn");
  const tgStatus = document.getElementById("export-tg-status");
  if (tgBtn) {
    tgBtn.addEventListener("click", async () => {
      tgBtn.disabled = true;
      tgStatus.textContent = "Отправляю…";
      try {
        const m = monthEl.value || thisMonth;
        const data = await sendJSON(`/api/export/excel/telegram?month=${m}`, "POST");
        tgStatus.textContent = `✅ Отправлено в Telegram (${data.filename})`;
      } catch (e) {
        tgStatus.textContent = "❌ " + e.message;
      } finally {
        tgBtn.disabled = false;
      }
    });
  }

  async function loadStatus() {
    try {
      const s = await getJSON("/api/llm/status");
      llmStatus.innerHTML = s.available
        ? `LLM: <strong>${escapeHtml(s.provider)}</strong> ✓`
        : `LLM: <span class="negative">${escapeHtml(s.reason || "шаблоны")}</span>`;
      const badge = document.getElementById("llm-badge");
      if (badge) badge.textContent = s.available ? s.provider : "fallback";
    } catch {
      llmStatus.textContent = "";
    }
  }

  weeklyBtn.addEventListener("click", async () => {
    weeklyBtn.disabled = true;
    weeklyText.textContent = "Генерирую…";
    try {
      const data = await sendJSON("/api/llm/weekly", "POST", {});
      weeklyText.textContent = data.text;
      await loadStatus();
    } catch (e) {
      weeklyText.textContent = "Ошибка: " + e.message;
    } finally {
      weeklyBtn.disabled = false;
    }
  });

  async function loadSummary() {
    const s = await getJSON("/api/summary");
    const set = (id, v, signed) => {
      const el = document.getElementById(id);
      el.textContent = fmtMoney(v);
      if (signed) {
        el.classList.toggle("positive", v > 0);
        el.classList.toggle("negative", v < 0);
      }
    };
    set("r-income", s.income);
    set("r-expense", s.expense);
    set("r-net", s.net, true);
    set("r-balance", s.total_balance);
  }

  if (window.Telegram && window.Telegram.WebApp) window.Telegram.WebApp.ready();
  Promise.all([loadStatus(), loadSummary()]).catch(console.error);
})();
