package main

import (
	"encoding/json"
	"errors"
	"fmt"
	"os"
	"path/filepath"

	"github.com/syanga/astack/components/claude-router/internal/claude"
)

type installation struct {
	State      string `json:"-"`
	Manager    string `json:"manager"`
	Name       string `json:"name,omitempty"`
	Descriptor string `json:"descriptor,omitempty"`
	Config     string `json:"config"`
	Helper     string `json:"helper,omitempty"`
	Python     string `json:"python,omitempty"`
	Manifest   string `json:"manifest,omitempty"`
}

const serviceRecord = "service.json"

func defaultState() string {
	if x := os.Getenv("XDG_STATE_HOME"); x != "" && filepath.IsAbs(x) {
		return filepath.Join(x, "astack", "claude-router")
	}
	home, err := os.UserHomeDir()
	if err != nil {
		return ""
	}
	return filepath.Join(home, ".local", "state", "astack", "claude-router")
}

func loadInstallation(state string) (installation, error) {
	if state == "" || !filepath.IsAbs(state) {
		return installation{}, errors.New("need an absolute -state directory")
	}
	in := installation{State: state, Manager: "foreground", Config: filepath.Join(state, "config.json")}
	data, err := os.ReadFile(filepath.Join(state, serviceRecord))
	if errors.Is(err, os.ErrNotExist) {
		return in, nil
	}
	if err != nil {
		return in, err
	}
	if err := json.Unmarshal(data, &in); err != nil {
		return in, fmt.Errorf("%s: %w", filepath.Join(state, serviceRecord), err)
	}
	in.State = state
	switch in.Manager {
	case "launchd", "systemd":
		if in.Name == "" || in.Descriptor == "" {
			return in, fmt.Errorf("%s: %s needs a name and a descriptor", serviceRecord, in.Manager)
		}
	case "foreground":
	default:
		return in, fmt.Errorf("%s: unknown manager %q", serviceRecord, in.Manager)
	}
	return in, nil
}

func (in installation) config() (claude.Config, error) {
	return claude.LoadConfig(in.Config)
}

func (in installation) supervisionDir() string {
	return filepath.Dir(in.Config)
}
