// Command claude-router-sim drives the production routing policy in
// internal/router with synthetic workloads, scripted fixtures, a line
// protocol for crash tests, and a performance workload.
package main

import (
	"encoding/json"
	"errors"
	"flag"
	"fmt"
	"io"
	"os"
	"path/filepath"
	"sort"
	"strconv"
	"strings"
	"sync"
	"time"

	"github.com/syanga/astack/components/claude-router/internal/router"
)

func main() {
	if err := run(os.Args[1:], os.Stdin, os.Stdout); err != nil {
		fmt.Fprintln(os.Stderr, "claude-router-sim:", err)
		os.Exit(1)
	}
}

const usage = `usage: claude-router-sim <command> [flags]

commands:
  run      simulate one workload fixture and write metrics and the trace
  compare  run capacity-only and reset-aware variants over fixtures and seeds
  check    run scripted fixtures and compare outcomes with their literal expectations
  serve    route line commands from stdin against durable state (crash tests)
  perf     place and route a large workload and report decision latency
  recover  open durable state, report its size, and exit`

func run(args []string, stdin io.Reader, stdout io.Writer) error {
	if len(args) == 0 {
		return errors.New(usage)
	}
	switch args[0] {
	case "run":
		return cmdRun(args[1:], stdout)
	case "compare":
		return cmdCompare(args[1:], stdout)
	case "check":
		return cmdCheck(args[1:], stdout)
	case "serve":
		return cmdServe(args[1:], stdin, stdout)
	case "perf":
		return cmdPerf(args[1:], stdout)
	case "recover":
		return cmdRecover(args[1:], stdout)
	}
	return fmt.Errorf("unknown command %q\n%s", args[0], usage)
}

func writeJSON(w io.Writer, v any) error {
	enc := json.NewEncoder(w)
	enc.SetIndent("", "  ")
	return enc.Encode(v)
}

func writeJSONFile(path string, v any) error {
	f, err := os.Create(path)
	if err != nil {
		return err
	}
	if err := writeJSON(f, v); err != nil {
		f.Close()
		return err
	}
	return f.Close()
}

func tempState() (string, func(), error) {
	dir, err := os.MkdirTemp("", "claude-router-sim-")
	if err != nil {
		return "", nil, err
	}
	return dir, func() { os.RemoveAll(dir) }, nil
}

// Variant is one policy configuration in a comparison.
type Variant struct {
	Label  string        `json:"label"`
	Config router.Config `json:"config"`
}

func variantConfig(rule router.Rule, bias float64, fresh time.Duration) Variant {
	cfg := router.DefaultConfig()
	cfg.Rule, cfg.ResetBias, cfg.FreshFor = rule, bias, fresh
	if rule == router.CapacityOnly {
		return Variant{Label: "capacity_only", Config: cfg}
	}
	return Variant{Label: fmt.Sprintf("reset_aware bias=%g fresh=%s", bias, fresh), Config: cfg}
}

// RunResult is one simulated run.
type RunResult struct {
	Fixture     string            `json:"fixture"`
	Seed        uint64            `json:"seed"`
	Variant     string            `json:"variant"`
	Config      router.Config     `json:"config"`
	Assumptions map[string]string `json:"assumptions"`
	Metrics     Metrics           `json:"metrics"`
	Trace       []Event           `json:"trace,omitempty"`
}

func cmdRun(args []string, stdout io.Writer) error {
	fs := flag.NewFlagSet("run", flag.ContinueOnError)
	fixture := fs.String("fixture", "", "workload fixture JSON")
	seed := fs.Uint64("seed", 1, "random seed")
	rule := fs.String("rule", string(router.DefaultConfig().Rule), "capacity_only or reset_aware")
	bias := fs.Float64("bias", router.DefaultConfig().ResetBias, "reset bias")
	fresh := fs.Duration("fresh", router.DefaultConfig().FreshFor, "observation freshness threshold")
	out := fs.String("out", "", "write the result here instead of stdout")
	trace := fs.Bool("trace", true, "include the routing trace")
	if err := fs.Parse(args); err != nil {
		return err
	}
	var fx Fixture
	if err := loadJSON(*fixture, &fx); err != nil {
		return err
	}
	v := variantConfig(router.Rule(*rule), *bias, *fresh)
	dir, cleanup, err := tempState()
	if err != nil {
		return err
	}
	defer cleanup()
	m, tr, err := Simulate(fx, *seed, v.Config, dir)
	if err != nil {
		return err
	}
	res := RunResult{Fixture: fx.Name, Seed: *seed, Variant: v.Label, Config: v.Config, Assumptions: fx.Assumptions, Metrics: m}
	if *trace {
		res.Trace = tr
	}
	if *out != "" {
		return writeJSONFile(*out, res)
	}
	return writeJSON(stdout, res)
}

