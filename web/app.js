/* Flint Arcade, alphaBell. */
(() => {
  'use strict';

  const base = new URL(document.baseURI);
  const $ = (id) => document.getElementById(id);
  const path = (value) => new URL(value.replace(/^\//, ''), base).href;
  const state = { config: {}, configReady: false, available: false, submitting: false, game: null, job: null, poll: null, routeVersion: 0, games: [] };
  const storageKey = 'alphabell.arcade.active-job';
  let toastTimer;
  const examples = [
    { label: 'Gravity garden', prompt: 'A tiny gardener on a little planet where planting a flower changes gravity. Catch floating vegetables, avoid thorny weeds, and survive three increasingly strange seasons. Use simple touch and keyboard controls.' },
    { label: 'Pigeon delivery', prompt: 'A pigeon delivering very important sandwiches across a windy city. Dodge umbrellas, land on moving balconies, and keep the sandwiches intact. A playful arcade game with touch and keyboard controls.' },
    { label: 'Jelly floor', prompt: 'A one-screen platform game in a tiny laboratory. Every ten seconds the floor turns to jelly and everything bounces. Collect escaped stars, avoid the cleaning robot, and reach the exit. Support touch and keyboard controls.' }
  ];

  function show(id, visible = true) { $(id).hidden = !visible; }
  function toast(message) {
    clearTimeout(toastTimer);
    $('toast').textContent = message;
    show('toast');
    toastTimer = setTimeout(() => show('toast', false), 3600);
  }

  async function api(endpoint, options = {}) {
    const controller = new AbortController();
    const timer = setTimeout(() => controller.abort(), 45000);
    try {
      const response = await fetch(path(endpoint), { credentials: 'same-origin', ...options, signal: controller.signal, headers: { Accept: 'application/json', ...(options.body ? { 'Content-Type': 'application/json' } : {}), ...options.headers } });
      let data;
      try { data = await response.json(); } catch (_) { data = {}; }
      if (!response.ok) {
        const error = new Error(response.status === 429 ? 'The arcade is busy. Give it a moment, then try again.' : response.status === 503 ? 'Flint is taking a break. You can still play the collection.' : response.status === 404 ? 'This game is no longer available.' : 'We couldn’t finish that request. Please try again.');
        error.status = response.status;
        throw error;
      }
      return data;
    } catch (error) {
      if (error.name === 'AbortError') throw new Error('The connection took too long. Please try again.');
      if (error instanceof TypeError) throw new Error('We couldn’t reach the arcade. Check your connection and try again.');
      throw error;
    } finally { clearTimeout(timer); }
  }

  function setPublicationLink(id, value, kind) {
    if (!value) return;
    try {
      const url = new URL(value);
      const allowed = kind === 'source' ? url.hostname === 'github.com' && /^\/alphabell-research(?:\/|$)/i.test(url.pathname) : url.hostname === 'huggingface.co' && /^(?:\/spaces|\/datasets)?\/alphabell(?:\/|$)/i.test(url.pathname);
      if (url.protocol === 'https:' && allowed) $(id).href = url.href;
    } catch (_) { /* Keep the alphaBell default. */ }
  }

  function updateControls() {
    const busy = state.submitting || Boolean(state.job && !['complete', 'failed'].includes(state.job.status));
    $('create-button').disabled = !state.available || busy;
    $('remix-button').disabled = !state.available || busy;
    $('create-button').setAttribute('aria-busy', String(state.submitting));
    $('remix-button').setAttribute('aria-busy', String(state.submitting));
    const message = !state.configReady ? 'Checking if Flint is ready…' : state.available ? '' : state.config.generation_message || 'New games are paused for now. The collection is open to play and download.';
    for (const id of ['generation-notice', 'remix-notice']) { $(id).textContent = message; show(id, Boolean(message)); }
  }

  async function loadConfig() {
    try {
      state.config = await api('api/config');
      state.available = state.config.generation_available === true;
      $('model-label').textContent = state.config.model_label || 'Flint 8-bit';
      setPublicationLink('source-link', state.config.source_url, 'source');
      setPublicationLink('hf-link', state.config.huggingface_url || state.config.space_url, 'hf');
    } catch (_) {
      state.config = { generation_message: 'We couldn’t check if Flint is ready. Refresh the page to try again.' };
    }
    state.configReady = true;
    updateControls();
  }

  function renderExamples() {
    for (const example of examples) {
      const button = document.createElement('button');
      button.type = 'button';
      button.className = 'example-button';
      button.textContent = example.label;
      button.addEventListener('click', () => { $('game-prompt').value = example.prompt; $('game-prompt').focus(); });
      $('prompt-examples').append(button);
    }
  }

  function cardFor(game, index) {
    const card = document.createElement('a');
    card.className = 'game-card';
    card.href = path(`g/${encodeURIComponent(game.id)}`);
    card.addEventListener('click', (event) => { if (event.button === 0 && !event.ctrlKey && !event.metaKey && !event.shiftKey && !event.altKey) { event.preventDefault(); navigate(game.id); } });
    const preview = document.createElement('div');
    preview.className = `preview abstract-preview variant-${index % 6}`;
    const art = document.createElement('span');
    art.className = 'tile-art';
    art.setAttribute('aria-hidden', 'true');
    art.textContent = (game.title || 'Game').split(/\s+/).filter(Boolean).slice(0, 2).map(word => Array.from(word)[0]).join('').toUpperCase();
    preview.append(art);
    if (game.thumbnail) {
      try {
        const imageURL = new URL(game.thumbnail, base);
        if (imageURL.origin === base.origin && /^https?:$/.test(imageURL.protocol)) {
          const image = document.createElement('img');
          image.alt = `${game.title || 'Arcade game'} screenshot`;
          image.loading = 'lazy';
          image.src = imageURL.href;
          image.addEventListener('load', () => { preview.classList.remove('abstract-preview'); art.hidden = true; }, { once: true });
          image.addEventListener('error', () => image.remove(), { once: true });
          preview.prepend(image);
        }
      } catch (_) { /* An abstract title tile remains when an image is unavailable. */ }
    }
    const play = document.createElement('span');
    play.className = 'card-play';
    play.setAttribute('aria-hidden', 'true');
    play.textContent = '▶';
    preview.append(play);
    const meta = document.createElement('div');
    meta.className = 'card-meta';
    const title = document.createElement('h3');
    title.textContent = game.title || 'Untitled game';
    const label = document.createElement('span');
    label.textContent = game.parent_id ? 'Remix' : 'Play + remix';
    meta.append(title, label);
    const description = document.createElement('p');
    description.textContent = game.description || 'A small world, ready to play.';
    card.append(preview, meta, description);
    return card;
  }

  async function loadGames() {
    $('refresh-gallery').disabled = true;
    show('gallery-error', false);
    show('gallery-empty', false);
    if (!state.games.length) show('gallery-status');
    try {
      const data = await api('api/games');
      state.games = (Array.isArray(data) ? data : data.games || []).filter(game => game && game.id);
      $('gallery').replaceChildren(...state.games.map(cardFor));
      $('game-count').textContent = state.games.length ? `${state.games.length} ${state.games.length === 1 ? 'game' : 'games'}` : '';
      show('gallery-empty', !state.games.length);
    } catch (_) { if (!state.games.length) show('gallery-error'); else toast('We couldn’t refresh the collection. Try again in a moment.'); }
    finally { show('gallery-status', false); $('refresh-gallery').disabled = false; }
  }

  function routeGameId() {
    const relative = location.pathname.startsWith(base.pathname) ? location.pathname.slice(base.pathname.length) : location.pathname.replace(/^\//, '');
    const match = relative.match(/^g\/([^/]+)\/?$/);
    if (!match) return null;
    try { return decodeURIComponent(match[1]); } catch (_) { return null; }
  }

  function navigate(id, options = {}) {
    const url = id ? path(`g/${encodeURIComponent(id)}`) : path('');
    history.pushState({}, '', url + (options.hash || ''));
    route();
    if (!options.hash) window.scrollTo({ top: 0, behavior: 'instant' });
    if (options.hash) requestAnimationFrame(() => document.querySelector(options.hash)?.scrollIntoView({ behavior: 'smooth' }));
  }

  function loadFrame(source, focus = false) {
    // A fresh frame keeps game restarts out of the page's Back history.
    const previous = $('game-frame');
    const frame = previous.cloneNode(false);
    if (source) frame.src = source;
    else frame.removeAttribute('src');
    if (focus) frame.addEventListener('load', () => frame.focus(), { once: true });
    previous.replaceWith(frame);
  }

  async function route() {
    const version = ++state.routeVersion;
    const id = routeGameId();
    show('home-view', !id);
    show('studio-view', Boolean(id));
    show('share-fallback', false);
    if (document.fullscreenElement) document.exitFullscreen().catch(() => {});
    loadFrame(null);
    state.game = null;
    if (!id) { document.title = 'Flint Arcade · alphaBell'; return; }
    show('studio-loading');
    show('studio-content', false);
    show('studio-error', false);
    try {
      const data = await api(`api/games/${encodeURIComponent(id)}`);
      if (version !== state.routeVersion) return;
      const game = data.game || data;
      state.game = game;
      $('studio-title').textContent = game.title || 'Your game';
      $('studio-description').textContent = game.description || '';
      $('studio-kicker').textContent = game.parent_id ? 'A new twist on a little world' : `Made with ${game.model_label || state.config.model_label || 'Flint'}`;
      $('game-frame').title = `Play ${game.title || 'your game'}`;
      loadFrame(path(`play/${encodeURIComponent(id)}`));
      $('download-button').href = path(`download/${encodeURIComponent(id)}`);
      const parent = $('parent-link');
      parent.href = game.parent_id ? path(`g/${encodeURIComponent(game.parent_id)}`) : '#';
      show('parent-link', Boolean(game.parent_id));
      $('remix-prompt').value = '';
      $('remix-research').checked = false;
      show('remix-error', false);
      document.title = `${game.title || 'Your game'} · Flint Arcade`;
      show('studio-content');
    } catch (error) {
      if (version !== state.routeVersion) return;
      $('studio-error-message').textContent = error.message;
      show('studio-error');
    } finally { if (version === state.routeVersion) show('studio-loading', false); }
  }

  function rememberJob(job) {
    try { if (job) sessionStorage.setItem(storageKey, JSON.stringify({ id: job.id, status: job.status })); else sessionStorage.removeItem(storageKey); } catch (_) { /* Browsing works when storage is unavailable. */ }
  }

  function renderJob(job) {
    const status = job.status || 'queued';
    $('job-panel').dataset.status = status;
    const copy = {
      queued: ['In the queue', 'A little world is on its way.', 'You can explore the arcade while Flint works.'],
      generating: ['Building', 'Turning your idea into a game.', 'Flint is writing the rules, the world and the little details.'],
      testing: ['Playtesting', 'Giving your world a test run.', 'Checking that the game opens and its controls work.'],
      repairing: ['A little repair', 'Working out the rough edges.', 'Flint is fixing what the playtest found.'],
      complete: ['Ready to play', 'Your world is ready.', 'Give it a try. Then take it somewhere stranger.'],
      failed: ['Couldn’t finish', 'This idea needs another try.', 'We couldn’t finish this game. Try a smaller idea or give it another go.']
    };
    const [stage, title, message] = copy[status] || copy.generating;
    $('job-stage').textContent = stage;
    $('job-title').textContent = title;
    $('job-message').textContent = message;
    const current = status === 'queued' ? 0 : status === 'generating' ? 1 : ['testing', 'repairing'].includes(status) ? 2 : status === 'complete' ? 4 : -1;
    document.querySelectorAll('.job-steps li').forEach((li, index) => { li.classList.toggle('done', index < current); li.classList.toggle('active', index === current); if (index === current) li.setAttribute('aria-current', 'step'); else li.removeAttribute('aria-current'); });
    show('job-open', status === 'complete' && Boolean(job.game_id));
    show('job-retry', status === 'failed');
    show('job-close', ['complete', 'failed'].includes(status));
    show('job-panel');
    updateControls();
  }

  async function pollJob(id, failures = 0) {
    clearTimeout(state.poll);
    try {
      const response = await api(`api/jobs/${encodeURIComponent(id)}`);
      const job = response.job || response;
      if (!state.job || state.job.id !== id) return;
      state.job = { ...state.job, ...job, id };
      renderJob(state.job);
      rememberJob(state.job);
      if (job.status === 'complete') {
        rememberJob(null);
        loadGames();
        if (job.game_id && state.job.autoOpen) { navigate(job.game_id); toast('Your game is ready. Have a play.'); show('job-panel', false); }
        return;
      }
      if (job.status === 'failed') { rememberJob(null); return; }
      state.poll = setTimeout(() => pollJob(id), 2300);
    } catch (error) {
      if (!state.job || state.job.id !== id) return;
      if (error.status === 404) {
        state.job.status = 'failed';
        renderJob(state.job);
        $('job-message').textContent = 'This build is no longer available. You can start another game.';
        rememberJob(null);
      } else {
        $('job-message').textContent = 'Reconnecting to your game. Your build can keep running while we reconnect.';
        state.poll = setTimeout(() => pollJob(id, failures + 1), Math.min(15000, 3000 + failures * 2000));
      }
    }
  }

  async function submitGame(event, remix = false) {
    event.preventDefault();
    if (!state.available || state.submitting || (state.job && !['failed', 'complete'].includes(state.job.status))) return;
    const prefix = remix ? 'remix' : 'create';
    const input = $(remix ? 'remix-prompt' : 'game-prompt');
    const prompt = input.value.trim();
    if (prompt.length < (remix ? 8 : 12)) { input.setCustomValidity('Add a little more detail about your idea.'); input.reportValidity(); input.addEventListener('input', () => input.setCustomValidity(''), { once: true }); return; }
    if (remix && !state.game) return;
    show(`${prefix}-error`, false);
    state.submitting = true;
    updateControls();
    try {
      const payload = { prompt, dataset_opt_in: $(`${prefix}-research`).checked };
      if (remix) payload.parent_id = state.game.id || routeGameId();
      const data = await api('api/jobs', { method: 'POST', body: JSON.stringify(payload) });
      const id = data.job_id || data.id;
      if (!id) throw new Error('We couldn’t start the game. Please try again.');
      state.job = { id, status: data.status || 'queued', autoOpen: true, originalPrompt: prompt, parentId: payload.parent_id };
      $(`${prefix}-research`).checked = false;
      rememberJob(state.job);
      renderJob(state.job);
      pollJob(id);
    } catch (error) { $(`${prefix}-error`).textContent = error.message; show(`${prefix}-error`); }
    finally { state.submitting = false; updateControls(); }
  }

  document.querySelectorAll('[data-home]').forEach(link => link.addEventListener('click', event => { if (event.ctrlKey || event.metaKey || event.shiftKey || event.altKey) return; event.preventDefault(); navigate(null); }));
  document.querySelectorAll('[data-create]').forEach(link => link.addEventListener('click', event => { event.preventDefault(); if (routeGameId()) navigate(null, { hash: '#create' }); else $('create').scrollIntoView({ behavior: 'smooth' }); setTimeout(() => $('game-prompt').focus({ preventScroll: true }), 350); }));
  document.querySelectorAll('[data-gallery]').forEach(link => link.addEventListener('click', event => { event.preventDefault(); if (routeGameId()) navigate(null, { hash: '#games' }); else $('games').scrollIntoView({ behavior: 'smooth' }); }));
  $('parent-link').addEventListener('click', event => { if (event.ctrlKey || event.metaKey || event.shiftKey || event.altKey || !state.game?.parent_id) return; event.preventDefault(); navigate(state.game.parent_id); });
  $('create-form').addEventListener('submit', event => submitGame(event));
  $('remix-form').addEventListener('submit', event => submitGame(event, true));
  $('refresh-gallery').addEventListener('click', loadGames);
  $('retry-gallery').addEventListener('click', loadGames);
  $('restart-button').addEventListener('click', () => { if (!state.game) return; loadFrame(path(`play/${encodeURIComponent(state.game.id || routeGameId())}`) + `?restart=${Date.now()}`, true); });
  $('fullscreen-button').addEventListener('click', async () => {
    try { if (document.fullscreenElement) await document.exitFullscreen(); else if ($('play-shell').requestFullscreen) { await $('play-shell').requestFullscreen(); $('game-frame').focus(); } else toast('Use your browser’s fullscreen control to expand the game.'); }
    catch (_) { toast('Fullscreen isn’t available here. The game is still ready to play.'); }
  });
  document.addEventListener('fullscreenchange', () => { $('fullscreen-button').lastElementChild.textContent = document.fullscreenElement ? 'Exit fullscreen' : 'Fullscreen'; });
  $('share-button').addEventListener('click', async () => {
    const url = path(`g/${encodeURIComponent(state.game?.id || routeGameId())}`);
    try { if (!navigator.clipboard?.writeText) throw new Error('Clipboard unavailable'); await navigator.clipboard.writeText(url); toast('Game link copied. Send it somewhere good.'); }
    catch (_) { $('share-url').value = url; show('share-fallback'); $('share-url').focus(); $('share-url').select(); }
  });
  $('select-share').addEventListener('click', () => { $('share-url').focus(); $('share-url').select(); });
  $('job-open').addEventListener('click', () => { if (state.job?.game_id) { navigate(state.job.game_id); show('job-panel', false); } });
  $('job-close').addEventListener('click', () => show('job-panel', false));
  $('job-retry').addEventListener('click', () => {
    const old = state.job;
    state.job = null;
    updateControls();
    show('job-panel', false);
    if (old?.parentId && state.game?.id === old.parentId) { $('remix-prompt').value = old.originalPrompt || ''; $('remix-prompt').focus(); }
    else { navigate(null, { hash: '#create' }); $('game-prompt').value = old?.originalPrompt || $('game-prompt').value; setTimeout(() => $('game-prompt').focus({ preventScroll: true }), 350); }
  });
  window.addEventListener('popstate', route);
  window.addEventListener('online', () => { if (state.job && !['complete', 'failed'].includes(state.job.status)) pollJob(state.job.id); });
  renderExamples();
  updateControls();
  loadConfig();
  loadGames();
  route();
  try {
    const previous = JSON.parse(sessionStorage.getItem(storageKey));
    if (previous?.id && !['complete', 'failed'].includes(previous.status)) { state.job = { ...previous, autoOpen: false }; renderJob(state.job); pollJob(previous.id); }
  } catch (_) { /* Start fresh when a saved job is unavailable. */ }
})();
