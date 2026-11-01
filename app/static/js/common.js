/* shared helpers — also loaded on pages that use app.js-style utils */

function fmtMoney(n) {
  return new Intl.NumberFormat("ru-RU", {
    style: "currency",
    currency: "RUB",
    maximumFractionDigits: 2,
  }).format(n);
}

function escapeHtml(str) {
  return String(str ?? "")
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;");
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
  let data = null;
  try {
    data = await res.json();
  } catch {
    /* empty */
  }
  if (!res.ok) {
    const detail = data && data.detail ? data.detail : `HTTP ${res.status}`;
    throw new Error(typeof detail === "string" ? detail : JSON.stringify(detail));
  }
  return data;
}

function todayISO() {
  const d = new Date();
  const pad = (n) => String(n).padStart(2, "0");
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}`;
}

function nowLocalInput() {
  return todayISO();
}
