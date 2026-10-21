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
* `/help` from anywhere and "Back" to where the user left off;
* a reminder on the cart;
* conditions as text: "the cart is not empty", "the address is in the delivery zone".
-/

namespace Figram.Examples

open Figram Figram.Format

private def v (to : NodeId) (calls : List String := []) : Variant := ⟨none, to, false, calls, []⟩
private def vIf (c : String) (to : NodeId) (calls : List String := []) : Variant :=
  ⟨some c, to, false, calls, []⟩
private def back (s : NodeId) : Variant := ⟨none, s, true, [], []⟩
private def msg (on : List (EventId × List Variant)) : NodeDoc := .message on [] []

/-- The pizza shop bot in the document format. -/
def pizzaDoc : Doc where
  requires := []
  conditions := [
    ("cart_not_empty", "The cart has at least one pizza"),
    ("in_delivery_zone", "The address is in the delivery zone")]
  calls := [
    ("clear_cart", "Clear the cart"),
    ("save_size", "Save the chosen size"),
    ("add_to_cart", "Add the pizza to the cart"),
    ("save_address", "Save the address from the message"),
    ("create_order", "Create the order in the CRM")]
  variables := [
    ("cart", "Cart contents"),
    ("address", "Delivery address")]
  bot := .sec "menu" [
      ("menu", msg [("btn:order", [v "order"])]),
      ("help", msg [("btn:back", [back "order"])]),
      ("done", msg []),
      ("reminder", msg [("btn:back", [back "order"])]),
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
          ("pay", msg [("payment:success", [v "done" ["create_order"]])]),
          ("pickup", msg [])
        ] [("btn:cancel", [v "menu" ["clear_cart"]])] [])
    ] [("cmd:start", [v "menu"]), ("cmd:help", [v "help"])] []
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
  calls := [("charge", "Charge the payment"), ("notify", "Notify the manager")]
  variables := []
  bot := .sec "home" [
      ("home", .message [
          ("btn:go", [vIf "is_vip" "vip", v "shop"]),
          ("btn:self", [v "home"])
        ] [("idle", 60000)] []),
      ("help", msg [("btn:back", [back "shop"])]),
      ("vip", msg []),
      ("paid", msg []),
      ("shop", .sec "catalog" [
          ("catalog", msg [
              ("btn:next", [v "sub"]),
              ("btn:x", [vIf "local_ok" "item2"])]),
          ("sub", .sec "item" [
              ("item", .message [
                  ("btn:buy", [vIf "in_stock" "paid" ["charge", "notify"], v "catalog"]),
                  ("btn:other", [v "item2"]),
                  ("timer:remind", [v "reminder"])
                ] [("remind", 5000)] []),
              ("item2", msg [("btn:back_item", [v "item"])])
            ] [] []),
          ("reminder", msg [("btn:back", [back "shop"])])
        ] [("btn:cancel", [v "home"]), ("btn:x", [v "reminder"])] [])
    ] [("cmd:start", [v "home"]), ("cmd:help", [v "help"])] []
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
    .ev "payment:success"]),
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
    some ⟨"help", [("shop", "deleted_message")], [], 7⟩, [.ev "btn:back"]),
  ("self_loop_restarts_timer", featuresDoc, none, [.ev "btn:self", .ev "btn:self"]),
  ("timer_tokens", featuresDoc, none, [
    .ev "btn:go" [("is_vip", false)], .ev "btn:next",
    .ev "btn:other", .ev "btn:back_item",
    .timerStale "remind" 4, .timerNow "remind"]),
  ("buy_action_order", featuresDoc, none, [
    .ev "btn:go" [("is_vip", false)], .ev "btn:next",
    .ev "btn:buy" [("in_stock", true)]]),
  ("recover_deleted_current", featuresDoc,
    some ⟨"deleted_message", [("shop", "item")], [("old_timer", 3)], 9⟩,
    [.ev "btn:go" [("is_vip", true)]])
]

def runGolden (name : String) : Option (Snapshot × List (List Action)) :=
  (golden.find? (·.1 == name)).map fun (_, d, f, ins) =>
    let (_, s, acts) := Trace.scripted d.toChart name f ins
    (s, acts)

def finalAt (name : String) : String := ((runGolden name).map (·.1.state)).getD "?"
def allActs (name : String) : List Action := ((runGolden name).map (·.2.flatten)).getD []

-- a successful order reaches the end and calls create_order
#guard finalAt "pizza_order_success" == "done"
#guard (allActs "pizza_order_success").contains (.call "create_order")
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
-- a deleted current message: the bot starts over and the old timers are cancelled
#guard finalAt "recover_deleted_current" == "home"
#guard (allActs "recover_deleted_current").take 1 == [.cancelTimer "old_timer"]
-- action order: exit → the arrow's actions → entry
#guard ((runGolden "buy_action_order").map (·.2.getLast!)) ==
  some [.cancelTimer "remind", .call "charge", .call "notify", .send "paid"]
-- a self-loop restarts the timer with a new token
#guard ((runGolden "self_loop_restarts_timer").map (·.2.getLast!)) ==
  some [.cancelTimer "idle", .startTimer "idle" 60000 3, .send "home"]

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

-- an unknown key is read and kept, the behaviour does not change
#guard match parse "{\"figram\": 1, \"bot\": {\"initial\": \"a\", \"children\": {\"a\": {\"x-ui\": {\"x\": 10}}}}, \"x-author\": \"Zoë\"}" >>= decode with
  | .ok d => d.ext.length == 1 && d.check.isEmpty
  | .error _ => false

-- a document with an unknown extension is rejected as a whole
#guard match parse "{\"figram\": 1, \"requires\": [\"parallel\"], \"bot\": {\"initial\": \"a\", \"children\": {\"a\": {}}}}" >>= decode with
  | .ok _ => false
  | .error _ => true

-- a duplicate key is rejected by the parser
#guard match parse "{\"a\": 1, \"a\": 2}" with
  | .ok _ => false
  | .error _ => true

#guard pizza.compile.wf && features.compile.wf

end Figram.Examples
