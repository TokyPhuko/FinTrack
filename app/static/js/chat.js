(function () {
  const log = document.getElementById("chat-log");
  const form = document.getElementById("chat-form");
  const input = document.getElementById("chat-input");
  const badge = document.getElementById("llm-badge");
  const polLabel = document.getElementById("pol-label");
  const polBtn = document.getElementById("pol-btn");
  const levels = ["сарказм", "обычно", "мягко", "полный мат"];
  let politeness = 1;

  function addMsg(text, who) {
    const div = document.createElement("div");
    div.className = `chat-msg ${who}`;
    div.textContent = text;
    log.appendChild(div);
    log.scrollTop = log.scrollHeight;
  }

  async function loadStatus() {
    try {
      const s = await getJSON("/api/llm/status");
      badge.textContent = s.available ? s.provider : "fallback";
      badge.title = s.reason || "";
      badge.className = s.available ? "muted small positive" : "muted small";
    } catch {
      badge.textContent = "?";
    }
  }

  form.addEventListener("submit", async (e) => {
    e.preventDefault();
    const msg = input.value.trim();
    if (!msg) return;
    input.value = "";
    addMsg(msg, "user");
    addMsg("…", "bot");
    try {
      const data = await sendJSON("/api/llm/chat", "POST", { message: msg });
      log.lastChild.textContent = data.answer;
      await loadStatus();
    } catch (err) {
      log.lastChild.textContent = "Ошибка: " + err.message;
    }
    log.scrollTop = log.scrollHeight;
  });

  polBtn.addEventListener("click", async () => {
    politeness = (politeness + 1) % 4;
    try {
      await sendJSON(`/api/llm/politeness?level=${politeness}`, "POST", {});
      polLabel.textContent = levels[politeness];
    } catch (e) {
      alert(e.message);
    }
  });

  // init politeness from /me
  getJSON("/me")
    .then((me) => {
      politeness = me.llm_politeness ?? 1;
      polLabel.textContent = levels[politeness] || levels[1];
    })
    .catch(() => {});

  if (window.Telegram && window.Telegram.WebApp) window.Telegram.WebApp.ready();
  loadStatus().catch(console.error);
})();
