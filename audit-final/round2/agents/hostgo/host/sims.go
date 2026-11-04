package main

// Simulated external systems. They live outside the host instance: a host crash does not reset
// them. Every system is idempotent by the key the host passes (a repeated key returns the stored
// answer without acting again) and keeps ground truth for the checks: the irreversible effects
// per logical action (tag = outbox row id, an oracle-only field the systems never use for
// idempotency) and, for the CRM, per business key (the cart).

import (
	"errors"
	"fmt"
	"math/rand"
	"sync"
	"sync/atomic"
	"time"
)

var (
	errTimeout = errors.New("timeout (no answer)")
	errServer  = errors.New("server error")
)

type ExtResult struct {
	Outcome string         // created, rejected, ok, held, taken, sent, logged
	Details map[string]any // reason, amount …
}

type stored struct {
	res   ExtResult
	acted bool
}

type Sim struct {
	name string
	mu   sync.Mutex
	rng  *rand.Rand
	byKey      map[string]stored
	effTag     map[int64]int    // irreversible effects per logical action
	effCart    map[string]int   // CRM: created orders per cart
	effects    atomic.Int64
	calls      atomic.Int64
	lookups    atomic.Int64
	lossyLeft  map[string]int // user → remaining "act but lose the answer" attempts
	dead       map[string]bool
	latency    time.Duration
	slow       map[string]bool // users whose calls take latency
	firstFail  map[string]bool // key → first attempt already failed
	paused     atomic.Bool
	pauseCh    chan struct{}
	pmu        sync.Mutex
}

func newSim(name string, seed int64) *Sim {
	return &Sim{name: name, rng: rand.New(rand.NewSource(seed)), byKey: map[string]stored{}, effTag: map[int64]int{},
		effCart: map[string]int{}, lossyLeft: map[string]int{}, dead: map[string]bool{}, firstFail: map[string]bool{}, slow: map[string]bool{}}
}

func (s *Sim) Pause() {
	s.pmu.Lock()
	if !s.paused.Load() {
		s.pauseCh = make(chan struct{})
		s.paused.Store(true)
	}
	s.pmu.Unlock()
}

func (s *Sim) Resume() {
	s.pmu.Lock()
	if s.paused.Load() {
		close(s.pauseCh)
		s.paused.Store(false)
	}
	s.pmu.Unlock()
}

func (s *Sim) waitPause() {
	s.pmu.Lock()
	ch := s.pauseCh
	p := s.paused.Load()
	s.pmu.Unlock()
	if p {
		<-ch
	}
}

type Sims struct {
	crm, loyalty, booking, notify, audit *Sim
	tg                                   *Telegram
}

func newSims(seed int64, users []string, notifyLatency time.Duration) *Sims {
	ss := &Sims{crm: newSim("crm", seed+1), loyalty: newSim("loyalty", seed+2), booking: newSim("booking", seed+3),
		notify: newSim("notify", seed+4), audit: newSim("audit", seed+5)}
	r := rand.New(rand.NewSource(seed))
	for _, u := range users {
		switch botOf(u) {
		case "shop":
			if r.Float64() < 0.15 {
				ss.crm.lossyLeft[u] = 3 // the CRM acts and loses the answer three times: reconcile
			}
		case "points":
			if r.Float64() < 0.10 {
				ss.loyalty.lossyLeft[u] = 3
			}
		case "booking":
			if r.Float64() < 0.10 {
				ss.booking.lossyLeft[u] = 3
			}
		}
		if r.Float64() < 0.10 {
			ss.notify.dead[u] = true // notifications to this user fail for good
		}
		if r.Float64() < 0.10 {
			ss.audit.dead[u] = true
		}
		if r.Float64() < 0.20 { // background calls for these users are slow
			ss.notify.slow[u] = true
			ss.audit.slow[u] = true
		}
	}
	ss.notify.latency = notifyLatency
	ss.audit.latency = notifyLatency
	return ss
}

func (ss *Sims) of(system string) *Sim {
	switch system {
	case "crm":
		return ss.crm
	case "loyalty":
		return ss.loyalty
	case "booking":
		return ss.booking
	case "notify":
		return ss.notify
	case "audit":
		return ss.audit
	}
	panic(system)
}

