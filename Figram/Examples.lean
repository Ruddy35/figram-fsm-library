import Figram.Run
import Figram.Lint
import Figram.FormatProofs
import Figram.FormatIO
import Figram.Trace
import Figram.RandomDoc

/-!
# Example: a pizza shop bot

Shows all the shortcuts:

* the section `bot` around everything — the global command `/start` from any screen;
* the section `order` — the "Cancel" button once for the whole order;
* `/help` from anywhere and "Back" to the message the user opened help from, otherwise to the menu
  (help is in a section of its own, so opening it always crosses a section boundary);
* "Back" from the reminder to where the user left off in the order;
* a reminder on the cart;
* conditions as text: "the cart is not empty", "the address is in the delivery zone";
* an action with outcomes: the order is created in the CRM while the user waits on `placing`;
  the root section handles each outcome (created, rejected with the reason, failed), so an
  outcome that comes while the user is on `/help` is not lost; and a background action that
  tells the kitchen.
-/

namespace Figram.Examples

open Figram Figram.Format

private def v (to : NodeId) (calls : List String := []) : Variant :=
  ⟨none, some to, false, false, calls, []⟩
private def vIf (c : String) (to : NodeId) (calls : List String := []) : Variant :=
  ⟨some c, some to, false, false, calls, []⟩
/-- "Resume section `s` where the user left off". -/
private def resume (s : NodeId) : Variant := ⟨none, some s, true, false, [], []⟩
/-- "Back to the previous section", otherwise to `fallback`. -/
private def goBack (fallback : NodeId) : Variant := ⟨none, some fallback, false, true, [], []⟩
/-- "Back to the previous section", otherwise resume section `s`. -/
private def goBackResume (s : NodeId) : Variant := ⟨none, some s, true, true, [], []⟩
/-- A variant without a target: the actions, without a move. -/
private def act (calls : List String) : Variant := ⟨none, none, false, false, calls, []⟩
private def msg (on : List (EventId × List Variant)) : NodeDoc := .message on [] []
/-- An action with only a text. -/
private def txt (t : String) : CallDecl := ⟨t, false, [], false, []⟩

/-- The pizza shop bot in the document format. -/
def pizzaDoc : Doc where
  requires := []
  conditions := [
    ("cart_not_empty", "The cart has at least one pizza"),
    ("in_delivery_zone", "The address is in the delivery zone"),
    ("address_rejected", "The CRM rejected the delivery address")]
  calls := [
    ("clear_cart", txt "Clear the cart"),
    ("save_size", txt "Save the chosen size"),
    ("add_to_cart", txt "Add the pizza to the cart"),
    ("save_address", txt "Save the address from the message"),
    ("create_order", ⟨"Create the order in the CRM", false, ["created", "rejected"], false, []⟩),
    ("notify_kitchen", ⟨"Tell the kitchen about the new order", true, [], false, []⟩)]
  variables := [
    ("cart", "Cart contents"),
    ("address", "Delivery address")]
  bot := .sec "menu" [
      ("menu", msg [("btn:order", [v "order"])]),
      ("help_section", .sec "help" [("help", msg [("btn:back", [goBack "menu"])])] [] []),
      ("done", msg []),
      ("reminder", msg [("btn:back", [resume "order"])]),
      ("order", .sec "catalog" [
          ("catalog", msg [("btn:pizza", [v "pizza"]), ("btn:cart", [v "cart"])]),
          ("pizza", .sec "pick_size" [
              ("pick_size", msg [("btn:size", [v "pick_toppings" ["save_size"]])]),
              ("pick_toppings", msg [("btn:add", [v "cart" ["add_to_cart"]])])
            ] [] []),
          ("cart", .message [
              ("btn:checkout", [vIf "cart_not_empty" "address", v "catalog"]),
              ("timer:cart_reminder", [v "reminder"])
            ] [("cart_reminder", 2 * 60 * 60 * 1000)] []),
          ("address", msg [
              ("msg:text", [vIf "in_delivery_zone" "pay" ["save_address"], v "pickup"])]),
          ("pay", msg [("payment:success", [v "placing" ["create_order"]])]),
          ("placing", msg []),
          ("order_failed", msg [("btn:retry", [v "placing" ["create_order"]])]),
          ("pickup", msg [])
        ] [("btn:cancel", [v "menu" ["clear_cart"]])] [])
    ] [("cmd:start", [v "menu"]), ("cmd:help", [v "help"]),
       -- the outcomes on the root: they are handled wherever the user is when they come
       ("call:create_order:created", [v "done" ["notify_kitchen"]]),
       ("call:create_order:rejected", [vIf "address_rejected" "address", v "order_failed"]),
       ("call:create_order:failed", [v "order_failed"])] []
  ext := []

