package host

import (
	"encoding/json"
	"fmt"
	"net"
	"net/http"
	"os"
	"path/filepath"
	"sync"
	"sync/atomic"
	"testing"
	"time"

	"figramhost/figram"
)

const bots = "/home/user/figram-audit/hosts/shared/bots"

// fakeExt answers every CRM / Telegram request with success.
func fakeExt(t *testing.T, sock string) *atomic.Int64 {
	var n atomic.Int64
	ln, err := net.Listen("unix", sock)
	if err != nil {
		t.Fatal(err)
	}
	srv := &http.Server{Handler: http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		n.Add(1)
		w.Write([]byte(`{"result":{"order_id":1,"ticket_id":1,"event_id":1,"message_id":1}}`))
	})}
	go srv.Serve(ln)
	t.Cleanup(func() { srv.Close() })
	return &n
}

func newTestHost(t *testing.T, broken map[string]bool) *Host {
	dir := t.TempDir()
	sockDir, _ := os.MkdirTemp("", "fgt")
	t.Cleanup(func() { os.RemoveAll(sockDir) })
	ext := filepath.Join(sockDir, "e.sock")
	fakeExt(t, ext)
	h, err := New(Config{DB: filepath.Join(dir, "h.sqlite"), Bots: bots, Family: "pizza", ExtSock: ext,
		Executors: 2, BusyMs: 3000, Sync: "NORMAL", EngineTimeout: 5 * time.Second, ExtTimeout: time.Second, Broken: broken})
	if err != nil {
		t.Fatal(err)
	}
	t.Cleanup(h.Close)
	return h
}

func upd(user string, id int, ev string) Update {
	return Update{User: user, UID: fmt.Sprintf("tg:%d", id), Event: ev}
}

func TestValidateUpdate(t *testing.T) {
	cases := []struct {
		body string
		ok   bool
	}{
		{`{"user":"u1","update_id":1,"event":"btn:order"}`, true},
		{`{"user":"u1","update_id":1,"event":"msg:text","arg":"Mira 1"}`, true},
		{`{"user":"u1","update_id":1,"event":"btn:x","extra":{"a":1}}`, true},
		{`{"user":"u1","user":"u2","update_id":1,"event":"btn:order"}`, false},
		{`{"user":"","update_id":1,"event":"btn:order"}`, false},
		{`{"user":"a b","update_id":1,"event":"btn:order"}`, false},
		{`{"user":"u1","update_id":-5,"event":"btn:order"}`, false},
		{`{"user":"u1","update_id":1.5,"event":"btn:order"}`, false},
		{`{"user":"u1","update_id":"77","event":"btn:order"}`, false},
		{`{"user":"u1","update_id":9007199254740992,"event":"btn:order"}`, false},
		{`{"user":"u1","update_id":true,"event":"btn:order"}`, false},
		{`{"user":"u1","update_id":1,"event":"timer:cart_reminder","token":3}`, false},
		{`{"user":"u1","update_id":1,"event":"btn:checkout","token":3}`, false},
		{`{"user":"u1","update_id":1,"event":"BTN:ORDER"}`, false},
		{`{"user":"u1","update_id":1,"event":"btn:order\n{}"}`, false},
		{`{"user":"u1","update_id":1,"event":"msg:text","arg":"a\u0000b"}`, false},
		{`{"user":"u1","update_id":1,"event":"msg:text","arg":123}`, false},
		{`[1,2,3]`, false},
		{`{not json`, false},
		{``, false},
		{"{\"user\":\"u\xff\",\"update_id\":1,\"event\":\"btn:order\"}", false},
		{`{"user":"u1","update_id":1,"event":"btn:order"} {"user":"u2"}`, false},
	}
	for _, c := range cases {
		_, err := ValidateUpdate([]byte(c.body), false)
		if (err == nil) != c.ok {
			t.Errorf("%q: ok=%v err=%v", c.body, c.ok, err)
		}
	}
	// broken variant really lets forged timer events through
	u, err := ValidateUpdate([]byte(`{"user":"u1","update_id":1,"event":"timer:cart_reminder","token":3}`), true)
	if err != nil || u.Token == nil || *u.Token != 3 {
		t.Fatalf("no-validate variant: %v %v", u, err)
	}
}

