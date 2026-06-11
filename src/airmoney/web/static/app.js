(() => {
  const scanFormPattern = /^\/(?:scan|collections\/[^/]+\/scan|items\/[^/]+\/scan)$/;
  const progressEl = document.querySelector("[data-scan-progress]");
  const progressTitle = document.querySelector("[data-scan-title]");
  const progressMessage = document.querySelector("[data-scan-message]");
  const progressPercent = document.querySelector("[data-scan-percent]");
  const progressBar = document.querySelector("[data-scan-bar]");
  const progressCount = document.querySelector("[data-scan-count]");
  const progressCounters = document.querySelector("[data-scan-counters]");

  let pollTimer = 0;
  let hideTimer = 0;
  let progressWasShown = false;

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

  document.addEventListener("click", (event) => {
    if (event.defaultPrevented || event.button !== 0 || event.metaKey || event.ctrlKey || event.shiftKey || event.altKey) {
      return;
    }
    const target = event.target instanceof Element ? event.target.closest("a") : null;
    if (!target || target.target || target.hasAttribute("download")) {
      return;
    }
    const url = new URL(target.href, window.location.href);
    if (url.origin === window.location.origin && url.pathname !== window.location.pathname) {
      document.body.classList.add("page-leaving");
    }
  });

  pollStatus();

  function isScanForm(form) {
    const path = new URL(form.action, window.location.href).pathname;
    return scanFormPattern.test(path);
  }

  async function runScanWithoutReload(form, submitter) {
    markSubmitting(form, submitter, true);
    showProgress({
      latest_scan: {
        status: "running",
        total_items: 0,
        current_item_index: 0,
        progress_message: "Запускаем скан",
      },
      scan_running: true,
    });
    schedulePoll(250);

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
      showProgress({
        latest_scan: {
          status: "error",
          total_items: 0,
          current_item_index: 0,
          progress_message: `Ошибка запуска: ${error.message}`,
        },
        scan_running: false,
      });
    } finally {
      markIdle(form, submitter);
      schedulePoll(250);
    }
  }

  function markSubmitting(form, submitter, disable) {
    form.classList.add("is-submitting");
    if (submitter) {
      submitter.classList.add("is-loading");
      submitter.setAttribute("aria-busy", "true");
      if (disable) {
        submitter.setAttribute("disabled", "disabled");
      }
    }
  }

  function markIdle(form, submitter) {
    form.classList.remove("is-submitting");
    if (submitter) {
      submitter.classList.remove("is-loading");
      submitter.removeAttribute("aria-busy");
      submitter.removeAttribute("disabled");
    }
  }

  function schedulePoll(delay) {
    window.clearTimeout(pollTimer);
    pollTimer = window.setTimeout(pollStatus, delay);
  }

  async function pollStatus() {
    if (!progressEl) {
      return;
    }
    try {
      const response = await fetch("/api/status", {
        credentials: "same-origin",
        headers: { Accept: "application/json" },
      });
      if (!response.ok) {
        return;
      }
      const data = await response.json();
      const running = Boolean(data.scan_running || data.latest_scan?.status === "running");
      if (running || progressWasShown) {
        showProgress(data);
      }
      if (running) {
        schedulePoll(900);
      } else if (progressWasShown) {
        window.clearTimeout(hideTimer);
        hideTimer = window.setTimeout(hideProgress, 5000);
        schedulePoll(8000);
      } else {
        schedulePoll(8000);
      }
    } catch (_) {
      if (progressWasShown) {
        schedulePoll(2500);
      }
    }
  }

  function showProgress(data) {
    if (!progressEl) {
      return;
    }
    const latest = data.latest_scan || {};
    const summary = data.scan_summary || {};
    const status = latest.status || (data.scan_running ? "running" : "");
    const total = numberOr(latest.total_items, summary.scan_targets, 0);
    let current = numberOr(latest.current_item_index, latest.scanned_items, 0);
    const running = Boolean(data.scan_running || status === "running");
    if (status === "success" && total > 0) {
      current = total;
    }
    const percent = total > 0 ? Math.min(100, Math.max(0, Math.round((current / total) * 100))) : running ? 8 : 100;
    const message = latest.progress_message || latest.error || (running ? "Скан выполняется" : "Готово");

    progressWasShown = true;
    progressEl.hidden = false;
    progressEl.classList.toggle("is-running", running);
    progressEl.classList.toggle("is-error", status === "error");
    progressEl.classList.toggle("is-indeterminate", running && total === 0);
    progressTitle.textContent = titleForStatus(status, running);
    progressMessage.textContent = message;
    progressPercent.textContent = `${percent}%`;
    progressBar.style.width = `${percent}%`;
    progressCount.textContent = total > 0 ? `${Math.min(current, total)} / ${total}` : "подготовка";
    progressCounters.textContent = `Лотов ${numberOr(latest.listings_saved, 0)}, кандидатов ${numberOr(latest.candidates_saved, 0)}`;
  }

  function hideProgress() {
    if (!progressEl) {
      return;
    }
    progressWasShown = false;
    progressEl.classList.remove("is-running", "is-error", "is-indeterminate");
    progressEl.hidden = true;
  }

  function titleForStatus(status, running) {
    if (running) {
      return "Сканирование";
    }
    if (status === "success") {
      return "Скан завершён";
    }
    if (status === "skipped") {
      return "Скан пропущен";
    }
    if (status === "error") {
      return "Ошибка скана";
    }
    return "Сканирование";
  }

  function numberOr(...values) {
    for (const value of values) {
      const number = Number(value);
      if (Number.isFinite(number)) {
        return number;
      }
    }
    return 0;
  }
})();