/-- The chart the machine executes. -/
def pizza : Chart := pizzaDoc.toChart

/-- A document with all the special cases of the behaviour. -/
def featuresDoc : Doc where
  requires := []
  conditions := [
    ("is_vip", "The user is a VIP"),
    ("local_ok", "Local check in the catalog"),
    ("in_stock", "The item is in stock")]
  calls := [("charge", txt "Charge the payment"), ("notify", txt "Notify the manager"),
    ("log", ⟨"Write to the log", true, [], false, []⟩),
    ("check_stock", ⟨"Ask the warehouse whether the item is in stock", false, ["yes", "no"], false, []⟩),
    ("ping", ⟨"Ping the manager; every ping is answered", true, ["pong"], true, []⟩)]
  variables := []
  bot := .sec "home" [
      ("home", .message [
          ("btn:go", [vIf "is_vip" "vip", v "shop"]),
          ("btn:self", [v "home"]),
          ("btn:log", [act ["log"]])
        ] [("idle", 60000)] []),
      ("help", msg [("btn:back", [resume "shop"])]),
      ("vip", .message [("timer:nudge", [act ["log"]])] [("nudge", 30000)] []),
      ("paid", msg [("btn:back", [goBackResume "shop"])]),
      ("shop", .sec "catalog" [
          ("catalog", msg [
              ("btn:next", [v "sub"]),
              ("btn:x", [vIf "local_ok" "item2"])]),
          ("sub", .sec "item" [
              ("item", .message [
                  ("btn:buy", [vIf "in_stock" "paid" ["charge", "notify"], v "catalog"]),
                  ("btn:other", [v "item2"]),
                  ("btn:check", [act ["check_stock"]]),
                  ("call:check_stock:yes", [v "item2"]),
                  ("call:check_stock:no", [v "catalog"]),
                  ("call:check_stock:failed", [act ["log"]]),
                  ("timer:remind", [v "reminder"])
                ] [("remind", 5000)] []),
              ("item2", msg [("btn:back_item", [v "item"])])
            ] [] []),
          ("reminder", msg [("btn:back", [resume "shop"])])
        ] [("btn:cancel", [v "home"]), ("btn:x", [v "reminder"])] [])
    ] [("cmd:start", [v "home"]), ("cmd:help", [v "help"]), ("btn:prev", [goBack "home"]),
       ("btn:ping", [act ["ping"]]),
       ("call:ping:pong", [act ["log"]]),
       ("call:ping:failed", [act []])] []
  ext := []

def features : Chart := featuresDoc.toChart

#guard featuresDoc.check.isEmpty

