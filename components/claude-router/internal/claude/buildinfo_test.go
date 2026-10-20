package claude

import (
	"runtime/debug"
	"testing"
)

func TestTheSDKVersionNamesAReplacementSoNoRecordMatchesIt(t *testing.T) {
	pinned := &debug.Module{Path: sdkModule, Version: "v8.0.11"}
	local := &debug.Module{Path: sdkModule, Version: "v8.0.11", Replace: &debug.Module{Path: "../CLIProxyAPI"}}
	other := &debug.Module{Path: sdkModule, Version: "v8.0.11", Replace: &debug.Module{Path: "example.com/fork", Version: "v8.0.12"}}

	got := []string{sdkVersionOf([]*debug.Module{pinned}), sdkVersionOf([]*debug.Module{local}), sdkVersionOf([]*debug.Module{other}), sdkVersionOf(nil)}

	want := []string{"v8.0.11", "v8.0.11 replaced by ../CLIProxyAPI", "v8.0.11 replaced by example.com/fork v8.0.12", "unknown"}
	for i := range want {
		if got[i] != want[i] {
			t.Errorf("case %d: sdk %q, want %q", i, got[i], want[i])
		}
	}
}
