# Routing: which agent, which surface does a change belong in?

Getting this right is most of the quality of Introspect's output. A preference written to the
wrong place either never fires (buried where the agent won't read it) or creates noise (project
trivia dumped into a global file). Decide per candidate, in two steps: **which agent**, then
**which surface within that agent**.

## Step 1 — which agent(s)?

- If the preference recurs in **one** agent's threads, target that agent's config.
- If it recurs across **several** (e.g. the same "use pnpm" correction in both Claude and Codex
  threads), it's a strong universal preference. Prefer a **cross-agent target**: a single
  `AGENTS.md` (Codex and Cursor both read it) or a `CLAUDE.md`↔`AGENTS.md` symlink the user
  already keeps, rather than writing the same rule into three files. **Always resolve symlinks
  before writing** so you know which physical file you're editing.

## Step 2 — which surface within the agent?

| If the change is…                                                     | Claude | Codex | Cursor |
|-----------------------------------------------------------------------|--------|-------|--------|
| A standing instruction for how the agent should behave, everywhere    | `~/.claude/CLAUDE.md` | `~/.codex/AGENTS.md` | User Rules (prefer a project `.mdc`) |
| A standing instruction scoped to one repo                             | `./CLAUDE.md` or `./.claude/CLAUDE.md` | `<repo>/AGENTS.md` | `.cursor/rules/*.mdc` |
| A durable *fact/preference* to recall later (not an instruction)      | memory node in the project's `memory/` | `AGENTS.md` (Codex has no memory files) | `.cursor/rules/*.mdc` |
| A repeatable multi-step *workflow* worth invoking by name             | skill (`~/.claude/skills/<name>/`) or command | skill (`~/.agents/skills/<name>/`) | a rule + saved prompt |
| A deterministic helper several sessions kept re-implementing          | a `scripts/` file bundled inside the relevant skill | same, under the Codex skill | (n/a — write a rule) |

Rules of thumb:

- **Instruction vs. fact.** "Always run `pnpm test` before committing" is an *instruction* → a
  rules file. "The billing rails went live on 2026-06-19" is a *fact* → Claude memory (Codex and
  Cursor have no separate memory file, so a fact you want them to keep goes in AGENTS.md / a rule).
- **Global vs. project.** Applies in every repo? → the agent's global file. About one codebase? →
  that repo's file. When unsure, prefer project scope; it keeps the always-loaded global lean.
- **Rule vs. skill.** A rule is a sentence the agent keeps in mind. A skill is a procedure it
  runs. "Here's the 6-step way I always deploy" is a skill/command, not a rules paragraph.

## On-disk formats (match what already exists — don't invent new conventions)

### Claude — `CLAUDE.md`
Short, imperative, grouped under `##` headings, explains *why*. Append under the most relevant
section; only add a new `##` for a genuinely new topic. Example register to match:
```markdown
## Testing Requirements
- EVERY code change MUST have a corresponding test — untested code is money lost.
- Run the suite after every change and confirm it passes before deploying.
```

### Claude — memory
Per-project, at `~/.claude/projects/<project-dir>/memory/`, where `<project-dir>` is the cwd with
`/` → `-`. An index file `MEMORY.md` plus one node file per fact.

`MEMORY.md` is a flat bullet list; each line links a node and summarises it in one clause:
```markdown
- [No flat card grids](feedback_no_flat_card_grids.md) - no hairline card grids on marketing.
- [Billing rails LIVE](project_billing_rails_live_2026-06-19.md) - Stripe meters live in prod.
```
Node naming: `<type>_<slug>[_<YYYY-MM-DD>].md`. Two frontmatter conventions are on disk — match
the category. Preferences → `feedback_*` (flat frontmatter, `type: feedback`); state → `project_*`
(nested `metadata:`); durable pointers → `reference_*`. Bodies are markdown, cross-linked with
`[[wikilink]]`. Always add the matching `MEMORY.md` index line; create `memory/` + `MEMORY.md` if
absent.
```markdown
---
name: No flat card-grid layouts on public marketing pages
description: User rejects 2/3-col hairline-grid-of-word-cards layouts on marketing pages.
type: feedback
originSessionId: <session-id-from-the-digest>
---
User consistently corrects this. **Why:** it reads as templated slop.
**How to apply:** default to a PillarSplit layout. Related: [[some_other_node]]
```

