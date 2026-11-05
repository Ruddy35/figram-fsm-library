//go:build linux && amd64 && !figram_system

package figram

// The static libraries of the Figram release, unchanged (lib/linux_amd64, from
// libfigram-<version>-linux-x86_64.tar.gz; scripts/update-lib.sh). cgo does not accept
// -Wl,--gc-sections here; pass it to drop the unused runtime code:
// go build -ldflags=-extldflags=-Wl,--gc-sections

// #cgo LDFLAGS: -L${SRCDIR}/lib/linux_amd64 -l:libfigram.a -Wl,--start-group -lInit -lleanrt -Wl,--end-group -Wl,-Bstatic -lc++ -lc++abi -luv -lgmp -Wl,-Bdynamic -lm -ldl -lpthread -lrt
import "C"

// The blank import keeps the libraries in `go mod vendor`, which copies only package directories.
import _ "github.com/Ruddy35/ffl-go/lib/linux_amd64"
