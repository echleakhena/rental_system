document.querySelectorAll('.demo-login-fill').forEach((button) => {
  button.addEventListener('click', () => {
    const identity = document.getElementById(button.dataset.demoIdentityTarget);
    const password = document.getElementById(button.dataset.demoPasswordTarget);
    if (!identity || !password) return;
    identity.value = button.dataset.demoIdentity;
    password.value = button.dataset.demoPassword;
    [identity, password].forEach((input) => {
      input.dispatchEvent(new Event('input', { bubbles: true }));
      input.dispatchEvent(new Event('change', { bubbles: true }));
    });
    button.closest('.demo-login').querySelector('.demo-login-hint').textContent = 'Demo details are ready. Select Sign in to continue.';
    const submit = password.form.querySelector('button[type="submit"], button.button');
    if (submit) submit.focus();
  });
});
