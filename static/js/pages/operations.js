(() => {
  document.addEventListener('DOMContentLoaded', () => {
    const app = window.AlarmApp;
    if (!app) {
      return;
    }

    const url = new URL(window.location.href);
    app.initCommonPageBindings();
    app.initOperationsPage({
      tab: url.searchParams.get('tab'),
    });
  });
})();
