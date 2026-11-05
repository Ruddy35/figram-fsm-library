package figram_test

import (
	"encoding/json"
	"fmt"
	"log"
	"os"

	figram "github.com/Ruddy35/ffl-go"
)

// A host loads the bot once and keeps one snapshot per user.
func Example() {
	doc, err := os.ReadFile("testdata/pizza.bot.json")
	if err != nil {
		log.Fatal(err)
	}
	bot, err := figram.Load(string(doc))
	if err != nil {
		log.Fatal(err)
	}
	defer bot.Close()

	res, err := bot.Start(0) // a new user
	if err != nil {
		log.Fatal(err)
	}
	actions, _ := json.Marshal(res.Actions)
	fmt.Println(res.Snapshot.At, string(actions))

	res, err = bot.Step(res.Snapshot, "btn:order", nil) // the user pressed a button
	if err != nil {
		log.Fatal(err)
	}
	stored, _ := json.Marshal(res.Snapshot) // what the host stores for the user
	fmt.Println(string(stored))
	for _, a := range res.Actions {
		if a.Kind == figram.Send {
			fmt.Println("send", a.Name)
		}
	}
	// Output:
	// menu [{"send":"menu"}]
	// {"at":"catalog","back":"menu","resume":{"order":"catalog"},"seq":2}
	// send catalog
}

// A new user comes with an update, here the button "Order" of an old message: the start
// processes it as if the user stood on the initial message.
func ExampleBot_StartWith() {
	doc, _ := os.ReadFile("testdata/pizza.bot.json")
	bot, err := figram.Load(string(doc))
	if err != nil {
		log.Fatal(err)
	}
	defer bot.Close()
	res, err := bot.StartWith(0, "btn:order", nil)
	if err != nil {
		log.Fatal(err)
	}
	stored, _ := json.Marshal(res.Snapshot)
	actions, _ := json.Marshal(res.Actions)
	fmt.Println(string(stored), string(actions))
	// Output:
	// {"at":"catalog","resume":{"order":"catalog"},"seq":1} [{"send":"catalog"}]
}

// The engine asks for the conditions it needs; Decide evaluates them and repeats the step.
func ExampleDecide() {
	doc, _ := os.ReadFile("testdata/pizza.bot.json")
	bot, err := figram.Load(string(doc))
	if err != nil {
		log.Fatal(err)
	}
	defer bot.Close()
	cart := figram.Snapshot{At: "cart", Seq: 3}
	cartNotEmpty := func(condition string) (bool, error) {
		fmt.Println("asked:", condition)
		return condition == "cart_not_empty", nil
	}
	res, err := figram.Decide(nil, cartNotEmpty, func(g map[string]bool) (*figram.Result, error) {
		return bot.Step(cart, "btn:checkout", g)
	})
	if err != nil {
		log.Fatal(err)
	}
	b, _ := json.Marshal(res.Actions)
	fmt.Println(res.Snapshot.At, string(b))
	// Output:
	// asked: cart_not_empty
	// address [{"cancel_timer":"cart_reminder"},{"send":"address"}]
}

// Check reports the problems of a document, such as a misspelt key.
func ExampleCheck() {
	err := figram.Check(`{"figram": 1, "bot": {"initial": "a", "children": {
		"a": {"on": {"btn:go": [{"iff": "ready", "to": "a"}]}}}}}`)
	fmt.Println(err)
	// Output:
	// figram: document rejected: `a` → `btn:go`: unknown key `iff` (annotation keys start with `x-`)
}

// A timer fires: pass its name and token from the start_timer action back to the engine.
func ExampleBot_Timer() {
	doc, _ := os.ReadFile("testdata/features.bot.json")
	bot, err := figram.Load(string(doc))
	if err != nil {
		log.Fatal(err)
	}
	defer bot.Close()
	res, _ := bot.Start(0)
	for _, a := range res.Actions {
		if a.Kind == figram.StartTimer {
			fmt.Printf("start %s for %d ms, token %d\n", a.Name, a.Ms, a.Token)
			fired, err := bot.Timer(res.Snapshot, a.Name, a.Token, nil)
			if err != nil {
				log.Fatal(err)
			}
			fmt.Println("after the timer:", fired.Snapshot.At, fired.Actions)
		}
	}
	// Output:
	// start idle for 60000 ms, token 1
	// after the timer: home []
}

// An action with outcomes: the call carries a token, and its outcome comes back as an event with
// that token. Here the CRM answers create_order with "created".
func ExampleBot_Outcome() {
	doc, _ := os.ReadFile("testdata/pizza.bot.json")
	bot, err := figram.Load(string(doc))
	if err != nil {
		log.Fatal(err)
	}
	defer bot.Close()
	pay := figram.Snapshot{At: "pay", Resume: map[string]string{"order": "pay"}, Seq: 5}
	res, err := bot.Step(pay, "payment:success", nil)
	if err != nil {
		log.Fatal(err)
	}
	for _, a := range res.Actions {
		if a.Kind == figram.Call && a.Outcomes {
			fmt.Printf("call %s, token %d; waiting: %v\n", a.Name, a.Token, res.Snapshot.Pending)
			done, err := bot.Outcome(res.Snapshot, a.Name, "created", a.Token, nil)
			if err != nil {
				log.Fatal(err)
			}
			b, _ := json.Marshal(done.Actions)
			fmt.Println(done.Snapshot.At, string(b))
		}
	}
	// Output:
	// call create_order, token 6; waiting: [{create_order 6}]
	// done [{"call":"notify_kitchen","detached":true},{"send":"done"}]
}
