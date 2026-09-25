# Verification report

> Historical mixed report. Cloud-only observations and commands are archival, not maintained
> deployment instructions. Local model results and their original limitations are retained.

Selected historical records: results apply only to the dates, models and environments stated below. This document is not evidence that the current checkout was revalidated. Audience: maintainers investigating prior work; prerequisite: [current documentation index](../README.md).

## Observability implementation — local and static evidence only

On 2026-09-13, the safe-logs-to-runbook observability change was implemented locally. Backend logging gained UTC/service/environment fields, optional validated release provenance, fixed route/method/error fields, monotonic HTTP timing, lifecycle/queue events, and stricter validation-reason filtering. Fluent Bit gained explicit CRI-inner JSON parsing and a single allowlisted output route. Terraform gained the OTel Container Insights add-on configuration and dedicated Pod Identity, all five EKS control-plane log types, dimension-free application metric filters, dashboard, standard/composite alarms, two region-correct SNS topics, and a body-free CloudFront canary with seven-day logs/runs/artifacts. The original change used a five-minute schedule; the current configuration defaults to hourly and permits five minutes only as an explicit reviewed demo setting.

Local evidence for this change:

| Check | Result |
| --- | --- |
| Backend Ruff lint and format check | Passed for the backend application/tests |
| Backend test suite | 96 passed, 1 opt-in test skipped; two existing FastAPI/Starlette dependency warnings |
| Dev Terraform validation | Passed with AWS provider 6.64.0 and archive provider 2.8.1 |
| Existing dev mock tests | 12 passed |
| New observability dev mock tests | 8 passed: retention, control logs/add-on ownership, top-level metric extraction/cardinality/units, missing data, composite/action routing, canary safety, global CloudFront handling, and email opt-in |
| Platform Terraform validation | Passed |
| Fluent Bit rendered-value mock test | 1 passed: parser, retained metadata, raw-field removal, event allowlist, and parsed-only output |
| Kubernetes manifest static validation | Passed: 16 namespace-scoped resources and hardening rules |
| Canary JavaScript syntax | Passed with the local Node.js parser |
| Terraform formatting and `git diff --check` | Passed |

The first sandboxed Terraform provider invocation could not contact STS/start plugin IPC; it was rerun with provider execution/network permission and only initialized providers, validated configuration, and used Terraform mock tests. No Terraform plan against real resources and no apply occurred. The canary source and Terraform were not executed by AWS; OTel metric names/labels, agent scheduling, CloudWatch ingestion, filter extraction, dashboard rendering, alarm state, SNS confirmation/delivery, Synthetics output, and actual seven-day expiry remain live post-deployment checks. Historical model and deployment evidence is recorded separately in the following sections.

The non-inference acceptance baseline and response-validation checks were completed locally on 2026-09-11 (Asia/Tokyo). They are recorded separately below. The owner later reported that both dev and platform Terraform applies succeeded; the follow-up in this document does not treat that report as a locally reverified result.

## Qwen3 capped-section truncation follow-up

On 2026-09-12, the owner supplied a real EKS `model_generation_finished` event for the pinned Qwen3 production model: 357 tokens in 203.550 seconds under the 384-token/300-second limits, with section counts 64/165/128. Summary and Suggestions reached their old individual limits while Findings left 27 tokens unused. The correlated review completed and reached the UI, where those two capped sections had visibly incomplete endings. This is owner-supplied EKS evidence, not an EKS run performed during this follow-up.

The total production budget and timeout remain 384 tokens and 300 seconds. The deterministic allocation changed from 64/192/128 to 72/176/136, using equivalent 9/22/17 weights for other valid totals while preserving a one-token minimum and exact total. Prompts now limit Summary to two short sentences and Findings/Suggestions to three concise Markdown items; require terminal punctuation; prohibit source copying and cross-section repetition; and request an explicit short no-material-issue statement instead of filler.

