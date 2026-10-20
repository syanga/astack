package claude

import (
	"crypto/sha256"
	"encoding/hex"
	"io"
	"os"
	"runtime/debug"
	"strings"
)

const sdkModule = "github.com/router-for-me/CLIProxyAPI/v8"

// linkedRevision is the source revision the astack installer passes to the
// linker with -X, because Go records no VCS revision for a build inside a
// linked Git worktree.
var linkedRevision string

// linkedCodeDigest is the router code digest the astack installer passes to
// the linker with -X. An acceptance record matches the binary by this digest.
var linkedCodeDigest string

func codeDigest() string {
	if linkedCodeDigest == "" {
		return "unknown"
	}
	return linkedCodeDigest
}

var buildRevision, sdkVersion = readBuildInfo()

func readBuildInfo() (string, string) {
	revision, sdk := "unknown", "unknown"
	if linkedRevision != "" {
		revision = linkedRevision
	}
	info, ok := debug.ReadBuildInfo()
	if !ok {
		return revision, sdk
	}
	modified := false
	for _, s := range info.Settings {
		switch s.Key {
		case "vcs.revision":
			if linkedRevision == "" {
				revision = s.Value[:min(7, len(s.Value))]
			}
		case "vcs.modified":
			modified = s.Value == "true" && linkedRevision == ""
		}
	}
	if modified && revision != "unknown" {
		revision += "+dirty"
	}
	return revision, sdkVersionOf(info.Deps)
}

func sdkVersionOf(deps []*debug.Module) string {
	for _, d := range deps {
		if d.Path != sdkModule {
			continue
		}
		if r := d.Replace; r != nil {
			return strings.TrimSpace(d.Version + " replaced by " + r.Path + " " + r.Version)
		}
		return d.Version
	}
	return "unknown"
}

// ExecutableDigest returns the hex SHA-256 of the running executable's file,
// or "unknown".
func ExecutableDigest() string {
	path, err := os.Executable()
	if err != nil {
		return "unknown"
	}
	f, err := os.Open(path)
	if err != nil {
		return "unknown"
	}
	defer f.Close()
	h := sha256.New()
	if _, err := io.Copy(h, f); err != nil {
		return "unknown"
	}
	return hex.EncodeToString(h.Sum(nil))
}
