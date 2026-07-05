#!/usr/bin/env bash
# introspect — daily thread digest (install to ~/.claude/hooks/introspect-daily.sh).
#
# The harness-native, LOCAL way to schedule introspect: cloud routines can't read your local
# ~/.claude/projects, ~/.codex/sessions, or Cursor DB, and CronCreate is session-only. A Stop hook
# is local, durable, and sees your real threads. Fast no-op on every Stop except the first evening
# run each day. Report-only: writes a digest + a "pending" flag; never edits any config.
#
# Wire it via `setup.py hook-snippet` (adds Stop + SessionStart entries to ~/.claude/settings.json).

STATE="$HOME/.introspect"
STAMP="$STATE/.last-run"
CFG="$STATE/config.json"
TODAY="$(date +%F)"
HOUR="$((10#$(date +%H)))"          # base-10 so 08/09 don't trip octal parsing

# config (written by setup.py) can enable/disable and set the evening hour; defaults otherwise
ENABLED="true"; SCHED_HOUR="17"
if [ -f "$CFG" ]; then
  LINE="$(python3 -c "import json,sys;c=json.load(open(sys.argv[1]));s=c.get('schedule') or {};print(('true' if (s.get('enabled',True) and s.get('mode','daily')=='daily') else 'false'), int(s.get('hour',17)))" "$CFG" 2>/dev/null)"
  if [ -n "$LINE" ]; then ENABLED="${LINE%% *}"; SCHED_HOUR="${LINE##* }"; fi
fi
[ "$ENABLED" = "true" ] || exit 0

# evening only, and only once per calendar day
if [ "$HOUR" -lt "$SCHED_HOUR" ]; then exit 0; fi
if [ -f "$STAMP" ] && [ "$(cat "$STAMP" 2>/dev/null)" = "$TODAY" ]; then exit 0; fi

PY="$HOME/.claude/skills/introspect/scripts/collect_threads.py"
if [ -f "$PY" ]; then
  mkdir -p "$STATE/reports"
  python3 "$PY" --days 1 --format md --out "$STATE/reports/digest-$TODAY.md" >/dev/null 2>&1 || true
  echo "$TODAY" > "$STAMP"
  echo "$TODAY" > "$STATE/.pending"
fi
exit 0
