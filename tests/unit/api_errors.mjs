import { afterEach, mock, test } from 'node:test';
import assert from 'node:assert/strict';
import { getJSON, postJSON } from '../../static/js/api.js';

afterEach(() => mock.restoreAll());

test('an upstream 412 message reaches the caller without retrying', async () => {
  const fetch = mock.method(globalThis, 'fetch', async () => new Response(JSON.stringify({error: 'Bilibili rejected the request (HTTP 412). Wait before trying again.'}), {status: 400}));
  await assert.rejects(getJSON('/fixture'), /Bilibili rejected.*HTTP 412/);
  assert.equal(fetch.mock.callCount(), 1);
});

test('distinct upstream statuses and API codes reach the caller unchanged', async () => {
  for (const detail of ['HTTP 401', 'HTTP 403', 'HTTP 404', 'HTTP 429', 'HTTP 503', 'HTTP 200, API code -412']) {
    const message = `Request: Creator video list. Bilibili returned an error (${detail}).`;
    mock.method(globalThis, 'fetch', async () => new Response(JSON.stringify({error: message}), {status: 400}));
    await assert.rejects(getJSON('/fixture'), error => error.message === message);
    mock.restoreAll();
  }
});

test('HTML errors report the dashboard HTTP status without raw content', async () => {
  mock.method(globalThis, 'fetch', async () => new Response('<html>private diagnostic content</html>', {status: 502}));
  await assert.rejects(getJSON('/fixture'), error => error.message.includes('HTTP 502') && !error.message.includes('private diagnostic'));
});

test('lost connections give useful guidance without retrying a POST', async () => {
  const fetch = mock.method(globalThis, 'fetch', async () => { throw new TypeError('Failed to fetch'); });
  await assert.rejects(postJSON('/fixture', {action: 'save'}), /Check that the app is running/);
  assert.equal(fetch.mock.callCount(), 1);
});

test('missing error messages report status without echoing the whole response', async () => {
  mock.method(globalThis, 'fetch', async () => new Response(JSON.stringify({debug: 'private response'}), {status: 503}));
  await assert.rejects(getJSON('/fixture'), error => error.message.includes('HTTP 503') && !error.message.includes('private response'));
});

test('successful POSTs retain the request and response contract', async () => {
  mock.method(globalThis, 'fetch', async (url, options) => {
    assert.equal(url, '/fixture');
    assert.equal(options.method, 'POST');
    assert.equal(options.headers['Content-Type'], 'application/json');
    assert.deepEqual(JSON.parse(options.body), {action: 'save'});
    return new Response(JSON.stringify({saved: true}));
  });
  assert.deepEqual(await postJSON('/fixture', {action: 'save'}), {saved: true});
});
