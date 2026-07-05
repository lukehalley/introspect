---
name: introspect
description: >-
  Review the user's recent AI coding threads across Claude Code, OpenAI Codex, and Cursor to
  learn how THEY prefer to work — the preferences and corrections they keep repeating — and to
  spot places where a missing rule or script made the agent repeat work, then propose and (on
  approval) write those updates back into each agent's config: CLAUDE.md / memory / skills for
  Claude, AGENTS.md for Codex, and .cursor/rules for Cursor. So the setup compounds and the user
  stops giving the same instruction twice. Use this whenever the user wants to review/introspect
  their sessions, learn from recent threads, run a "retro" on their day/week, "close the loop"
  across Claude and Codex and Cursor, figure out where the agent repeated the same mistake, turn
  corrections into rules, reduce repeated prompting, or self-improve their config from actual
  usage — even if they don't say the word "introspect". Also the target for a daily or
  every-N-sessions self-improvement routine.
metadata:
  version: 0.1.0
  trigger: Reviewing past AI coding threads (Claude/Codex/Cursor), learning from usage, updating CLAUDE.md/AGENTS.md/.cursor rules/memory/skills, running a retro, closing the loop, reducing repeated corrections.
  author: lukehalley
  homepage: https://github.com/lukehalley/introspect
---

# Introspect

Turn what happened in your AI coding threads into changes to how your agents work for you — so
your setup **compounds** instead of resetting every session.

It's **agent-agnostic**. It reads your threads from **Claude Code, OpenAI Codex, and Cursor**,
finds the handful of things worth changing, and — with your approval — writes them back into
whichever agent's config actually steers future sessions. Claude Code already ships `/insights`,
which reads your last 30 days and shows you a report. That's the *read* half, Claude-only.
Introspect is the *write* half, across every agent you use. Youssef put it well in the thread
this came from: *fix the system once so you don't have to fix the output a thousand times.*

## The one idea that makes this work: patterns, not tactics

The failure mode of "what could I have done better?" is that it surfaces **tactics** — a better
flag for one command, a cleverer approach to one bug. Those don't compound. They're gone
tomorrow.

What compounds is a **habit you keep hitting**: the same correction you give across three
different threads, the same preference you restate every week, the same wrong turn the agent
takes every time it touches your test setup. So the whole skill is biased toward recurrence:

> **A candidate change earns its place only when it shows up more than once, isn't already
> written down somewhere, and would plausibly have changed the outcome.** Everything else is
> noise — log it, don't act on it.

This is also the honest answer to the two fears people raised in that thread:

- *"I worry about compounding bad decisions / my workflow going stupid."* → Nothing is written
  without you seeing the exact diff and approving it. Every applied change is logged and
  reversible. When in doubt, propose fewer things.
- *"Does it just update the skill every day forever — does it ever converge?"* → It should
  converge. If a rule already exists, you **strengthen or merge** it; you don't add a second one.
  A quiet day produces an empty changeset, and that's a success, not a failure.

## The pipeline

```
Collect  →  Extract  →  Diff vs. existing setup  →  Propose changeset  →  Approve  →  Apply + Log
(script)    (you)        (you, read current config)   (you, with evidence)  (user)     (you, reversibly)
```

### 1. Collect — run the script, don't re-parse anything by hand

`scripts/collect_threads.py` reads each agent's thread store (Claude's JSONL, Codex's rollout
files, Cursor's SQLite), filters to a window, redacts secrets, and emits ONE common digest:
per session it tags which agent it was, the human's typed prompts, the tools used, where tools
errored, and lightweight "friction" signals (prompts that read like corrections, tools called
many times in a row).

```bash
python3 scripts/collect_threads.py --days 1               # today, every installed agent (auto)
python3 scripts/collect_threads.py --agent all --days 7   # last week, force all three
python3 scripts/collect_threads.py --agent codex --format md
python3 scripts/collect_threads.py --since 2026-07-01 --until 2026-07-05 --format json
python3 scripts/collect_threads.py --project ~/dev/myproject   # one project
python3 scripts/collect_threads.py --selftest             # sanity-check all three parsers
```

`--agent` defaults to `auto` (detects which agents are installed). Use `--format md` when you'll
read it yourself; `--format json` to post-process. The script never decides anything — it gives
you clean, normalised raw material so you spend your judgement on what matters, not on parsing
three different transcript formats.

