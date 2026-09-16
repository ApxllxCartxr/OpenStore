/* OpenStore shell: fetch wrapper (S24). Injects operator identity, session
 * credentials, and CSRF; renders errors uniformly. */
"use strict";

function makeApi(operatorId) {
  const headers = { "Content-Type": "application/json" };
  if (operatorId) headers["X-Operator-Id"] = operatorId;

  function csrfToken() {
    const m = document.querySelector('meta[name="csrf-token"]');
    return m ? m.content : "";
  }

  async function api(path, body, opts) {
    const h = { ...headers };
    const csrf = csrfToken();
    if (csrf) h["X-OpenStore-CSRF"] = csrf;
    const res = await fetch(path, {
      method: (opts && opts.method) || "POST",
      headers: h,
      credentials: "same-origin",
      body: body === undefined ? "{}" : JSON.stringify(body),
    });
    const data = await res.json().catch(() => ({}));
    if (!res.ok) {
      const detail = data && data.detail ? data.detail : data;
      const code = detail && detail.reason_code ? detail.reason_code : "http_" + res.status;
      const err = new Error(code);
      err.code = code;
      err.status = res.status;
      throw err;
    }
    return data;
  }

  async function get(path) {
    const res = await fetch(path, { credentials: "same-origin" });
    const data = await res.json().catch(() => ({}));
    if (!res.ok) {
      const err = new Error("http_" + res.status);
      err.status = res.status;
      throw err;
    }
    return data;
  }

  return { api, get, headers };
}