open Trace in
/-- Fixed scenarios: each one checks a specific behaviour. -/
def golden : List (String × Doc × Option Snapshot × List In) := [
  ("pizza_order_success", pizzaDoc, none, [
    .ev "btn:order", .ev "btn:pizza", .ev "btn:size", .ev "btn:add",
    .ev "btn:checkout" [("cart_not_empty", true)],
    .ev "msg:text" [("in_delivery_zone", true)],
    .ev "payment:success", .outcomeNow "create_order" "created"]),
  ("pizza_order_rejected_address", pizzaDoc, none, [
    .ev "btn:order", .ev "btn:cart",
    .ev "btn:checkout" [("cart_not_empty", true)],
    .ev "msg:text" [("in_delivery_zone", true)],
    .ev "payment:success", .outcomeNow "create_order" "rejected" [("address_rejected", true)]]),
  ("pizza_order_failed_retry", pizzaDoc, none, [
    .ev "btn:order", .ev "btn:cart",
    .ev "btn:checkout" [("cart_not_empty", true)],
    .ev "msg:text" [("in_delivery_zone", true)],
    .ev "payment:success", .outcomeNow "create_order" "failed", .ev "btn:retry",
    .outcomeNow "create_order" "created"]),
  -- the user leaves "placing" while create_order is waited for and orders again: the new call
  -- replaces the wait (only the latest call is answered), so the outcome of the first is ignored
  ("pizza_order_again_while_waiting", pizzaDoc, none, [
    .ev "btn:order", .ev "btn:cart",
    .ev "btn:checkout" [("cart_not_empty", true)],
    .ev "msg:text" [("in_delivery_zone", true)],
    .ev "payment:success", .ev "btn:cancel",
    .ev "btn:order", .ev "btn:cart",
    .ev "btn:checkout" [("cart_not_empty", true)],
    .ev "msg:text" [("in_delivery_zone", true)],
    .ev "payment:success", .outcomeTok "create_order" "created" 6,
    .outcomeNow "create_order" "created"]),
  ("pizza_empty_cart_and_pickup", pizzaDoc, none, [
    .ev "btn:order", .ev "btn:cart",
    .ev "btn:checkout" [("cart_not_empty", false)],
    .ev "btn:cart",
    .ev "btn:checkout" [("cart_not_empty", true)],
    .ev "msg:text" [("in_delivery_zone", false)]]),
  ("guard_true", featuresDoc, none, [.ev "btn:go" [("is_vip", true)]]),
  ("guard_false", featuresDoc, none, [.ev "btn:go" [("is_vip", false)]]),
  ("bubble_after_false_local", featuresDoc, none, [
    .ev "btn:go" [("is_vip", false)], .ev "btn:x" [("local_ok", false)]]),
  ("local_true_no_bubble", featuresDoc, none, [
    .ev "btn:go" [("is_vip", false)], .ev "btn:x" [("local_ok", true)]]),
  ("nested_resume", featuresDoc, none, [
    .ev "btn:go" [("is_vip", false)], .ev "btn:next", .ev "btn:other",
    .ev "cmd:help", .ev "btn:back"]),
  ("stale_resume_value", featuresDoc,
    some ⟨"help", [("shop", "deleted_message")], [], 7, none, []⟩, [.ev "btn:back"]),
  ("self_loop_restarts_timer", featuresDoc, none, [.ev "btn:self", .ev "btn:self"]),
  ("timer_tokens", featuresDoc, none, [
    .ev "btn:go" [("is_vip", false)], .ev "btn:next",
    .ev "btn:other", .ev "btn:back_item",
    .timerStale "remind" 4, .timerNow "remind"]),
  ("buy_action_order", featuresDoc, none, [
    .ev "btn:go" [("is_vip", false)], .ev "btn:next",
    .ev "btn:buy" [("in_stock", true)]]),
  ("recover_deleted_current", featuresDoc,
    some ⟨"deleted_message", [("shop", "item")], [("old_timer", 3)], 9, none, []⟩,
    [.ev "btn:go" [("is_vip", true)]]),
  -- `start` with a first event: as if the user stood on the initial message
  ("start_event_into_initial", pizzaDoc, none, [.start "cmd:start", .ev "btn:order"]),
  ("start_event_deep", pizzaDoc, none, [.start "btn:order", .ev "btn:pizza"]),
  ("start_event_guard_true", featuresDoc, none, [.start "btn:go" [("is_vip", true)], .ev "cmd:start"]),
  ("start_event_guard_false", featuresDoc, none, [.start "btn:go" [("is_vip", false)], .ev "btn:next"]),
  ("start_event_root_arrow", featuresDoc, none, [.start "cmd:help", .ev "btn:back"]),
  ("start_event_unmatched", featuresDoc, none, [.start "btn:nothing", .ev "btn:self"]),
  -- "back to the previous section"
  ("back_toggle", featuresDoc, none, [
    .ev "btn:go" [("is_vip", false)], .ev "btn:next", .ev "btn:other", .ev "btn:prev", .ev "btn:prev"]),
  ("back_from_help", featuresDoc, none, [
    .ev "btn:go" [("is_vip", false)], .ev "btn:next", .ev "cmd:help", .ev "btn:prev"]),
  ("back_same_section", featuresDoc, none, [.ev "cmd:help", .ev "btn:prev"]),
  ("back_fallback", featuresDoc, none, [.ev "btn:prev"]),
  ("back_stale_place", featuresDoc, some ⟨"help", [], [], 7, some "deleted_message", []⟩, [.ev "btn:prev"]),
  ("back_resume_fallback", featuresDoc, some ⟨"paid", [("shop", "item")], [], 4, none, []⟩,
    [.ev "btn:back"]),
  ("back_after_start_event", featuresDoc, none, [.start "btn:go" [("is_vip", false)], .ev "btn:prev"]),
  ("back_recovery_kept", featuresDoc, some ⟨"deleted_message", [], [], 3, some "catalog", []⟩,
    [.ev "btn:prev"]),
  -- arrows without a target, background actions, outcomes
  ("stay_keeps_message", featuresDoc, none, [.ev "btn:log", .ev "btn:log"]),
  ("stay_consumes_timer", featuresDoc, none, [
    .ev "btn:go" [("is_vip", true)], .timerNow "nudge", .timerStale "nudge" 2]),
  ("outcome_on_waiting_message", featuresDoc, none, [
    .ev "btn:go" [("is_vip", false)], .ev "btn:next", .ev "btn:check",
    .outcomeNow "check_stock" "yes"]),
  ("outcome_latest_only", featuresDoc, none, [
    .ev "btn:go" [("is_vip", false)], .ev "btn:next", .ev "btn:check", .ev "btn:check",
    .outcomeTok "check_stock" "no" 4, .outcomeNow "check_stock" "no"]),
  ("outcome_after_leaving", featuresDoc, none, [
    .ev "btn:go" [("is_vip", false)], .ev "btn:next", .ev "btn:check", .ev "btn:other",
    .outcomeNow "check_stock" "yes", .outcomeTok "check_stock" "yes" 4]),
  ("outcome_failed_stays", featuresDoc, none, [
    .ev "btn:go" [("is_vip", false)], .ev "btn:next", .ev "btn:check",
    .outcomeNow "check_stock" "failed"]),
  ("outcome_every_anywhere", featuresDoc, none, [
    .ev "btn:ping", .ev "btn:go" [("is_vip", false)], .ev "btn:ping",
    .outcomeTok "ping" "pong" 2, .outcomeTok "ping" "pong" 4, .outcomeTok "ping" "pong" 2]),
  ("outcome_recovery", featuresDoc, some ⟨"deleted_message", [], [], 6, none, [("ping", 6), ("check_stock", 5)]⟩, [
    .outcomeTok "ping" "pong" 6, .outcomeTok "check_stock" "yes" 5]),
  ("outcome_recovery_stale", featuresDoc, some ⟨"deleted_message", [], [], 6, none, [("ping", 6)]⟩, [
    .outcomeTok "ping" "pong" 5])
]

