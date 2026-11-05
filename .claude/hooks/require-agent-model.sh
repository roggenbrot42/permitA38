#!/bin/sh
# PreToolUse hook for the Agent tool: refuse agent starts without an explicit model,
# and refuse implementer tasks that are not bounded (see AGENTS.md, "Subagents").
input=$(cat)
deny() {
  jq -n --arg r "$1" '{hookSpecificOutput:{hookEventName:"PreToolUse",permissionDecision:"deny",permissionDecisionReason:$r}}'
  exit 0
}
model=$(printf '%s' "$input" | jq -r '.tool_input.model // empty')
[ -n "$model" ] || deny "Set model explicitly when starting an agent: sonnet for implementer and review passes, haiku for implementer-small, opus for specs. See AGENTS.md, Subagents."
agent=$(printf '%s' "$input" | jq -r '.tool_input.subagent_type // empty')
case "$agent" in
  implementer) want=sonnet; max=6 ;;
  implementer-small) want=haiku; max=3 ;;
  reviewer) [ "$model" = sonnet ] || deny "reviewer runs on sonnet; reviews never run on Haiku. See AGENTS.md, Merge gate."; exit 0 ;;
  *) exit 0 ;;
esac
prompt=$(printf '%s' "$input" | jq -r '.tool_input.prompt // empty')
[ "$model" = "$want" ] || deny "$agent runs on $want. See AGENTS.md, Subagents."
for label in Contract Files Stop Report; do
  printf '%s\n' "$prompt" | grep -q "^$label:" || deny "$agent prompt needs a $label: line. See AGENTS.md, Subagents."
done
n=$(printf '%s\n' "$prompt" | sed -n 's/^Files: *//p' | head -1 | tr ',' '\n' | grep -c .)
[ "$n" -le "$max" ] || deny "Task too big: at most $max files. Split it into work packages."
exit 0
