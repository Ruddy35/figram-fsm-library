package refwrap

// Audit additions (not part of the reference wrapper): access to the reference validator and to
// the reply-corruption hook, for the host's own raw-path checks and negative controls.

// CheckReply exposes the reference validator checkReply of clients/go.
func CheckReply(r map[string]any, id int64, op string) (string, error) { return checkReply(r, id, op) }

// SetCorrupt installs one of the reference corruptions (clients/go -corrupt) or a custom one.
func SetCorrupt(f func(map[string]any)) { corrupt = f }

// Corruption returns a named reference corruption.
func Corruption(name string) func(map[string]any) { return corruptions[name] }