// Comparison is the output of compare.
type Comparison struct {
	Fixtures []FixtureInfo  `json:"fixtures"`
	Seeds    []uint64       `json:"seeds"`
	Variants []VariantStats `json:"variants"`
}

// FixtureInfo names a fixture and its labeled assumptions.
type FixtureInfo struct {
	Name        string            `json:"name"`
	Assumptions map[string]string `json:"assumptions"`
}

// VariantStats holds one variant's runs per fixture.
type VariantStats struct {
	Variant
	Fixtures []FixtureStats `json:"fixtures"`
}

// FixtureStats holds per-seed metrics and their means for one fixture.
type FixtureStats struct {
	Fixture string             `json:"fixture"`
	Mean    map[string]float64 `json:"mean"`
	Runs    []Metrics          `json:"runs"`
}

var meanFields = []string{
	"completed_turns", "useful_output_tokens", "unfinished_conversations", "waits", "wait_minutes",
	"unused_five_hour_share", "unused_weekly_share", "upstream_attempts_rejected_for_quota",
	"exhaustion_migrations", "healthy_automatic_migrations", "cache_write_tokens_from_migration",
	"cache_write_tokens_ordinary", "cache_read_tokens", "recovery_mismatches",
	"parallel_first_disagreements", "subagent_requests_not_inherited",
}

func means(runs []Metrics) map[string]float64 {
	out := map[string]float64{}
	for _, m := range runs {
		var fields map[string]any
		data, _ := json.Marshal(m)
		_ = json.Unmarshal(data, &fields)
		for _, f := range meanFields {
			if v, ok := fields[f].(float64); ok {
				out[f] += v / float64(len(runs))
			}
		}
	}
	for k, v := range out {
		out[k] = float64(int64(v*1000+0.5)) / 1000
	}
	return out
}

func parseList[T any](s string, parse func(string) (T, error)) ([]T, error) {
	var out []T
	for _, part := range strings.Split(s, ",") {
		v, err := parse(strings.TrimSpace(part))
		if err != nil {
			return nil, err
		}
		out = append(out, v)
	}
	return out, nil
}

