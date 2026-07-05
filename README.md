# introspect

An agent-agnostic skill that reads your recent AI coding threads across **Claude Code, OpenAI
Codex, and Cursor**, learns the preferences and corrections you keep repeating, and writes them
back into each agent's config once you approve. Your setup compounds instead of resetting every
session. It also catches where a missing rule or script made an agent repeat work, so you write
that down once and move on.

Claude Code ships `/insights`: it reads your last 30 days and shows you a report, Claude-only.
That's the read half. **introspect is the write half, across every agent you use.** It closes
the loop and edits the files that actually steer future sessions.

## Works with

| Agent | Reads threads from | Writes learned rules to |
|-------|--------------------|-------------------------|
| Claude Code | `~/.claude/projects/**/*.jsonl` | `CLAUDE.md`, per-project memory, skills, commands |
| OpenAI Codex | `~/.codex/sessions/**/rollout-*.jsonl` | `AGENTS.md` (global + project) |
| Cursor | `state.vscdb` + `~/.cursor/**/agent-transcripts` | `.cursor/rules/*.mdc` |

`AGENTS.md` is read by Codex and Cursor, so a genuinely universal preference can be written once
where every agent sees it instead of three times.

## Install

```bash
npx skills add lukehalley/introspect
```

Or drop the `introspect/` folder into `~/.claude/skills/`.

## Use

Just ask, in a session:

- "review my threads from today"
- "what did I keep repeating this week, across Claude and Codex?"
- "turn today's corrections into rules"
- "close the loop on my recent sessions"

introspect runs its collector over whichever agents you have installed, shows you a short,
evidence-backed **changeset**, and applies only what you approve.

Run it yourself first to see the raw material:

```bash
python3 ~/.claude/skills/introspect/scripts/collect_threads.py --days 7 --format md   # auto-detect
python3 ~/.claude/skills/introspect/scripts/collect_threads.py --agent all --days 7   # force all three
python3 ~/.claude/skills/introspect/scripts/collect_threads.py --selftest             # verify the parsers
```

## What makes it safe to run daily

- **Patterns, not tactics.** It proposes only what recurs across multiple threads: habits you
  keep hitting, not one-off cleverness. A one-time fix is noise.
- **About you, not about the agent.** It captures how you like to work. It won't add rules that
  just restate an agent's baseline behaviour (like "read before editing"), which is bloat.
- **Propose, don't impose.** You see every diff and approve it. Nothing is written silently.
- **Converge, don't churn.** If a rule already exists, it strengthens that rule instead of adding
  a near-duplicate. A quiet day produces no changes, and that's the correct outcome.
- **Capped and reversible.** At most ~5 changes per run; everything applied is logged to a ledger
  with an undo hint. Secrets are redacted and never stored.

## Scheduling

Run it daily through your harness's **native** scheduler (in Claude Code, `/schedule`), which runs
the routine inside the harness with the same tools and permissions, not an OS cron. Unattended
runs write a dated report and wait for your approval rather than applying changes on their own.

## Layout

```
introspect/
├── SKILL.md                      the workflow + safety model
├── scripts/collect_threads.py    multi-agent transcript → digest (stdlib only, has --selftest)
├── references/routing.md         which agent + surface each change belongs in, and every format
├── references/changeset-format.md the changeset / report / ledger templates
└── evals/evals.json              test prompts
```

## License

MIT
