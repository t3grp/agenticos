#!/usr/bin/env python3
"""The backend test files one CI shard runs.

    python ../scripts/ci_backend_shard.py 2/3 tests

prints, one per line, every `test_*.py` under `tests` whose position in the sorted
list is congruent to 1 modulo 3. `make test` passes that list to pytest when
`BACKEND_SHARD` is set, and the `test` job in `ci.yml` combines the three coverage
files the shards leave behind before it applies the platform gate.

The split is by file, and deterministic, on purpose. pytest-xdist already balances
tests across the workers inside one shard; what this has to guarantee is the thing
the aggregate gate cannot check afterwards - that every file runs in exactly one
shard. A plugin that splits by recorded duration balances better and decides
membership from a cache file, which is a second input that can drift. Round-robin
over the sorted paths needs nothing but the tree, and spreads a directory of slow
integration tests over every shard rather than handing it to one.

Paths are sorted as POSIX strings so the order, and therefore the split, is the same
on every platform.
"""

import sys
from pathlib import Path


def test_files(root: Path) -> list[str]:
    """Every pytest-discoverable test module under `root`, sorted."""
    return sorted(path.as_posix() for path in root.rglob("test_*.py"))


def select(paths: list[str], index: int, total: int) -> list[str]:
    """The paths shard `index` of `total` runs (1-based, round-robin)."""
    if total < 1 or not 1 <= index <= total:
        raise ValueError(f"shard {index}/{total} does not exist")
    return paths[index - 1 :: total]


def parse(spec: str) -> tuple[int, int]:
    """`"2/3"` -> `(2, 3)`."""
    index, separator, total = spec.partition("/")
    if not separator or not index.isdigit() or not total.isdigit():
        raise ValueError(f"expected a shard as K/N, got {spec!r}")
    return int(index), int(total)


def main(argv: list[str]) -> int:
    if len(argv) != 2:
        print("usage: ci_backend_shard.py K/N TESTS_DIR", file=sys.stderr)
        return 2
    index, total = parse(argv[0])
    chosen = select(test_files(Path(argv[1])), index, total)
    if not chosen:
        # An empty list would make pytest collect the whole of `testpaths`, which
        # is the opposite of a shard.
        print(f"shard {index}/{total} selected no test files", file=sys.stderr)
        return 1
    print("\n".join(chosen))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