### Claude — skills & commands
`~/.claude/skills/<name>/SKILL.md` (YAML frontmatter; only `name` + `description` required; make
the description specific and a little pushy so it triggers). Commands: `~/.claude/commands/<name>.md`
(a prompt file; optional `description:` frontmatter). Preserve an existing skill's `name`/dir when
editing.

### Codex — `AGENTS.md` (Markdown)
Codex's analogue of CLAUDE.md. Discovery/precedence: global `~/.codex/AGENTS.md` first, then each
`AGENTS.md` from the git root down to cwd, concatenated (closer-to-cwd overrides). **Gotchas:**
- **32 KiB cap** (`project_doc_max_bytes`) — combined AGENTS.md is silently truncated past it.
  Keep writes compact; append high-value rules near the top of the relevant file.
- **Desktop app** may not load the *global* `~/.codex/AGENTS.md` reliably — prefer the
  **project-root `AGENTS.md`** for things that must apply.
- Write inside an idempotent managed block so re-runs don't clobber hand-written guidance:
```markdown
<!-- introspect:begin -->
## Learned preferences
- Default to `pnpm`, never `npm` — pnpm workspaces everywhere.
<!-- introspect:end -->
```
Codex settings live in `~/.codex/config.toml` (TOML) — do NOT put natural-language preferences
there; it's for `model`, `model_reasoning_effort`, `project_doc_max_bytes`, etc. Codex skills live
at `~/.agents/skills/<name>/SKILL.md` (or `<repo>/.agents/skills/`).

### Cursor — `.cursor/rules/*.mdc`
Project rules at the repo root, version-controlled, **must** use the `.mdc` extension. YAML
frontmatter + Markdown body. Four types by which frontmatter fields are set:
- **Always** (always in context): `alwaysApply: true`.
- **Auto-attached** (when matching files are touched): `globs: <pattern>` + `alwaysApply: false`.
- **Agent-requested** (model pulls it in by relevance): `description: <text>` + `alwaysApply: false`.
- **Manual** (@-mention only): none of the above.

For learned preferences, use **Always** or **Agent-requested**:
```md
---
description: Personal coding preferences learned from past sessions
alwaysApply: true
---

- Prefer named exports, not default exports
- Run `pnpm test` before proposing a commit
```
Legacy `.cursorrules` (plain text, repo root) is still read but deprecated — only append if it
already exists. `AGENTS.md` is also honored by Cursor (good cross-agent target). **Do NOT** try to
write Cursor's "Memories" — they're cloud-only; use rule files. The global User Rules live in an
undocumented `state.vscdb` key (`ItemTable` → `aicontext.personalContext`) and require Cursor to be
closed; avoid writing it — prefer a project `.mdc`.

## Read sources (what the collector parses — for when you need to dig past the digest)

- **Claude:** `~/.claude/projects/<project-dir>/<sessionId>.jsonl`. Human prompt = `type=="user"`,
  `promptSource in {"typed","command"}`, no `toolUseResult`, not `isMeta`. Tool result (may be an
  error) also `type=="user"` but carries `toolUseResult`. Assistant blocks: `thinking`/`text`/
  `tool_use`. `ai-title` holds the session title. `timestamp` is ISO-8601 UTC.
- **Codex:** `~/.codex/sessions/YYYY/MM/DD/rollout-*.jsonl`. Records are `{timestamp,type,payload}`.
  Human prompt = `event_msg` with `payload.type=="user_message"` (`payload.message`). Tool call =
  `response_item`/`function_call` (`payload.name`); its output = `function_call_output` (a
  `Process exited with code N` in `payload.output` is the failure signal). `session_meta.payload.cwd`
  attributes the thread; `turn_context.payload.model` is the model. Ignore the first
  `response_item`/`role:user` — it's injected AGENTS.md, not a human prompt.
- **Cursor:** global `state.vscdb` → table `cursorDiskKV`: `composerData:<id>` (a thread; has
  `createdAt`/`lastUpdatedAt` in epoch ms and either `fullConversationHeadersOnly` or an inline
  `conversation`) and `bubbleId:<id>:<bubble>` (a message: `type 1`=user, `type 2`=assistant,
  `toolFormerData` present = tool call). Open read-only (`?mode=ro&immutable=1`); never write while
  Cursor runs. Also `~/.cursor/projects/*/agent-transcripts/*.jsonl` for the CLI/newer path.

Everything past parsing is your judgement — the collector only extracts and normalises.
