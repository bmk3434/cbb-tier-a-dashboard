// GitHub's artifact list can lag behind a successful upload. Bound that wait,
// and verify the exact artifact from this attempt rather than an older upload.
module.exports = async ({ github, context, core, sleep = ms => new Promise(resolve => setTimeout(resolve, ms)) }) => {
  const artifactId = Number(process.env.PAGES_ARTIFACT_ID);
  const name = process.env.PAGES_ARTIFACT_NAME;
  if (!Number.isSafeInteger(artifactId) || artifactId <= 0 || !name) {
    throw new Error('Missing uploaded Pages artifact ID or name');
  }
  for (let attempt = 1; attempt <= 12; attempt++) {
    try {
      const { data } = await github.rest.actions.listWorkflowRunArtifacts({
        ...context.repo, run_id: context.runId, name, per_page: 100
      });
      if (data.artifacts.some(a => a.id === artifactId && a.name === name && !a.expired)) {
        core.info(`Pages artifact ${artifactId} is visible (check ${attempt}).`);
        return;
      }
    } catch (error) {
      if (![429, 500, 502, 503, 504].includes(error.status)) throw error;
      core.warning(`Transient artifact lookup error: HTTP ${error.status}`);
    }
    if (attempt < 12) await sleep(5000);
  }
  throw new Error(`Pages artifact ${artifactId} (${name}) was uploaded but is still not visible after 12 checks. Retry only the publishing job.`);
};
