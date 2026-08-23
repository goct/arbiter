#!/usr/bin/env python3
"""Turn a WoW talent loadout import string into a list of talents taken, with ranks.

Usage:
    python decode-loadout.py "CYEAAAAA..."
    python decode-loadout.py --data ptr "CYEAAAAA..."

Needs a talent tree dump to resolve node IDs to names. Downloads and caches
Raidbots' published dump (talents-live.json) next to this script on first run.
Re-download after a patch: delete the cache file, or pass --refresh.

Why a dump is required: the import string stores only *which* nodes are taken,
by position in the tree's node list. Names, max ranks, and the tree's node
order all live in game data, not in the string.
"""

import argparse
import json
import os
import sys
import urllib.request

CHARS = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/"
DATA_URL = "https://www.raidbots.com/static/data/{branch}/talents.json"
HERE = os.path.dirname(os.path.abspath(__file__))


class BitReader:
    """Blizzard packs bits least-significant-first, 6 bits per base64 char."""

    def __init__(self, text):
        self.bits = []
        for ch in text.strip():
            if ch not in CHARS:
                raise ValueError(f"invalid character {ch!r} in import string")
            value = CHARS.index(ch)
            self.bits.extend((value >> i) & 1 for i in range(6))
        self.pos = 0

    def take(self, count):
        if self.pos + count > len(self.bits):
            raise ValueError("import string ended early - wrong tree data for this patch?")
        value = 0
        for i in range(count):
            value |= self.bits[self.pos + i] << i
        self.pos += count
        return value

    def remaining(self):
        return len(self.bits) - self.pos


def load_trees(branch, refresh):
    cache = os.path.join(HERE, f"talents-{branch}.json")
    if refresh or not os.path.exists(cache):
        url = DATA_URL.format(branch=branch)
        print(f"downloading {url} ...", file=sys.stderr)
        with urllib.request.urlopen(url, timeout=120) as response:
            payload = response.read()
        with open(cache, "wb") as handle:
            handle.write(payload)
    with open(cache, "rb") as handle:
        return json.load(handle)


def decode(import_string, trees):
    reader = BitReader(import_string)

    version = reader.take(8)
    spec_id = reader.take(16)
    for _ in range(16):  # 128-bit tree hash; zero-filled by third-party sites
        reader.take(8)

    if version != 2:
        print(f"warning: serialization version {version}, expected 2", file=sys.stderr)

    spec = next((t for t in trees if t["specId"] == spec_id), None)
    if spec is None:
        raise ValueError(f"spec id {spec_id} not present in the talent dump")

    nodes = {}
    for group in ("classNodes", "specNodes", "heroNodes", "subTreeNodes"):
        for node in spec.get(group, []):
            nodes[node["id"]] = dict(node, _group=group)

    picks = {"classNodes": [], "specNodes": [], "heroNodes": [], "subTreeNodes": []}
    hero_tree = None

    for node_id in spec["fullNodeOrder"]:
        if not reader.take(1):  # node not selected at all
            continue
        purchased = reader.take(1)  # 0 = granted free, costs no point
        rank, entry_index = None, None
        if purchased:
            if reader.take(1):  # partially ranked
                rank = reader.take(6)
            if reader.take(1):  # choice node
                entry_index = reader.take(2)

        node = nodes.get(node_id)
        if node is None:  # belongs to another spec's tree; nothing to name
            continue

        entries = node.get("entries") or [{}]
        entry = entries[entry_index if entry_index is not None else 0]

        # A maxed talent writes NO rank bits - absence means "all ranks".
        max_ranks = node.get("maxRanks", 1)
        picks[node["_group"]].append({
            "name": entry.get("name") or node["name"],
            "rank": max_ranks if rank is None else rank,
            "max_ranks": max_ranks,
            "granted": not purchased,
            "sub_tree": node.get("subTreeId"),
            "choice": f"option {(entry_index or 0) + 1} of {len(entries)}" if len(entries) > 1 else None,
        })

        if node["_group"] == "subTreeNodes" and purchased:
            hero_tree = {"id": entry.get("traitSubTreeId"), "name": entry.get("name")}

    if reader.remaining() != 0:
        print(f"warning: {reader.remaining()} bits left over - tree data may be from "
              f"a different patch than the build", file=sys.stderr)

    # Every hero tree's entry node is granted, so drop the one you did not pick.
    if hero_tree:
        picks["heroNodes"] = [p for p in picks["heroNodes"]
                              if p["sub_tree"] == hero_tree["id"]]

    return spec, hero_tree, picks


def report(spec, hero_tree, picks):
    print(f"{spec['specName']} {spec['className']}  (spec {spec['specId']}, tree {spec['traitTreeId']})")
    if hero_tree:
        print(f"Hero tree: {hero_tree['name']}")
    print()

    sections = [("Class tree", "classNodes"),
                ("Spec tree", "specNodes"),
                (f"Hero tree - {hero_tree['name'] if hero_tree else '?'}", "heroNodes")]

    for title, key in sections:
        rows = picks[key]
        spent = sum(p["rank"] for p in rows if not p["granted"])
        print(f"== {title} == ({spent} points)")
        for p in sorted(rows, key=lambda p: p["name"]):
            rank = f"{p['rank']}/{p['max_ranks']}"
            notes = []
            if p["granted"]:
                notes.append("granted, free")
            if p["choice"]:
                notes.append(p["choice"])
            suffix = f"   [{', '.join(notes)}]" if notes else ""
            print(f"  {rank:>5}  {p['name']}{suffix}")
        print()


def main():
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("import_string")
    parser.add_argument("--data", default="live", choices=["live", "ptr", "beta"],
                        help="which Raidbots data branch to resolve against")
    parser.add_argument("--refresh", action="store_true", help="re-download the dump")
    parser.add_argument("--json", action="store_true", help="emit JSON instead of text")
    args = parser.parse_args()

    trees = load_trees(args.data, args.refresh)
    spec, hero_tree, picks = decode(args.import_string, trees)

    if args.json:
        json.dump({"spec": spec["specName"], "class": spec["className"],
                   "heroTree": hero_tree, "picks": picks}, sys.stdout, indent=2)
        print()
    else:
        report(spec, hero_tree, picks)


if __name__ == "__main__":
    main()