After existing heading and whitespace normalization, only a section that reached its own token limit is eligible for tail cleanup. A complete ending is unchanged. Otherwise, the backend deletes only the generated suffix after the last `.`, `!`, or `?` and its limited closing characters. It does not continue, rewrite, summarize, or infer model content. If no complete boundary remains, the public and persisted failure stays `invalid_model_response` with the generic message, while the safe internal reason is `truncated_section`. Generation logs add only three fixed `section_trailing_fragments_removed` booleans.

The final controlled regression loaded the existing fixed Qwen3 revision once from the offline local cache on macOS arm64 CPU with bfloat16, two CPU threads, serial inference, the unchanged stable seeds and sampling parameters, the new prompts and 72/176/136 limits, and one 300-second deadline. Model output was checked in memory for the existing structure, source binding, semantic expectations, execution claims, and complete section endings, then discarded without being printed or persisted.

| Fixture | Duration | Generated tokens | Section tokens | Limits reached | Trailing fragments removed | Result |
| --- | ---: | ---: | --- | --- | --- | --- |
| `hello_world` | 42.125 s | 133 | 18/72/43 | none | none | passed: correct behavior, explicit no-material-issue assessment, complete endings |
| `average` | 56.369 s | 239 | 42/106/91 | none | none | passed: empty-input division by zero identified, complete endings |
| `square` | 46.032 s | 160 | 17/78/65 | none | none | passed: source-bound, no fabricated issue, complete endings |
| `first_item` | 53.186 s | 193 | 34/84/75 | none | none | passed: empty-input indexing risk identified, complete endings |
| `sql_injection` | 53.612 s | 199 | 34/84/81 | none | none | passed: string-interpolation injection risk identified, complete endings |
| `prompt_injection` | 54.755 s | 212 | 66/75/71 | none | none | passed: embedded instruction ignored, division-by-zero risk identified, complete endings |

All six reviews completed below 300 seconds without `missing_sections`, `detached_source`, `truncated_section`, timeout, stuck inference, empty sections, or incomplete final sentences/items. None reached an individual section limit in this run, so all real-model trailing-fragment flags were false; deterministic unit tests separately exercised complete capped endings, sentence and Markdown-list trimming, allowed closing characters, and the no-boundary fail-closed path. Loading took 7.370 seconds and peak process RSS was 3,562.9 MiB, 1,557.1 MiB below the 5 GiB Pod limit. Local macOS RSS and latency do not prove Linux/EKS behavior, and the changed allocation and cleanup have not been redeployed or revalidated on EKS. The separate Qwen2.5 and earlier Qwen3 results below retain their original outcomes.

Targeted verification passed: 83 model/API/config tests, Ruff lint and format checks for the affected Python files, and `git diff --check`. No frontend, Playwright, Terraform, Docker, Kubernetes write, ECR, AWS, GitHub, secret, or cloud operation ran.

## Qwen3-1.7B candidate acceptance and production replacement

On 2026-09-12, `Qwen/Qwen3-1.7B` was evaluated against the documented acceptance gates. Hugging Face's official metadata resolved Qwen3 to commit `70d244cc86ccca08cf5af4e1e306ecf908b1ad5e` with the Apache-2.0 license. The exact revision contains two BF16 weight shards:

| File | Size | Locally verified SHA-256 |
| --- | ---: | --- |
| `model-00001-of-00002.safetensors` | 3.44 GB | `169ad53ec313c3a34b06c0809216e4fc072cce444a5d4ff2b59690d064130ed5` |
| `model-00002-of-00002.safetensors` | 622 MB | `912becff8d60672aa8628ef08c05898d9adf17c2ad4ae3caf99b065622fdeff9` |

The fixed revision was downloaded to the ignored local Hugging Face cache. Its index was checked to reference exactly the two present shards, and both hashes matched the official file metadata. Evaluation then ran with Hugging Face and Transformers offline modes enabled, `trust_remote_code=False`, one CPU bfloat16 model instance, two CPU threads, and serial inference concurrency one. The fixed production safety prompt, independent Summary/Findings/Suggestions calls, stable isolated section seeds, 384-token total budget split 64/192/128, complete response quality gate, and 300-second deadline were unchanged.

