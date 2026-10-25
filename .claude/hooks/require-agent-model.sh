#!/bin/sh
# PreToolUse hook for the Agent tool: refuse agent starts without an explicit model.
# haiku for implementation, sonnet for review/contracts (see AGENTS.md, "Subagents").
input=$(cat)
model=$(printf '%s' "$input" | jq -r '.tool_input.model // empty')
if [ -z "$model" ]; then
  printf '%s\n' '{"hookSpecificOutput":{"hookEventName":"PreToolUse","permissionDecision":"deny","permissionDecisionReason":"Set `model` explicitly when starting an agent: haiku for implementation (code, tests, docs, fixes), sonnet for review passes and contracts. See AGENTS.md, Subagents."}}'
fi
exit 0