### 2. Extract — read the digest and pull out three kinds of thing

Read the digest as if you were the user's chief of staff reviewing the day across all their
tools. The **user's preferences come first** — this skill is mostly about learning how *they*
like to work, not about policing an agent's baseline behaviour. Separate:

1. **Durable preferences (the main event)** — the user correcting a default or restating how they
   like things ("no, use pnpm", "stop adding comments", "always cut a branch off development
   first", "don't touch the worktree"). A preference that shows up in BOTH your Claude and Codex
   threads is an especially strong signal — it's about you, not the tool.
2. **Setup gaps that made the agent repeat work** — where the agent re-derived or got stuck
   across *multiple* sessions because something wasn't written down: re-learning a build command,
   re-finding a file that always lives in the same place, missing a piece of project context. The
   fix is to write the missing thing into the user's config — NOT to add a rule scolding the
   agent. If a repetition is just generic agent behaviour with no user-specific fix (e.g. "read a
   file before editing it"), that's not a rule, it's noise; leave it out.
3. **Reusable workflows** — a multi-step thing the user drove more than once that deserves to be
   a skill or a command so it's one step next time.

For each candidate, write down the **evidence**: which agent, which sessions, how many times, a
short quote. No evidence of recurrence → it's a tactic, not a pattern → drop it.

### 3. Diff against the existing setup — the anti-bloat / convergence step

Before proposing anything, read what's already there for the relevant agent(s), so you
strengthen the system instead of bloating it. `references/routing.md` has the exact read/write
locations and formats for each agent; the short version:

| Agent | Existing config to read (and later write) |
|-------|--------------------------------------------|
| Claude | `~/.claude/CLAUDE.md` (global), `./CLAUDE.md`, per-project memory `~/.claude/projects/<dir>/memory/`, `~/.claude/skills/`, `~/.claude/commands/` |
| Codex | `~/.codex/AGENTS.md` (global), `<repo>/AGENTS.md` (project), skills in `~/.agents/skills/` |
| Cursor | `.cursor/rules/*.mdc` (project), legacy `.cursorrules`, `AGENTS.md` |

For each candidate ask: *Is this already covered?* If yes → drop it, or propose a small edit that
**sharpens the existing rule**. Never add a second rule that says almost the same thing. If two
candidates overlap, merge them. Prefer editing over adding.

**Cross-agent tip:** `AGENTS.md` is read by Codex and Cursor, and many people symlink it to
`CLAUDE.md`. If a preference is genuinely universal, prefer writing it where every agent sees it
(one `AGENTS.md`, or a CLAUDE.md↔AGENTS.md symlink) rather than duplicating it three times.
Always resolve symlinks before writing so you know which file you're really editing.

### 4. Propose a changeset — concrete diffs with evidence, capped

Present a **changeset**: an ordered, skimmable list where each item has the target agent + file,
the exact edit (a diff or a clearly-marked before/after), the evidence, and a confidence. Use the
format in `references/changeset-format.md`. Two rules keep it sane:

- **Cap it.** Surface at most ~5 changes per run, highest-confidence first. Anything else goes to
  a "backlog" section — noted, not applied. A flood of edits is how workflows go stupid.
- **Rank by recurrence × impact.** A preference stated three times that will save a correction
  every session beats a clever one-off every time.

Always **save the changeset to a dated report** even if nothing is applied, so there's a record
to look back on (this answers the thread's "do you save the outputs anywhere, or just read and
move on?"). Write it to `~/.introspect/reports/YYYY-MM-DD.md`.

### 5. Approve — the gate

Show the changeset and let the user choose what to apply: all, some, or none. Default to
**propose-and-approve** — do not edit any agent's config without an explicit yes. Use
`AskUserQuestion` (multi-select) to let them pick items when there are several. If the user has
explicitly asked for an unattended/scheduled run, see "Unattended runs" below.

### 6. Apply + log — reversibly

For each approved item, write to the right surface (full formats in `references/routing.md`):

- **Claude — CLAUDE.md:** append under the right `##` section or edit the rule in place; keep the
  house style (short, imperative, explains *why*).
- **Claude — memory:** create/append a node file and add one index line to `MEMORY.md`; match the
  `feedback_*` / `project_*` / `reference_*` conventions and `[[wikilink]]` cross-links.
- **Claude — skill/command:** create/edit `~/.claude/skills/<name>/SKILL.md` or
  `~/.claude/commands/<name>.md`. If several sessions re-wrote the same helper script, bundle it.
- **Codex — AGENTS.md:** append inside a managed block (`<!-- introspect:begin --> … <!-- end -->`)
  so re-runs are idempotent; keep it compact (Codex truncates AGENTS.md at ~32 KiB). Prefer the
  project-root `AGENTS.md` (the Desktop app doesn't reliably load the global one).
- **Cursor — .cursor/rules/*.mdc:** write a rule file with YAML frontmatter (`alwaysApply: true`
  for always-on prefs, or `description:` + `alwaysApply: false`). Do NOT try to write Cursor's
  cloud "Memories"; use rule files. Don't write the live `state.vscdb` while Cursor is running.

Then **append a line to the ledger** at `~/.introspect/ledger.jsonl` — one JSON object per applied
change: `{date, agent, target_file, kind, summary, evidence, sessions, undo_hint}`. The ledger
lets you (a) show how the setup has compounded over time, and (b) undo a change later. Before
editing an existing file, note its prior state in `undo_hint` (or keep a `.bak`).

## Safety model (read this — it's the whole reason this is safe to run daily)

- **Propose, don't impose.** Approval gate on by default. The user sees every diff.
- **Recurrence threshold.** Don't propose from a single occurrence. Habits, not tactics.
- **Converge, don't churn.** Strengthen/merge existing rules instead of adding near-duplicates. A
  run that changes nothing is fine and expected on a quiet day.
- **Cap the blast radius.** ~5 changes max per run; the rest to backlog.
- **Rules are about the user, not about the agent.** Capture how the user wants to work. Don't
  manufacture rules that just restate an agent's baseline competence (like "read before editing")
  — that's bloat. A "the agent got stuck / repeated itself" signal earns a change only when
  writing down a piece of the *user's* setup would prevent the repetition.
- **Never store secrets.** The collector redacts common key/token shapes, but stay alert: never
  copy an API key, password, `.env` value, or token into a rule or report. If you see one, leave
  it out and (if it looks live) flag it to the user.
- **Reversible.** Everything applied is in the ledger with an undo hint.
- **Right surface, right scope.** User-wide preferences → the global file for that agent.
  Project-specific facts → that project's config. Don't put project trivia in a global file.

## Triggers — three ways to run

1. **On demand.** "review my threads", "run introspect", "what did I keep repeating this week
   across Claude and Codex", "turn today's corrections into rules". Default window: today.
2. **Daily / scheduled (harness-native).** Register the run through the harness's **native**
   scheduler — the `schedule` skill or the `CronCreate` routine tool — **not** an OS cron,
   launchd, or a shell loop. Native scheduling runs the routine inside the harness with the same
   tools and permissions, and the user manages it with `/schedule`. A good default is once a day
   in the evening over that day's window.
3. **Every-N-sessions / on friction (optional).** Some people (see buildooor in the source
   thread) prefer to trigger this when something has gone wrong or every ~10 sessions rather than
   on a clock. Wire it as a `Stop` hook that counts invocations and nudges after N. Offer it;
   never install a hook without approval.

### Unattended runs

If invoked by a schedule/routine with no human present, do **not** silently apply changes to any
agent's config. Instead: run the pipeline, write the dated report with the proposed changeset to
`~/.introspect/reports/`, and surface a short summary (via the routine's notification) so the user
can approve on their next interactive session. The only category safe to auto-apply unattended is
a *new Claude memory node that duplicates nothing* — and even then, log it. Keep the compounding
under human control; that's the point.

## References

- `references/routing.md` — per-agent read sources + write surfaces + exact on-disk formats
  (CLAUDE.md, memory, AGENTS.md, .cursor/rules/*.mdc) and the transcript schemas the collector uses.
- `references/changeset-format.md` — the changeset + report + ledger templates.
- `scripts/collect_threads.py` — the multi-agent collector (run `--selftest` to verify it).

Repeating the core loop, because it's the whole thing: **collect the threads from every agent →
find the recurring patterns (not one-off tactics) → check they're not already written down →
propose a small, capped, evidence-backed changeset → apply only what's approved to the right
agent's config → log it reversibly.**