def runGolden (name : String) : Option (Snapshot × List (List Action)) :=
  (golden.find? (·.1 == name)).map fun (_, d, f, ins) =>
    let (_, s, acts) := Trace.scripted d.toChart name f ins
    (s, acts)

def finalAt (name : String) : String := ((runGolden name).map (·.1.state)).getD "?"
def finalBack (name : String) : Option NodeId := ((runGolden name).map (·.1.back)).getD (some "?")
def allActs (name : String) : List Action := ((runGolden name).map (·.2.flatten)).getD []

-- a successful order reaches the end and calls create_order
#guard finalAt "pizza_order_success" == "done"
#guard (allActs "pizza_order_success").any fun | .call "create_order" false .latest _ => true | _ => false
-- the kitchen is told in the background once the order is created
#guard (allActs "pizza_order_success").any fun | .call "notify_kitchen" true .free _ => true | _ => false
-- both sides of the conditions
#guard finalAt "guard_true" == "vip"
#guard finalAt "guard_false" == "catalog"
-- a false local branch bubbles up to the section
#guard finalAt "bubble_after_false_local" == "reminder"
#guard finalAt "local_true_no_bubble" == "item2"
-- "resume" into a nested section
#guard finalAt "nested_resume" == "item2"
-- a stale remembered place leads to the section's initial message
#guard finalAt "stale_resume_value" == "catalog"
-- a stale token is ignored, the current one fires
#guard finalAt "timer_tokens" == "reminder"
-- a deleted current message: the bot starts over, the old timers are cancelled first and the
-- event is processed as at the initial message (`btn:go` with `is_vip`: straight to `vip`)
#guard finalAt "recover_deleted_current" == "vip"
#guard allActs "recover_deleted_current" == [.cancelTimer "old_timer", .startTimer "nudge" 30000 10, .send "vip"]
-- recovery with an event without an arrow, or with a timer event: the plain start over
#guard (features.step ⟨"gone", [("shop", "item")], [("t", 3)], 9, none, []⟩ (.named "btn:nothing") (fun _ => true)).2 ==
  [.cancelTimer "t", .startTimer "idle" 60000 10, .send "home"]
