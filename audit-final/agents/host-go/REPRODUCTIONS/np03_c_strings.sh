#!/usr/bin/env bash
# NP-03: C-string ABI: raw NUL would truncate (binding refuses), invalid UTF-8 is
# silently replaced by U+FFFD (also in the echoed id), an empty request gets an error
# reply with id null (the figram-engine process ignores empty lines).
W=/home/user/figram-audit-worktrees/host-go/audit/host-go
source /home/user/figram-audit/env.sh; export CGO_LDFLAGS=-lstdc++
cd "$W" && go test -count=1 -run 'TestNULIsRejectedNotTruncated|TestInvalidUTF8IsSilentlyReplaced' -v ./figram
"$W/bin/fgprobe" /home/user/figram-audit/hosts/shared/bots/pizza.bot.json | grep -E 'bad-utf8|empty|nul'
printf '\n{"id":1,"op":"hello"}\n' | /home/user/figram-audit/fresh/figram-engine /home/user/figram-audit/hosts/shared/bots/pizza.bot.json
