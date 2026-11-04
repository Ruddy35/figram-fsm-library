package main

// The engine through the reference Go wrapper (clients/go, copied unchanged into refwrap):
// several loaded documents (machines) shared by all goroutines; reload loads the new version,
// swaps the pointer and closes the old machine while other goroutines may still hold it.

import (
	"encoding/json"
	"fmt"
	"os"
	"path/filepath"
	"strings"
	"sync"
	"sync/atomic"
	"time"

	"hostgo/refwrap"
)

var botsDir = "/home/user/figram-audit/agents6/hostgo/bots"

// docText returns the text of a document version: "shop@1" → bots/shop.bot.json,
// "shop@2" → bots/shop.v2.bot.json.
var docCache sync.Map

func docText(key string) []byte {
	if v, ok := docCache.Load(key); ok {
		return v.([]byte)
	}
	name, ver, _ := strings.Cut(key, "@")
	file := name + ".bot.json"
	if ver != "1" {
		file = name + ".v" + ver + ".bot.json"
	}
	b, err := os.ReadFile(filepath.Join(botsDir, file))
	if err != nil {
		panic(err)
	}
	docCache.Store(key, b)
	return b
}

type DocVer struct {
	key string // name@ver
	bot *refwrap.Bot
}

type Registry struct {
	cur    sync.Map // name → *atomic.Pointer[DocVer]
	mu     sync.Mutex
	loaded []*DocVer
	closed atomic.Int64 // requests that hit a closed bot and were retried
	loads  atomic.Int64
}

func (r *Registry) ptr(name string) *atomic.Pointer[DocVer] {
	p, _ := r.cur.LoadOrStore(name, new(atomic.Pointer[DocVer]))
	return p.(*atomic.Pointer[DocVer])
}

func (r *Registry) Current(name string) *DocVer { return r.ptr(name).Load() }

func loadDoc(key string) (*DocVer, error) {
	b, err := refwrap.Load(string(docText(key)))
	if err != nil {
		return nil, fmt.Errorf("load %s: %w", key, err)
	}
	if resp, v, err := b.Request(map[string]any{"op": "hello"}); err != nil || v != "hello" {
		return nil, fmt.Errorf("hello %s: %v %v %v", key, resp, v, err)
	}
	return &DocVer{key: key, bot: b}, nil
}

// Install makes key the current version of its document and closes the previous machine.
// Close waits for the requests running on the old machine (sync.RWMutex of the wrapper);
// goroutines that still hold the old *DocVer get "bot is closed" and retry with the new one.
func (r *Registry) Install(in *Instance, key string) error {
	name, _, _ := strings.Cut(key, "@")
	dv, err := loadDoc(key)
	if err != nil {
		return err
	}
	r.loads.Add(1)
	if in != nil {
		if err := in.crashPoint("reload_after_load"); err != nil {
			dv.bot.Close()
			return err
		}
	}
	old := r.ptr(name).Swap(dv)
	if in != nil && in.store != nil {
		if err := in.store.tx(in, func(t txLike) error {
			_, err := t.Exec(`INSERT INTO docs(name, ver) VALUES(?, ?) ON CONFLICT(name) DO UPDATE SET ver=excluded.ver`, name, key)
			return err
		}); err != nil {
			// the swap already happened in memory; the instance is dying, its registry goes too
			if old != nil {
				old.bot.Close()
			}
			return err
		}
	}
	if old != nil {
		if in != nil && in.cfg.unloadDelay > 0 {
			time.Sleep(in.cfg.unloadDelay)
		}
		old.bot.Close()
	}
	return nil
}

func (r *Registry) CloseAll() {
	r.cur.Range(func(k, v any) bool {
		if dv := v.(*atomic.Pointer[DocVer]).Swap(nil); dv != nil {
			dv.bot.Close()
		}
		return true
	})
}

// Reply of one engine call after the need loop.
type Reply struct {
	Snapshot  map[string]any
	Actions   []any
	Recovered bool
	Guards    map[string]bool
	DocV      string
	ErrCode   string
	ErrText   string
}

var protoViolations atomic.Int64
var protoViolationSample atomic.Value

// call performs one logical engine operation (start, restart, step) with the need loop:
// guards(names) computes conditions. If the machine was closed by a reload in the meantime,
// the whole operation is repeated on the current version (a step is a pure function).
func (r *Registry) call(name string, req map[string]any, guards func([]string) map[string]bool) (Reply, error) {
	for attempt := 0; ; attempt++ {
		dv := r.Current(name)
		if dv == nil {
			return Reply{}, fmt.Errorf("no machine for %s", name)
		}
		g := map[string]bool{}
		q := make(map[string]any, len(req)+1)
		for k, v := range req {
			q[k] = v
		}
		retry := false
		for round := 0; round < 20; round++ {
			if len(g) > 0 {
				q["guards"] = g
			}
			resp, variant, err := dv.bot.Request(q)
			if err != nil {
				if err.Error() == "bot is closed" {
					r.closed.Add(1)
					retry = true
					break
				}
				protoViolations.Add(1)
				protoViolationSample.Store(fmt.Sprintf("%v (reply %v)", err, resp))
				return Reply{}, fmt.Errorf("protocol violation: %w", err)
			}
			switch variant {
			case "need":
				names := []string{}
				for _, n := range resp["need"].([]any) {
					names = append(names, n.(string))
				}
				for k, v := range guards(names) {
					g[k] = v
				}
				continue
			case "error":
				return Reply{ErrCode: resp["code"].(string), ErrText: resp["error"].(string), DocV: dv.key}, nil
			case "result":
				rep := Reply{Snapshot: resp["snapshot"].(map[string]any), Actions: resp["actions"].([]any), Guards: g, DocV: dv.key}
				if rec, ok := resp["recovered"]; ok && rec == true {
					rep.Recovered = true
				}
				return rep, nil
			default:
				return Reply{}, fmt.Errorf("unexpected variant %q", variant)
			}
		}
		if !retry {
			return Reply{}, fmt.Errorf("need loop did not converge")
		}
		if attempt > 1000 {
			return Reply{}, fmt.Errorf("machine keeps closing")
		}
	}
}

func mustJSON(v any) string {
	b, err := json.Marshal(v)
	if err != nil {
		panic(err)
	}
	return string(b)
}

func decodeJSON(s string) any {
	if s == "" {
		return nil
	}
	d := json.NewDecoder(strings.NewReader(s))
	d.UseNumber()
	var v any
	if err := d.Decode(&v); err != nil {
		panic(fmt.Sprintf("decode %q: %v", s, err))
	}
	return v
}