#guard (features.step ⟨"gone", [], [("t", 3)], 9, none, []⟩ (.timer "t" 3) (fun _ => true)).2 ==
  [.cancelTimer "t", .startTimer "idle" 60000 10, .send "home"]
-- action order: exit → the arrow's actions → entry
#guard ((runGolden "buy_action_order").map (·.2.getLast!)) ==
  some [.cancelTimer "remind", .call "charge" false .free 4, .call "notify" false .free 4, .send "paid"]
-- `start` with a first event
#guard finalAt "start_event_deep" == "pick_size"
#guard finalAt "start_event_guard_true" == "home"
#guard finalAt "start_event_root_arrow" == "catalog"
-- a variant into the initial message enters it once: exactly `start`
#guard pizza.startWith 0 (.named "cmd:start") (fun _ => false) == pizza.start
-- the initial message is not entered: no `send` of it, its timers are not started
#guard (pizza.startWith 0 (.named "btn:order") (fun _ => false)).2 == [.send "catalog"]
#guard (features.startWith 0 (.named "btn:go") (fun _ => true)).2 == [.startTimer "nudge" 30000 1, .send "vip"]
-- no matching variant, or a timer event: the plain first entry
#guard features.startWith 0 (.named "btn:nothing") (fun _ => true) == features.start
#guard features.startWith 0 (.timer "idle" 1) (fun _ => true) == features.start
-- the counter continues from the high-water mark
#guard (features.startWith 41 (.named "btn:go") (fun _ => true)).1.seq == 42
-- "back": a move into another section remembers the message left, a move inside a section
-- keeps the place, "back" leads there and back again
#guard (features.step ⟨"home", [], [], 1, none, []⟩ (.named "btn:go") (fun _ => false)).1.back == some "home"
#guard finalAt "back_toggle" == "item2" && finalBack "back_toggle" == some "catalog"
#guard (runGolden "back_toggle").map (·.2.getLast!) == some [.send "item2"]
-- "back" from a message of another section returns to the message the user left
#guard finalAt "back_from_help" == "item" && finalBack "back_from_help" == some "help"
-- `home` and `help` are both directly in the root section: no boundary is crossed, so "back"
-- has no place and leads to its fallback
#guard finalAt "back_same_section" == "home" && finalBack "back_same_section" == none
-- nothing to go back to yet, or the place is gone: the fallback `to`
#guard finalAt "back_fallback" == "home" && finalBack "back_fallback" == none
#guard finalAt "back_stale_place" == "home"
#guard finalAt "back_resume_fallback" == "item"
-- entering the bot with an event leaves no message; recovery keeps the place
#guard finalAt "back_after_start_event" == "home"
#guard finalAt "back_recovery_kept" == "catalog"
-- pizza: help is in a section of its own, so "Back" returns to wherever help was opened
#guard (pizza.step (pizza.step ⟨"done", [("order", "pay")], [], 6, some "pay", []⟩ (.named "cmd:help")
  (fun _ => false)).1 (.named "btn:back") (fun _ => false)).1.state == "done"
