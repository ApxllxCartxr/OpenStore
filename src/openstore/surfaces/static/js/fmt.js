/* OpenStore shell: formatting (S24). INR minor units, RFC 3339 dates, esc. */
"use strict";

function esc(s) {
  return String(s === null || s === undefined ? "" : s)
    .replace(/&/g, "&").replace(/</g, "<").replace(/>/g, ">")
    .replace(/"/g, """).replace(/'/g, "'");
}

function inr(minor) {
  return "₹" + (Number(minor) / 100).toFixed(2);
}

function fmtWhen(iso) {
  if (!iso) return "—";
  return String(iso).slice(0, 16).replace("T", " ");
}

