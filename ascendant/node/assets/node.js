let busy = false, initialized = false, feedbackTimer;
function hideFeedback() { clearTimeout(feedbackTimer); el('feedback').hidden = true; }
function showFeedback(message, error = false) {
  hideFeedback();
  el('feedback').textContent = message;
  el('feedback').className = error ? 'error' : 'muted';
  el('feedback').hidden = false;
  feedbackTimer = setTimeout(hideFeedback, error ? 8000 : 4000);
}
const el = id => document.getElementById(id);

async function request(url, data) {
  const response = await fetch(url, data ? {method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify(data)} : {});
  const result = await response.json();
  if (!response.ok) throw Error(result.error || 'Node request failed.');
  return result;
}

function render(state) {
  if (!initialized) { el('node-name').value = state.name; initialized = true; }
  el('connection-status').textContent = state.connection + (state.connection === 'connected' && !state.coordinator_enabled ? ' · paused by main' : '');
  el('activity-main').textContent = state.url || '—';
  el('activity-task').textContent = state.active?.label || 'None';
  el('activity-completed').textContent = state.completed;
  el('activity-rate').textContent = `${state.rate} request(s)/s`;
  el('activity-progress').value = state.progress;
  el('activity-message').textContent = state.message;
  el('node-memory').textContent = state.memory?.rss_bytes == null
    ? 'Node process RAM: unavailable.'
    : `Whole node process RAM (RSS): ${(state.memory.rss_bytes / 1048576).toFixed(1)} MiB · PID ${state.memory.pid}. Browser RAM is separate.`;
  const paired = Boolean(state.node_id);
  ['node-name','node-url','node-code','node-rate','node-cookie'].forEach(id => { el(id).disabled = busy || paired; });
  el('connect').disabled = busy || paired || Boolean(state.active);
  el('check-connection').disabled = busy;
  el('pause').disabled = busy || !paired || !state.enabled;
  el('resume').disabled = busy || !paired || state.enabled;
  el('disconnect').disabled = busy || (!paired && !state.url) || Boolean(state.active);
}

async function act(url, payload, paired=false) {
  if (busy) return;
  busy = true;
  hideFeedback();
  document.querySelectorAll('button').forEach(button => {button.disabled = true;});
  try {
    const result = await request(url, payload);
    if (url === '/api/check-connection') {
      showFeedback(result.message);
    }
    if (paired) { el('node-code').value = ''; el('node-cookie').value = ''; }
  } catch (error) {
    showFeedback(error.message, true);
  } finally {
    busy = false;
    await refresh();
  }
}

el('connect').addEventListener('click', () => act('/api/connect', {
  name:el('node-name').value, url:el('node-url').value, code:el('node-code').value,
  rate:el('node-rate').value, cookie:el('node-cookie').value
}, true));
el('check-connection').addEventListener('click', () => act('/api/check-connection', {url:el('node-url').value}));
['pause','resume','disconnect'].forEach(action => el(action).addEventListener('click', () => act('/api/control', {action})));
async function refresh() {
  try { if (!busy) render(await request('/api/status')); }
  catch (error) { el('connection-status').textContent = 'Node GUI unavailable'; }
}
await refresh();
setInterval(refresh, 1000);