// Call performs action with idempotency key key. tag is the oracle-only logical action id.
func (s *Sim) Call(key string, tag int64, user, action string, payload map[string]any) (ExtResult, error) {
	s.waitPause()
	s.calls.Add(1)
	if s.latency > 0 && s.slow[user] {
		time.Sleep(s.latency)
	}
	s.mu.Lock()
	defer s.mu.Unlock()
	if n := s.lossyLeft[user]; n > 0 && s.name != "notify" && s.name != "audit" {
		// the system acts on the first attempt and the answer is lost on every attempt
		s.lossyLeft[user] = n - 1
		if _, ok := s.byKey[key]; !ok {
			res := s.decide(user, payload)
			s.act(key, tag, user, cartOf(payload), res)
		}
		return ExtResult{}, errTimeout
	}
	if st, ok := s.byKey[key]; ok {
		return st.res, nil
	}
	if s.dead[user] {
		return ExtResult{}, errServer
	}
	switch s.name {
	case "notify", "audit":
		if !s.firstFail[key] && s.rng.Float64() < 0.3 {
			s.firstFail[key] = true
			return ExtResult{}, errServer
		}
		res := ExtResult{Outcome: map[string]string{"notify": "sent", "audit": "logged"}[s.name]}
		s.act(key, tag, user, "", res)
		return res, nil
	}
	p := s.rng.Float64()
	switch {
	case p < 0.04:
		return ExtResult{}, errServer // did not act
	case p < 0.08:
		return ExtResult{}, errTimeout // did not act
	case p < 0.13:
		res := s.decide(user, payload)
		s.act(key, tag, user, cartOf(payload), res)
		return ExtResult{}, errTimeout // acted, answer lost
	}
	res := s.decide(user, payload)
	s.act(key, tag, user, cartOf(payload), res)
	return res, nil
}

func cartOf(p map[string]any) string {
	if p == nil {
		return ""
	}
	c, _ := p["cart"].(string)
	return c
}

func (s *Sim) decide(user string, payload map[string]any) ExtResult {
	switch s.name {
	case "crm":
		if a, _ := payload["addr"].(string); a == "zone-bad" {
			return ExtResult{Outcome: "rejected", Details: map[string]any{"reason": "address"}}
		}
		if s.rng.Float64() < 0.08 {
			return ExtResult{Outcome: "rejected", Details: map[string]any{"reason": "stock"}}
		}
		return ExtResult{Outcome: "created", Details: map[string]any{"order": fmt.Sprintf("o%d", s.rng.Int63())}}
	case "loyalty":
		return ExtResult{Outcome: "ok", Details: map[string]any{"amount": 1 + s.rng.Intn(5)}}
	case "booking":
		if s.rng.Float64() < 0.3 {
			return ExtResult{Outcome: "taken"}
		}
		return ExtResult{Outcome: "held"}
	}
	panic(s.name)
}

// act records an irreversible effect (only answers that change the external world count:
// a rejection or a "taken" changes nothing).
func (s *Sim) act(key string, tag int64, user, cart string, res ExtResult) {
	s.byKey[key] = stored{res: res, acted: true}
	switch res.Outcome {
	case "created", "ok", "held", "sent", "logged":
		s.effTag[tag]++
		s.effects.Add(1)
		if s.name == "crm" && cart != "" {
			s.effCart[cart]++
		}
	}
}

// Lookup is the reconciliation by key: the stored answer if the system acted on that key.
func (s *Sim) Lookup(key string) (ExtResult, bool) {
	s.lookups.Add(1)
	s.mu.Lock()
	defer s.mu.Unlock()
	st, ok := s.byKey[key]
	return st.res, ok
}

// ---------------- Telegram ----------------

type TgMsg struct {
	Name string
	Seq  int64
}

type Telegram struct {
	mu    sync.Mutex
	rng   *rand.Rand
	inbox map[string]chan TgMsg
	sent  atomic.Int64
	fails atomic.Int64
}

func newTelegram(seed int64, users []string) *Telegram {
	t := &Telegram{rng: rand.New(rand.NewSource(seed)), inbox: map[string]chan TgMsg{}}
	for _, u := range users {
		t.inbox[u] = make(chan TgMsg, 4096)
	}
	return t
}

// Send delivers a message at least once (a retry after a lost answer is a visible duplicate).
func (t *Telegram) Send(user, msg string, seq int64) error {
	t.mu.Lock()
	p := t.rng.Float64()
	t.mu.Unlock()
	if p < 0.01 {
		t.fails.Add(1)
		return errServer
	}
	ch := t.inbox[user]
	select {
	case ch <- TgMsg{Name: msg, Seq: seq}:
	default: // the user's client is gone; the message is dropped like an unread chat
	}
	t.sent.Add(1)
	if p < 0.02 {
		return errTimeout // delivered, answer lost: the executor sends again
	}
	return nil
}
