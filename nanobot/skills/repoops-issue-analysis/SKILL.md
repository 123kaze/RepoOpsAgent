---
name: repoops-issue-analysis
description: "Analyze one GitHub Issue with completeness checks, similar-Issue search, code localization, hypotheses, and cited evidence."
metadata: {"nanobot":{"emoji":"🐛"}}
---

# Issue analysis workflow

1. Call `repoops_get_issue`; load `repoops_get_task_state`.
2. Check for reproduction steps, expected/actual behavior, version, environment,
   complete logs, and minimal example. Record missing information.
3. Classify as bug, feature, documentation, question, configuration, performance,
   security, or insufficient-information.
4. Search similar Issues using distinctive errors and symptoms.
5. Search the checked-out workspace first when available; otherwise use
   `repoops_search_code`, then read exact files and relevant tests.
6. Create hypotheses only when evidence does not establish a fact. Each
   hypothesis needs confidence, supporting evidence IDs, and a falsification
   test.
7. Persist facts, evidence, hypotheses, files, related Issues, and next actions.
8. Report conclusions with citations and explicitly list what remains unknown.

If important information is missing, recommend a question draft but do not post
it. Use `repoops_create_draft` only when the user asks for a GitHub write.
