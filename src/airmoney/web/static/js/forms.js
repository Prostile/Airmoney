const scanFormPattern = /^\/(?:scan|collections\/[^/]+\/scan|items\/[^/]+\/scan)$/;

export function initFormState() {
  document.addEventListener("submit", (event) => {
    const form = event.target;
    if (!(form instanceof HTMLFormElement)) {
      return;
    }

    const message = form.getAttribute("data-confirm");
    if (message && !window.confirm(message)) {
      event.preventDefault();
      return;
    }

    const submitter = event.submitter instanceof HTMLElement ? event.submitter : null;
    if (isScanForm(form)) {
      event.preventDefault();
      runScanWithoutReload(form, submitter);
      return;
    }

    markSubmitting(form, submitter, false);
  });
}

export function isScanForm(form) {
  const path = new URL(form.action, window.location.href).pathname;
  return scanFormPattern.test(path);
}

export async function runScanWithoutReload(form, submitter) {
  markSubmitting(form, submitter, true);
  window.dispatchEvent(new CustomEvent("airmoney:scan-start"));

  try {
    const response = await fetch(form.action, {
      method: "POST",
      body: new FormData(form),
      credentials: "same-origin",
      headers: { "X-Requested-With": "fetch" },
    });
    if (!response.ok) {
      throw new Error(`HTTP ${response.status}`);
    }
  } catch (error) {
    window.dispatchEvent(
      new CustomEvent("airmoney:scan-error", {
        detail: { message: `Ошибка запуска: ${error.message}` },
      }),
    );
  } finally {
    markIdle(form, submitter);
    window.dispatchEvent(new CustomEvent("airmoney:scan-poll"));
  }
}

function markSubmitting(form, submitter, disable) {
  form.classList.add("is-submitting");
  if (!submitter) {
    return;
  }
  submitter.classList.add("is-loading");
  submitter.setAttribute("aria-busy", "true");
  if (disable) {
    submitter.setAttribute("disabled", "disabled");
  }
}

function markIdle(form, submitter) {
  form.classList.remove("is-submitting");
  if (!submitter) {
    return;
  }
  submitter.classList.remove("is-loading");
  submitter.removeAttribute("aria-busy");
  submitter.removeAttribute("disabled");
}
