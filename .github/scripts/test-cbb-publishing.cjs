const assert = require('node:assert/strict');
const { test } = require('node:test');
const wait = require('./wait-pages-artifact.cjs');
const signal = require('./cbb-scan-signal.cjs');
const context = { repo: { owner: 'test', repo: 'dashboard' }, runId: 123, runNumber: 4, eventName: 'schedule' };
const core = { info() {}, warning() {}, error() {} };

test('waits through indexing lag, wrong ID, and transient API failure', async () => {
  process.env.PAGES_ARTIFACT_ID = '42';
  process.env.PAGES_ARTIFACT_NAME = 'github-pages-2';
  let calls = 0, sleeps = 0;
  const github = { rest: { actions: { listWorkflowRunArtifacts: async () => {
    calls++;
    if (calls === 2) throw Object.assign(new Error('unavailable'), { status: 503 });
    return { data: { artifacts: calls === 1 ? [] : [{ id: calls === 3 ? 41 : 42, name: 'github-pages-2', expired: false }] } };
  } } } };
  await wait({ github, context, core, sleep: async () => { sleeps++; } });
  assert.equal(calls, 4);
  assert.equal(sleeps, 3);
});

test('fails after a bounded wait and does not hide authorization errors', async () => {
  let calls = 0;
  const github = { rest: { actions: { listWorkflowRunArtifacts: async () => {
    calls++; return { data: { artifacts: [] } };
  } } } };
  await assert.rejects(wait({ github, context, core, sleep: async () => {} }), /12 checks/);
  assert.equal(calls, 12);
  github.rest.actions.listWorkflowRunArtifacts = async () => { throw Object.assign(new Error('forbidden'), { status: 403 }); };
  await assert.rejects(wait({ github, context, core }), /forbidden/);
});

test('reports saved scans separately and never calls stale or failed data fresh', async () => {
  const now = new Date().toISOString();
  Object.assign(process.env, {
    CBB_JOB_STATUS: 'success', CBB_SCANNER_OUTCOME: 'success', CBB_SYNC_OUTCOME: 'success',
    CBB_COMMIT_OUTCOME: 'success', CBB_SKIPPED: 'false', CBB_TARGET_DATE: now.slice(0, 10)
  });
  let body;
  let state = { status: 'OK', last_updated_pt: now, runs: [{ date: now.slice(0, 10) }] };
  const github = { rest: {
    repos: { getContent: async () => ({ data: { content: Buffer.from(JSON.stringify(state)).toString('base64') } }) },
    actions: { getWorkflowRun: async () => ({ data: { run_started_at: now } }) },
    issues: { createComment: async args => { body = args.body; } }
  } };
  await signal({ github, context, core });
  assert.match(body, /Result:\*\* SUCCESS/);
  assert.match(body, /FRESH_COMMITTED_STATE/);
  assert.match(body, /PENDING_SEPARATE_JOB/);
  state.last_updated_pt = '2000-01-01T00:00:00Z';
  await signal({ github, context, core });
  assert.match(body, /STALE_OR_UNVERIFIED/);
  Object.assign(process.env, { CBB_JOB_STATUS: 'failure', CBB_COMMIT_OUTCOME: 'failure' });
  state.last_updated_pt = now;
  await signal({ github, context, core });
  assert.match(body, /Result:\*\* FAILURE/);
  assert.match(body, /STALE_OR_UNVERIFIED/);
  process.env.CBB_SKIPPED = 'true';
  await signal({ github, context, core });
  assert.match(body, /SKIPPED_KENPOM_ARCHIVE/);
  assert.match(body, /STALE_OR_UNVERIFIED/);
});
