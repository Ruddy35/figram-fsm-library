package main

// Simulated Telegram users. A user reacts to the newest message it has received (the host's
// `send` actions, delivered at least once), sends updates that Telegram delivers at least once
// (redelivered until the host acknowledges them, sometimes twice), acts while a wait is pending
// (cancels on "please wait", presses "again" while a slot is held, earns points without waiting),
// and sometimes asks support to reset it or to delete its data.

import (
	"math/rand"
	"sync"
	"sync/atomic"
	"time"
)

type Host struct {
	cur     atomic.Pointer[Instance]
	nextUpd atomic.Int64
	dupProb float64
	ups     atomic.Int64
	dupsSent atomic.Int64
}

func (h *Host) deliver(up Update) {
	for {
		in := h.cur.Load()
		if in != nil && in.Ingest(up) == nil {
			return
		}
		time.Sleep(500 * time.Microsecond)
	}
}

// Send an update: Telegram delivers it until acknowledged; with dupProb a second delivery
// races with the first.
func (h *Host) send(user, event string, data map[string]any, rng *rand.Rand) {
	up := Update{User: user, ID: h.nextUpd.Add(1), Event: event, Data: data}
	h.ups.Add(1)
	if h.dupProb > 0 && rng.Float64() < h.dupProb {
		h.dupsSent.Add(1)
		var wg sync.WaitGroup
		wg.Add(1)
		go func() { defer wg.Done(); h.deliver(up) }()
		h.deliver(up)
		wg.Wait()
		return
	}
	h.deliver(up)
}

type UserStats struct {
	noReply      atomic.Int64 // waited for a message that did not come
	noReplyWait  atomic.Int64 // … while on a waiting message (placing, hold)
	goalsReached atomic.Int64
	actions      atomic.Int64
	supportOps   atomic.Int64
}

type SimUser struct {
	id    string
	h     *Host
	tg    *Telegram
	rng   *rand.Rand
	last  TgMsg
	scale float64
	st    *UserStats
	goals int
	maxActions int
	replyWait time.Duration
}

// waitMsg waits for a message newer than the last one; false on timeout.
func (u *SimUser) waitMsg(d time.Duration) bool {
	ch := u.tg.inbox[u.id]
	deadline := time.After(d)
	for {
		select {
		case m := <-ch:
			if m.Seq > u.last.Seq {
				u.last = m
				// drain what is already there (duplicates, later messages)
				for {
					select {
					case m2 := <-ch:
						if m2.Seq > u.last.Seq {
							u.last = m2
						}
						continue
					default:
					}
					break
				}
				return true
			}
		case <-deadline:
			return false
		}
	}
}

func (u *SimUser) sleepBot(ms int) {
	time.Sleep(time.Duration(float64(ms)*u.scale*float64(time.Millisecond)) + 5*time.Millisecond)
}

func (u *SimUser) act(event string, data map[string]any, expectReply bool) bool {
	u.st.actions.Add(1)
	u.h.send(u.id, event, data, u.rng)
	if !expectReply {
		return true
	}
	if u.waitMsg(u.replyWait) {
		return true
	}
	u.st.noReply.Add(1)
	return false
}

func (u *SimUser) run() {
	u.act("cmd:start", nil, true)
	done := 0
	for n := 0; n < u.maxActions && done < u.goals; n++ {
		p := u.rng.Float64()
		if p < 0.01 {
			u.st.supportOps.Add(1)
			u.act("cmd:support", map[string]any{"support": "reset"}, true)
			continue
		}
		if p < 0.015 {
			u.st.supportOps.Add(1)
			u.act("cmd:support", map[string]any{"support": "lose"}, false)
			// comes back later with a button of an old message
			time.Sleep(2 * time.Millisecond)
			u.act(map[string]string{"shop": "btn:order", "points": "btn:shop", "booking": "btn:book"}[botOf(u.id)], nil, true)
			continue
		}
		switch botOf(u.id) {
		case "shop":
			done += u.shop()
		case "points":
			done += u.points()
		case "booking":
			done += u.booking()
		}
	}
	u.st.goalsReached.Add(int64(done))
}

func (u *SimUser) pick(ps ...float64) int {
	p := u.rng.Float64()
	for i, q := range ps {
		if p < q {
			return i
		}
		p -= q
	}
	return len(ps)
}

