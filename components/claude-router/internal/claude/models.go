package claude

import (
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"net/http"
	"sort"
	"strings"
	"time"

	"github.com/router-for-me/CLIProxyAPI/v8/sdk/cliproxy"
	coreauth "github.com/router-for-me/CLIProxyAPI/v8/sdk/cliproxy/auth"
)

// catalogURLs are the model catalog that the pinned SDK's own server fetches
// (internal/registry/model_updater.go). The router reads only its claude
// section, once per start.
var catalogURLs = []string{
	"https://raw.githubusercontent.com/router-for-me/models/refs/heads/main/models.json",
	"https://models.router-for.me/models.json",
}

const (
	catalogTimeout  = 5 * time.Second
	catalogMaxBytes = 8 << 20
)

func fetchCatalog(ctx context.Context) ([]byte, error) {
	ctx, cancel := context.WithTimeout(ctx, catalogTimeout)
	defer cancel()
	var last error
	for _, url := range catalogURLs {
		req, err := http.NewRequestWithContext(ctx, http.MethodGet, url, nil)
		if err != nil {
			return nil, err
		}
		resp, err := http.DefaultClient.Do(req)
		if err != nil {
			last = err
			continue
		}
		data, err := io.ReadAll(io.LimitReader(resp.Body, catalogMaxBytes))
		resp.Body.Close()
		if resp.StatusCode != http.StatusOK {
			last = fmt.Errorf("%s answered %d", url, resp.StatusCode)
			continue
		}
		if err != nil {
			last = err
			continue
		}
		return data, nil
	}
	return nil, last
}

func catalogClaudeModels(data []byte) ([]*cliproxy.ModelInfo, error) {
	var doc struct {
		Claude []*cliproxy.ModelInfo `json:"claude"`
	}
	if err := json.Unmarshal(data, &doc); err != nil {
		return nil, fmt.Errorf("catalog is not JSON of the SDK's format: %w", err)
	}
	seen := map[string]bool{}
	var out []*cliproxy.ModelInfo
	for _, m := range doc.Claude {
		if m == nil || !strings.HasPrefix(m.ID, "claude-") || seen[m.ID] {
			continue
		}
		seen[m.ID] = true
		out = append(out, m)
	}
	if len(out) == 0 {
		return nil, errors.New("catalog lists no claude models")
	}
	return out, nil
}

// registerCatalogModels adds, to each account's registered models, the
// catalog's Claude models that none of them has, and returns their IDs. The
// router runs the SDK without its file watcher, model updater, and
// management routes, the only paths that register an account's models again
// after start, so the additions last until the service stops.
func registerCatalogModels(core *coreauth.Manager, catalog []*cliproxy.ModelInfo, authIDs []string) []string {
	reg := cliproxy.GlobalModelRegistry()
	known := map[string]bool{}
	for _, id := range authIDs {
		for _, m := range reg.GetModelsForClient(id) {
			known[m.ID] = true
		}
	}
	var added []*cliproxy.ModelInfo
	var names []string
	for _, m := range catalog {
		if !known[m.ID] {
			added = append(added, m)
			names = append(names, m.ID)
		}
	}
	if len(added) == 0 {
		return nil
	}
	for _, id := range authIDs {
		reg.RegisterClient(id, "claude", append(reg.GetModelsForClient(id), added...))
		core.RefreshSchedulerEntry(id)
	}
	sort.Strings(names)
	return names
}
