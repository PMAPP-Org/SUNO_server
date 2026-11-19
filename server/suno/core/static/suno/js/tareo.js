// Totales de jornales en vivo en la cuadrícula del tareo. Comodidad: sin este archivo la
// página funciona igual (los totales se recalculan al guardar). Sin dependencias.
(() => {
  const grid = document.querySelector('[data-ta-grid]');
  if (!grid) return;
  const parse = (text) => {
    const value = Number.parseFloat(String(text).trim().replace(',', '.'));
    return Number.isFinite(value) ? value : 0;
  };
  const show = (value) => (Math.round(value * 10000) / 10000).toString().replace('.', ',');

  function render() {
    const days = new Map();
    grid.querySelectorAll('tbody tr[data-ta-row]').forEach((row) => {
      let total = 0;
      row.querySelectorAll('[data-ta-cell]').forEach((cell) => {
        const value = parse(cell.value);
        total += value;
        const key = cell.dataset.taCell;
        days.set(key, (days.get(key) || 0) + value);
      });
      const target = row.querySelector('[data-ta-row-total]');
      if (target) target.textContent = show(total);
    });
    grid.querySelectorAll('[data-ta-day-total]').forEach((cell) => {
      const total = days.get(cell.dataset.taDayTotal) || 0;
      cell.textContent = show(total);
    });
  }

  grid.addEventListener('input', (event) => {
    if (event.target.matches('[data-ta-cell]')) render();
  });
})();