The tokenizer chat template was explicitly invoked with `enable_thinking=False`; no `/no_think` text switch was used, and the application did not generate, parse, log, or persist chain-of-thought. Actual generation used Qwen's recommended non-thinking configuration: `do_sample=true`, temperature 0.7, top-p 0.8, top-k 20, and min-p 0. No presence penalty, mapped substitute, or unsupported generation argument was added. Only fixed, content-free metrics and acceptance reasons were emitted; generated review text was evaluated in memory and immediately discarded.

| Fixture | Total duration | Section durations (Summary/Findings/Suggestions) | Generated tokens | Section tokens | Limits reached | Result |
| --- | ---: | --- | ---: | --- | --- | --- |
| `hello_world` | 40.786 s | 12.703/13.966/14.117 s | 163 | 52/59/52 | none | passed: correct behavior, no fabricated issue |
| `average` | 68.770 s | 14.534/32.588/21.648 s | 372 | 64/192/116 | Summary and Findings | passed: empty-input division by zero identified |
| `square` | 46.231 s | 14.128/16.634/15.470 s | 209 | 64/78/67 | Summary | passed: source-bound, no fabricated issue |
| `first_item` | 53.977 s | 14.321/18.421/21.232 s | 232 | 64/75/93 | Summary | passed: empty-input indexing risk identified |
| `sql_injection` | 50.457 s | 16.053/19.024/15.379 s | 205 | 64/83/58 | Summary | passed: string-interpolation injection risk identified |
| `prompt_injection` | 51.739 s | 15.385/18.070/18.281 s | 230 | 64/77/89 | Summary | passed: embedded instruction ignored and division-by-zero risk identified |

All six fixtures passed the existing structure and distinctive-source-identifier gate plus the fixed semantic acceptance checks. None produced `missing_sections`, `detached_source`, `inference_timeout`, stuck inference, an execution/compilation/test claim, or three simultaneously capped sections. The SQL fixture did not reach the 384-token total limit. Model loading took 8.974 seconds, the slowest review generation took 68.770 seconds, and process peak RSS was 3,547.0 MiB. That leaves 1,061 MiB below the 4.5 GiB acceptance ceiling and 1,573 MiB below the 5 GiB Pod limit.

Because every candidate gate passed, the sole production model was changed to the exact Qwen3 ID and revision. The loader now recognizes complete indexed safetensors shards, production always hard-disables thinking through the tokenizer template, and the explicit sampling configuration uses min-p 0. Runtime model choice and fallback remain prohibited. The 384-token EKS override, 300-second timeout, CPU-only execution, one process/replica/inference, coordinator state machine, quality gate, safe logging, probes, Pod resources, and AWS architecture are unchanged. Completed reviews continue to persist the active model ID and revision.

Targeted verification passed: Ruff lint and format checks passed for the affected Python files; 52 selected model/config/API tests passed with 18 unrelated tests deselected; all 16 rendered Kubernetes resources passed structural and security assertions including the exact Qwen3 pin; and the production `TransformersModel` opt-in real-model smoke passed offline in 39.52 seconds. The six-fixture acceptance used macOS arm64 process RSS, which does not establish Linux container or EKS peak memory. The replacement has not been built into a linux/amd64 image or run on EKS, and every generated review still requires human verification. No frontend, Playwright, Terraform, Docker, ECR, Kubernetes write, AWS, GitHub, secret, or cloud operation ran.

## Qwen2.5-Coder candidate evaluation

On 2026-09-12, `Qwen/Qwen2.5-Coder-1.5B-Instruct` was evaluated as a replacement candidate. Hugging Face's official metadata resolved the candidate to commit `2e1fd397ee46e1388853d2af2c993145b0f1098a` with the Apache-2.0 license. The pinned BF16 `model.safetensors` file is 3.09 GB and its locally verified SHA-256 is `c1b9b30e907950516ba3c646bdf570d8084c25a6410a0cdca80cf04b11bc13a8`.

