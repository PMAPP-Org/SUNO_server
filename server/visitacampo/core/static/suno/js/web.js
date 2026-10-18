(() => {
  const passwordButton = document.querySelector('[data-password-toggle]');
  if (passwordButton) {
    const input = passwordButton.closest('.password-field')?.querySelector('input');
    passwordButton.addEventListener('click', () => {
      if (!input) return;
      const revealing = input.type === 'password';
      input.type = revealing ? 'text' : 'password';
      passwordButton.setAttribute('aria-pressed', String(revealing));
      passwordButton.setAttribute('aria-label', revealing ? 'Ocultar contraseña' : 'Mostrar contraseña');
      const label = passwordButton.querySelector('span');
      if (label) label.textContent = revealing ? 'Ocultar' : 'Mostrar';
    });
  }

  const loginForm = document.querySelector('[data-login-form]');
  loginForm?.addEventListener('submit', () => {
    const submit = loginForm.querySelector('[type="submit"]');
    if (!submit) return;
    submit.disabled = true;
    submit.textContent = submit.dataset.loadingLabel || 'Ingresando…';
    submit.setAttribute('aria-busy', 'true');
  });

  const sidebarToggle = document.querySelector('[data-sidebar-toggle]');
  const sidebar = document.querySelector('#workspace-sidebar');
  const closeSidebar = () => {
    sidebar?.classList.remove('open');
    sidebarToggle?.setAttribute('aria-expanded', 'false');
  };
  sidebarToggle?.addEventListener('click', () => {
    const opened = sidebar?.classList.toggle('open') || false;
    sidebarToggle.setAttribute('aria-expanded', String(opened));
  });
  sidebar?.querySelectorAll('a').forEach((link) => link.addEventListener('click', closeSidebar));
  document.addEventListener('keydown', (event) => {
    if (event.key === 'Escape') closeSidebar();
  });

  const search = document.querySelector('[data-platform-search]');
  const searchableRows = [...document.querySelectorAll('[data-searchable]')];
  const searchEmpty = document.querySelector('[data-search-empty]');
  search?.addEventListener('input', () => {
    const query = search.value.trim().toLocaleLowerCase('es');
    let visible = 0;
    searchableRows.forEach((row) => {
      const match = !query || row.dataset.searchable.toLocaleLowerCase('es').includes(query);
      row.hidden = !match;
      if (match) visible += 1;
    });
    if (searchEmpty) searchEmpty.hidden = !query || visible > 0 || searchableRows.length === 0;
  });

  const mapData = document.querySelector('#platform-map-data');
  const markerLayer = document.querySelector('[data-map-markers]');
  const mapStage = document.querySelector('[data-map-stage]');
  if (mapData && markerLayer && mapStage) {
    const points = JSON.parse(mapData.textContent || '[]');
    const panel = document.querySelector('[data-record-panel]');
    const values = (key) => points.map((point) => Number(point[key]));
    const lats = values('lat');
    const lons = values('lon');
    const minLat = Math.min(...lats);
    const maxLat = Math.max(...lats);
    const minLon = Math.min(...lons);
    const maxLon = Math.max(...lons);
    const latSpan = Math.max(maxLat - minLat, 0.0001);
    const lonSpan = Math.max(maxLon - minLon, 0.0001);
    const formatter = new Intl.DateTimeFormat('es-CO', { dateStyle: 'medium', timeStyle: 'short' });

    points.forEach((point) => {
      const marker = document.createElement('button');
      marker.type = 'button';
      marker.className = 'data-map-marker';
      marker.style.left = `${10 + ((Number(point.lon) - minLon) / lonSpan) * 80}%`;
      marker.style.top = `${90 - ((Number(point.lat) - minLat) / latSpan) * 80}%`;
      marker.setAttribute('aria-label', `Abrir ${point.title}`);
      marker.title = point.title;
      marker.addEventListener('click', () => {
        if (!panel) return;
        panel.hidden = false;
        panel.querySelector('[data-record-title]').textContent = point.title;
        panel.querySelector('[data-record-author]').textContent = point.author;
        panel.querySelector('[data-record-date]').textContent = formatter.format(new Date(point.captured_at));
        panel.querySelector('[data-record-coordinates]').textContent = `${Number(point.lat).toFixed(5)}, ${Number(point.lon).toFixed(5)}`;
      });
      markerLayer.appendChild(marker);
    });

    panel?.querySelector('[data-record-close]')?.addEventListener('click', () => { panel.hidden = true; });
    let zoom = 1;
    document.querySelectorAll('[data-map-zoom]').forEach((control) => {
      control.addEventListener('click', () => {
        zoom = control.dataset.mapZoom === 'in' ? Math.min(1.6, zoom + 0.15) : Math.max(1, zoom - 0.15);
        mapStage.style.transform = `scale(${zoom})`;
      });
    });
    document.querySelector('[data-map-layer]')?.addEventListener('click', (event) => {
      const button = event.currentTarget;
      const visible = !mapStage.classList.toggle('without-topography');
      button.setAttribute('aria-pressed', String(visible));
    });
  }
})();
