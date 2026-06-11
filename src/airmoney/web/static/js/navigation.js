export function initNavigationState() {
  document.addEventListener("click", (event) => {
    if (
      event.defaultPrevented ||
      event.button !== 0 ||
      event.metaKey ||
      event.ctrlKey ||
      event.shiftKey ||
      event.altKey
    ) {
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
}