#guard (pizza.step (pizza.step ⟨"menu", [("order", "cart")], [], 6, some "cart", []⟩ (.named "cmd:help")
  (fun _ => false)).1 (.named "btn:back") (fun _ => false)).1.state == "menu"
-- recovery can leave the place in the user's own section, even the current message: "back"
-- then does not return but repeats that message (docs/format.md, section 7, decision 10)
#guard (features.step ⟨"gone", [], [], 3, some "item2", []⟩ (.named "btn:prev") (fun _ => false)).1 ==
  ⟨"item2", [("shop", "item2")], [], 4, some "item2", []⟩
#guard features.step ⟨"item2", [("shop", "item2")], [], 4, some "item2", []⟩ (.named "btn:prev") (fun _ => false) ==
  (⟨"item2", [("shop", "item2")], [], 5, some "item2", []⟩, [.send "item2"])
-- a self-loop restarts the timer with a new token
#guard ((runGolden "self_loop_restarts_timer").map (·.2.getLast!)) ==
  some [.cancelTimer "idle", .startTimer "idle" 60000 3, .send "home"]

-- an action with outcomes: the user waits on `placing`, the root handles each outcome
#guard finalAt "pizza_order_rejected_address" == "address"
#guard finalAt "pizza_order_failed_retry" == "done"
-- ordering again while waiting: the first outcome (token 6) is ignored (the new call replaced
-- its wait), the second (token 12) is accepted
#guard ((runGolden "pizza_order_again_while_waiting").map fun r =>
    (r.1.state, r.1.pending, r.2.reverse.getD 1 [])) == some ("done", [], [])
#guard ((runGolden "pizza_order_success").map fun r => r.1.pending) == some []
-- an arrow without a target: no exit, no entry, the timers are not restarted, `seq` grows
#guard ((runGolden "stay_keeps_message").map fun r => (r.1.state, r.1.seq, r.1.timers, r.2.getLast!)) ==
  some ("home", 3, [("idle", 1)], [.call "log" true .free 3])
-- a timer firing handled without a move is consumed: a redelivery is not accepted
#guard ((runGolden "stay_consumes_timer").map fun r => (r.1.timers, r.2.getLast!)) == some ([], [])
-- only the latest call is answered: the outcome of the earlier one is ignored
#guard ((runGolden "outcome_latest_only").map fun r => (r.1.state, r.2.reverse.getD 1 [])) ==
  some ("catalog", [])
-- an outcome after leaving the waiting message: accepted (the wait is removed), no arrow there
#guard ((runGolden "outcome_after_leaving").map fun r => (r.1.state, r.1.pending)) == some ("item2", [])
-- `failed` handled without a move keeps the message and its timer
#guard ((runGolden "outcome_failed_stays").map fun r => (r.1.state, r.1.timers)) ==
  some ("item", [("remind", 3)])
-- every call is answered once, wherever the user is (the arrows of the root section)
#guard ((runGolden "outcome_every_anywhere").map fun r => r.2.reverse.take 3) ==
  some [[], [.call "log" true .free 6], [.call "log" true .free 5]]
-- recovery: an accepted outcome is matched at the initial message (an arrow without a target
-- leads there), an outcome without a wait is the plain start over and keeps the waits
#guard ((runGolden "outcome_recovery").map fun r => r.2.head!) ==
  some [.call "log" true .free 7, .startTimer "idle" 60000 7, .send "home"]
#guard ((runGolden "outcome_recovery_stale").map fun r => (r.1.pending, r.2.head!)) ==
  some ([("ping", 6)], [.startTimer "idle" 60000 7, .send "home"])
-- the latest call replaces the wait, `every` keeps each wait
#guard (features.step ⟨"item", [], [], 4, none, [("check_stock", 4)]⟩ (.named "btn:check") (fun _ => false)).1.pending ==
  [("check_stock", 5)]
