(() => {
  document.addEventListener('DOMContentLoaded', () => {
    const app = window.AlarmApp;
    if (!app) {
      return;
    }

    app.initCommonPageBindings();
    app.initLegacyPage();
  });
})();
