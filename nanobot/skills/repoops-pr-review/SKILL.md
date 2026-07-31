---
name: repoops-pr-review
description: "Review a GitHub PR with diff context, CI checks, public-interface risk, test coverage, and certainty-separated findings."
metadata: {"nanobot":{"emoji":"🔎"}}
---

# Pull request review workflow

1. Call `repoops_get_pull_request`, `repoops_get_pull_request_diff`, and
   `repoops_get_task_state`.
2. Read the full implementation around changed symbols, not just diff fragments.
3. Inspect tests and `repoops_get_ci_status`.
4. Check behavior, error paths, concurrency, compatibility, security boundaries,
   public interfaces, migrations, and missing tests.
5. Persist claim-linked evidence and hypotheses.
6. Report four distinct sections:
   - confirmed defects;
   - potential risks that need verification;
   - non-blocking improvements;
   - guesses that could not be verified.

Every blocking finding must identify a concrete failure mode and cite a file,
line range, diff hunk, test, or CI result. Do not post a review without a local
draft and later-turn user approval.
