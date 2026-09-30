import { afterEach, expect, it } from "bun:test";
import { chmod, mkdir, mkdtemp, readFile, readdir, rename, rm, writeFile } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { openStore, type Store } from "./store.ts";

const directories: string[] = [];
const handles: Store[] = [];
const script = join(import.meta.dir, "orch.ts");

async function fixture() {
  const dir = await mkdtemp(join(tmpdir(), "astack-orch-"));
  directories.push(dir);
  const store = openStore(dir);
  handles.push(store);
  await store.init();
  return { dir, store };
}

afterEach(async () => {
  for (const handle of handles.splice(0)) await handle.close();
  for (const dir of directories.splice(0)) await rm(dir, { recursive: true, force: true });
});

it("retains verdict history and verifier precedence without carrying a verdict to a new head", async () => {
  const { dir, store } = await fixture();
  await store.units.add({ id: "u1", track: "main" });
  await store.units.set({ id: "u1", state: "needs-verify", pr: 40, sha: "new-head", agent: "session-2" });
  for (const name of ["test.log", "failure.log", "worker-retry.log"]) await writeFile(join(dir, name), "");
  await store.ledger.record({ pr: 40, sha: "old-head", verdict: "unit-test-verified", evidence: "test.log" });
  await store.ledger.record({ pr: 40, sha: "old-head", verdict: "verifier-failed", evidence: "failure.log", verifier: "reviewer" });
  await store.ledger.record({ pr: 40, sha: "old-head", verdict: "unit-test-verified", evidence: "worker-retry.log" });
  expect((await store.ledger.check({ pr: 40, sha: "old-head" })).verdict).toBe("verifier-failed");
  await expect(store.ledger.check({ pr: 40, sha: "new-head" })).rejects.toThrow("NOT-VERIFIED");
  expect((await readFile(join(dir, "ledger.tsv"), "utf8")).split("\n").filter(Boolean)).toHaveLength(4);
  expect(await store.units.get("u1")).toMatchObject({ agent: "session-2", sha: "new-head" });
  await store.status.render();
  expect(await readFile(join(dir, "status.md"), "utf8")).toContain("| u1 | 40 | new-head | NOT-VERIFIED |");
});

it("reads the prior documented tables and preserves ownership and receipts on a write", async () => {
  const { dir, store } = await fixture();
  await writeFile(join(dir, "units.tsv"), "id\ttrack\tstate\tagent\tbranch\tpr\thead_sha\tbrief\nu1\tmain\trunning\tsession-1\tfeature\t40\thead\tbrief.md\n");
  await writeFile(join(dir, "ledger.tsv"), "pr\thead_sha\trole\tverdict\tevidence\n40\thead\tverifier\tverifier-failed\tfailure.log\n");
  expect(await store.units.set({ id: "u1", state: "blocked" })).toEqual({
    id: "u1", track: "main", state: "blocked", agent: "session-1", branch: "feature", pr: "40", sha: "head", brief: "brief.md",
  });
  await writeFile(join(dir, "worker.log"), "");
  await store.ledger.record({ pr: 40, sha: "head", verdict: "unit-test-verified", evidence: "worker.log" });
  expect((await store.ledger.check({ pr: 40, sha: "head" })).verdict).toBe("verifier-failed");
  const reopened = openStore(dir);
  handles.push(reopened);
  expect((await reopened.units.get("u1")).agent).toBe("session-1");
  expect((await reopened.ledger.check({ pr: 40, sha: "head" })).evidence).toBe("failure.log");
});

it("archives drained reports and recovers a batch interrupted before inbox recreation", async () => {
  const { dir, store } = await fixture();
  await store.inbox.push({ agent: "worker", unit: "u1", status: "done", report: "one.log" });
  const drained = await store.inbox.drain();
  expect(drained.map((row) => [row.unit, row.report])).toEqual([["u1", "one.log"]]);
  expect(await store.inbox.peek()).toEqual([]);
  expect((await store.inbox.history()).map((row) => row.report)).toEqual(["one.log"]);
  await store.inbox.push({ agent: "worker", unit: "u2", status: "failed", report: "two.log" });
  await rename(join(dir, "inbox"), join(dir, ".inbox-drain-interrupted"));
  await store.init();
  expect((await store.inbox.history()).map((row) => row.report).sort()).toEqual(["one.log", "two.log"]);
  await store.inbox.push({ agent: "worker", unit: "u3", status: "done", report: "three.log" });
  expect((await store.inbox.peek()).map((row) => row.report)).toEqual(["three.log"]);
});

it("keeps malformed drain input intact for repair", async () => {
  const { dir, store } = await fixture();
  await writeFile(join(dir, "inbox/bad.tsv"), "broken\trow\n");
  await expect(store.inbox.drain()).rejects.toThrow("malformed");
  expect(await readFile(join(dir, "inbox/bad.tsv"), "utf8")).toBe("broken\trow\n");
  await rm(join(dir, "inbox/bad.tsv"));
  await store.inbox.push({ agent: "worker", unit: "u1", status: "done" });
  expect((await store.inbox.drain()).map((row) => row.unit)).toEqual(["u1"]);
});

it("renders only the latest session handoff into derived status", async () => {
  const { dir, store } = await fixture();
  await writeFile(join(dir, "overview.md"), "# Program\n\n## Handoff 2026-09-18\nOld next step.\n\n## Handoff 2026-09-19\nRun test_api.py in /work/project.\n");
  await store.status.render();
  const status = await readFile(join(dir, "status.md"), "utf8");
  expect(status).toContain("Run test_api.py in /work/project.");
  expect(status).not.toContain("Old next step.");
});