func cmdCompare(args []string, stdout io.Writer) error {
	fs := flag.NewFlagSet("compare", flag.ContinueOnError)
	fixtures := fs.String("fixtures", "", "comma-separated workload fixtures")
	seeds := fs.String("seeds", "1,2,3", "comma-separated seeds")
	biases := fs.String("biases", "1,2,4", "reset biases to sweep")
	freshes := fs.String("fresh", "5m,15m,60m", "freshness thresholds to sweep")
	workers := fs.Int("workers", 8, "concurrent runs")
	out := fs.String("out", "", "write the comparison here instead of stdout")
	if err := fs.Parse(args); err != nil {
		return err
	}
	seedList, err := parseList(*seeds, func(s string) (uint64, error) { return strconv.ParseUint(s, 10, 64) })
	if err != nil {
		return err
	}
	biasList, err := parseList(*biases, func(s string) (float64, error) { return strconv.ParseFloat(s, 64) })
	if err != nil {
		return err
	}
	freshList, err := parseList(*freshes, time.ParseDuration)
	if err != nil {
		return err
	}
	var fxs []Fixture
	cmp := Comparison{Seeds: seedList}
	for _, path := range strings.Split(*fixtures, ",") {
		var fx Fixture
		if err := loadJSON(path, &fx); err != nil {
			return err
		}
		fxs = append(fxs, fx)
		cmp.Fixtures = append(cmp.Fixtures, FixtureInfo{Name: fx.Name, Assumptions: fx.Assumptions})
	}
	variants := []Variant{variantConfig(router.CapacityOnly, 0, router.DefaultConfig().FreshFor)}
	for _, b := range biasList {
		for _, f := range freshList {
			variants = append(variants, variantConfig(router.ResetAware, b, f))
		}
	}
	type job struct{ v, f, s int }
	results := make([][][]Metrics, len(variants))
	var jobs []job
	for v := range variants {
		results[v] = make([][]Metrics, len(fxs))
		for f := range fxs {
			results[v][f] = make([]Metrics, len(seedList))
			for s := range seedList {
				jobs = append(jobs, job{v, f, s})
			}
		}
	}
	work := make(chan job)
	errs := make(chan error, len(jobs))
	var wg sync.WaitGroup
	for range max(1, *workers) {
		wg.Add(1)
		go func() {
			defer wg.Done()
			for j := range work {
				dir, cleanup, err := tempState()
				if err != nil {
					errs <- err
					continue
				}
				m, _, err := Simulate(fxs[j.f], seedList[j.s], variants[j.v].Config, dir)
				cleanup()
				if err != nil {
					errs <- fmt.Errorf("%s %s seed %d: %w", variants[j.v].Label, fxs[j.f].Name, seedList[j.s], err)
					continue
				}
				results[j.v][j.f][j.s] = m
			}
		}()
	}
	for _, j := range jobs {
		work <- j
	}
	close(work)
	wg.Wait()
	close(errs)
	if err := <-errs; err != nil {
		return err
	}
	for v, variant := range variants {
		vs := VariantStats{Variant: variant}
		for f, fx := range fxs {
			vs.Fixtures = append(vs.Fixtures, FixtureStats{Fixture: fx.Name, Mean: means(results[v][f]), Runs: results[v][f]})
		}
		cmp.Variants = append(cmp.Variants, vs)
	}
	if *out != "" {
		return writeJSONFile(*out, cmp)
	}
	return writeJSON(stdout, cmp)
}

// CheckReport is the output of check.
type CheckReport struct {
	Defaults router.Config  `json:"defaults"`
	Results  []ScriptResult `json:"results"`
	Pass     bool           `json:"pass"`
}

func cmdCheck(args []string, stdout io.Writer) error {
	fs := flag.NewFlagSet("check", flag.ContinueOnError)
	dir := fs.String("scripts", "testdata/scripts", "directory of scripted fixtures")
	out := fs.String("out", "", "write the report here instead of stdout")
	if err := fs.Parse(args); err != nil {
		return err
	}
	rep, err := CheckScripts(*dir)
	if err != nil {
		return err
	}
	if *out != "" {
		err = writeJSONFile(*out, rep)
	} else {
		err = writeJSON(stdout, rep)
	}
	if err == nil && !rep.Pass {
		err = errors.New("a scripted fixture did not match its literal expectations")
	}
	return err
}

// CheckScripts runs every script in dir under both rules with the default
// parameters.
func CheckScripts(dir string) (CheckReport, error) {
	paths, err := filepath.Glob(filepath.Join(dir, "*.json"))
	if err != nil {
		return CheckReport{}, err
	}
	if len(paths) == 0 {
		return CheckReport{}, fmt.Errorf("no scripts in %s", dir)
	}
	sort.Strings(paths)
	rep := CheckReport{Defaults: router.DefaultConfig(), Pass: true}
	for _, path := range paths {
		var sc Script
		if err := loadJSON(path, &sc); err != nil {
			return rep, err
		}
		for _, rule := range []router.Rule{router.CapacityOnly, router.ResetAware} {
			cfg := router.DefaultConfig()
			cfg.Rule = rule
			state, cleanup, err := tempState()
			if err != nil {
				return rep, err
			}
			res, err := RunScript(sc, cfg, state)
			cleanup()
			if err != nil {
				return rep, fmt.Errorf("%s: %w", path, err)
			}
			rep.Pass = rep.Pass && res.Pass
			rep.Results = append(rep.Results, res)
		}
	}
	return rep, nil
}
