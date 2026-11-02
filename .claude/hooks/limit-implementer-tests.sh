#!/bin/sh
# PreToolUse hook for Bash: implementer agents run only affected test files, never a full suite.
input=$(cat)
case "$(printf '%s' "$input" | jq -r '.agent_type // empty')" in
  implementer|implementer-small) ;;
  *) exit 0 ;;
esac
cmd=$(printf '%s' "$input" | jq -r '.tool_input.command // empty')
if printf '%s' "$cmd" | grep -Eq 'make[^;&|]*[[:space:]]test(-all|-random|-web|-summary)?([[:space:]]|$)'; then
  jq -n '{hookSpecificOutput:{hookEventName:"PreToolUse",permissionDecision:"deny",permissionDecisionReason:"implementer runs only affected test files, e.g. pytest tests/backend/test_x.py -q."}}'
fi
exit 0
