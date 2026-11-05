// refjudge: verdicts of the reference Go checkReply (clients/go/main.go) on a corpus.
// Input JSONL: {"k":key,"op":op,"id":id,"line":replyLine}. Output: key \t verdict.
// The reply is decoded exactly as clients/go Bot.Request does: json.Decoder with UseNumber
// into map[string]any (no check of text after the value).
package main

import (
	"bufio"
	"encoding/json"
	"fmt"
	"os"
	"strings"
)

var _ = figramLib

func main() {
	sc := bufio.NewScanner(os.Stdin)
	sc.Buffer(nil, 64<<20)
	w := bufio.NewWriter(os.Stdout)
	defer w.Flush()
	for sc.Scan() {
		var c struct {
			K    string `json:"k"`
			Op   string `json:"op"`
			ID   int64  `json:"id"`
			Line string `json:"line"`
		}
		if err := json.Unmarshal(sc.Bytes(), &c); err != nil {
			panic(err)
		}
		var resp map[string]any
		dec := json.NewDecoder(strings.NewReader(c.Line))
		dec.UseNumber()
		v := ""
		if err := dec.Decode(&resp); err != nil {
			v = "ERR"
		} else if resp == nil {
			v = "ERR" // the JSON null: Bot.Request would get a nil map
		} else if got, err := checkReply(resp, c.ID, c.Op); err != nil {
			v = "ERR"
		} else {
			v = got
		}
		fmt.Fprintf(w, "%s\t%s\n", c.K, v)
	}
	if err := sc.Err(); err != nil {
		panic(err)
	}
}
