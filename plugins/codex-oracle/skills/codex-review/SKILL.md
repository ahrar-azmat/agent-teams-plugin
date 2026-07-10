---
name: codex-review
description: Multi-model code review workflow. Activates before committing or pushing code. Ensures Codex Oracle and Gemini review all changes before they ship.
---

# Multi-Model Code Review

Before committing or pushing any code changes, you MUST get independent reviews:

## Step 1: Gather the diff
Run `git diff` to collect all staged and unstaged changes.

## Step 2: Launch both reviewers in parallel
- **Codex Oracle**: `code_review` with the diff and relevant context
- **Gemini**: `gemini_review_pr` or `gemini_analyze_code` with the same diff

## Step 3: Process findings
1. Collect all findings from both models
2. Categorize by severity: CRITICAL > HIGH > MEDIUM > LOW
3. Any CRITICAL or HIGH finding MUST be addressed or explicitly acknowledged to the user
4. If both models flag the same issue — it's almost certainly real, fix it

## Step 4: Present to user
Summarize:
- What Codex found
- What Gemini found
- Your own assessment
- Which findings you've already fixed vs which need user input

## Skip conditions
Only skip if the user explicitly says "skip review" or "just push it".
