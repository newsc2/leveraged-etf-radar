(function () {
  'use strict';
  const DAY = 86400000;
  const percent = value => value === null ? '—' : `${value > 0 ? '+' : ''}${value.toFixed(1)}%`;
  const dollars = value => new Intl.NumberFormat('en-US', {
    style: 'currency', currency: 'USD', maximumFractionDigits: 0,
  }).format(value);
  const compact = value => '$' + (value >= 1e6 ? (value / 1e6).toFixed(2) + 'm'
    : value >= 1e3 ? (value / 1e3).toFixed(1) + 'k' : Math.round(value));
  const timestamp = date => Date.parse(date + 'T00:00:00Z');
  const annualize = (ratio, start, end) => end > start
    ? 100 * (Math.pow(ratio, 365.2425 * DAY / (timestamp(end) - timestamp(start))) - 1) : null;

  function anniversary(start, years) {
    const day = new Date(timestamp(start));
    const month = day.getUTCMonth();
    day.setUTCFullYear(day.getUTCFullYear() + years);
    if (day.getUTCMonth() !== month) day.setUTCDate(0);
    return day.toISOString().slice(0, 10);
  }

  function calculate(data, selected) {
    if (!/^\d{4}-\d{2}-\d{2}$/.test(selected) || !Number.isFinite(timestamp(selected))
      || new Date(timestamp(selected)).toISOString().slice(0, 10) !== selected
      || selected < data.start || selected > data.end) throw new Error('Start date is outside the available range.');
    return data.funds.map(fund => {
      const rows = fund.prices.filter(row => row[0] >= selected && row[0] <= data.end);
      if (!rows.length) return { ...fund, available: false };
      const [start, base] = rows[0];
      const [end, last] = rows[rows.length - 1];
      const annual = {};
      let previous = rows[0];
      for (let year = Number(start.slice(0, 4)); year <= Number(end.slice(0, 4)); year++) {
        const observations = rows.filter(row => Number(row[0].slice(0, 4)) === year);
        if (!observations.length) continue;
        const final = observations[observations.length - 1];
        annual[year] = { total: 100 * (final[1] / previous[1] - 1),
          partial: year === Number(start.slice(0, 4)), start: previous[0], end: final[0] };
        previous = final;
      }
      const horizons = [1, 3, 5, 10].map(years => {
        const target = anniversary(start, years);
        const close = rows.find(row => row[0] >= target);
        if (!close) return null;
        const ratio = close[1] / base;
        return { start, end: close[0], total: 100 * (ratio - 1), cagr: annualize(ratio, start, close[0]) };
      });
      return { ...fund, available: true, start, end, value: 10000 * last / base,
        cagr: annualize(last / base, start, end), annual, horizons,
        chart: rows.map(row => [row[0], 10000 * row[1] / base]) };
    });
  }

  function mount() {
    const root = document.getElementById('investment-comparison');
    if (!root) return;
    const data = JSON.parse(document.getElementById('comparison-data').textContent);
    const picker = root.querySelector('#comparison-start');
    const chart = root.querySelector('#comparison-chart');
    const error = root.querySelector('#comparison-error');
    const colors = ['#0D7680', '#2E6E9E', '#6B8F71', '#A6485F', '#8B7D98', '#C4715E',
      '#0D7680', '#2E6E9E', '#A6485F', '#646C36', '#555555', '#B87618', '#9A7819'];
    let selected = data.defaultStart;
    try {
      const saved = localStorage.getItem('radar-comparison-start');
      if (saved && saved >= data.start && saved <= data.end) selected = saved;
    } catch (_) { /* Storage may be disabled in private browsing. */ }
    if (selected > data.end) selected = data.start;
    picker.min = data.start;
    picker.max = data.end;
    picker.value = selected;
    root.querySelector('#comparison-asof').textContent = `Completed closes through ${data.end}`;
    let results;
    let resizeTimer;

    function renderTables() {
      root.querySelector('#comparison-horizons').innerHTML =
        '<thead><tr><th scope="col">Asset</th><th scope="col">Effective start</th><th scope="col">$10k at cutoff</th>' +
        '<th scope="col">Overall CAGR</th>' + [1, 3, 5, 10].map(n => `<th scope="col">${n}Y forward</th>`).join('') +
        '</tr></thead><tbody>' + results.map(f => {
          const name = `<th scope="row"><a href="${f.source}" target="_blank" rel="noopener">${f.label}</a></th>`;
          if (!f.available) return `<tr>${name}<td colspan="7">No history for this period</td></tr>`;
          return `<tr>${name}<td>${f.start}</td><td>${dollars(f.value)}</td><td>${percent(f.cagr)}</td>` +
            f.horizons.map(h => h ? `<td>${percent(h.total)}<small>${percent(h.cagr)} / yr</small><small>to ${h.end}</small></td>` :
              '<td aria-label="Window not yet complete">—</td>').join('') + '</tr>';
        }).join('') + '</tbody>';
      const endYear = Number(data.end.slice(0, 4));
      root.querySelector('#comparison-annual').innerHTML = '<thead><tr><th scope="col">Year</th>' +
        results.map(f => `<th scope="col">${f.label}</th>`).join('') + '</tr></thead><tbody>' +
        Array.from({length: endYear - Number(selected.slice(0, 4)) + 1}, (_, i) => endYear - i).map(year =>
          `<tr><th scope="row">${year}${year === endYear && !data.end.endsWith('12-31') ? ' YTD' : ''}</th>` +
          results.map(f => {
            const a = f.available ? f.annual[year] : null;
            return a ? `<td class="${a.total >= 0 ? 'positive' : 'negative'}" title="${a.start} through ${a.end}">${percent(a.total)}${a.partial ? '*' : ''}</td>` : '<td>—</td>';
          }).join('') + '</tr>').join('') + '</tbody>';
    }

    function renderChart() {
      if (typeof Plotly === 'undefined') {
        chart.textContent = 'The chart library could not load. Returns are available in the tables below.';
        return;
      }
      const width = chart.getBoundingClientRect().width;
      const height = width < 640 ? 560 : 580;
      const traces = [];
      let minimum = 10000, maximum = 10000;
      results.forEach((f, i) => {
        if (!f.available) return;
        f.chart.forEach(row => { minimum = Math.min(minimum, row[1]); maximum = Math.max(maximum, row[1]); });
        traces.push({ x: f.chart.map(p => p[0]), y: f.chart.map(p => p[1]), name: f.label,
          type: 'scatter', mode: f.chart.length === 1 ? 'markers' : 'lines',
          line: { color: colors[i], width: 1.6, dash: f.leveraged ? 'dash' : 'solid' },
          marker: { color: colors[i], size: 5 },
          hovertemplate: '%{x|%b %d, %Y}<br>$%{y:,.0f}<extra>%{fullData.name}</extra>', showlegend: false });
      });
      const low = Math.log10(minimum) - .12, high = Math.log10(maximum) + .18;
      const labels = results.map((f, i) => f.available ? {
        f, color: colors[i], original: (Math.log10(f.value) - low) / (high - low),
        y: (Math.log10(f.value) - low) / (high - low),
      } : null).filter(Boolean).sort((a, b) => a.y - b.y);
      const gap = 19 / (height - 90);
      for (let i = 1; i < labels.length; i++) labels[i].y = Math.max(labels[i].y, labels[i - 1].y + gap);
      if (labels.length && labels[labels.length - 1].y > .97) {
        labels[labels.length - 1].y = .97;
        for (let i = labels.length - 2; i >= 0; i--) labels[i].y = Math.min(labels[i].y, labels[i + 1].y - gap);
      }
      const labelText = f => `${f.label}${width >= 760 ? ' ' + compact(f.value) : ''}`;
      const end = selected === data.end ? new Date(timestamp(data.end) + DAY).toISOString().slice(0, 10) : data.end;
      const lastX = selected === data.end ? 0 : 1;
      Plotly.react(chart, traces, {
        autosize: true, height, margin: { l: width < 640 ? 57 : 72, r: width >= 760 ? 157 : 94, t: 20, b: 60 },
        font: { family: 'Inter, system-ui, sans-serif', color: '#222222', size: 12 },
        paper_bgcolor: '#FFFFFF', plot_bgcolor: '#FFFFFF', showlegend: false,
        xaxis: { type: 'date', title: { text: 'Calendar date' }, range: [selected, end], nticks: width < 640 ? 4 : 7,
          gridcolor: '#E8E8E8', zeroline: false, fixedrange: true },
        yaxis: { type: 'log', title: { text: 'Portfolio value (USD)' }, tickprefix: '$', tickformat: '~s',
          range: [low, high], gridcolor: '#E8E8E8', zeroline: false, fixedrange: true },
        hovermode: 'x unified', hoverlabel: { bgcolor: '#FFFFFF', font: { size: 12 } },
        annotations: labels.map(l => ({ xref: 'paper', yref: 'paper', x: 1.035, y: l.y,
          text: labelText(l.f), showarrow: false, xanchor: 'left', yanchor: 'middle', font: { color: '#222222', size: 12 } })),
        shapes: labels.map(l => ({ type: 'line', xref: 'paper', yref: 'paper', x0: lastX,
          x1: 1.027, y0: l.original, y1: l.y, line: { color: l.color, width: 1 } })),
      }, { responsive: true, displayModeBar: false, scrollZoom: false });
    }

    function update() {
      try {
        if (!picker.value || !picker.checkValidity()) throw new Error(`Choose a date from ${data.start} through ${data.end}.`);
        const next = calculate(data, picker.value);
        selected = picker.value;
        results = next;
        error.hidden = true;
        renderTables();
        renderChart();
        try { localStorage.setItem('radar-comparison-start', selected); } catch (_) { /* Optional persistence. */ }
      } catch (failure) {
        error.textContent = failure.message;
        error.hidden = false;
      }
    }
    picker.addEventListener('change', update);
    new ResizeObserver(() => { clearTimeout(resizeTimer); resizeTimer = setTimeout(() => {
      if (results) renderChart();
    }, 120); }).observe(chart);
    update();
  }

  const api = { calculate, anniversary, annualize };
  if (typeof module !== 'undefined' && module.exports) module.exports = api;
  if (typeof document !== 'undefined') mount();
})();
