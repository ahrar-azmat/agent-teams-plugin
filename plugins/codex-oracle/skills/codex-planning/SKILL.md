---
name: codex-planning
description: Multi-model planning workflow. Activates when planning features, architecture, or non-trivial changes. Ensures Codex Oracle and Gemini are consulted before implementation begins.
---

# Multi-Model Planning

When planning any non-trivial feature, fix, or architectural change, you MUST gather three perspectives before finalizing:

## Step 1: Launch all advisors in parallel (SAME tool call batch)
- **Codex Oracle**: `architect_review` with the proposed approach and relevant file paths
- **Antigravity (Gemini)**: `antigravity_brainstorm` or `antigravity_analyze_code` for alternative perspectives
- **Your own agents**: Explore/Plan subagents for codebase investigation

## Step 2: Synthesize findings
Once all three return:
1. Summarize each model's key findings
2. Identify agreements and disagreements
3. If Codex or Gemini raised CONCERNS/REJECT — critically analyze why. Do you agree?
4. Present ALL perspectives to the user with your own assessment

## Step 3: User decides
**The user makes the final call.** Never proceed past planning without presenting the multi-model synthesis.

## Skip conditions
Only skip if the user explicitly says "skip review", "skip codex", or "just do it".
