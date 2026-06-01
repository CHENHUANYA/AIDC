(() => {
  document.addEventListener('DOMContentLoaded', () => {
    const app = window.AlarmApp;
    if (!app) {
      return;
    }

    const url = new URL(window.location.href);
    app.initCommonPageBindings();
    app.initAssistantPage({
      tab: url.searchParams.get('tab'),
      code: url.searchParams.get('code'),
      manual: url.searchParams.get('manual'),
    });
  });
})();
