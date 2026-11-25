module.exports = async ({ github, context, core }) => {
  const { owner, repo } = context.repo;
  const issue_number = 4; // Dedicated draft PR: automation event feed
  const scanStatus = process.env.CBB_JOB_STATUS || 'unknown';
  const skipped = process.env.CBB_SKIPPED === 'true';
  const saved = process.env.CBB_SCANNER_OUTCOME === 'success' &&
    process.env.CBB_SYNC_OUTCOME === 'success' && process.env.CBB_COMMIT_OUTCOME === 'success';
  const result = skipped ? 'SKIPPED_KENPOM_ARCHIVE' :
    saved ? 'SUCCESS' :
    scanStatus === 'failure' ? 'FAILURE' : scanStatus.toUpperCase();
  let state = null;
  let readError = '';
  try {
    const response = await github.rest.repos.getContent({
      owner, repo, path: 'site/data/state.json', ref: 'main'
    });
    if (!response.data.content) throw new Error('state.json has no content');
    state = JSON.parse(Buffer.from(response.data.content, 'base64').toString('utf8'));
  } catch (e) { readError = String(e.message || e).slice(0, 180); }
  let sourceRun = {};
  try {
    sourceRun = (await github.rest.actions.getWorkflowRun({
      owner, repo, run_id: context.runId
    })).data;
  } catch (e) { core.warning('Run timing unavailable: ' + String(e.message || e)); }
  const updated = state?.last_updated_pt || 'unavailable';
  const dataMs = Date.parse(updated);
  const startedMs = Date.parse(sourceRun.run_started_at || sourceRun.created_at || '');
  const fresh = result === 'SUCCESS' && state?.status === 'OK' &&
    Number.isFinite(dataMs) && Number.isFinite(startedMs) &&
    dataMs >= startedMs - 3 * 60000 && dataMs <= Date.now() + 60000 &&
    updated.slice(0, 10) === process.env.CBB_TARGET_DATE;
  const dataCheck = fresh ? 'FRESH_COMMITTED_STATE' : 'STALE_OR_UNVERIFIED';
  const runUrl = `https://github.com/${owner}/${repo}/actions/runs/${context.runId}`;
  const lines = [
    '## CBB_SCAN_COMPLETED',
    `**Result:** ${result}`,
    `**Data check:** ${dataCheck}`,
    `**Scanner outcome:** ${process.env.CBB_SCANNER_OUTCOME || 'unknown'}`,
    `**Save outcome:** ${process.env.CBB_COMMIT_OUTCOME || 'unknown'}`,
    `**Dashboard publishing:** ${saved ? 'PENDING_SEPARATE_JOB' : 'NOT_REQUESTED'}`,
    `**Run attempt:** ${process.env.GITHUB_RUN_ATTEMPT || 'unknown'}`,
    `**Target date:** ${process.env.CBB_TARGET_DATE || 'unknown'}`,
    `**Workflow URL:** ${runUrl}`,
    `**Workflow run number:** ${context.runNumber}`,
    `**Source trigger:** ${context.eventName}`,
    `**Committed state updated (PT):** ${updated}`,
    `**State status:** ${state?.status || 'unavailable'}`,
    `**Last committed scan date:** ${state?.runs?.at(-1)?.date || 'unavailable'}`,
    readError ? `**Data read error:** ${readError}` : '',
    '',
    '**For ChatGPT Work:** This comment is an event signal, NOT a betting recommendation.',
    'Independently fetch the latest committed state and verify ATS v4 and Totals v1.',
    'Honor KenPom location/venue QC, neutral spreads, current line freshness, tip time,',
    'and frozen qualification thresholds. Exclude stale or already-started plays.',
    'If failed/skipped, report the failure or PASS; never invent prices or bets.',
    '',
    `<!-- cbb-bridge-run-${context.runId} -->`
  ].filter(Boolean);
  try {
    await github.rest.issues.createComment({
      owner, repo, issue_number, body: lines.join('\n')
    });
    core.info(`CBB scan ${result}; data ${dataCheck}; notified PR #${issue_number}`);
  } catch (err) {
    const accepted = err.response?.headers?.['x-accepted-github-permissions'] || 'not provided';
    core.error(`CBB_BRIDGE_POST_FAILED: HTTP ${err.status || 'unknown'}; ${err.message}; accepted permissions: ${accepted}`);
    core.error('Inspect Set up job > GITHUB_TOKEN Permissions and Settings > Actions > General > Workflow permissions.');
    throw err;
  }
};
