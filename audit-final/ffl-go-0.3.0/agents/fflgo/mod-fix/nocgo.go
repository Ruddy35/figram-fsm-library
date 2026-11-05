//go:build !cgo

package figram

// The engine is a C library: the package needs cgo (CGO_ENABLED=1 and a C compiler).
var _ = figram_needs_cgo_set_CGO_ENABLED_1
