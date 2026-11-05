//go:build figram_system

package figram

// With the build tag figram_system the library is taken from pkg-config (figram.pc of the
// library archive, or of a build from source) instead of lib/linux_amd64, e.g.
// PKG_CONFIG_PATH=/opt/libfigram-0.2.0-linux-x86_64/lib/pkgconfig go build -tags figram_system

// #cgo pkg-config: figram
import "C"
