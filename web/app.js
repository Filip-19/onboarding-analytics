(() => {
  const DAY_MIN = 1440;
  const day = s => new Date(s + 'T00:00:00Z');
  const fmtDate = (s, opts = { day: 'numeric', month: 'short' }) => day(s).toLocaleDateString('en-GB', { ...opts, timeZone: 'UTC' });
  const int = n => Math.round(n).toLocaleString('en-GB');
  const pct = (x, dp = 1) => (x * 100).toFixed(dp) + '%';
  const dur = m => m < 60 ? Math.round(m) + ' min' : m < DAY_MIN ? (m / 60).toFixed(1) + ' h' : (m / DAY_MIN).toFixed(1) + ' days';
  const esc = s => String(s).replace(/&/g, '&amp;').replace(/"/g, '&quot;').replace(/</g, '&lt;');
  const $ = s => document.querySelector(s);

  // ---------- state + API ----------
  const state = { days: 30, channel: '', platform: '' };
  let data = null, requestId = 0;

  async function api(path, params = {}) {
    const q = new URLSearchParams();
    for (const [k, v] of Object.entries(params)) if (v !== '' && v != null) q.set(k, v);
    const res = await fetch(`/api/${path}?${q}`);
    if (!res.ok) throw new Error((await res.json().catch(() => ({}))).detail || `${path} returned ${res.status}`);
    return res.json();
  }

  function showError(err) {
    const el = $('#error');
    el.textContent = `Couldn't load the dashboard data. ${err.message}`;
    el.hidden = false;
  }

  async function load() {
    const id = ++requestId, f = { days: state.days, channel: state.channel, platform: state.platform };
    // keep the previous render on screen, dimmed, while the new numbers arrive
    $('#tiles').classList.add('loading'); $('#grid').classList.add('loading');
    try {
      const [overview, funnel, channels, trend, cohorts, time] = await Promise.all([
        api('overview', f), api('funnel', f), api('channels', { days: f.days, platform: f.platform }),
        api('trend', f), api('cohorts', { channel: f.channel, platform: f.platform }), api('time-to-activate', f),
      ]);
      if (id !== requestId) return;
      data = { overview, funnel, channels, trend, cohorts, time };
      $('#error').hidden = true;
      draw();
    } catch (err) {
      if (id === requestId) showError(err);
    } finally {
      if (id === requestId) { $('#tiles').classList.remove('loading'); $('#grid').classList.remove('loading'); }
    }
  }

  // ---------- tooltip ----------
  const tip = $('#tip');
  function showTip(html, x, y) {
    tip.innerHTML = html; tip.hidden = false;
    const w = tip.offsetWidth, h = tip.offsetHeight;
    tip.style.left = Math.max(8, Math.min(x + 14, innerWidth - w - 8)) + 'px';
    tip.style.top = Math.max(8, y - h - 12 < 8 ? y + 18 : y - h - 12) + 'px';
  }
  const hideTip = () => { tip.hidden = true; };
  // any element with data-tip gets the shared tooltip on hover and on keyboard focus
  document.addEventListener('pointermove', e => { const el = e.target.closest('[data-tip]'); if (el) showTip(el.dataset.tip, e.clientX, e.clientY); else if (!e.target.closest('#trend-hit')) hideTip(); });
  document.addEventListener('focusin', e => { const el = e.target.closest('[data-tip]'); if (el) { const b = el.getBoundingClientRect(); showTip(el.dataset.tip, b.left + b.width / 2, b.top); } });
  document.addEventListener('focusout', hideTip);

  const table = (head, rows) => `<table class="plain"><thead><tr>${head.map(h => `<th>${h}</th>`).join('')}</tr></thead><tbody>${rows.map(r => `<tr>${r.map(c => `<td>${c}</td>`).join('')}</tr>`).join('')}</tbody></table>`;
  function fill(id, chart, tbl) { const c = $('#card-' + id); c.querySelector('.chart').innerHTML = chart; c.querySelector('.tbl').innerHTML = tbl; }
  const niceMax = v => { const p = Math.pow(10, Math.floor(Math.log10(v || 1))), m = v / p; return (m <= 1 ? 1 : m <= 2 ? 2 : m <= 4 ? 4 : m <= 5 ? 5 : m <= 8 ? 8 : 10) * p; };

  // ---------- renderers ----------
  function renderTiles({ current: cur, previous: prev }) {
    const label = `vs previous ${state.days} days`;
    const delta = (d, txt, goodUp) => d == null ? `<span>No earlier period to compare</span>` :
      `<b class="${Math.abs(d) < 1e-9 ? '' : (d > 0) === goodUp ? 'good' : 'bad'}">${d > 0 ? '▲' : d < 0 ? '▼' : '•'} ${txt}</b> ${label}`;
    const diff = (a, b) => a == null || b == null ? null : a - b;
    const dAct = diff(cur.activation_rate, prev.activation_rate), dRet = diff(cur.week1_retention, prev.week1_retention);
    const dN = prev.signups ? cur.signups / prev.signups - 1 : null, dMed = diff(cur.median_minutes, prev.median_minutes);
    const pts = d => d == null ? '' : (Math.abs(d) * 100).toFixed(1) + ' pts';
    const tiles = [
      ['Sign-ups', int(cur.signups), delta(dN, dN == null ? '' : pct(Math.abs(dN)), true)],
      ['Activation rate', cur.activation_rate == null ? '–' : pct(cur.activation_rate), delta(dAct, pts(dAct), true)],
      ['Median time to activate', cur.median_minutes == null ? '–' : dur(cur.median_minutes), delta(dMed, dMed == null ? '' : dur(Math.abs(dMed)), false)],
      ['Week 1 retention', cur.week1_retention == null ? '–' : pct(cur.week1_retention), delta(dRet, pts(dRet), true)],
    ];
    $('#tiles').innerHTML = tiles.map(t => `<div class="tile"><span class="label">${t[0]}</span><span class="value">${t[1]}</span><span class="delta">${t[2]}</span></div>`).join('');
  }

  function renderFunnel(steps) {
    const s = steps.map(x => x.users), total = s[0], n = total || 1;
    const conv = s.map((v, i) => i ? (s[i - 1] ? v / s[i - 1] : 0) : 1);
    let worst = 1; for (let i = 2; i < s.length; i++) if (conv[i] < conv[worst]) worst = i;
    $('#funnel-note').textContent = total ? `${pct(1 - conv[worst], 0)} of users who reach "${steps[worst - 1].label}" never get to "${steps[worst].label}".` : 'No sign-ups match these filters.';
    let html = '<div class="funnel">';
    steps.forEach((st, i) => {
      const v = st.users;
      if (i) html += `<div class="f-step">↓ ${pct(conv[i], 0)} continue${i === worst && total ? ' <span class="chip"><i></i>Biggest drop-off</span>' : ''}</div>`;
      const t = `<b>${st.label}</b><br>${int(v)} users · ${pct(v / n)} of sign-ups${i ? `<br>${int(s[i - 1] - v)} dropped before this step` : ''}`;
      html += `<div class="f-row" tabindex="0" data-tip="${esc(t)}"><span class="f-name">${st.label}</span><div class="f-track"><div class="f-fill" style="width:${(v / n * 100).toFixed(2)}%"></div></div><span class="f-num">${int(v)}<small>${pct(v / n, 0)}</small></span></div>`;
    });
    fill('funnel', html + '</div>', table(['Step', 'Users', '% of sign-ups', '% from previous step'], steps.map((st, i) => [st.label, int(st.users), pct(st.users / n), i ? pct(conv[i]) : '–'])));
  }

  function renderChannels(rows) {
    const html = '<div class="bars">' + rows.map(r => {
      const rate = r.activation_rate || 0;
      const t = `<b>${r.label}</b><br>${pct(rate)} activated<br>${int(r.activated)} of ${int(r.signups)} sign-ups`;
      return `<div class="bar-row${state.channel && state.channel !== r.channel ? ' dim' : ''}" tabindex="0" data-tip="${esc(t)}"><span>${r.label}</span><div class="bar-track"><div class="f-fill" style="width:${(rate * 100).toFixed(2)}%"></div></div><span class="f-num">${pct(rate, 0)}</span></div>`;
    }).join('') + '</div>';
    fill('channel', html, table(['Channel', 'Sign-ups', 'Activated', 'Activation rate'], rows.map(r => [r.label, int(r.signups), int(r.activated), r.activation_rate == null ? '–' : pct(r.activation_rate)])));
  }

  let trend = null;
  function renderTrend(rows) {
    const R = rows.length, a = rows.map(r => r.signups), b = rows.map(r => r.activated);
    const box = $('#card-trend .chart'), W = Math.max(280, box.clientWidth || 800), H = 250;
    const m = { l: 36, r: 46, t: 12, b: 26 }, pw = W - m.l - m.r, ph = H - m.t - m.b;
    const max = niceMax(Math.max(...a, 1)), x = i => m.l + (R === 1 ? 0 : i / (R - 1) * pw), y = v => m.t + ph - v / max * ph;
    const path = arr => arr.map((v, i) => (i ? 'L' : 'M') + x(i).toFixed(1) + ' ' + y(v).toFixed(1)).join('');
    let g = '';
    for (let k = 0; k <= 4; k++) { const v = max * k / 4; g += `<line class="${k ? 'gridline' : 'axisline'}" x1="${m.l}" x2="${m.l + pw}" y1="${y(v)}" y2="${y(v)}"/><text x="${m.l - 8}" y="${y(v) + 4}" text-anchor="end">${int(v)}</text>`; }
    const every = Math.ceil(R / Math.max(2, Math.floor(pw / 70)));
    for (let i = R - 1; i >= 0; i -= every) g += `<text x="${x(i)}" y="${H - 6}" text-anchor="${i === R - 1 ? 'end' : 'middle'}">${fmtDate(rows[i].date)}</text>`;
    let ya = y(a[R - 1]), yb = y(b[R - 1]); if (yb - ya < 14) yb = ya + 14;
    const end = (arr, col, ly) => `<circle cx="${x(R - 1)}" cy="${y(arr[R - 1])}" r="4" fill="var(${col})" stroke="var(--surface)" stroke-width="2"/><text class="val" x="${x(R - 1) + 9}" y="${ly + 4}">${int(arr[R - 1])}</text>`;
    box.innerHTML = `<svg width="${W}" height="${H}" viewBox="0 0 ${W} ${H}" role="img" aria-label="Daily sign-ups and activated users">${g}
      <path d="${path(a)}" fill="none" stroke="var(--s1)" stroke-width="2" stroke-linejoin="round" stroke-linecap="round"/>
      <path d="${path(b)}" fill="none" stroke="var(--s2)" stroke-width="2" stroke-linejoin="round" stroke-linecap="round"/>
      ${end(a, '--s1', ya)}${end(b, '--s2', yb)}
      <g id="cross" visibility="hidden"><line class="axisline" id="cx" y1="${m.t}" y2="${m.t + ph}"/><circle id="ca" r="4" fill="var(--s1)" stroke="var(--surface)" stroke-width="2"/><circle id="cb" r="4" fill="var(--s2)" stroke="var(--surface)" stroke-width="2"/></g>
      <rect id="trend-hit" x="${m.l}" y="${m.t}" width="${pw}" height="${ph}" fill="transparent"/></svg>`;
    trend = { rows, a, b, x, y, R };
    $('#card-trend .tbl').innerHTML = table(['Sign-up date', 'Sign-ups', 'Activated', 'Activation rate'],
      rows.slice().reverse().map(r => [fmtDate(r.date), int(r.signups), int(r.activated), r.signups ? pct(r.activated / r.signups) : '–']));
    const hit = $('#trend-hit'), cross = $('#cross');
    hit.addEventListener('pointermove', e => {
      const t = trend, bx = hit.getBoundingClientRect();
      const i = Math.max(0, Math.min(t.R - 1, Math.round((e.clientX - bx.left) / bx.width * (t.R - 1))));
      cross.setAttribute('visibility', 'visible');
      $('#cx').setAttribute('x1', t.x(i)); $('#cx').setAttribute('x2', t.x(i));
      $('#ca').setAttribute('cx', t.x(i)); $('#ca').setAttribute('cy', t.y(t.a[i]));
      $('#cb').setAttribute('cx', t.x(i)); $('#cb').setAttribute('cy', t.y(t.b[i]));
      showTip(`<b>${fmtDate(t.rows[i].date, { weekday: 'short', day: 'numeric', month: 'short' })}</b><br><span class="k" style="background:var(--s1)"></span>${int(t.a[i])} sign-ups<br><span class="k" style="background:var(--s2)"></span>${int(t.b[i])} activated${t.a[i] ? ' · ' + pct(t.b[i] / t.a[i], 0) : ''}`, e.clientX, e.clientY);
    });
    hit.addEventListener('pointerleave', () => { cross.setAttribute('visibility', 'hidden'); hideTip(); });
  }

  function renderCohorts({ weeks, cohorts }) {
    const max = Math.max(.01, ...cohorts.flatMap(c => c.retention.filter(v => v != null)));
    const step = v => Math.min(7, 1 + Math.floor(v / max * 7));
    let h = '<table class="heat"><thead><tr><th>Week of</th><th>Users</th>';
    for (let w = 1; w <= weeks; w++) h += `<th>Week ${w}</th>`;
    h += '</tr></thead><tbody>';
    for (const c of cohorts) {
      const label = fmtDate(c.start);
      h += `<tr><th>${label}</th><td class="base">${int(c.users)}</td>`;
      c.retention.forEach((v, i) => {
        if (v == null) { h += '<td class="empty"></td>'; return; }
        const k = step(v), t = `<b>Signed up week of ${label}</b><br>Week ${i + 1}: ${pct(v)} active<br>${int(v * c.users)} of ${int(c.users)} users`;
        h += `<td tabindex="0" style="background:var(--seq-${k});color:var(--seq-ink-${k})" data-tip="${esc(t)}">${pct(v, 0)}</td>`;
      });
      h += '</tr>';
    }
    $('#cohort').innerHTML = h + '</tbody></table>';
    let sc = '<span>0%</span>'; for (let k = 1; k <= 7; k++) sc += `<i style="background:var(--seq-${k})"></i>`;
    $('#scale').innerHTML = sc + `<span>${pct(max, 0)} of cohort active</span>`;
  }

  function renderHist({ total, buckets }) {
    const n = total || 1, fast = buckets[0].users + buckets[1].users;
    $('#hist-note').textContent = total ? `${pct(fast / n, 0)} of activated users get there within 2 hours of signing up.` : 'No activated users match these filters.';
    const box = $('#card-hist .chart'), W = Math.max(260, box.clientWidth || 420), H = 230;
    const m = { l: 36, r: 8, t: 20, b: 26 }, pw = W - m.l - m.r, ph = H - m.t - m.b;
    const max = niceMax(Math.max(...buckets.map(b => b.users / n), .01) * 100) / 100, band = pw / buckets.length, bw = Math.min(24, band - 8);
    const y = v => m.t + ph - v / max * ph;
    let g = '';
    for (let k = 0; k <= 4; k++) { const v = max * k / 4; g += `<line class="${k ? 'gridline' : 'axisline'}" x1="${m.l}" x2="${m.l + pw}" y1="${y(v)}" y2="${y(v)}"/><text x="${m.l - 8}" y="${y(v) + 4}" text-anchor="end">${Math.round(v * 100)}%</text>`; }
    buckets.forEach((b, i) => {
      const v = b.users / n, cx = m.l + band * (i + .5), top = y(v), hgt = m.t + ph - top, r = Math.min(4, hgt);
      const t = `<b>${b.label}</b><br>${int(b.users)} users · ${pct(v)} of activated`;
      g += `<path fill="var(--s1)" d="M${cx - bw / 2} ${m.t + ph}V${top + r}q0 ${-r} ${r} ${-r}h${bw - 2 * r}q${r} 0 ${r} ${r}V${m.t + ph}z"/>
        <text class="val" x="${cx}" y="${top - 6}" text-anchor="middle">${pct(v, 0)}</text>
        <text x="${cx}" y="${H - 6}" text-anchor="middle">${b.label}</text>
        <rect tabindex="0" x="${cx - band / 2}" y="${m.t}" width="${band}" height="${ph}" fill="transparent" data-tip="${esc(t)}"/>`;
    });
    fill('hist', `<svg width="${W}" height="${H}" viewBox="0 0 ${W} ${H}" role="img" aria-label="Distribution of time from sign-up to activation">${g}</svg>`,
      table(['Time to activate', 'Users', 'Share of activated'], buckets.map(b => [b.label, int(b.users), pct(b.users / n)])));
  }

  function draw() {
    if (!data) return;
    hideTip();
    renderTiles(data.overview);
    renderFunnel(data.funnel);
    renderChannels(data.channels);
    renderTrend(data.trend);
    renderCohorts(data.cohorts);
    renderHist(data.time);
  }

  // ---------- controls ----------
  $('#range').addEventListener('click', e => {
    const b = e.target.closest('button'); if (!b) return;
    state.days = +b.dataset.r;
    for (const x of $('#range').children) x.setAttribute('aria-pressed', x === b);
    load();
  });
  $('#channel').addEventListener('change', e => { state.channel = e.target.value; load(); });
  $('#platform').addEventListener('change', e => { state.platform = e.target.value; load(); });
  document.querySelectorAll('.tbl-btn').forEach(btn => btn.addEventListener('click', () => {
    const card = btn.closest('.card'), on = btn.getAttribute('aria-pressed') !== 'true';
    btn.setAttribute('aria-pressed', on); btn.textContent = on ? 'Chart' : 'Table';
    card.querySelector('.chart').hidden = on; card.querySelector('.tbl').hidden = !on;
    const lg = card.querySelector('.legend'); if (lg) lg.hidden = on;
    if (!on) draw();   // charts size themselves to the card, so redraw once it is visible again
  }));
  let lastW = 0, timer;
  new ResizeObserver(() => { const w = $('.wrap').clientWidth; if (w === lastW) return; lastW = w; clearTimeout(timer); timer = setTimeout(draw, 60); }).observe($('.wrap'));

  api('meta').then(meta => {
    $('#through').textContent = `Data through ${fmtDate(meta.data_through, { day: 'numeric', month: 'short', year: 'numeric' })}`;
    const options = (sel, items) => items.forEach(o => sel.add(new Option(o.label, o.key)));
    options($('#channel'), meta.channels); options($('#platform'), meta.platforms);
    return load();
  }).catch(err => { $('#through').textContent = 'No data'; showError(err); });
})();
