export function initScanProgress() {
  const view = createProgressView();
  if (!view.root) {
    return;
  }

  let pollTimer = 0;
  let hideTimer = 0;
  let progressWasShown = false;

  window.addEventListener("airmoney:scan-start", () => {
    showProgress(
      {
        latest_scan: {
          status: "running",
          total_items: 0,
          current_item_index: 0,
          progress_message: "Запускаем скан",
        },
        scan_running: true,
      },
      view,
    );
    progressWasShown = true;
    schedulePoll(250);
  });

  window.addEventListener("airmoney:scan-error", (event) => {
    showProgress(
      {
        latest_scan: {
          status: "error",
          total_items: 0,
          current_item_index: 0,
          progress_message: event.detail?.message || "Ошибка запуска",
        },
        scan_running: false,
      },
      view,
    );
    progressWasShown = true;
  });

  window.addEventListener("airmoney:scan-poll", () => schedulePoll(250));

  pollStatus();

  function schedulePoll(delay) {
    window.clearTimeout(pollTimer);
    pollTimer = window.setTimeout(pollStatus, delay);
  }

  async function pollStatus() {
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
        showProgress(data, view);
        progressWasShown = true;
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

  function hideProgress() {
    progressWasShown = false;
    view.root.classList.remove("is-running", "is-error", "is-indeterminate");
    view.root.hidden = true;
  }
}

function createProgressView() {
  return {
    root: document.querySelector("[data-scan-progress]"),
    title: document.querySelector("[data-scan-title]"),
    message: document.querySelector("[data-scan-message]"),
    percent: document.querySelector("[data-scan-percent]"),
    bar: document.querySelector("[data-scan-bar]"),
    count: document.querySelector("[data-scan-count]"),
    counters: document.querySelector("[data-scan-counters]"),
  };
}

function showProgress(data, view) {
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

  view.root.hidden = false;
  view.root.classList.toggle("is-running", running);
  view.root.classList.toggle("is-error", status === "error");
  view.root.classList.toggle("is-indeterminate", running && total === 0);
  view.title.textContent = titleForStatus(status, running);
  view.message.textContent = message;
  view.percent.textContent = `${percent}%`;
  view.bar.style.width = `${percent}%`;
  view.count.textContent = total > 0 ? `${Math.min(current, total)} / ${total}` : "подготовка";
  view.counters.textContent = `Лотов ${numberOr(latest.listings_saved, 0)}, кандидатов ${numberOr(latest.candidates_saved, 0)}`;
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
