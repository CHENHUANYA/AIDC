(() => {
  const STORAGE_KEYS = {
    alarmHistory: 'alarmHistory',
    alarmLog: 'alarmLog',
    fbBad: 'fbBad',
    fbGood: 'fbGood',
    fbHistory: 'fbHistory',
    queryTimings: 'queryTimings',
  };

  function readStorage(key, fallback) {
    try {
      const raw = localStorage.getItem(key);
      return raw ? JSON.parse(raw) : fallback;
    } catch (_) {
      return fallback;
    }
  }

  function writeStorage(key, value) {
    localStorage.setItem(key, JSON.stringify(value));
  }

  function incrementStoredCounter(key) {
    const parsed = Number(localStorage.getItem(key) || '0');
    const current = Number.isFinite(parsed) ? parsed : 0;
    localStorage.setItem(key, String(current + 1));
  }

  window.AlarmCoreStorage = {
    keys: STORAGE_KEYS,
    readStorage,
    writeStorage,
    incrementStoredCounter,
  };
})();
