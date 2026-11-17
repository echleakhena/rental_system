/* Native dialog keeps focus contained and restores it to the selected photo. */
(() => {
  const dialog = document.querySelector('.room-lightbox');
  const photos = [...document.querySelectorAll('[data-room-photo]')];
  if (!dialog || !photos.length) return;
  let current = 0;
  const previous = dialog.querySelector('[data-photo-prev]');
  const next = dialog.querySelector('[data-photo-next]');
  function show(index) {
    current = Math.max(0, Math.min(photos.length - 1, index));
    const photo = photos[current];
    const image = dialog.querySelector('[data-photo-image]');
    image.src = photo.href;
    image.alt = photo.querySelector('img').alt;
    dialog.querySelector('[data-photo-title]').textContent = photo.dataset.category;
    dialog.querySelector('[data-photo-position]').textContent = `${current + 1} / ${photos.length}`;
    dialog.querySelector('[data-photo-note]').textContent = photo.dataset.sample === 'true' ? 'Sample interior · for illustration' : 'Room photo';
    previous.disabled = current === 0;
    next.disabled = current === photos.length - 1;
  }
  photos.forEach((photo, index) => photo.addEventListener('click', event => {
    if (event.metaKey || event.ctrlKey || event.shiftKey || event.altKey) return;
    event.preventDefault();
    show(index);
    dialog.showModal();
  }));
  previous.addEventListener('click', () => show(current - 1));
  next.addEventListener('click', () => show(current + 1));
  dialog.querySelector('[data-photo-close]').addEventListener('click', () => dialog.close());
  dialog.addEventListener('keydown', event => {
    if (event.key === 'ArrowLeft' || event.key === 'ArrowRight') {
      event.preventDefault();
      show(current + (event.key === 'ArrowLeft' ? -1 : 1));
    }
  });
  dialog.addEventListener('click', event => {if (event.target === dialog) dialog.close();});
})();
