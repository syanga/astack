from dataclasses import dataclass, field
import json
import os
from pathlib import Path
import shutil
import subprocess
from typing import Callable

import astack_files as files


@dataclass(frozen=True)
class Source:
    marketplaces: dict[str, str] = field(default_factory=dict)
    plugins: tuple[str, ...] = ()


@dataclass(frozen=True)
class ConfigDir:
    path: Path
    variable: str
    home: Path
    use_env: bool

    def environment(self):
        env = dict(os.environ)
        if not self.use_env:
            env["HOME"] = str(self.home)
            env.pop(self.variable, None)
        return env


@dataclass(frozen=True)
class PluginCli:
    command: str
    list_marketplaces: tuple[str, ...]
    list_plugins: tuple[str, ...]
    add_marketplace: tuple[str, ...]
    refresh_marketplace: tuple[str, ...]
    install: tuple[str, ...]
    update: tuple[str, ...] | None
    remove_plugin: tuple[str, ...]
    remove_marketplace: tuple[str, ...]
    marketplace_sources: Callable[[object], dict[str, str | None]]
    plugin_ids: Callable[[object], set[str]]
    used_plugin_ids: Callable[[object], set[str]]


def codex_plugin_ids(data):
    return {item["pluginId"] for item in data["installed"] if item.get("installed")}


CLIS = {
    "claude": PluginCli(
        command="claude",
        list_marketplaces=("plugin", "marketplace", "list", "--json"),
        list_plugins=("plugin", "list", "--json"),
        add_marketplace=("plugin", "marketplace", "add"),
        refresh_marketplace=("plugin", "marketplace", "update"),
        install=("plugin", "install"),
        update=("plugin", "update"),
        remove_plugin=("plugin", "uninstall"),
        remove_marketplace=("plugin", "marketplace", "remove"),
        marketplace_sources=lambda data: {item["name"]: None for item in data},
        plugin_ids=lambda data: {item["id"] for item in data if item.get("scope") == "user"},
        used_plugin_ids=lambda data: {item["id"] for item in data},
    ),
    "codex": PluginCli(
        command="codex",
        list_marketplaces=("plugin", "marketplace", "list", "--json"),
        list_plugins=("plugin", "list", "--json"),
        add_marketplace=("plugin", "marketplace", "add"),
        refresh_marketplace=("plugin", "marketplace", "upgrade"),
        install=("plugin", "add"),
        update=None,
        remove_plugin=("plugin", "remove"),
        remove_marketplace=("plugin", "marketplace", "remove"),
        marketplace_sources=lambda data: {item["name"]: item.get("marketplaceSource", {}).get("source")
                                          for item in data["marketplaces"]},
        plugin_ids=codex_plugin_ids,
        used_plugin_ids=codex_plugin_ids,
    ),
}


def marketplace(plugin):
    return plugin.partition("@")[2]


def local(source):
    return Path(source).is_absolute()


def load(path, target):
    """Read the plugins for one harness: the shared list plus that harness's entry under targets.

    A marketplace source that starts with ./ is a directory relative to the checkout.
    """
    if not path.exists():
        return Source()
    data = json.loads(path.read_text(encoding="utf-8"))
    marketplaces = data.get("marketplaces", {}) if isinstance(data, dict) else None
    plugins = data.get("plugins", []) if isinstance(data, dict) else None
    targets = data.get("targets", {}) if isinstance(data, dict) else None
    if not isinstance(marketplaces, dict) or not isinstance(plugins, list) or not isinstance(targets, dict) or \
            not all(isinstance(name, str) and isinstance(source, str) for name, source in marketplaces.items()) or \
            not all(isinstance(extra, list) for extra in targets.values()):
        raise ValueError("Plugin source needs a marketplaces object, a plugins list, and lists under targets: {}".format(path))
    plugins = plugins + targets.get(target, [])
    for plugin in plugins:
        if not isinstance(plugin, str) or not plugin.partition("@")[0] or marketplace(plugin) not in marketplaces:
            raise ValueError("Plugin must be name@marketplace with a listed marketplace: {!r}".format(plugin))
    checkout = path.resolve().parent.parent
    marketplaces = {name: str(checkout / source) if source.startswith("./") else source
                    for name, source in marketplaces.items()}
    return Source(marketplaces, tuple(dict.fromkeys(plugins)))


def check_config(target, state, config):
    recorded = state.get("plugins", {}).get(target, {}).get("config")
    if recorded and recorded != str(config.path):
        raise ValueError("{} plugins were installed in {}; set {} back to that directory to change them".format(
            target, recorded, config.variable))


