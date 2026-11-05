// auditlimit: under SetMaxRequest, press a button whose `every` action is never answered until
// the engine refuses the step; then try the documented recovery path with the stored snapshot
// (Outcome of the oldest wait, Restart). Usage: auditlimit DOC EVENT ACTION LIMIT
package main

import (
	"encoding/json"
	"errors"
	"fmt"
	"os"
	"strconv"

	figram "github.com/Ruddy35/ffl-go"
)

func code(err error) string {
	var pe *figram.ProtocolError
	if errors.As(err, &pe) {
		return pe.Code
	}
	if err != nil {
		return "go-error: " + err.Error()
	}
	return "ok"
}

func main() {
	doc, _ := os.ReadFile(os.Args[1])
	event, action := os.Args[2], os.Args[3]
	limit, _ := strconv.Atoi(os.Args[4])
	bot, err := figram.Load(string(doc))
	if err != nil {
		panic(err)
	}
	figram.SetMaxRequest(limit)
	res, err := bot.Start(0)
	if err != nil {
		panic(err)
	}
	s := res.Snapshot
	steps := 0
	for ; steps < 100000; steps++ {
		r, err := bot.Step(s, event, nil)
		if err != nil {
			stored, _ := json.Marshal(s)
			fmt.Printf("step %d refused: %s (stored snapshot: %d waits, %d bytes as ffl-go encodes it, limit %d, 15/16 = %d)\n",
				steps, code(err), len(s.Pending), len(stored), limit, limit*15/16)
			break
		}
		s = r.Snapshot
	}
	oldest := s.Pending[len(s.Pending)-1]
	_, err = bot.Outcome(s, action, "pong", oldest.Token, nil)
	fmt.Printf("Outcome of the oldest wait with the stored snapshot: %s\n", code(err))
	_, err = bot.Restart(s)
	fmt.Printf("Restart with the stored snapshot: %s\n", code(err))
	// The same snapshot as the engine itself encodes it (no HTML escaping), sent raw.
	stored, _ := json.Marshal(s)
	var buf []byte
	{
		enc := json.NewEncoder(nil)
		_ = enc
	}
	raw := string(stored)
	raw = unescapeHTML(raw)
	line := fmt.Sprintf(`{"id":1,"op":"restart","snapshot":%s}`, raw)
	out, err := bot.Request([]byte(line))
	_ = buf
	fmt.Printf("Restart via Request with the engine's encoding (%d bytes): %.120s %v\n", len(line), out, err)
}

func unescapeHTML(s string) string {
	r := []rune{}
	_ = r
	out := ""
	for i := 0; i < len(s); i++ {
		if i+6 <= len(s) && s[i] == '\\' && s[i+1] == 'u' {
			switch s[i+2 : i+6] {
			case "003c":
				out += "<"
				i += 5
				continue
			case "003e":
				out += ">"
				i += 5
				continue
			case "0026":
				out += "&"
				i += 5
				continue
			}
		}
		out += string(s[i])
	}
	return out
}
