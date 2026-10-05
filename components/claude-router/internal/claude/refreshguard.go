package claude

import (
	"context"
	"crypto/sha256"
	"errors"
	"fmt"
	"net/http"
	"sync"

	coreauth "github.com/router-for-me/CLIProxyAPI/v8/sdk/cliproxy/auth"
)

// spentPerAuth bounds the refresh tokens the guard remembers per credential.
// A credential holds either a token no exchange has sent or the one its
// last exchange sent, so a short history covers it.
const spentPerAuth = 8

// errRefreshDeclined wraps context.Canceled because the SDK records nothing
// for a canceled refresh (conductor_refresh.go:603-606): no LastError, no
// retry backoff.
var errRefreshDeclined = fmt.Errorf("the router declined its refresh: the credential changed or its refresh token was spent: %w", context.Canceled)

// refreshGuard wraps the SDK's Claude executor. The SDK calls Refresh for
// every refresh, its own and the router's, while it holds the credential's
// refresh lock, and hands it a clone of the credential as it is at that
// moment.
type refreshGuard struct {
	coreauth.ProviderExecutor

	mu       sync.Mutex
	exchange func(context.Context, *coreauth.Auth) (*coreauth.Auth, error)
	spent    map[string][]spentToken
}

// spentToken is a refresh token an exchange sent and did not get back.
// failed is set when that exchange returned an error, which includes an
// exchange whose outcome at the token endpoint is unknown. unseen is set
// when the exchange may have run on the executor the guard replaced, so the
// guard did not see how it ended.
type spentToken struct {
	hash   [sha256.Size]byte
	failed bool
	unseen bool
}

type routerRefreshKey struct{}

// withRouterRefresh marks a refresh as the router's, decided on a credential
// whose access token was observed.
func withRouterRefresh(ctx context.Context, observed string) context.Context {
	return context.WithValue(ctx, routerRefreshKey{}, observed)
}

func routerRefreshOf(ctx context.Context) (string, bool) {
	observed, ok := ctx.Value(routerRefreshKey{}).(string)
	return observed, ok
}

// installRefreshGuard registers the guard in place of the SDK's Claude
// executor. An SDK refresh that began before it runs on the old executor, so
// the guard treats the refresh token of a credential with a refresh marker
// as spent and unseen, and of one whose refresh failed as failed.
//
// The SDK's auto-refresh job sets NextRefreshAfter 60 s ahead when it is
// queued and changes it only when the job finishes, so an exchange that
// outlives the 60 s leaves an expired marker that is still set.
func installRefreshGuard(core *coreauth.Manager, authIDs []string) (*refreshGuard, error) {
	inner, ok := core.Executor("claude")
	if !ok {
		return nil, errors.New("the SDK has no claude executor")
	}
	g := &refreshGuard{ProviderExecutor: inner, exchange: inner.Refresh, spent: map[string][]spentToken{}}
	core.RegisterExecutor(g)
	for _, id := range authIDs {
		a, ok := core.GetByID(id)
		if !ok || a == nil {
			continue
		}
		if a.LastError != nil || !a.NextRefreshAfter.IsZero() {
			g.mu.Lock()
			g.spendLocked(id, refreshToken(a), a.LastError != nil)
			g.findLocked(id, refreshToken(a)).unseen = a.LastError == nil
			g.mu.Unlock()
		}
	}
	return g, nil
}

func (g *refreshGuard) Refresh(ctx context.Context, a *coreauth.Auth) (*coreauth.Auth, error) {
	id, rt := a.ID, refreshToken(a)
	g.mu.Lock()
	if observed, ok := routerRefreshOf(ctx); ok {
		if accessToken(a) != observed || g.findLocked(id, rt) != nil {
			g.mu.Unlock()
			return nil, errRefreshDeclined
		}
	}
	g.spendLocked(id, rt, false)
	exchange := g.exchange
	g.mu.Unlock()

	b, err := exchange(ctx, a)

	g.mu.Lock()
	defer g.mu.Unlock()
	switch {
	case err != nil:
		g.spendLocked(id, rt, true)
	case b == nil || refreshToken(b) == rt:
		g.forgetLocked(id, rt)
	}
	return b, err
}

// failed reports whether an exchange of the credential's refresh token
// failed or ended unknown. For an exchange the guard did not see, the SDK's
// failure record on the credential stands in. The SDK sets LastError for a
// failed refresh and for a failed inference request; while the token from
// before the guard is current, the account's reading stays unknown, so no
// inference runs on it and LastError comes from the refresh.
func (g *refreshGuard) failed(a *coreauth.Auth) bool {
	g.mu.Lock()
	defer g.mu.Unlock()
	s := g.findLocked(a.ID, refreshToken(a))
	return s != nil && (s.failed || s.unseen && a.LastError != nil)
}

func (g *refreshGuard) findLocked(id, rt string) *spentToken {
	h := sha256.Sum256([]byte(rt))
	for i := range g.spent[id] {
		if g.spent[id][i].hash == h {
			return &g.spent[id][i]
		}
	}
	return nil
}

func (g *refreshGuard) spendLocked(id, rt string, failed bool) {
	if s := g.findLocked(id, rt); s != nil {
		s.failed = s.failed || failed
		s.unseen = false
		return
	}
	list := append(g.spent[id], spentToken{hash: sha256.Sum256([]byte(rt)), failed: failed})
	if len(list) > spentPerAuth {
		list = list[len(list)-spentPerAuth:]
	}
	g.spent[id] = list
}

func (g *refreshGuard) forgetLocked(id, rt string) {
	h := sha256.Sum256([]byte(rt))
	list := g.spent[id][:0]
	for _, s := range g.spent[id] {
		if s.hash != h {
			list = append(list, s)
		}
	}
	g.spent[id] = list
}

// The SDK looks these up on the registered executor by type assertion, and
// the Claude executor implements all three.

func (g *refreshGuard) PrepareRequest(req *http.Request, a *coreauth.Auth) error {
	if p, ok := g.ProviderExecutor.(coreauth.RequestPreparer); ok {
		return p.PrepareRequest(req, a)
	}
	return nil
}

func (g *refreshGuard) ShouldPrepareRequestAuth(a *coreauth.Auth) bool {
	p, ok := g.ProviderExecutor.(coreauth.RequestAuthPreparer)
	return ok && p.ShouldPrepareRequestAuth(a)
}

func (g *refreshGuard) PrepareRequestAuth(ctx context.Context, a *coreauth.Auth) (*coreauth.Auth, error) {
	if p, ok := g.ProviderExecutor.(coreauth.RequestAuthPreparer); ok {
		return p.PrepareRequestAuth(ctx, a)
	}
	return a, nil
}

func (g *refreshGuard) SupportsApplyPatch() bool {
	p, ok := g.ProviderExecutor.(coreauth.ApplyPatchSupport)
	return ok && p.SupportsApplyPatch()
}

// refreshToken reads the keys the SDK reads (authRefreshToken).
func refreshToken(a *coreauth.Auth) string {
	if a == nil {
		return ""
	}
	if t, _ := a.Metadata["refresh_token"].(string); t != "" {
		return t
	}
	t, _ := a.Metadata["refreshToken"].(string)
	return t
}
