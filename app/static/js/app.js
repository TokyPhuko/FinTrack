function fmtMoney(n) {
  return new Intl.NumberFormat("ru-RU", {
    style: "currency",
    currency: "RUB",
    maximumFractionDigits: 2,
  }).format(n);
}

async function getJSON(url) {
  const res = await fetch(url, { headers: { Accept: "application/json" } });
  if (res.status === 401) {
    window.location.href = "/login";
    throw new Error("unauthorized");
  }
  if (!res.ok) throw new Error(await res.text());
  return res.json();
}

async function sendJSON(url, method, body) {
  const res = await fetch(url, {
    method,
    headers: { "Content-Type": "application/json", Accept: "application/json" },
    body: body !== undefined ? JSON.stringify(body) : undefined,
  });
  if (res.status === 401) {
    window.location.href = "/login";
    throw new Error("unauthorized");
  }
  if (res.status === 204) return null;
  const data = await res.json().catch(() => null);
  if (!res.ok) {
    const detail = data && data.detail ? data.detail : await res.text();
    throw new Error(typeof detail === "string" ? detail : JSON.stringify(detail));
  }
  return data;
}

async function loadSummary() {
  const s = await getJSON("/api/summary");
  const set = (id, val, signed) => {
    const el = document.getElementById(id);
    if (!el) return;
    el.textContent = fmtMoney(val);
    if (signed) {
      el.classList.toggle("positive", val > 0);
      el.classList.toggle("negative", val < 0);
    }
  };
  set("stat-income", s.income);
  set("stat-expense", s.expense);
  set("stat-net", s.net, true);
  set("stat-balance", s.total_balance);

  const ul = document.getElementById("accounts-list");
  if (ul) {
    ul.innerHTML = s.accounts
      .map(
        (a) =>
          `<li><span>${escapeHtml(a.name)}</span><span>${fmtMoney(a.balance)}</span></li>`
      )
      .join("");
  }
  const note = document.getElementById("transfers-note");
  if (note) {
    note.textContent =
      s.transfers_count > 0
        ? `Переводов за месяц: ${s.transfers_count} (не учитываются как расходы)`
        : "";
  }
}

async function loadCategories() {
  const cats = await getJSON("/api/categories");
  const ul = document.getElementById("categories-list");
  if (!ul) return;
  ul.innerHTML = cats
    .map(
      (c) =>
        `<li title="${c.type}">${c.icon || ""} ${escapeHtml(c.name)}${
          c.type === "income" ? " ↓" : ""
        }</li>`
    )
    .join("");
}

function escapeHtml(str) {
  return String(str ?? "")
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;");
}

document.addEventListener("DOMContentLoaded", () => {
  if (window.Telegram && window.Telegram.WebApp) {
    window.Telegram.WebApp.ready();
  }
  loadSummary().catch(console.error);
  loadCategories().catch(console.error);
  loadStreak().catch(console.error);
});

async function loadStreak() {
  const body = document.getElementById("streak-body");
  const btn = document.getElementById("checkin-btn");
  if (!body) return;
  try {
    const s = await getJSON("/api/social/streak");
    const fire = "🔥".repeat(Math.min(s.current, 10)) || "—";
    let html = `<div><span style="font-size:1.4em">${fire}</span> <strong>${s.current} дн.</strong>`;
    html += ` · рекорд ${s.best}`;
    html += ` · сегодня ${s.today_done ? "✅" : "❌"}</div>`;
    if (s.pair) {
      const you = s.pair.you.current;
      const them = s.pair.partner_streak.current;
      let head;
      if (s.pair.leader === "you") head = `🏆 Ты впереди на ${s.pair.diff}`;
      else if (s.pair.leader === "partner") head = `😤 ${escapeHtml(s.pair.partner)} впереди на ${s.pair.diff}`;
      else head = "🤝 Ничья";
      html += `<div class="muted small" style="margin-top:6px">👥 Пара с ${escapeHtml(s.pair.partner)} — Ты: ${you} · ${them}: ${them}</div>`;
      html += `<div class="muted small">${head}</div>`;
    } else {
      html += `<div class="muted small" style="margin-top:6px">Пары нет — пригласи через бота: /pair username</div>`;
    }
    if (s.pending_invites && s.pending_invites.length) {
      html += `<div class="small" style="margin-top:6px">📨 Входящее приглашение от ${escapeHtml(s.pending_invites.join(", "))} — ответь в боте: /pair accept</div>`;
    }
    body.innerHTML = html;
    if (btn) {
      btn.disabled = s.today_done;
      btn.textContent = s.today_done ? "Сегодня ✅" : "Check-in дня";
    }
  } catch (e) {
    body.textContent = e.message;
  }
}

document.addEventListener("DOMContentLoaded", () => {
  const btn = document.getElementById("checkin-btn");
  if (btn) {
    btn.addEventListener("click", async () => {
      btn.disabled = true;
      try {
        await sendJSON("/api/social/checkin", "POST");
        await loadStreak();
      } catch (e) {
        alert(e.message);
        btn.disabled = false;
      }
    });
  }
});
