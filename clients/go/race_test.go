package main

import (
	"os"
	"sync"
	"testing"
)

// One request map shared by several goroutines.
func TestSharedRequestMap(t *testing.T) {
	doc, _ := os.ReadFile("../../examples/pizza.bot.json")
	if initFigram() != 0 {
		t.Fatal("init")
	}
	bot, err := Load(string(doc))
	if err != nil {
		t.Fatal(err)
	}
	req := map[string]any{"op": "hello"}
	var wg sync.WaitGroup
	for i := 0; i < 8; i++ {
		wg.Add(1)
		go func() { defer wg.Done(); for j := 0; j < 200; j++ { bot.Request(req) } }()
	}
	wg.Wait()
}
