// Navegación tipo "vista rápida": los enlaces con `data-peek` abren la página en un
// panel flotante a la derecha, con opción de expandirla a página completa.
(() => {
  const framed = document.documentElement.classList.contains('is-peek');
  const MESSAGE = 'suno-peek-close';

  if (framed) {
    // Dentro del panel: Esc y los enlaces "Cancelar" cierran el panel padre.
    const closeParent = () => window.parent.postMessage({ type: MESSAGE }, window.location.origin);
    document.addEventListener('keydown', (event) => {
      if (event.key === 'Escape') closeParent();
    });
    document.addEventListener('click', (event) => {
      const target = event.target instanceof Element ? event.target.closest('[data-peek-close]') : null;
      if (!target) return;
      event.preventDefault();
      closeParent();
    });
    return;
  }

  const icons = {
    expand: '<svg viewBox="0 0 24 24" width="16" height="16" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M15 3h6v6"/><path d="M9 21H3v-6"/><path d="M21 3l-7 7"/><path d="M3 21l7-7"/></svg>',
    close: '<svg viewBox="0 0 24 24" width="16" height="16" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" aria-hidden="true"><path d="M18 6L6 18"/><path d="M6 6l12 12"/></svg>',
  };

  let root = null;
  let frame = null;
  let titleEl = null;
  let closeBtn = null;
  let opener = null;
  let dirty = false;

  const build = () => {
    root = document.createElement('div');
    root.className = 'peek-root';
    root.hidden = true;
    root.innerHTML = `
      <div class="peek-backdrop" data-peek-backdrop></div>
      <aside class="peek-panel" role="dialog" aria-modal="true" aria-label="Vista rápida">
        <header class="peek-bar">
          <button type="button" class="peek-btn" data-peek-action="close" aria-label="Cerrar" title="Cerrar (Esc)">${icons.close}</button>
          <button type="button" class="peek-btn peek-expand" data-peek-action="expand" title="Abrir como página completa">${icons.expand}<span>Expandir</span></button>
          <span class="peek-title" data-peek-title></span>
        </header>
        <iframe class="peek-frame" title="Contenido de la vista rápida"></iframe>
      </aside>`;
    document.body.appendChild(root);
    frame = root.querySelector('iframe');
    titleEl = root.querySelector('[data-peek-title]');
    closeBtn = root.querySelector('[data-peek-action="close"]');
    root.querySelector('[data-peek-backdrop]').addEventListener('click', close);
    root.querySelector('[data-peek-action="close"]').addEventListener('click', close);
    root.querySelector('[data-peek-action="expand"]').addEventListener('click', expand);
    frame.addEventListener('load', onFrameLoad);
  };

  const currentUrl = () => {
    try {
      return frame.contentWindow.location.href;
    } catch (error) {
      return frame.src;
    }
  };

  const onFrameLoad = () => {
    try {
      const doc = frame.contentDocument;
      if (!doc || frame.src === 'about:blank') return;
      titleEl.textContent = (doc.title || '').replace(/\s*·\s*SUNO\s*$/, '');
      // Un mensaje de éxito tras guardar significa que hay datos nuevos que mostrar al cerrar.
      if (doc.querySelector('.eq-message-success')) dirty = true;
    } catch (error) {
      /* mismo origen: no debería ocurrir */
    }
  };

  const open = (url, trigger) => {
    if (!root) build();
    opener = trigger;
    dirty = false;
    titleEl.textContent = 'Cargando…';
    frame.src = url;
    root.hidden = false;
    document.documentElement.classList.add('peek-open');
    requestAnimationFrame(() => root.classList.add('is-visible'));
    closeBtn.focus();
  };

  function close() {
    if (!root || root.hidden) return;
    root.classList.remove('is-visible');
    root.hidden = true;
    document.documentElement.classList.remove('peek-open');
    frame.src = 'about:blank';
    const shouldRefresh = dirty;
    dirty = false;
    if (opener && document.contains(opener)) opener.focus();
    if (shouldRefresh) window.location.reload();
  }

  function expand() {
    const url = currentUrl();
    dirty = false;
    window.location.assign(url);
  }

  document.addEventListener('click', (event) => {
    if (event.defaultPrevented || event.button !== 0) return;
    if (event.metaKey || event.ctrlKey || event.shiftKey || event.altKey) return;
    const link = event.target instanceof Element ? event.target.closest('a[data-peek]') : null;
    if (!link || link.target === '_blank') return;
    event.preventDefault();
    open(link.href, link);
  });

  document.addEventListener('keydown', (event) => {
    if (event.key === 'Escape' && root && !root.hidden) close();
  });

  window.addEventListener('message', (event) => {
    if (event.origin !== window.location.origin) return;
    if (!frame || event.source !== frame.contentWindow) return;
    if (event.data && event.data.type === MESSAGE) close();
  });
})();
