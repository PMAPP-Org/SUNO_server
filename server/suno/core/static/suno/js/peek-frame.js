// Marca temprana (antes del primer pintado) de que la página se muestra dentro del
// panel flotante, para ocultar el chrome de navegación sin parpadeo.
(function () {
  try {
    if (window.self !== window.top) document.documentElement.classList.add('is-peek');
  } catch (error) {
    document.documentElement.classList.add('is-peek');
  }
})();
