package main

import (
	"encoding/json"
	"fmt"
	"log"
	"os"

	figram "github.com/Ruddy35/ffl-go"
)

func main() {
	doc, err := os.ReadFile(os.Args[1])
	if err != nil {
		log.Fatal(err)
	}
	bot, err := figram.Load(string(doc))
	if err != nil {
		log.Fatal(err)
	}
	defer bot.Close()
	fmt.Println("bundled", figram.BundledVersion, "protocol", figram.ProtocolVersion)
	res, err := bot.Start(0)
	if err != nil {
		log.Fatal(err)
	}
	res, err = bot.Step(res.Snapshot, "btn:order", nil)
	if err != nil {
		log.Fatal(err)
	}
	stored, _ := json.Marshal(res.Snapshot)
	fmt.Println(string(stored))
	for _, a := range res.Actions {
		fmt.Println(a.Kind, a.Name)
	}
}
