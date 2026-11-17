(() => {
  const key = 'rpms-theme';
  const preference = window.matchMedia('(prefers-color-scheme: dark)');
  let saved;
  try {
    saved = localStorage.getItem(key);
  } catch (_) {
    /* Storage may be disabled. */
  }
  let explicit = saved === 'dark' || saved === 'light';
  const apply = (theme) => {
    document.documentElement.dataset.theme = theme;
    const button = document.getElementById('theme-toggle');
    if (button) {
      const dark = theme === 'dark';
      button.setAttribute('aria-pressed', String(dark));
      button.setAttribute('aria-label', `Switch to ${dark ? 'light' : 'dark'} mode`);
      button.title = button.getAttribute('aria-label');
      button.querySelector('.theme-label').textContent = dark ? 'Light mode' : 'Dark mode';
    }
  };
  apply(explicit ? saved : preference.matches ? 'dark' : 'light');
  preference.addEventListener('change', (event) => {
    if (!explicit) apply(event.matches ? 'dark' : 'light');
  });
  document.addEventListener('DOMContentLoaded', () => {
    apply(document.documentElement.dataset.theme);
    document.getElementById('theme-toggle')?.addEventListener('click', () => {
      const next = document.documentElement.dataset.theme === 'dark' ? 'light' : 'dark';
      explicit = true;
      apply(next);
      try {
        localStorage.setItem(key, next);
      } catch (_) {
        /* Keep this page usable. */
      }
    });
  });
})();
