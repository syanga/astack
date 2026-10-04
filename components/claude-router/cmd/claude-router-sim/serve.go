package main

import (
	"bufio"
	"errors"
	"flag"
	"fmt"
	"io"
	"os"
	"sort"
	"strconv"
	"strings"
	"sync"
	"syscall"
	"time"

	"github.com/syanga/astack/components/claude-router/internal/router"
)

var serveStart = time.Date(2026, 10, 5, 9, 0, 0, 0, time.UTC)

func parseAccounts(spec string) ([]router.Account, error) {
	var out []router.Account
	for _, part := range strings.Split(spec, ",") {
		id, capacity, ok := strings.Cut(part, ":")
		if !ok {
			return nil, fmt.Errorf("account %q: want id:capacity", part)
		}
		c, err := strconv.ParseFloat(capacity, 64)
		if err != nil {
			return nil, err
		}
		out = append(out, router.Account{ID: router.AccountID(id), Capacity: c})
	}
	return out, nil
}

func dash(s string) string {
	if s == "" {
		return "-"
	}
	return s
}

func undash(s string) string {
	if s == "-" {
		return ""
	}
	return s
}

type server struct {
	r          *router.Router
	out        io.Writer
	outMu      sync.Mutex
	routes     int
	routesMu   sync.Mutex
	killBefore int
	killAfter  int
}

func (sv *server) printf(format string, args ...any) {
	sv.outMu.Lock()
	defer sv.outMu.Unlock()
	fmt.Fprintf(sv.out, format+"\n", args...)
}

func (sv *server) route(at time.Time, req router.Request) error {
	sv.routesMu.Lock()
	sv.routes++
	n := sv.routes
	sv.routesMu.Unlock()
	if n == sv.killBefore {
		syscall.Kill(os.Getpid(), syscall.SIGKILL)
	}
	d, err := sv.r.Route(at, req)
	if err != nil {
		return err
	}
	if n == sv.killAfter {
		syscall.Kill(os.Getpid(), syscall.SIGKILL)
	}
	sv.printf("ACK %s %s %s %s %s", req.Conversation, dash(req.Agent), d.Kind, dash(string(d.Account)), dash(string(d.From)))
	return nil
}

func at(field string) (time.Time, error) {
	m, err := strconv.ParseFloat(field, 64)
	if err != nil {
		return time.Time{}, err
	}
	return serveStart.Add(minutes(m)), nil
}

func parseWindow(now time.Time, spec string) (router.Window, error) {
	parts := strings.Split(spec, ":")
	if len(parts) != 3 {
		return router.Window{}, fmt.Errorf("window %q: want kind:utilization|rejected:reset-minutes|-", spec)
	}
	w := router.Window{Kind: router.WindowKind(parts[0])}
	if parts[1] == "rejected" {
		w.Rejected = true
		one := 1.0
		w.Utilization = &one
	} else {
		u, err := strconv.ParseFloat(parts[1], 64)
		if err != nil {
			return w, err
		}
		w.Utilization = &u
	}
	if parts[2] != "-" {
		m, err := strconv.ParseFloat(parts[2], 64)
		if err != nil {
			return w, err
		}
		w.ResetsAt = serveStart.Add(minutes(m))
	}
	return w, nil
}

