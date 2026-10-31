// Progressive enhancement for the "try it" form (D-110). With this disabled or
// blocked, the plain HTML form below still posts to /panel and works exactly
// the same; this only intercepts the submit to avoid the full-page reload.
"use strict";

function bind(section) {
  for (const form of section.querySelectorAll("form")) {
    form.addEventListener("submit", onSubmit);
  }
}

async function onSubmit(event) {
  const form = event.target;
  // Built before preventDefault: if an old browser throws here, the plain post still goes.
  const data = new FormData(form, event.submitter);
  event.preventDefault();
  const section = form.closest("section.try");
  const buttons = section.querySelectorAll("button");
  const primary = section.querySelector("button.primary");
  const original = primary.textContent;

  clearError(section);
  for (const button of buttons) button.disabled = true;
  primary.textContent = section.dataset.busy;
  primary.classList.add("busy");

  try {
    const response = await fetch("/panel", {
      method: "POST",
      body: data,
      signal: AbortSignal.timeout(30000), // never hang: the analysis takes 5-9 s
    });
    if (!response.ok) throw new Error(`status ${response.status}`);
    const html = await response.text();
    const next = new DOMParser()
      .parseFromString(html, "text/html")
      .querySelector("section.try");
    if (!next) throw new Error("no result section in response");
    section.replaceWith(next);
    bind(next);
  } catch (error) {
    for (const button of buttons) button.disabled = false;
    primary.textContent = original;
    primary.classList.remove("busy");
    showError(section, section.dataset.error);
  }
}

function showError(section, message) {
  clearError(section);
  const p = document.createElement("p");
  p.className = "note js-error";
  p.setAttribute("role", "alert");
  p.textContent = message;
  section.querySelector("form").after(p);
}

function clearError(section) {
  const existing = section.querySelector(".js-error");
  if (existing) existing.remove();
}

document.addEventListener("DOMContentLoaded", () => {
  const section = document.querySelector("section.try");
  if (section) bind(section);
});