it("blocks another CLI writer while the store is locked and admits it after release", async () => {
  const { dir, store } = await fixture();
  const command = [process.execPath, script, "--store", dir, "unit", "add", "u1", "--track", "main"];
  const blocked = Bun.spawnSync(command);
  expect(blocked.exitCode).toBe(1);
  expect(blocked.stderr.toString()).toContain("store lock held by pid");
  await store.close();
  const accepted = Bun.spawnSync(command);
  expect(accepted.exitCode).toBe(0);
  expect(accepted.stdout.toString()).toStartWith("u1\tmain\tpending");
});

it("recovers a lock left by an exited process on the next mutation", async () => {
  const { dir, store } = await fixture();
  await store.close();
  const child = Bun.spawn([process.execPath, "-e", "process.stdout.write(String(process.pid))"], { stdout: "pipe" });
  const pid = await new Response(child.stdout).text();
  await child.exited;
  await writeFile(join(dir, ".orch.lock"), pid + "\n");
  const result = Bun.spawnSync([process.execPath, script, "--store", dir, "unit", "add", "recovered", "--track", "main"]);
  expect(result.exitCode).toBe(0);
  expect(result.stdout.toString()).toStartWith("recovered\tmain\tpending");
  expect(result.stderr.toString()).toContain("replacing stale store lock");
});

function git(repo: string, args: string[]): string {
  const result = Bun.spawnSync(["git", "-C", repo, ...args]);
  if (result.exitCode !== 0) throw new Error(result.stderr.toString());
  return result.stdout.toString().trim();
}

it("supports a GitHub frontier without Graphite and rejects head drift and wrong order", async () => {
  const { dir, store } = await fixture();
  const repo = join(dir, "repo");
  await mkdir(repo);
  git(repo, ["init", "--initial-branch=main"]);
  git(repo, ["config", "user.name", "Orch Test"]);
  git(repo, ["config", "user.email", "orch@example.com"]);
  git(repo, ["commit", "--allow-empty", "-m", "base"]);
  const base = git(repo, ["rev-parse", "HEAD"]);
  git(repo, ["checkout", "-b", "first"]);
  git(repo, ["commit", "--allow-empty", "-m", "first"]);
  const first = git(repo, ["rev-parse", "HEAD"]);
  git(repo, ["checkout", "-b", "second"]);
  git(repo, ["commit", "--allow-empty", "-m", "second"]);
  const second = git(repo, ["rev-parse", "HEAD"]);
  const rows = [
    { number: 10, state: "OPEN", headRefName: "first", headRefOid: first, baseRefName: "main", isCrossRepository: false },
    { number: 11, state: "OPEN", headRefName: "second", headRefOid: second, baseRefName: "first", isCrossRepository: false },
  ];
  const payload = join(dir, "prs.json");
  await writeFile(payload, JSON.stringify(rows));
  const bin = join(dir, "bin");
  await mkdir(bin);
  await writeFile(join(bin, "gh"), `#!/usr/bin/env bun\nconst rows=await Bun.file(${JSON.stringify(payload)}).json();if(process.argv[2]!=="pr"||process.argv[3]!=="view")process.exit(2);console.log(JSON.stringify(rows.find(r=>r.number===Number(process.argv[4]))));\n`);
  await chmod(join(bin, "gh"), 0o755);
  const oldPath = process.env.PATH;
  process.env.PATH = `${bin}:${oldPath}`;
  try {
    const result = await store.frontier.set({ repo, source: "github", prs: [10, 11] });
    expect(result).toEqual({ generation: 1, lowestUnmerged: 10, prs: [
      { pr: 10, branches: "first", sha: first, state: "OPEN" },
      { pr: 11, branches: "second", sha: second, state: "OPEN" },
    ] });
    await expect(store.frontier.set({ repo, source: "github" })).rejects.toThrow("requires --prs");
    await expect(store.frontier.set({ repo, source: "github", prs: [11, 10] })).rejects.toThrow("before its parent");
    rows[0]!.headRefOid = base;
    await writeFile(payload, JSON.stringify(rows));
    await expect(store.frontier.set({ repo, source: "github", prs: [10, 11] })).rejects.toThrow("head differs");
    expect((await store.frontier.show()).generation).toBe(1);
    rows[0]!.headRefOid = first;
    rows[0]!.state = "CLOSED";
    await writeFile(payload, JSON.stringify(rows));
    await expect(store.frontier.set({ repo, source: "github", prs: [10, 11] })).rejects.toThrow("closed, unmerged");
    rows[0]!.state = "OPEN";
    rows[0]!.isCrossRepository = true;
    await writeFile(payload, JSON.stringify(rows));
    await expect(store.frontier.set({ repo, source: "github", prs: [10, 11] })).rejects.toThrow("cross-repository");
    rows[0]!.isCrossRepository = false;
    await writeFile(payload, JSON.stringify(rows));
    await store.close();
    const cli = Bun.spawnSync([process.execPath, script, "--store", dir, "--json",
      "frontier", "set", "--source", "github", "--repo", repo, "--prs", "10,11"],
      { env: { ...process.env, PATH: `${bin}:${oldPath ?? ""}` } });
    expect(cli.exitCode, cli.stdout.toString() + cli.stderr.toString()).toBe(0);
    expect(JSON.parse(cli.stdout.toString())).toMatchObject({ generation: 2, lowestUnmerged: 10 });
  } finally {
    if (oldPath === undefined) delete process.env.PATH;
    else process.env.PATH = oldPath;
  }
});