#guard (features.step ⟨"home", [], [], 4, none, [("ping", 2)]⟩ (.named "btn:ping") (fun _ => false)).1.pending ==
  [("ping", 5), ("ping", 2)]
-- a move keeps the waits
#guard (features.step ⟨"home", [], [], 4, none, [("ping", 2)]⟩ (.named "btn:go") (fun _ => false)).1.pending ==
  [("ping", 2)]

-- all example documents and random documents survive the text round trip
def textRoundTrip (d : Doc) : Bool :=
  match parse (print (encode d)) >>= decode with
  | .ok d' => print (encode d') == print (encode d)
  | .error _ => false

#guard textRoundTrip pizzaDoc && textRoundTrip featuresDoc
#guard (List.range 40).all fun i => let d := RandomDoc.doc i 10; d.check.isEmpty && textRoundTrip d

-- a duplicate key is caught by the check before export
#guard !({ pizzaDoc with conditions := [("same", "one"), ("same", "two")] }).check.isEmpty

-- empty names are rejected
#guard match parse "{\"figram\": 1, \"bot\": {\"initial\": \"a\", \"children\": {\"a\": {\"on\": {\"\": [{\"to\": \"\"}]}}}}}" >>= decode with
  | .ok d => !d.check.isEmpty
  | .error _ => true

-- a huge exponent is rejected at once
#guard match parse "{\"x\": 1e1000000000}" with
  | .ok _ => false
  | .error _ => true

-- an annotation key (`x-…`) is read and kept, the behaviour does not change
#guard match parse "{\"figram\": 1, \"bot\": {\"initial\": \"a\", \"children\": {\"a\": {\"x-ui\": {\"x\": 10}}}}, \"x-author\": \"Zoë\"}" >>= decode with
  | .ok d => d.ext.length == 1 && d.check.isEmpty
  | .error _ => false

-- a key without the `x-` prefix (a misspelt `if`, `timers`, `bot`) is kept by the decoder,
-- so `fmt` still round-trips it, but the check rejects the document
#guard match parse "{\"figram\": 1, \"Bot\": 1, \"bot\": {\"initial\": \"a\", \"children\": {\"a\": {\"tiemrs\": {\"t\": 5}, \"on\": {\"go\": [{\"iff\": \"c\", \"to\": \"a\"}]}}}}}" >>= decode with
  | .ok d => textRoundTrip d
      && d.check.contains "document: unknown key `Bot` (annotation keys start with `x-`)"
      && d.check.contains "node `a`: unknown key `tiemrs` (annotation keys start with `x-`)"
      && d.check.contains "`a` → `go`: unknown key `iff` (annotation keys start with `x-`)"
  | .error _ => false

-- V3 holds for the fallback of a "back" variant as well: `resume` into a message and a missing
-- `to` are rejected with the errors they get without `back` (the final well-formedness check
-- alone accepts the first one)
#guard match parse "{\"figram\": 1, \"bot\": {\"initial\": \"a\", \"children\": {\"a\": {\"on\": {\"btn:b\": [{\"to\": \"c\", \"resume\": true, \"back\": true}]}}, \"c\": {}}}}" >>= decode with
  | .ok d => d.check.contains "arrow `a` → `c` with `resume`: not a section"
  | .error _ => false
#guard match parse "{\"figram\": 1, \"bot\": {\"initial\": \"a\", \"children\": {\"a\": {\"on\": {\"btn:b\": [{\"to\": \"nope\", \"back\": true}]}}, \"c\": {}}}}" >>= decode with
  | .ok d => d.check.contains "arrow `a` → `nope`: target does not exist"
  | .error _ => false

-- a document with an unknown extension is rejected as a whole
#guard match parse "{\"figram\": 1, \"requires\": [\"parallel\"], \"bot\": {\"initial\": \"a\", \"children\": {\"a\": {}}}}" >>= decode with
  | .ok _ => false
  | .error _ => true

-- a duplicate key is rejected by the parser
#guard match parse "{\"a\": 1, \"a\": 2}" with
  | .ok _ => false
  | .error _ => true