func TestValidateSnapshot(t *testing.T) {
	good := []string{`{"at":"menu"}`, `{"v":1,"at":"cart","resume":{"order":"cart"},"timers":{"t":5},"seq":9007199254740991}`}
	bad := []string{`[1]`, `null`, `{"at":""}`, `{"seq":1}`, `{"at":"m","v":2}`, `{"at":"m","evil":1}`,
		`{"at":"m","seq":"3"}`, `{"at":"m","seq":1.0}`, `{"at":"m","seq":9007199254740992}`, `{"at":"m","timers":{"":1}}`,
		`{"at":"m","resume":{"a":""}}`, `{"at":"m","at":"n"}`, `{"at":"m","seq":-1}`}
	for _, s := range good {
		if _, err := ParseSnapshotText([]byte(s)); err != nil {
			t.Errorf("%s: %v", s, err)
		}
	}
	for _, s := range bad {
		if _, err := ParseSnapshotText([]byte(s)); err == nil {
			t.Errorf("%s accepted", s)
		}
	}
}

func hwOf(t *testing.T, h *Host, user string) (int64, int) {
	var hw int64
	if err := h.db.QueryRow(`SELECT hw_seq FROM users WHERE user=?`, user).Scan(&hw); err != nil {
		t.Fatal(err)
	}
	var n int
	h.db.QueryRow(`SELECT COUNT(*) FROM transitions WHERE user=? AND changed=1`, user).Scan(&n)
	return hw, n
}

// Same user, many concurrent distinct updates (cmd:start is a transition from every
// state): per-user serialisation must not lose any of them.
func TestSameUserConcurrentNoLostUpdate(t *testing.T) {
	h := newTestHost(t, nil)
	const N = 40
	var wg sync.WaitGroup
	for i := 0; i < N; i++ {
		wg.Add(1)
		go func(i int) {
			defer wg.Done()
			if _, err := h.Step(upd("u1", 100+i, "cmd:start"), nil); err != nil {
				t.Error(err)
			}
		}(i)
	}
	wg.Wait()
	hw, n := hwOf(t, h, "u1")
	if hw != N || n != N {
		t.Fatalf("hw_seq=%d transitions=%d, want %d", hw, n, N)
	}
}

// The same update delivered concurrently 8 times: exactly one transition.
func TestDuplicateDeliveryOnce(t *testing.T) {
	h := newTestHost(t, nil)
	if _, err := h.Step(upd("u1", 1, "cmd:start"), nil); err != nil {
		t.Fatal(err)
	}
	var wg sync.WaitGroup
	var dups atomic.Int64
	for i := 0; i < 8; i++ {
		wg.Add(1)
		go func() {
			defer wg.Done()
			st, err := h.Step(upd("u1", 2, "btn:order"), nil)
			if err != nil {
				t.Error(err)
			}
			if st == "dup" {
				dups.Add(1)
			}
		}()
	}
	wg.Wait()
	hw, n := hwOf(t, h, "u1")
	if hw != 2 || n != 2 || dups.Load() != 7 {
		t.Fatalf("hw=%d transitions=%d dups=%d", hw, n, dups.Load())
	}
}

