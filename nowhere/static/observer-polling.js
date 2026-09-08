/* Poll only while visible; abort in-flight reads and handle back/forward cache. */
function createVisiblePoller(refresh, intervalMs = 4000) {
  let timer = null;
  let controller = null;
  let pageActive = true;

  async function run() {
    if (!pageActive || document.hidden || controller) return;
    const current = new AbortController();
    controller = current;
    try {
      await refresh(current.signal);
    } catch (error) {
      // A sleeping service or an aborted read can be retried on the next tick.
    } finally {
      if (controller === current) controller = null;
    }
  }

  function stop() {
    if (timer !== null) clearInterval(timer);
    timer = null;
    if (controller) controller.abort();
    controller = null;
  }

  function resume() {
    stop();
    if (!pageActive || document.hidden) return;
    run();
    timer = setInterval(run, intervalMs);
  }

  document.addEventListener("visibilitychange", resume);
  window.addEventListener("pagehide", () => { pageActive = false; stop(); });
  window.addEventListener("pageshow", () => {
    if (!pageActive) { pageActive = true; resume(); }
  });
  resume();
  return { refresh: run };
}