-- actions: the text form, the object form with properties, and their errors
#guard match parse "{\"figram\": 1, \"calls\": {\"a\": {\"text\": \"A\"}}, \"bot\": {\"initial\": \"m\", \"children\": {\"m\": {}}}}" >>= decode with
  | .ok d => renderCompact (encode d) == "{\"figram\":1,\"calls\":{\"a\":\"A\"},\"bot\":{\"initial\":\"m\",\"children\":{\"m\":{}}}}"
  | .error _ => false
#guard match parse "{\"figram\": 1, \"calls\": {\"a\": {\"text\": \"A\", \"outcomes\": [\"ok\", \"failed\", \"x:y\", \"ok\"], \"x-ui\": 1, \"oops\": 2}, \"b\": {\"text\": \"B\", \"every\": true}}, \"bot\": {\"initial\": \"m\", \"children\": {\"m\": {}}}}" >>= decode with
  | .ok d => textRoundTrip d
      && d.check.contains "action `a`: `failed` is an outcome of every action with outcomes and is not listed"
      && d.check.contains "action `a`: outcome `x:y` contains `:`"
      && d.check.contains "action `a`: duplicate outcome `ok`"
      && d.check.contains "action `a`: unknown key `oops` (annotation keys start with `x-`)"
      && d.check.contains "action `b`: `every` needs `outcomes`"
  | .error _ => false
-- an outcome event must name an action with that outcome; an action with outcomes occurs at
-- most once in a variant; `resume` and `back` need `to`
#guard match parse "{\"figram\": 1, \"calls\": {\"a\": {\"text\": \"A\", \"outcomes\": [\"ok\"]}, \"p\": \"P\"}, \"bot\": {\"initial\": \"m\", \"children\": {\"m\": {\"on\": {\"call:a:ok\": [{}], \"call:a:failed\": [{}], \"call:a:no\": [{}], \"call:p:ok\": [{}], \"call:zz\": [{}], \"btn:x\": [{\"do\": [\"a\", \"a\"]}], \"btn:y\": [{\"resume\": true}]}}}}}" >>= decode with
  | .ok d => d.check.contains "event `call:a:no`: `a` is not an action with the outcome `no` (the prefix `call:` is reserved)"
      && d.check.contains "event `call:p:ok`: `p` is not an action with the outcome `ok` (the prefix `call:` is reserved)"
      && d.check.contains "event `call:zz`: an outcome event is `call:<action>:<outcome>` (the prefix `call:` is reserved)"
      && d.check.contains "`m` → `btn:x`: action `a` with outcomes occurs twice in `do`"
      && d.check.contains "`m` → `btn:y`: `resume` and `back` need `to`"
      && !(d.check.any (·.startsWith "event `call:a:ok`")) && !(d.check.any (·.startsWith "event `call:a:failed`"))
  | .error _ => false
-- the count is per action, whatever its kind of wait (`every`) or a background action with
-- outcomes; an action without outcomes may occur twice
#guard match parse "{\"figram\": 1, \"calls\": {\"e\": {\"text\": \"E\", \"outcomes\": [\"ok\"], \"every\": true}, \"d\": {\"text\": \"D\", \"outcomes\": [\"ok\"], \"detached\": true}, \"p\": \"P\"}, \"bot\": {\"initial\": \"m\", \"children\": {\"m\": {\"on\": {\"btn:e\": [{\"do\": [\"e\", \"p\", \"e\"]}], \"btn:d\": [{\"do\": [\"d\", \"p\", \"d\"]}], \"btn:p\": [{\"do\": [\"p\", \"p\", \"e\", \"d\"]}]}}}}}" >>= decode with
  | .ok d => d.check.contains "`m` → `btn:e`: action `e` with outcomes occurs twice in `do`"
      && d.check.contains "`m` → `btn:d`: action `d` with outcomes occurs twice in `do`"
      && !(d.check.any (·.startsWith "`m` → `btn:p`")) && d.check.length == 2
  | .error _ => false

#guard pizza.compile.wf && features.compile.wf

end Figram.Examples