func (sv *server) handle(st *router.Store, accounts []router.Account, line string) error {
	f := strings.Fields(line)
	if len(f) == 0 {
		return nil
	}
	switch f[0] {
	case "route":
		if len(f) != 6 {
			return errors.New("route <minutes> <conversation> <agent|-> <parent|-> <model>")
		}
		now, err := at(f[1])
		if err != nil {
			return err
		}
		return sv.route(now, router.Request{Conversation: router.ConversationID(f[2]), Agent: undash(f[3]), ParentAgent: undash(f[4]), Model: f[5], Attempt: 1})
	case "burst":
		if len(f) != 5 {
			return errors.New("burst <n> <minutes> <conversation> <model>")
		}
		n, err := strconv.Atoi(f[1])
		if err != nil {
			return err
		}
		now, err := at(f[2])
		if err != nil {
			return err
		}
		var wg sync.WaitGroup
		errs := make([]error, n)
		start := make(chan struct{})
		for i := range n {
			wg.Add(1)
			go func() {
				defer wg.Done()
				<-start
				errs[i] = sv.route(now, router.Request{Conversation: router.ConversationID(f[3]), Agent: fmt.Sprintf("burst-%d", i), Model: f[4], Attempt: 1})
			}()
		}
		close(start)
		wg.Wait()
		return errors.Join(errs...)
	case "report":
		if len(f) < 5 {
			return errors.New("report <minutes> <account> <model> <class> [kind:util|rejected:reset|-]...")
		}
		now, err := at(f[1])
		if err != nil {
			return err
		}
		obs := router.Observation{At: now}
		for _, spec := range f[5:] {
			w, err := parseWindow(now, spec)
			if err != nil {
				return err
			}
			obs.Windows = append(obs.Windows, w)
		}
		class := sv.r.Report(router.Failure{Account: router.AccountID(f[2]), Model: f[3], Class: router.Class(f[4]), AttemptStart: now, Observation: obs})
		sv.printf("CLASS %s %s", f[2], class)
	case "observe":
		if len(f) < 3 {
			return errors.New("observe <minutes> <account> kind:util:reset...")
		}
		now, err := at(f[1])
		if err != nil {
			return err
		}
		obs := router.Observation{At: now}
		for _, spec := range f[3:] {
			w, err := parseWindow(now, spec)
			if err != nil {
				return err
			}
			obs.Windows = append(obs.Windows, w)
		}
		sv.r.Observe(router.AccountID(f[2]), obs)
		sv.printf("OBSERVED %s", f[2])
	case "overage":
		if len(f) != 3 {
			return errors.New("overage <minutes> <state>")
		}
		now, err := at(f[1])
		if err != nil {
			return err
		}
		for _, a := range accounts {
			sv.r.ObserveOverage(a.ID, router.Overage(f[2]), now)
		}
		sv.printf("OVERAGE %s", f[2])
	case "move":
		if len(f) != 4 {
			return errors.New("move <minutes> <conversation> <account>")
		}
		now, err := at(f[1])
		if err != nil {
			return err
		}
		if err := sv.r.Move(now, router.ConversationID(f[2]), router.AccountID(f[3])); err != nil {
			return err
		}
		sv.printf("MOVED %s %s", f[2], f[3])
	case "dump":
		bindings := st.Bindings()
		convs := make([]string, 0, len(bindings))
		for c := range bindings {
			convs = append(convs, string(c))
		}
		sort.Strings(convs)
		for _, c := range convs {
			b := bindings[router.ConversationID(c)]
			sv.printf("BINDING %s %s %s", c, b.Account, b.Reason)
		}
		sv.printf("END")
	default:
		return fmt.Errorf("unknown command %q", f[0])
	}
	return nil
}

func cmdServe(args []string, stdin io.Reader, stdout io.Writer) error {
	fs := flag.NewFlagSet("serve", flag.ContinueOnError)
	state := fs.String("state", "", "durable state directory")
	spec := fs.String("accounts", "acct-a:1,acct-b:1,acct-c:1", "id:capacity list in tie-break order")
	killBefore := fs.Int("kill-before", 0, "SIGKILL before the Nth route")
	killAfter := fs.Int("kill-after", 0, "SIGKILL after the Nth route commits, before its ACK")
	if err := fs.Parse(args); err != nil {
		return err
	}
	if *state == "" {
		return errors.New("serve needs -state")
	}
	accounts, err := parseAccounts(*spec)
	if err != nil {
		return err
	}
	st, err := router.OpenStore(*state)
	if err != nil {
		return err
	}
	defer st.Close()
	r, err := router.New(router.DefaultConfig(), accounts, st)
	if err != nil {
		return err
	}
	sv := &server{r: r, out: stdout, killBefore: *killBefore, killAfter: *killAfter}
	sv.printf("READY %d", len(st.Bindings()))
	sc := bufio.NewScanner(stdin)
	for sc.Scan() {
		if err := sv.handle(st, accounts, sc.Text()); err != nil {
			sv.printf("ERROR %v", err)
			return err
		}
	}
	return sc.Err()
}