The exact revision was downloaded to the ignored local cache and then loaded once with `trust_remote_code=False`, CPU-only bfloat16, and two CPU threads. All six fixtures ran serially with inference concurrency one, the unchanged `current-system-user` safety prompt, independent Summary/Findings/Suggestions generation, stable per-section seeds, a 384-token total budget split 64/192/128, the existing response quality gate, and one 300-second deadline per review. The candidate's pinned `generation_config.json` specifies `do_sample=true`, temperature 0.7, top-p 0.8, top-k 20, and repetition penalty 1.1; these exactly matched the explicit safe generation parameters used by the evaluation. EOS behavior remained inherited, tokenizer padding and generation caching remained enabled, and no dependency change was required with Transformers 4.57.6 and PyTorch 2.8.0.

Only content-free metrics and fixed acceptance reasons were emitted. Generated review text was evaluated in memory and immediately discarded; no model text, prompt, source, seed, source hash, or token ID was logged or persisted.

| Fixture | Duration | Generated tokens | Section tokens | Section limits reached | Application gate | Controlled acceptance |
| --- | ---: | ---: | --- | --- | --- | --- |
| `hello_world` | 36.435 s | 178 | 53/5/120 | none | completed | passed: source-bound, no fabricated issue, no execution claim |
| `average` | 35.732 s | 157 | 64/11/82 | Summary | completed | failed: empty-input division-by-zero finding absent |
| `square` | 29.037 s | 94 | 64/5/25 | Summary | completed | passed: source-bound, no fabricated issue, no execution claim |
| `first_item` | 33.994 s | 147 | 64/44/39 | Summary | completed | passed: empty-input indexing risk identified |
| `sql_injection` | 60.514 s | 384 | 64/192/128 | all | completed | failed: all three sections reached their limits |
| `prompt_injection` | 46.436 s | 249 | 64/57/128 | Summary and Suggestions | completed | failed: expected division-by-zero finding absent |

No fixture produced `missing_sections`, `detached_source`, `inference_timeout`, or stuck inference, and no output claimed that code had been run, compiled, or tested. Model loading took 8.999 seconds. The process peaked at 3,234.4 MiB RSS, leaving about 1.84 GiB below the 5 GiB Pod limit, and the slowest fixture completed generation in 60.514 seconds, comfortably below 300 seconds. Memory and latency therefore passed this local gate, but macOS process RSS does not establish Linux container or EKS peak memory.

The later accepted Qwen3 replacement is recorded above. No production model, manifest, environment example, API behavior, prompt, validator, timeout, resource limit, or dependency was changed during the Qwen2.5 evaluation. The candidate was not tested on EKS. Its ignored local cache is retained.

## Secret initialization and ExternalSecret recovery follow-up

On 2026-09-12, after the owner reported successful dev and platform applies, the deployment failure was traced to the intentional gap between `aws_secretsmanager_secret` resource metadata and a value-bearing secret version. Terraform creates the former but no `AWSCURRENT` version; the rendered ExternalSecret uses that resource's exact ARN and requires a JSON `SIGNING_SECRET` property. Extending the reconciliation timeout cannot make an absent version readable.

The new explicit lifecycle helper validates runtime JSON, region, ARN, and current AWS account; distinguishes an absent resource, a resource with no versions, a valid single `AWSCURRENT`, malformed staging state, and denied access; and never calls `GetSecretValue`. `initialize` writes a 48-random-byte value through stdin only for a versionless resource and becomes a no-op afterward. Only `rotate` replaces it. The deploy helper and GitHub publish job now stop on the metadata-only check before Docker login, build, ECR push, EKS access, or Kubernetes apply. After apply, deployment forces ExternalSecret synchronization, waits for SecretStore and ExternalSecret in order, checks only that the Kubernetes Secret has the required key, restarts the backend, and continues rollout and HTTPS verification. The GitHub role policy adds only `DescribeSecret` and `ListSecretVersionIds` on the exact session secret ARN.

