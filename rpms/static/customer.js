/* Swipe and keyboard friendly featured-room carousel. */
document.querySelectorAll('[data-carousel]').forEach(track => {
  const buttons = [...track.closest('.discovery').querySelectorAll('[data-slide]')];
  const slides = [...track.children];
  buttons.forEach((button, index) => button.addEventListener('click', () => {
    track.scrollTo({left: slides[index].offsetLeft - slides[0].offsetLeft,
      behavior: matchMedia('(prefers-reduced-motion: reduce)').matches ? 'instant' : 'smooth'});
  }));
  track.addEventListener('scroll', () => {
    const index = Math.round(track.scrollLeft / track.clientWidth);
    buttons.forEach((button, i) => {
      button.classList.toggle('selected', i === index);
      button.setAttribute('aria-pressed', String(i === index));
    });
  }, {passive: true});
  track.addEventListener('keydown', event => {
    if (event.target !== track || !['ArrowLeft', 'ArrowRight'].includes(event.key)) return;
    event.preventDefault();
    const delta = event.key === 'ArrowRight' ? 1 : -1;
    const index = Math.max(0, Math.min(slides.length - 1, Math.round(track.scrollLeft / track.clientWidth) + delta));
    buttons[index]?.click();
  });
});
