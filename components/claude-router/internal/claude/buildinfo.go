package claude

import "runtime/debug"

const sdkModule = "github.com/router-for-me/CLIProxyAPI/v8"

var buildRevision, sdkVersion = readBuildInfo()

func readBuildInfo() (string, string) {
	revision, sdk := "unknown", "unknown"
	info, ok := debug.ReadBuildInfo()
	if !ok {
		return revision, sdk
	}
	modified := false
	for _, s := range info.Settings {
		switch s.Key {
		case "vcs.revision":
			revision = s.Value[:min(7, len(s.Value))]
		case "vcs.modified":
			modified = s.Value == "true"
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
