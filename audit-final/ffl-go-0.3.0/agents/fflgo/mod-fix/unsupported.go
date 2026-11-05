//go:build !(linux && amd64) && !figram_system

package figram

// The bundled libraries are for linux/amd64. On another platform build libfigram from source
// (README, "Other platforms") and use the build tag figram_system.
var _ = figram_bundled_libraries_are_linux_amd64_only_use_tag_figram_system
