// Gantt del cronograma: SVG propio, sin librerías. Lee #gantt-data
// ({cut, rows, links}) y dibuja una fila por tarea con su barra programada,
// la línea base debajo, los hitos como rombos, las dependencias (FC, CC, FF, CF)
// y la fecha de corte. Rojo = ruta crítica (dato del Project).
(() => {
  const host = document.querySelector('[data-gantt]');
  const source = document.querySelector('#gantt-data');
  if (!host || !source) return;
  const data = JSON.parse(source.textContent || '{}');
  const rows = data.rows || [];
  const scaleSelect = document.querySelector('[data-gantt-scale]');
  const NS = 'http://www.w3.org/2000/svg';
  const DAY = 86400000;
  const ROW = 26;
  // En pantallas angostas la columna de nombres se reduce para que se vean barras.
  const NARROW = host.clientWidth < 640;
  const LABEL = NARROW ? 150 : 300;
  const LABEL_CHARS = NARROW ? 22 : 44;
  const HEAD = 30;

  const toTime = (iso) => (iso ? Date.parse(iso + 'T00:00:00Z') : null);
  const el = (tag, attrs, text) => {
    const node = document.createElementNS(NS, tag);
    Object.entries(attrs).forEach(([k, v]) => node.setAttribute(k, v));
    if (text) node.textContent = text;
    return node;
  };

  function draw() {
    host.textContent = '';
    if (!rows.length) {
      host.textContent = 'Todavía no hay cronograma importado.';
      return;
    }
    const pxPerDay = Number(scaleSelect ? scaleSelect.value : 18);
    const times = rows.flatMap((r) => [r.start, r.finish, r.bstart, r.bfinish].map(toTime)).filter((t) => t !== null);
    const cut = toTime(data.cut);
    if (cut !== null) times.push(cut);
    const min = times.reduce((a, b) => (b < a ? b : a), Infinity) - 2 * DAY;
    const max = times.reduce((a, b) => (b > a ? b : a), -Infinity) + 3 * DAY;
    const x = (t) => LABEL + ((t - min) / DAY) * pxPerDay;
    const width = x(max) + 20;
    const height = HEAD + rows.length * ROW + 10;
    const svg = el('svg', { width, height, viewBox: `0 0 ${width} ${height}`, role: 'img', 'aria-label': 'Diagrama de Gantt' });

    // Encabezado: una marca por lunes (o por día 1 en escala mensual).
    for (let t = min; t <= max; t += DAY) {
      const d = new Date(t);
      const monthly = pxPerDay < 12;
      if ((monthly && d.getUTCDate() === 1) || (!monthly && d.getUTCDay() === 1)) {
        svg.appendChild(el('line', { x1: x(t), x2: x(t), y1: HEAD - 6, y2: height, class: 'grid' }));
        const label = monthly ? d.toISOString().slice(0, 7) : d.toISOString().slice(5, 10);
        svg.appendChild(el('text', { x: x(t) + 3, y: HEAD - 10 }, label));
      }
    }

    const index = new Map(rows.map((r, i) => [r.id, i]));
    rows.forEach((r, i) => {
      const y = HEAD + i * ROW;
      const name = `${r.wbs} ${r.name}`;
      // El sangrado come ancho de la columna de etiquetas: se acorta el texto en proporción.
      const maxChars = Math.max(12, LABEL_CHARS - 2 * (r.level - 1));
      svg.appendChild(el('text', { x: 4 + (r.level - 1) * 12, y: y + 16, 'font-weight': r.summary ? 600 : 400 }, name.length > maxChars ? name.slice(0, maxChars - 1) + '…' : name));
      const bs = toTime(r.bstart);
      const bf = toTime(r.bfinish);
      if (bs !== null && bf !== null && !r.milestone) {
        svg.appendChild(el('rect', { x: x(bs), y: y + 17, width: Math.max(2, x(bf + DAY) - x(bs)), height: 4, class: 'baseline' }));
      }
      const s = toTime(r.start);
      const f = toTime(r.finish);
      if (s === null || f === null) return;
      const cls = `${r.summary ? 'summary' : ''} ${r.critical ? 'critical' : ''}`;
      if (r.milestone) {
        const cx = x(f);
        const diamond = el('path', { d: `M${cx} ${y + 4} l7 7 l-7 7 l-7 -7 z`, class: `milestone ${cls}` });
        diamond.appendChild(el('title', {}, `${name}\n${r.finish}`));
        svg.appendChild(diamond);
      } else {
        const bar = el('rect', { x: x(s), y: y + (r.summary ? 8 : 5), width: Math.max(2, x(f + DAY) - x(s)), height: r.summary ? 6 : 11, class: `bar ${cls}` });
        bar.appendChild(el('title', {}, `${name}\n${r.start} → ${r.finish}${r.bfinish ? `\nLínea base: ${r.bstart} → ${r.bfinish}` : ''}`));
        svg.appendChild(bar);
      }
    });

    (data.links || []).forEach((link) => {
      const a = index.get(link.from);
      const b = index.get(link.to);
      if (a === undefined || b === undefined) return;
      const from = rows[a];
      const to = rows[b];
      const kind = ['FS', 'SS', 'FF', 'SF'].includes(link.type) ? link.type : 'FS';
      const fromStart = kind === 'SS' || kind === 'SF';
      const toFinish = kind === 'FF' || kind === 'SF';
      const fromIso = fromStart ? from.start : from.finish;
      const toIso = toFinish ? to.finish : to.start;
      if (!fromIso || !toIso) return;
      // Fin de tarea = fin del último día; comienzo = inicio del primer día.
      const x1 = x(toTime(fromIso) + (fromStart ? 0 : DAY));
      const y1 = HEAD + a * ROW + 10;
      const x2 = x(toTime(toIso) + (toFinish ? DAY : 0));
      const y2 = HEAD + b * ROW + 10;
      const mid = toFinish ? Math.max(x1 + 6, x2 + 6) : fromStart ? Math.min(x1 - 6, x2 - 6) : Math.max(x1 + 6, x2 - 6);
      svg.appendChild(el('path', { d: `M${x1} ${y1} H${mid} V${y2} H${x2}`, class: 'link' }));
    });

    if (cut !== null) {
      svg.appendChild(el('line', { x1: x(cut + DAY), x2: x(cut + DAY), y1: HEAD - 6, y2: height, class: 'cut' }));
    }
    host.appendChild(svg);
  }

  scaleSelect?.addEventListener('change', draw);
  draw();
})();