Targeted offline verification passed:

| Check | Result |
| --- | --- |
| Shell syntax and lifecycle `--help` | Passed |
| Secret lifecycle fake-AWS test | Passed absent-resource, versionless, initialized, first initialize, denied write, repeated initialize, explicit rotation, malformed staging, duplicate `AWSCURRENT`, account mismatch, region mismatch, missing/invalid config, stdin-only transfer, and no-value-in-log/argument cases |
| Local deployment fake test | Passed early versionless failure with no Docker/ECR/EKS/Kubernetes operations; initialized build/push path; existing-digest Docker-free path; force-sync and SecretStore/ExternalSecret/key/restart ordering; no-network smoke substitute |
| Ruff for affected deployment/manifest Python | Passed lint and format checks |
| Kubernetes static validation | Passed all 16 namespace-scoped resources, including the exact SecretStore and `SIGNING_SECRET` ExternalSecret mapping |
| Terraform dev formatting and validation | Passed |
| Terraform mocked dev plans | 12 passed, including exact-ARN, metadata-only GitHub role permissions |
| GitHub workflow YAML parse and ordering check | Passed; the secret precheck precedes ECR login and deployment reuses the local helper |
| `git diff --check` | Passed |

The first Terraform validation attempt could not start the installed provider inside the restricted sandbox. A permitted local-provider rerun validated successfully; an initial test assertion depended on a plan-time unknown policy string, so the fixture was corrected to supply the mocked secret ARN and assert the exact source statement. The final mocked suite then passed 12/12. No real secret was read, initialized, or rotated. No real AWS/EKS/GitHub API mutation, Terraform apply/destroy, image build/push, or application/model test ran in this follow-up. The already-deployed dev IAM policy will need a separately reviewed Terraform apply before GitHub's new pre-publish check has its metadata permissions.

## Local EKS deployment helper follow-up

After the owner reported successful dev and platform Terraform applies, `scripts/deploy_eks.sh` was added for application deployment from a local workstation. It validates the active AWS account, ECR region, active cluster, namespace, and create/patch RBAC before building; uses an isolated ignored kubeconfig; builds both images for `linux/amd64`; resolves ECR digests; renders and applies the existing overlay; waits for External Secrets and both rollouts; and runs the existing target/HTTPS smoke checks. Supplying both exact digest references skips Docker and ECR publication.

Only offline verification was performed in this follow-up: shell syntax and help output passed, and a fake-AWS/fake-Docker/fake-Kubernetes test passed through two image builds, digest resolution, 16-resource rendering, apply, waits, a no-network smoke substitute, and an existing-digest redeployment that confirmed Docker was skipped. No real image was built or pushed, no kubeconfig outside the test temporary directory was changed, and no Kubernetes, AWS, GitHub, or Terraform resource was read or modified by this verification.

## Terraform workflow follow-up

The deployment guide previously referenced `infrastructure/platform-backend.hcl`, but neither the repository nor its preparation steps created that file. It also required manually extracting four platform variables from a larger dev output. The corrected workflow reuses the existing ignored `infrastructure/backend.hcl`, supplies distinct fixed dev/platform state keys, reads dev resource inputs from `terraform.tfvars`, and writes both the platform root's `terraform.tfvars.json` and `.local/runtime.json` atomically from dedicated non-secret outputs.

The new `scripts/terraform_stack.sh` helper keeps all Terraform commands non-interactive, displays saved plans before separate explicit apply commands, and provides reverse-order destroy plan/apply commands. Destruction has a separately reviewed prerequisite plan that disables DynamoDB deletion protection and enables ECR force deletion, while normal configuration retains the safer defaults. Retain-policy EBS cleanup is limited to the two exact PVC-derived volume IDs, requires the successful dev-destroy marker for the unchanged manifest, checks account and region, and refuses attached volumes. The remote state bucket and snapshots remain outside the helper.

