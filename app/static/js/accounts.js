(function () {
  const form = document.getElementById("account-form");
  const listEl = document.getElementById("accounts-list");
  const errorEl = document.getElementById("acc-error");
  const totalEl = document.getElementById("acc-total");

  async function loadList() {
    const rows = await getJSON("/api/accounts");
    const total = rows.reduce((s, a) => s + a.balance, 0);
    totalEl.textContent = "Итого: " + fmtMoney(total);
    if (!rows.length) {
      listEl.innerHTML = `<li class="muted">Нет счетов</li>`;
      return;
    }
    const typeLabels = {
      card: "Карта",
      cash: "Наличные",
      savings: "Накопительный",
      deposit: "Вклад",
    };
    listEl.innerHTML = rows
      .map(
        (a) => `<li data-id="${a.id}">
          <div>
            <strong>${escapeHtml(a.name)}</strong>
            <span class="muted small"> · ${typeLabels[a.type] || a.type} · ${escapeHtml(a.currency)}</span>
          </div>
          <div class="row-actions">
            <span class="num">${fmtMoney(a.balance)}</span>
            <button class="btn btn-ghost btn-sm" data-action="edit" data-id="${a.id}"
              data-name="${escapeHtml(a.name)}" data-balance="${a.balance}">✎</button>
            <button class="btn btn-ghost btn-sm" data-action="archive" data-id="${a.id}">📦</button>
          </div>
        </li>`
      )
      .join("");
  }

  listEl.addEventListener("click", async (e) => {
    const btn = e.target.closest("button[data-action]");
    if (!btn) return;
    const id = Number(btn.dataset.id);
    try {
      if (btn.dataset.action === "archive") {
        if (!confirm("Архивировать счёт?")) return;
        await sendJSON(`/api/accounts/${id}/archive`, "POST", {});
      } else if (btn.dataset.action === "edit") {
        const name = prompt("Название счёта:", btn.dataset.name);
        if (name === null) return;
        const balStr = prompt("Баланс (абсолютное значение):", btn.dataset.balance);
        if (balStr === null) return;
        await sendJSON(`/api/accounts/${id}`, "PUT", {
          name: name.trim() || btn.dataset.name,
          balance: Number(balStr.replace(",", ".")),
        });
      }
      await loadList();
    } catch (err) {
      alert(err.message);
    }
  });

  form.addEventListener("submit", async (e) => {
    e.preventDefault();
    errorEl.textContent = "";
    try {
      await sendJSON("/api/accounts", "POST", {
        name: document.getElementById("acc-name").value.trim(),
        type: document.getElementById("acc-type").value,
        currency: document.getElementById("acc-currency").value.trim() || "RUB",
        balance: Number(document.getElementById("acc-balance").value || 0),
      });
      form.reset();
      document.getElementById("acc-currency").value = "RUB";
      document.getElementById("acc-balance").value = "0";
      await loadList();
    } catch (err) {
      errorEl.textContent = err.message;
    }
  });

  if (window.Telegram && window.Telegram.WebApp) window.Telegram.WebApp.ready();
  loadList().catch((err) => {
    listEl.innerHTML = `<li class="muted">${escapeHtml(err.message)}</li>`;
  });

  // ---- shared accounts + data shares ----
  const shareForm = document.getElementById("share-form");
  const shareAccSel = document.getElementById("share-acc");
  const shareErr = document.getElementById("share-error");
  const sharedList = document.getElementById("shared-list");
  const dsForm = document.getElementById("datashare-form");
  const dsErr = document.getElementById("datashare-error");
  const dsList = document.getElementById("datashare-list");

  async function loadVisible() {
    const rows = await getJSON("/api/social/accounts/visible");
    if (shareAccSel) {
      shareAccSel.innerHTML = rows
        .filter((a) => a.is_mine)
        .map((a) => `<option value="${a.id}">${escapeHtml(a.name)}</option>`)
        .join("");
    }
    if (sharedList) {
      const shared = rows.filter((a) => !a.is_mine || (a.members && a.members.length));
      if (!shared.length) {
        sharedList.innerHTML = `<li class="muted">Совместных счетов пока нет</li>`;
      } else {
        sharedList.innerHTML = rows
          .filter((a) => a.members && a.members.length)
          .map((a) => {
            const members = a.members
              .map((m) => `${escapeHtml(m.username)} (${m.role})`)
              .join(", ");
            const who = a.is_mine ? `Ты владелец · участники: ${members}` : `Владелец · участники: ${members}`;
            return `<li><div><strong>${escapeHtml(a.name)}</strong><div class="muted small">${who}</div></div>
              <span class="num">${fmtMoney(a.balance)}</span></li>`;
          })
          .join("");
      }
    }
  }

  async function loadShares() {
    const data = await getJSON("/api/social/shares");
    if (!dsList) return;
    const mine = (data.i_share_with || []).map((s) => `→ ${escapeHtml(s.username)}`).join(", ");
    const theirs = (data.they_share_with_me || []).map((s) => `← ${escapeHtml(s.username)}`).join(", ");
    const parts = [];
    if (mine) parts.push(`Ты шаришь: ${mine}`);
    if (theirs) parts.push(`С тобой делятся: ${theirs}`);
    dsList.innerHTML = parts.join(" · ") || "Пока никто не шарит данные";
  }

  if (shareForm) {
    shareForm.addEventListener("submit", async (e) => {
      e.preventDefault();
      shareErr.textContent = "";
      try {
        const accId = shareAccSel.value;
        await sendJSON(`/api/social/accounts/${accId}/share`, "POST", {
          username: document.getElementById("share-user").value.trim(),
        });
        document.getElementById("share-user").value = "";
        await loadVisible();
      } catch (err) {
        shareErr.textContent = err.message;
      }
    });
  }

  if (dsForm) {
    dsForm.addEventListener("submit", async (e) => {
      e.preventDefault();
      dsErr.textContent = "";
      try {
        await sendJSON("/api/social/shares", "POST", {
          username: document.getElementById("datashare-user").value.trim(),
        });
        document.getElementById("datashare-user").value = "";
        await loadShares();
      } catch (err) {
        dsErr.textContent = err.message;
      }
    });
  }

  loadVisible().catch(console.error);
  loadShares().catch(console.error);
})();