// Negative control of the two tests above: without the user lock and the version
// check updates of one user are lost.
func TestNoUserLockLosesUpdates(t *testing.T) {
	h := newTestHost(t, map[string]bool{"no-user-lock": true})
	if _, err := h.Step(upd("u1", 1, "cmd:start"), nil); err != nil {
		t.Fatal(err)
	}
	const N = 40
	var wg sync.WaitGroup
	for i := 0; i < N; i++ {
		wg.Add(1)
		go func(i int) {
			defer wg.Done()
			h.Step(upd("u1", 100+i, "cmd:start"), nil)
		}(i)
	}
	wg.Wait()
	hw, n := hwOf(t, h, "u1")
	t.Logf("broken no-user-lock: hw=%d transitions=%d (want %d)", hw, n, N+1)
	if hw == N+1 && n == N+1 {
		t.Skip("race not hit this time (negative control is probabilistic)")
	}
}

// Many users in parallel through the whole order path, with a document reload of
// the same document every few ms (old automaton retired after in-flight calls).
func TestParallelUsersWithReloads(t *testing.T) {
	h := newTestHost(t, nil)
	stop := make(chan struct{})
	var reloads atomic.Int64
	go func() {
		for {
			select {
			case <-stop:
				return
			default:
			}
			if _, err := h.eng.Reload("pizza"); err != nil {
				t.Error(err)
				return
			}
			reloads.Add(1)
			time.Sleep(2 * time.Millisecond)
		}
	}()
	script := []struct{ ev, arg string }{{"cmd:start", ""}, {"btn:order", ""}, {"btn:pizza", ""}, {"btn:size", "L"},
		{"btn:add", ""}, {"btn:checkout", ""}, {"msg:text", "Mira 1"}, {"payment:success", ""}}
	var wg sync.WaitGroup
	for u := 0; u < 20; u++ {
		wg.Add(1)
		go func(u int) {
			defer wg.Done()
			for i, s := range script {
				up := upd(fmt.Sprintf("p%02d", u), 1000*u+i, s.ev)
				if s.arg != "" {
					a := s.arg
					up.Arg = &a
				}
				if _, err := h.Step(up, nil); err != nil {
					t.Errorf("user %d step %d: %v", u, i, err)
					return
				}
			}
		}(u)
	}
	wg.Wait()
	close(stop)
	if err := h.Barrier(30 * time.Second); err != nil {
		t.Fatal(err)
	}
	for u := 0; u < 20; u++ {
		var snap string
		h.db.QueryRow(`SELECT snapshot FROM users WHERE user=?`, fmt.Sprintf("p%02d", u)).Scan(&snap)
		var s map[string]any
		json.Unmarshal([]byte(snap), &s)
		if s["at"] != "done" || s["seq"] != float64(8) {
			t.Errorf("p%02d ended at %v", u, snap)
		}
	}
	var orders int
	h.db.QueryRow(`SELECT COUNT(*) FROM orders_local`).Scan(&orders)
	st := figram.GetStats()
	t.Logf("reloads=%d orders=%d binding=%+v", reloads.Load(), orders, st)
	if orders != 20 || reloads.Load() < 10 {
		t.Fatalf("orders=%d reloads=%d", orders, reloads.Load())
	}
}

// Rule G1: a guard sees the effect of the user's own earlier, not yet executed call.
func TestGuardSeesOwnPendingCall(t *testing.T) {
	for _, broken := range []bool{false, true} {
		var b map[string]bool
		if broken {
			b = map[string]bool{"no-drain": true}
		}
		h := newTestHost(t, b)
		h.exec.paused.Store(true) // the background executor is late
		for i, ev := range []string{"cmd:start", "btn:order", "btn:pizza", "btn:size", "btn:add", "btn:checkout"} {
			if _, err := h.Step(upd("g1", 10+i, ev), nil); err != nil {
				t.Fatal(err)
			}
		}
		var snap string
		h.db.QueryRow(`SELECT snapshot FROM users WHERE user='g1'`).Scan(&snap)
		var s map[string]any
		json.Unmarshal([]byte(snap), &s)
		want := "address"
		if broken {
			want = "catalog" // stale guard: cart looks empty
		}
		if s["at"] != want {
			t.Fatalf("broken=%v: at=%v want %s", broken, s["at"], want)
		}
	}
}
