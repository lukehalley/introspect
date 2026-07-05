# Changeset, report, and ledger formats

Introspect produces one **changeset** per run, saves it as a dated **report**, and records what
actually gets applied in a **ledger**. Keeping these three consistent is what lets a user look
back and see their setup compounding — and undo a change that went wrong.

## Changeset item

Present each proposed change like this. The evidence and confidence are not decoration — they
are how the user decides quickly, and how you keep yourself honest about recurrence.

```
### [C1] Add rule: prefer pnpm over npm            (confidence: high)
- Agent:  claude          # which agent's config this targets (claude | codex | cursor)
- Target: ~/.claude/CLAUDE.md  →  ## Package Managers
- Kind:   claude-md-rule
- Evidence: corrected in 3 sessions this week
    · "no use pnpm" (claude a5e6…, 2026-07-02)
    · "why did you run npm, it's a pnpm repo" (codex 5a0d…, 2026-07-04)
    · "pnpm not npm please" (claude a28c…, 2026-07-04)
- Change (append):
    ## Package Managers
    - Default to `pnpm`, never `npm`, in this user's repos — they use pnpm workspaces
      everywhere and npm corrupts the lockfile.
```

Fields:
- **Agent** — `claude`, `codex`, or `cursor`. If a preference recurs across more than one agent,
  say so in the evidence and consider a cross-agent target (an `AGENTS.md` both read, or a
  CLAUDE.md↔AGENTS.md symlink) instead of duplicating the rule.
- **Confidence** — high/medium/low. High = seen ≥3× and unambiguous. Low candidates belong in the
  backlog, not the main list.
- **Kind** — one of: `claude-md-rule`, `memory-node`, `new-skill`, `edit-skill`, `new-command`,
  `bundle-script` (Claude); `agents-md-rule` (Codex); `cursor-rule` (Cursor `.mdc`).
- **Evidence** — the recurrence, with agent + session ids and short quotes. If you can't cite
  recurrence, it's a tactic; drop it.
- **Change** — an exact diff or a clearly-marked append/before-after, ready to apply verbatim.

## Report (always written, even when nothing is applied)

Path: `~/.introspect/reports/YYYY-MM-DD.md`

```markdown
# Introspect report — 2026-07-05
Window: last 7 days · 41 sessions (claude:33, codex:8) · 9 projects

## Applied (2)
- [C1] pnpm-over-npm rule → ~/.claude/CLAUDE.md  (claude)
- [C3] "make local serves the blog preview" → myapp/AGENTS.md  (codex)

## Proposed but not applied (0)

## Backlog / low-confidence (3)
- Possible skill for the "switch worktree to branch X and review" flow — seen twice, watch for a
  third before committing to it.
- ...

## Declined on purpose
- "read a file before editing it" recurred 18× but it's generic agent behaviour, not a user
  preference — left out (see the skill's "rules are about the user" rule).

## Nothing-to-do notes
- Test-first preference already covered by existing CLAUDE.md "Testing Requirements" — left as is.
```

The "Declined" and "Nothing-to-do" sections matter: recording what you *deliberately didn't*
change is how you demonstrate convergence and restraint rather than churn.

## Ledger (append-only, one JSON object per applied change)

Path: `~/.introspect/ledger.jsonl`

```json
{"date":"2026-07-05","agent":"claude","kind":"claude-md-rule","target_file":"~/.claude/CLAUDE.md","summary":"Default to pnpm, never npm","evidence":"3 corrections in 7 days across claude+codex","sessions":["a5e6...","5a0d...","a28c..."],"undo_hint":"remove the '## Package Managers' block appended on 2026-07-05"}
```

Use the ledger to answer "what has Introspect changed over time?" (read it back, grouped by month)
and to undo a specific change (the `undo_hint` says how). When editing an existing file rather
than appending, capture enough prior state in `undo_hint` — or write a `.bak` — that the edit can
be reversed.