def sync(target, source, state, state_path, config, dry_run):
    cli = CLIS[target]
    owned = state.get("plugins", {}).get(target, {})
    owned_marketplaces = set(owned.get("marketplaces", []))
    owned_plugins = set(owned.get("plugins", []))
    if not (source.plugins or owned_marketplaces or owned_plugins):
        return
    needed = list(dict.fromkeys(marketplace(plugin) for plugin in source.plugins))
    executable = shutil.which(cli.command)
    if executable is None:
        print("SKIP {} plugins: {} not found".format(target, cli.command))
        return
    if dry_run:
        for name in needed:
            print("MARKETPLACE {} ({})".format(name, target))
        for plugin in source.plugins:
            print("PLUGIN {} ({})".format(plugin, target))
        for plugin in sorted(owned_plugins - set(source.plugins)):
            print("REMOVE PLUGIN {} ({})".format(plugin, target))
        for name in sorted(owned_marketplaces - set(needed)):
            print("REMOVE MARKETPLACE {} ({}) if no other plugin uses it".format(name, target))
        return

    env = config.environment()

    def run(prefix, *arguments):
        argv = [*prefix, *arguments]
        result = subprocess.run([executable, *argv], stdin=subprocess.DEVNULL,
                                capture_output=True, text=True, env=env)
        if result.returncode:
            tail = "\n".join((result.stderr.strip() or result.stdout.strip()).splitlines()[-5:])
            raise ValueError("{} {} failed with exit {}: {}".format(
                cli.command, " ".join(argv), result.returncode, tail))
        return result.stdout

    def query(prefix, parse):
        output = run(prefix)
        try:
            return parse(json.loads(output))
        except (ValueError, KeyError, TypeError):
            raise ValueError("Unexpected output from {} {}".format(cli.command, " ".join(prefix))) from None

    def record():
        entries = state.setdefault("plugins", {})
        entries[target] = {"config": str(config.path), "marketplaces": sorted(owned_marketplaces),
                           "plugins": sorted(owned_plugins)}
        if not owned_marketplaces and not owned_plugins:
            entries.pop(target)
        if not entries:
            state.pop("plugins")
        files.save_manifest(state_path, state)

    known = query(cli.list_marketplaces, cli.marketplace_sources)
    installed = query(cli.list_plugins, cli.plugin_ids)
    for name in needed:
        if name in owned_marketplaces and local(source.marketplaces[name]) and \
                known.get(name) not in (None, source.marketplaces[name]):
            print("MOVE MARKETPLACE {} ({})".format(name, target))
            run(cli.remove_marketplace, name)
            known.pop(name)
        if name not in known:
            print("ADD MARKETPLACE {} ({})".format(name, target))
            run(cli.add_marketplace, source.marketplaces[name])
            owned_marketplaces.add(name)
            record()
        # A local marketplace is read from disk, and Codex cannot upgrade one.
        if not local(source.marketplaces[name]):
            print("REFRESH MARKETPLACE {} ({})".format(name, target))
            run(cli.refresh_marketplace, name)
    for plugin in source.plugins:
        if plugin not in installed:
            print("INSTALL PLUGIN {} ({})".format(plugin, target))
            run(cli.install, plugin)
            owned_plugins.add(plugin)
            record()
        elif cli.update:
            print("UPDATE PLUGIN {} ({})".format(plugin, target))
            run(cli.update, plugin)
        elif local(source.marketplaces[marketplace(plugin)]):
            # Codex has no update command; adding an installed plugin again copies its current files.
            print("UPDATE PLUGIN {} ({})".format(plugin, target))
            run(cli.install, plugin)
    installed |= set(source.plugins)
    for plugin in sorted(owned_plugins - set(source.plugins)):
        if plugin in installed:
            print("REMOVE PLUGIN {} ({})".format(plugin, target))
            run(cli.remove_plugin, plugin)
            installed.discard(plugin)
        owned_plugins.discard(plugin)
        record()
    unneeded = sorted(owned_marketplaces - set(needed))
    used = query(cli.list_plugins, cli.used_plugin_ids) if unneeded else set()
    in_use = {marketplace(plugin) for plugin in used}
    for name in unneeded:
        if name in in_use:
            print("KEEP MARKETPLACE {} ({}): other plugins use it".format(name, target))
        elif name in known:
            print("REMOVE MARKETPLACE {} ({})".format(name, target))
            run(cli.remove_marketplace, name)
        owned_marketplaces.discard(name)
        record()
