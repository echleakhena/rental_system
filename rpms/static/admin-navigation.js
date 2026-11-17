/* Keep nested desktop menus compact and visible in the phone navigation strip. */
(() => {
  const nav = document.querySelector('.essential-sidebar nav');
  if (!nav) return;
  const mobile = matchMedia('(max-width: 650px)');
  const groups = [...nav.querySelectorAll('.sidebar-settings-group')];
  const reveal = element => {
    if (!element) return;
    nav.scrollLeft += element.getBoundingClientRect().left - nav.getBoundingClientRect().left - 8;
  };
  const arrange = () => {
    if (!mobile.matches) return;
    groups.forEach(group => { group.open = false; });
    reveal(nav.querySelector('summary.settings-active') || nav.querySelector('[aria-current="page"]'));
  };
  groups.forEach(group => group.addEventListener('toggle', () => {
    if (!mobile.matches || !group.open) return;
    groups.forEach(other => { if (other !== group) other.open = false; });
    reveal(group);
  }));
  mobile.addEventListener('change', arrange);
  arrange();
})();
