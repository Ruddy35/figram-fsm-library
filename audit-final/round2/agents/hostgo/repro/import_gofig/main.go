// A Go developer following docs/embedding.md ("To use the wrapper in your own module, pass the
// directory of figram.h and the flags from ffi/out/LDFLAGS in the environment") imports the
// reference wrapper.
package main

import gofig "gofig"

func main() { _, _ = gofig.Load("{}") }