func (u *SimUser) shop() int {
	switch u.last.Name {
	case "menu":
		switch u.pick(0.8, 0.1) {
		case 0:
			u.act("btn:order", nil, true)
		case 1:
			u.act("btn:resume", nil, true)
		default:
			u.act("cmd:help", nil, true)
		}
	case "help":
		u.act("btn:back", nil, true)
	case "catalog":
		switch u.pick(0.7, 0.2) {
		case 0:
			u.act("btn:add", nil, true)
		case 1:
			u.act("btn:cart", nil, true)
		default:
			u.sleepBot(3000) // the idle timer fires (arrow without a target on the section)
			u.act("btn:add", nil, true)
		}
	case "cart":
		switch u.pick(0.6, 0.15, 0.15) {
		case 0:
			u.act("btn:checkout", nil, true) // reads cart_not_empty right after add_to_cart
		case 1:
			u.act("btn:more", nil, true)
		case 2:
			u.sleepBot(2000) // the reminder fires (timer arrow without a target)
			u.act("btn:checkout", nil, true)
		default:
			u.act("btn:cancel", nil, true)
		}
	case "address":
		text := "zone-ok"
		switch u.pick(0.1, 0.1) {
		case 0:
			text = "far away"
		case 1:
			text = "zone-bad" // the CRM rejects this address
		}
		u.act("msg:text", map[string]any{"text": text}, true)
	case "pay":
		if u.rng.Float64() < 0.2 {
			// a fast double tap: "Cancel" right after paying, before "please wait" arrives; the
			// barrier holds it until create_order ran, and the outcome then arrives on `menu`
			u.act("payment:success", nil, false)
			u.act("btn:cancel", nil, true)
			return 0
		}
		u.act("payment:success", nil, true)
	case "placing", "placing2":
		if u.rng.Float64() < 0.15 {
			u.act("btn:cancel", nil, true) // acts while the wait is pending
			return 0
		}
		if !u.waitMsg(u.replyWait) {
			u.st.noReplyWait.Add(1)
			u.act("cmd:start", nil, true)
		}
	case "order_failed":
		if u.rng.Float64() < 0.7 {
			u.act("btn:retry", nil, true)
		} else {
			u.act("cmd:start", nil, true)
		}
	case "done":
		if u.rng.Float64() < 0.4 {
			u.act("btn:ping", nil, false) // a background notification with outcomes, every
		}
		u.act("cmd:start", nil, true)
		return 1
	default:
		u.act("cmd:start", nil, true)
	}
	return 0
}

func (u *SimUser) points() int {
	switch u.last.Name {
	case "home":
		switch u.pick(0.5, 0.2) {
		case 0:
			for i, k := 0, 1+u.rng.Intn(6); i < k; i++ {
				u.act("btn:earn", nil, false) // no reply: an arrow without a target; waits pile up
			}
			time.Sleep(time.Millisecond)
		case 1:
			u.act("btn:wallet", nil, true)
		default:
			u.act("btn:shop", nil, true)
		}
	case "wallet", "purse":
		if u.rng.Float64() < 0.5 {
			u.act("btn:earn", nil, false)
		} else {
			u.sleepBot(500) // refresh fires: background audit with outcomes
		}
		u.act("btn:back", nil, true)
	case "shop":
		if u.rng.Float64() < 0.8 {
			u.act("btn:buy", nil, true) // enough_points reads the effect of credit
		} else {
			u.act("btn:back", nil, true)
		}
	case "bought":
		u.act("btn:back", nil, true)
		return 1
	case "poor":
		if u.rng.Float64() < 0.5 {
			u.act("btn:earn", nil, true)
		} else {
			u.act("btn:back", nil, true)
		}
	default:
		u.act("cmd:start", nil, true)
	}
	return 0
}

func (u *SimUser) booking() int {
	switch u.last.Name {
	case "lobby":
		switch u.pick(0.3, 0.6) {
		case 0:
			u.act("btn:topup", nil, false)
			u.act("btn:book", nil, true) // has_credit later reads the effect of topup
		case 1:
			u.act("btn:book", nil, true)
		default:
			u.act("cmd:help", nil, true)
		}
	case "help":
		u.act("btn:back", nil, true)
	case "pick":
		switch u.pick(0.6, 0.1, 0.1, 0.1) {
		case 0:
			u.act("btn:slot", nil, true)
		case 3:
			// "again" before the hold message arrives: the second reserve replaces the first wait
			u.act("btn:slot", nil, false)
			u.act("btn:again", nil, true)
		case 4:
			// support resets the user while the reserve call is pending: restart clears the wait
			u.act("btn:slot", nil, false)
			u.st.supportOps.Add(1)
			u.act("cmd:support", map[string]any{"support": "reset"}, true)
		case 1:
			u.act("btn:leave", nil, true)
		default:
			u.act("btn:ping", nil, false)
		}
	case "hold":
		switch u.pick(0.15, 0.05, 0.05) {
		case 0:
			u.act("btn:again", nil, true) // a new call replaces the wait (latest)
		case 1:
			u.st.supportOps.Add(1)
			u.act("cmd:support", map[string]any{"support": "reset"}, true) // restart clears the wait
		case 2:
			u.act("btn:leave", nil, true) // the outcome arrives on another message
		default:
			if !u.waitMsg(u.replyWait) {
				u.st.noReplyWait.Add(1)
				u.act("cmd:start", nil, true)
			}
		}
	case "confirm":
		switch u.pick(0.7, 0.2) {
		case 0:
			u.act("btn:pay", nil, true)
		case 1:
			u.sleepBot(2000) // expire → pick, release
			if !u.waitMsg(u.replyWait) {
				u.st.noReply.Add(1)
				u.act("cmd:start", nil, true)
			}
		default:
			u.act("btn:leave", nil, true)
		}
	case "topup_needed":
		u.act("btn:topup", nil, true)
	case "booked":
		u.act("btn:again", nil, true)
		return 1
	default:
		u.act("cmd:start", nil, true)
	}
	return 0
}
