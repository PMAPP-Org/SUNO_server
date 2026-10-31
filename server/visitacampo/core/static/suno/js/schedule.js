// Plegado del WBS: cada fila lleva data-id / data-parent; el botón de una
// tarea resumen oculta o muestra toda su rama. Sin dependencias.
(() => {
  const table = document.querySelector('[data-wbs]');
  if (!table) return;
  const rows = Array.from(table.querySelectorAll('tbody tr[data-id]'));
  const byId = new Map(rows.map((r) => [r.dataset.id, r]));
  const collapsed = new Set();

  const hidden = (row) => {
    let parent = row.dataset.parent;
    while (parent) {
      if (collapsed.has(parent)) return true;
      const next = byId.get(parent);
      parent = next ? next.dataset.parent : undefined;
    }
    return false;
  };

  function render() {
    rows.forEach((row) => {
      row.hidden = hidden(row);
      const button = row.querySelector('[data-toggle]');
      if (button) {
        const open = !collapsed.has(row.dataset.id);
        button.setAttribute('aria-expanded', String(open));
        button.textContent = open ? '▾' : '▸';
      }
    });
  }

  table.addEventListener('click', (event) => {
    const button = event.target.closest('[data-toggle]');
    if (!button) return;
    const id = button.closest('tr').dataset.id;
    if (collapsed.has(id)) collapsed.delete(id);
    else collapsed.add(id);
    render();
  });
  document.querySelector('[data-expand-all]')?.addEventListener('click', () => {
    collapsed.clear();
    render();
  });
  document.querySelector('[data-collapse-all]')?.addEventListener('click', () => {
    rows.filter((r) => r.querySelector('[data-toggle]')).forEach((r) => collapsed.add(r.dataset.id));
    render();
  });
})();
