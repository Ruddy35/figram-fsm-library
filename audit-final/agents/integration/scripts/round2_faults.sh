#!/usr/bin/env bash
# Round 2: verify-faults.sh — prebuilt on the real tree (none only), prebuilt on a simulated release
# copy (bin/ = local stripped build of integration HEAD), full on 4 key faults with the hash waiver.
source /home/user/figram-audit/env.sh
export CGO_LDFLAGS=-lstdc++
W=/home/user/figram-audit-worktrees/integration; L=/home/user/figram-audit/agents/integration/LOGS
cd $W
echo "== prebuilt none on the real tree (bin/ = baseline release)"; FAULT_LOGS=$L/faults-prebuilt-realtree nice -n 10 ./verify-faults.sh prebuilt none; echo "exit=$?"
echo "== prebuilt ALL on simulated release copy"; GATE_TREE=/tmp/claude-0/-home-user-figram-fsm-library/57129652-0dc1-5f90-93b6-ee8cd24dbb93/scratchpad/release-sim FAULT_LOGS=$L/faults-prebuilt-releasesim nice -n 10 ./verify-faults.sh prebuilt; echo "exit=$?"
echo "== full, 4 key faults + none, waiver"; FIGRAM_SHA256SUMS=/home/user/figram-audit/agents/integration/REPRODUCTIONS/sums/SHA256SUMS.integration-local-gcc EXPECT_BASE=3 FAULT_LOGS=$L/faults-full-int nice -n 10 ./verify-faults.sh full none sums-tampered custom-axiom engine-bug-golden-edited go-missing; echo "exit=$?"
