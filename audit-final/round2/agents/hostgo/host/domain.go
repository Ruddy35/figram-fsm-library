package main

// Domain of the three bots: which actions are effects in the host's own database (applied with
// the action's key under a unique index, exactly once, in the transaction that marks the action
// executed), which go to simulated external systems, how conditions are computed.

import (
	"strings"
)

// Own-database actions and their effect on the user's data (kv). Conditions read kv, so these
// actions are the ones the per-user barrier protects.
var ownDB = map[string]bool{
	"add_to_cart": true, "clear_cart": true, "save_address": true, "count_sent": true, "note_late": true,
	"credit": true, "debit": true, "note_fail": true, "count_audit": true,
	"release": true, "charge": true, "topup": true, "count_ping": true,
}

// External actions → simulated system.
var extSystem = map[string]string{
	"create_order": "crm", "award": "loyalty", "reserve": "booking",
	"notify": "notify", "nudge": "notify", "log_idle": "notify", "alert_ops": "notify", "apologize": "notify",
	"audit": "audit", "log_ping": "audit",
}

func applyEffect(kv map[string]int64, action string, data map[string]any) {
	switch action {
	case "add_to_cart":
		kv["cart"]++
	case "clear_cart":
		kv["cart"] = 0
		kv["cartgen"]++
	case "save_address":
		kv["addr"]++
	case "count_sent":
		kv["sent"]++
	case "note_late":
		kv["late"]++
	case "credit":
		amt := int64(1)
		if a, ok := data["amount"]; ok {
			switch x := a.(type) {
			case float64:
				amt = int64(x)
			case int64:
				amt = x
			case int:
				amt = int64(x)
			default:
				if n, ok := a.(interface{ Int64() (int64, error) }); ok {
					amt, _ = n.Int64()
				}
			}
		}
		kv["points"] += amt
	case "debit":
		kv["points"] -= 10
	case "note_fail":
		kv["fails"]++
	case "count_audit":
		kv["audits"]++
	case "release":
		kv["released"]++
	case "charge":
		kv["credit"]--
	case "topup":
		kv["credit"]++
	case "count_ping":
		kv["pings"]++
	}
}

// guardValue computes a condition from the user's data and the data of the event.
func guardValue(name string, kv map[string]int64, ev map[string]any) bool {
	switch name {
	case "cart_not_empty":
		return kv["cart"] > 0
	case "in_zone":
		t, _ := ev["text"].(string)
		return strings.HasPrefix(t, "zone")
	case "address_rejected":
		r, _ := ev["reason"].(string)
		return r == "address"
	case "enough_points":
		return kv["points"] >= 10
	case "has_credit":
		return kv["credit"] > 0
	}
	panic("unknown condition " + name)
}

// botOf: the document of a user (user ids are "<bot>-<n>").
func botOf(user string) string {
	b, _, _ := strings.Cut(user, "-")
	return b
}
