# Agent Teams — the Runtime Capability Law

Read this when a change calls into a swappable backend: a browser engine, DB driver, storage/LLM/queue provider, cloud SDK, container binary or any configurable vendor SDK.

### The Runtime Capability Law — "present" is not "supported"

> **A missing method fails at lint time — you find out in seconds. A present-but-unsupported
> method fails in production, on a real portal, on a real customer's document.**

That asymmetry decides how much verification a call is worth. Absence is loud, instant, free.
Presence-without-support is silent, late, and paid for in lost customer data. **The further a
failure can travel before it surfaces, the more you must spend up front to pull it earlier.**

Type stubs, autocomplete, `hasattr`, and a clean import describe the **union of every backend a
library supports** — never the one you actually run. A call can be present, type-clean,
lint-clean, import-clean, and still be unimplemented by the engine underneath.

**The case that named this law:** Playwright's `page.pdf()` is Headless-Chromium-only. A project
switched its browser engine to Camoufox, which **is Firefox**. The method still existed and still
type-checked — and raised *"PDF generation is only supported for Headless Chromium"* on **every
page**, silently costing runs their bill evidence **for a month** before anyone noticed. No
amount of static checking would have caught it. Running it once on the real engine would have.

**Every teammate — researcher, architect, implementer, reviewer — applies this:**

1. **At every call crossing into a swappable backend** — browser engine, DB driver/dialect,
   storage/LLM/queue provider, cloud SDK against a compatible-but-not-identical endpoint,
   container-provided binary, any vendor SDK whose implementation is configurable — ask *which
   backend implements this, and is that the backend we deploy?* Not "does this method exist".
2. **Verify against the vendor's compatibility matrix**, never the type signature and never
   autocomplete.
3. **Prove it with a runtime probe on the real engine.** A green suite on the library's *default*
   backend proves nothing about the deployed one.
4. **Engine/driver/provider/version swaps are where this bug is born.** After any such swap,
   sweep every call into that surface. The swap commit is the crime scene.
5. **Never let `try`/`except` degrade it to a silent no-op.** If you catch, *distinguish* the
   capability miss from a genuine failure and **record which happened** — a reviewer reading the
   artifact later must be able to tell them apart. Silent degradation of evidence, money, or
   customer data is a defect no matter how neatly it is "handled".
6. **Same defect in other clothes:** a parameter accepted then ignored, clamped, or silently
   downgraded; a config key that parses but no longer exists in the installed version; an
   instruction with no mechanism behind it. **Accepted-but-ignored is worse than rejected** —
   the caller stops checking. Reject loudly or honour it.

The MCP server injects this hunt into every `code_review` / `architect_review` automatically,
so the advisor looks for it whether or not you remember to ask.
