# Release tags, manual publishing pause and recovery

`main` owns source and trusted release tools. `deployment-release` owns approved
deployment snapshots. Normal releases and rollbacks both go through an immutable
`release-candidate/*` PR and the required `deployment/snapshot` check. Nothing in
these workflows automatically merges a deployment PR or reverts main.

## Version records

After a deployment PR merges, **Tag approved release** checks that the merged tree
matches its validated candidate, the candidate has the recorded release base, and
all source commits belong to main's history. It atomically publishes two annotated
Git tags:

| Tag | Target |
| --- | --- |
| `deploy/v0.1.3-rc.17` | That PR's actual merge/squash commit in deployment-release |
| `v0.1.3-rc.17` | The snapshot's `sourceSha` in main history |

Each annotation records the source SHA, deployment SHA, candidate SHA, PR number,
candidate workflow run and operation. Component image provenance remains in
`release.json`; unchanged components can originate from older main commits.
Tag names retain the existing `-rc.N` version scheme. A merge records approved
desired state, **not successful cluster deployment**. Verify Argo sync and an actual
business request separately; CI has no cluster access.

The finalizer uses the PR's exact merge commit even if another deployment has since
merged. A retry accepts matching existing tags and refuses conflicting tags. It
never force-pushes or moves a tag. Snapshot branches must remain available for audit.

Install the tag protection ruleset once using a maintainer identity after reviewing
the checked-in configuration. This permits creation but blocks updates and deletion
for both tag namespaces, with no bypass actors:

```bash
gh api --method POST repos/GodfreyLyu/LocalQwenDemo/rulesets --input deploy/release/tag-ruleset.json
```

If a ruleset named **Immutable release tags** already exists, update that ruleset
instead of creating a duplicate. This repository file does not apply itself.

The workflow must first be merged into main. To backfill a historical **format-3**
release or recover failed tagging, run it with the merged deployment PR number.
Replace `123` with the actual PR number; its retained candidate must still have a
successful `deployment/snapshot` status:

```bash
gh workflow run release-finalize.yml --ref main -f pr_number=123
```

## Incident: B is broken, A was healthy

### 1. Manually pause ordinary deployment merges

Record the pause, failed version B, intended recovery version A, and incident owner
in the incident record. Stop merging ordinary PRs into `deployment-release`. Disable
auto-merge on any outstanding ordinary deployment PRs and close stale candidates;
retain their branches. For a specific PR, replace `123` with its number:

```bash
gh pr merge 123 --disable-auto
```

```bash
gh pr close 123
```

Keep main CI and Argo CD auto-sync enabled. Main changes can still create new
candidate PRs; leave those unmerged until recovery is approved. Successful checks
alone do not lift the manual pause. This phase has **no automatic global lock or
`RELEASE_PAUSED` variable**: collaborators must honor the pause. Rollback PRs are
excluded from automatic superseding by normal candidates, so a background build
does not close the incident's pending recovery PR.

If a release is already merged/syncing, the pause does not undo it. Use the actual
current release as the base of the recovery candidate. Do not disable all Actions,
disable Argo sync, reset either branch, or move an existing tag.

### 2. Generate a rollback candidate C from A's deployment tag

Confirm A was healthy and its code is compatible with current stored data. This
operation does not restore a database, Secret, PVC content or external platform
configuration. Both applications reconcile independently, not as one transaction.

Use **Prepare release** on main with `rollback_tag` and `rollback_reason`. Replace
the example tag and reason with the incident values:

```bash
gh workflow run release-candidate.yml --ref main -f rollback_tag=deploy/v0.1.3-rc.17 -f rollback_reason='Frontend regression after the latest release'
```

The same workflow numbers both normal and rollback candidates, avoiding collisions
between independent workflow counters. Do not also set `rebuild=true`.
The workflow checks main CI, confirms the target tag pair and source history, and
copies A's approved snapshot into a checkout based on the **current** release HEAD.
It preserves all target files except the application Chart version and release
metadata, reuses the exact image digests, verifies their availability in GHCR, and
opens a new versioned rollback PR. It does not rebuild images or execute scripts
from the historical snapshot.

C records `operation: rollback` and a `rollback` object with:

- `of`: the current deployment commit being replaced (B in this example).
- `targetTag` and `targetSha`: the approved snapshot being restored (A).
- `reason`: the operator's incident explanation.

C keeps A's `sourceSha`, input fingerprints and component provenance. Its own
`candidate.baseSha`, run identity and version are new. Do not copy an old
`release.json` verbatim or open a raw Git revert PR against the snapshot branch.
Admission checks compare the complete rollback tree and provenance against A,
allowing only the documented version/metadata differences.

### 3. Review, merge and verify C

Check the PR diff, target tag, reason, component digests and data compatibility.
Require `deployment/snapshot` to succeed, then manually merge only this recovery PR.
If the release branch changes before merging, start a **new workflow run** to obtain
a fresh candidate based on its new HEAD. An ordinary retry cannot rewrite an
immutable candidate or its original release base.
If main advances while rollback preparation is running, publication also declines
to open the PR; start a new run against current main.

Argo CD syncs C automatically. Check both applications' desired revisions, sync and
health, and submit a real review through the UI to verify inference and saved
history. If tagging fails, retry **Tag approved release** using C's merged PR number;
tagging failure does not stop Argo CD from syncing an already merged snapshot.

C gets its own tag pair. Its source tag may point to the same main commit as A's
source tag. B's tags and history remain intact. Old pending candidates become stale
after C merges and must be closed or regenerated, not updated in place.

### 4. Fix main and resume publishing

If the cause is application code or source-controlled Chart/configuration, open a
targeted main fix or revert PR. Link B, C, the offending source PR and regression
test evidence. Preserve unrelated good main changes; do not merge/cherry-pick the
deployment rollback commit into main. If the cause is external infrastructure,
record the environment repair instead of reverting correct source code.

After main CI passes, review the new candidate D against the current C baseline.
Confirm it contains the fix, perform relevant regression checks, and explicitly
record that the manual pause is lifted before merging D. Argo then deploys D;
repeat the business acceptance check. Do not resume by enabling auto-merge on old
pending candidates.

If a main revert makes all deployable inputs match C, **Prepare release** may produce
no new candidate. Record the main PR's relationship to C; no empty deployment or
new version is needed solely to record the source repair.

## Tracking a version

Fetch version references, then inspect the annotation, source and deployment
manifest. Replace the example version with the one under investigation:

```bash
git fetch origin --tags
```

```bash
git show --no-patch v0.1.3-rc.17
```

```bash
git show deploy/v0.1.3-rc.17:release.json
```

Implementation references: [candidate workflow](../../.github/workflows/release-candidate.yml),
[finalizer](../../.github/workflows/release-finalize.yml), and
[release lifecycle](../../scripts/release/lifecycle.py). GitHub documents
[merged PR events](https://docs.github.com/en/actions/reference/workflows-and-actions/events-that-trigger-workflows#running-your-pull_request_target-workflow-when-a-pull-request-merges)
and [tag rulesets](https://docs.github.com/en/repositories/configuring-branches-and-merges-in-your-repository/managing-rulesets/creating-rulesets-for-a-repository).
