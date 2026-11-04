#!/usr/bin/env bash
# "Resume" / "back" re-enter a waiting message after its wait is gone: the user is on
# "please wait" with no wait in the snapshot, so no outcome will ever move them.
# 1) booking.bot.json (outcome arrows on the root, as host-contract §2 advises): leave `hold`,
#    the outcome arrives on `lobby` (root arrow: release), "book" resumes into `hold`.
# 2) examples/pizza.bot.json: /help from `placing`, the outcome arrives on `help` (no arrow:
#    ignored, the wait is removed), "back" returns to `placing`.
E=/home/user/figram-audit-worktrees/a6-hostgo/.lake/build/bin/figram-engine
F=/home/user/figram-audit-worktrees/a6-hostgo/.lake/build/bin/figram
B=/home/user/figram-audit/agents6/hostgo/bots/booking.bot.json
P=/home/user/figram-audit-worktrees/a6-hostgo/examples/pizza.bot.json
echo "figram check booking: $($F check $B 2>&1 | tr '\n' ' ')"
echo "figram check pizza:   $($F check $P 2>&1 | tr '\n' ' ')"
printf '%s\n' \
 '{"id":1,"op":"step","snapshot":{"at":"hold","resume":{"booking":"hold"},"timers":{"patience":4},"pending":[{"call":"reserve","token":4}],"seq":4},"event":"btn:leave"}' \
 '{"id":2,"op":"step","snapshot":{"at":"lobby","back":"hold","resume":{"booking":"hold"},"pending":[{"call":"reserve","token":4}],"seq":5},"event":"call:reserve:held","token":4}' \
 '{"id":3,"op":"step","snapshot":{"at":"lobby","back":"hold","resume":{"booking":"hold"},"seq":6},"event":"btn:book"}' | $E $B
printf '%s\n' \
 '{"id":4,"op":"step","snapshot":{"at":"placing","resume":{"order":"placing"},"pending":[{"call":"create_order","token":6}],"seq":6},"event":"cmd:help"}' \
 '{"id":5,"op":"step","snapshot":{"at":"help","back":"placing","resume":{"order":"placing"},"pending":[{"call":"create_order","token":6}],"seq":7},"event":"call:create_order:created","token":6}' \
 '{"id":6,"op":"step","snapshot":{"at":"help","back":"placing","resume":{"order":"placing"},"seq":7},"event":"btn:back"}' | $E $P
