package claude

import "runtime/debug"

const sdkModule = "github.com/router-for-me/CLIProxyAPI/v8"

// linkedRevision is the source revision the astack installer passes to the
// linker with -X, because Go records no VCS revision for a build inside a
// linked Git worktree.
var linkedRevision string

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
	for _, d := range info.Deps {
		if d.Path == sdkModule {
			sdk = d.Version
		}
	}
	return revision, sdk
}