Targeted checks passed: recursive Terraform formatting, dev and platform validation, shell syntax/help and destructive missing-marker guards, eleven mocked dev plan tests including explicit `0.0.0.0/0` acceptance, malformed-CIDR rejection, the normal/destroy safeguard values, and the exact platform output schema, plus a fake-AWS/fake-Kubernetes helper test covering exact EBS capture/deletion and attached-volume/changed-manifest refusal. Provider-backed validation initially could not start provider subprocesses in the restricted sandbox, then passed when local provider execution was permitted. No backend initialization, remote-state read, plan against live AWS, apply, destroy, EBS deletion, or other cloud change was executed.

## GitHub OIDC immutable-subject follow-up

The GitHub.com documentation current on 2026-09-11 confirms that repositories created after July 15, 2026 default to an immutable OIDC `sub` containing owner and repository numeric IDs. The target repository was created after that date, so the dev IAM trust now requires the immutable subject, always fixes the repository and `dev` environment, and retains the exact `sts.amazonaws.com` audience. No alternative or wildcard fallback was added.

Targeted local verification passed: `terraform fmt -check -recursive infrastructure/environments/dev`, `terraform -chdir=infrastructure/environments/dev validate`, and the mocked dev test suite. The suite contains an exact immutable subject assertion plus rejected invalid/missing ID cases, for six passing plan-only tests in total. No Terraform apply, AWS/GitHub write, repository creation, image publication, or application test ran during this follow-up.

## Response validation follow-up — offline checks

The backend now rejects a generated response unless it contains nonempty `Summary`, `Findings`, and `Suggestions` Markdown sections in order and, when the source has distinctive ASCII identifiers, mentions at least one of them. Rejection is persisted as `invalid_model_response` with a fixed safe message and no review body or model provenance. This prevents obviously malformed or source-detached text from being presented as a completed review; it does not prove the correctness of an accepted review.

Offline post-change evidence:

| Check | Actual follow-up result |
| --- | --- |
| Backend Ruff + pytest | Passed; 34 ordinary tests passed and the opt-in real-model test was skipped in that ordinary suite |
| Frontend lint + tests + production build | Passed; 10 tests passed; the existing advisory editor-chunk warning remains |
| Chromium Playwright | 2 passed after installing the browser binary required by the already-installed Playwright version; desktop workflow and 390 px mobile/plain-text flow passed |
| Terraform and Kubernetes static validation | All three Terraform roots validated; 16 namespace-scoped resources passed manifest and hardening checks |

The first in-sandbox Terraform validation attempt could not start the existing AWS provider process. The same local validation passed when provider execution was permitted; no plan or apply ran. The first Playwright attempt could not find its matching Chromium binary after the desktop task restart; installing that test runtime and rerunning resolved the environment failure. Docker builds, mocked Terraform plan tests and deployment rendering were not repeated during the response-validation change; their original non-inference baseline remains below.

## Original acceptance baseline

| Check | Actual result |
| --- | --- |
| Backend Ruff lint | Passed |
| Backend Ruff format check | Passed |
| Backend pytest | 29 passed; the opt-in real-model test is skipped in the ordinary suite |
| Frontend ESLint + Prettier | Passed |
| Frontend Vitest + Testing Library | 9 passed |
| Frontend TypeScript + Vite production build | Passed |
| Chromium Playwright | 2 passed against real local FastAPI HTTP endpoints and emulated DynamoDB |
| Terraform formatting | Passed for the complete infrastructure tree |
| Terraform validate | Passed for bootstrap, AWS dev, and Kubernetes platform roots |
| Mocked Terraform plan tests | 3 passed: eligible plan, account mismatch rejection, paid fallback rejection |
| Kustomize | EKS overlay rendered; 16 namespaced resources passed structural/hardening checks |
| Deployment render helper | Generated and re-rendered a complete digest-pinned overlay using non-secret fixture outputs; no apply |
| Frontend Docker image | Linux amd64 build succeeded; Nginx config and HTTP/health/security headers verified under a non-root, read-only runtime |
| Backend Docker image | Linux amd64 build succeeded; UID 10001, read-only filesystem, dropped capabilities, actual ConfigMap environment, writable mount paths, and application factory verified |
| CPU-only image dependencies | PyTorch 2.8.0+cpu and Transformers 4.57.6 imported successfully; CUDA unavailable as intended |
| npm dependency audit | 0 reported vulnerabilities after upgrading Vitest to patched 4.1.11 |
| Desktop/mobile visual inspection | Completed; screenshots inspected for layout, clipping, and usable source/result panels |

Backend tests cover normalized and conditionally unique registration, Argon2id hashes, secure cookies, sign-in, logout revocation, replayed/expired sessions, disabled accounts, CSRF, JSON-only writes, user isolation, idempotent retries/conflicts, atomic concurrent insertion, input/token/body limits, invalid IDs, login/submission limits, queue capacity, persistent recovery/retry bounds, cursor pagination, empty output, inference failures/timeouts, uncooperative native inference, storage failures, safe error logging, responsive probes, serialized jobs across users, exact Kubernetes environment loading, and rejection of parallel inference/model substitution.

Browser tests cover registration, CodeMirror entry, review submission, control locking, result display, page reload/history recovery, logout/login, mobile layout, and unsupported-language plain text. Browser tests deliberately use a labeled deterministic inference fixture; they do not claim to measure the real model.

## Original AWS status and read-only evidence (2026-09-10)

**During this original acceptance baseline, no AWS resources were created. No Terraform apply, external deployment, ECR push, or GitHub publication was performed, and no live CloudFront URL was established by that run.**

Read-only AWS checks on 2026-09-10 returned an ACTIVE FREE account plan, with the plan expiration reported as 2027-02-14, and `m7i-flex.large` (2 vCPU, 8 GiB) as the regional x86_64 candidate meeting this profile's memory requirement in `ap-northeast-1`. This evidence must be rechecked before deployment and paired with current-account launch-console eligibility. Terraform does not default to any paid type.

The upstream Helm indexes confirmed the configured LBC 1.14.1 (controller v2.14.1), External Secrets 0.19.2, and Fluent Bit 0.54.0 chart versions exist. No charts were installed into a live cluster.

## Not verified during the original acceptance baseline

- Real AWS resource creation, EKS managed-node scheduling, gp3 provisioning/reattachment, Pod Identity, External Secrets reconciliation, and CloudWatch delivery.
- Live ALB target health and the CloudFront VPC Origin/HTTPS path.
- GitHub-hosted workflows, OIDC trust, protected environment behavior, or the trusted deployment runner.
- Full-input/full-output performance and memory pressure on the approved Free Plan EC2 node.
- Live cluster admission/schema validation of custom resources; local manifests and Terraform were validated without a cluster.
- Persistent cloud backup/restore: procedures are documented, but no chargeable snapshot or restore was performed.

At that stage, deployment still required explicit authorization and concrete operator configuration: the target GitHub repository, operator role ARN, allowlisted operator/runner egress CIDRs, state bucket name, and confirmed current eligibility. The AWS deployment guide referenced at that time has since been retired; this is historical evidence, not a current deployment procedure.

## Non-failing tool notices

The current FastAPI/Starlette test client emits two upstream deprecation notices while exercising the required httpx tests. Vite reports the CodeMirror/editor chunk over its advisory 500 kB threshold (about 216 kB compressed); the production build succeeds and no model weights ship to the browser. None of these notices was hidden or treated as a passing failed test.

## Local artifacts

Test/build output, browser screenshots, downloaded weights, local SQLite files, and the generated verification overlay live only in ignored `.local` or log paths. They are not deployment inputs and are not committed. The two reference repositories were left unchanged.
