"""The migration chain has one head, and this test needs no database to say so.

Two branches each wrote a migration against a `main` that ended at `0043`, each
was green, and neither could see the other: `0044_agent_embed_key_version` (#552)
and `0044_audit_impersonator` (#943) both pointed at `0043_rag_document_source_path`.
Merged one after the other they made two heads, and `alembic upgrade head` answers
`Multiple head revisions are present` and exits 255 - so no deployment could
upgrade, and four cases in `tests/test_migrations.py` failed at once (#1059).

Nothing on either pull request could have caught it. The required checks ran
against a `main` holding only one of the two, and `make db-check` is
`alembic check`, which compares the models to the chain's head and says nothing
about how many heads there are.

So the guard lives here rather than in `tests/test_migrations.py`: that module
skips on a laptop with no Postgres, and a divergence is created by a merge on
somebody's machine long before CI's database ever sees it. `ScriptDirectory`
reads the files, and the files are the whole of the question.
"""

from pathlib import Path

import pytest
from alembic.config import Config
from alembic.script import ScriptDirectory


def _chain() -> ScriptDirectory:
    root = Path(__file__).resolve().parent.parent
    config = Config(str(root / "alembic.ini"))
    config.set_main_option("script_location", str(root / "alembic"))
    return ScriptDirectory.from_config(config)


def test_the_chain_has_exactly_one_head() -> None:
    heads = _chain().get_heads()
    assert len(heads) == 1, (
        f"{len(heads)} alembic heads: {', '.join(sorted(heads))}. Two migrations "
        "written against the same parent were merged one after the other; add "
        "a merge migration without rewriting either deployed history."
    )


def test_no_unmerged_migrations_claim_the_same_parent() -> None:
    """The same defect one step earlier, and it names both files.

    One head is what breaks the upgrade, so the test above is the one that
    matters. This one fails on the fork itself rather than on its consequence,
    which is the difference between a message naming the two revisions that
    collided and one naming the two heads they produced.
    """
    parents: dict[str, list[str]] = {}
    for revision in _chain().walk_revisions():
        down = revision.down_revision
        if down is None:
            continue
        for parent in (down,) if isinstance(down, str) else down:
            parents.setdefault(parent, []).append(revision.revision)

    # Released histories must not be rewritten: an explicit merge may join them.
    chain = _chain()
    merged_ancestors = [
        {ancestor.revision for ancestor in chain.iterate_revisions(revision.revision, "base")}
        for revision in chain.walk_revisions()
        if isinstance(revision.down_revision, tuple) and len(revision.down_revision) > 1
    ]
    forked = {
        parent: sorted(children)
        for parent, children in parents.items()
        if len(children) > 1
        and not any(set(children) <= ancestors for ancestors in merged_ancestors)
    }
    assert not forked, f"unmerged revisions sharing a parent: {forked}"


@pytest.mark.parametrize(
    ("current", "remaining"),
    [
        (
            "0296_personal_model_connections",
            ["0297_agent_messages", "0298_composio_connections", "0299_merge_personal_composio"],
        ),
        (
            "0298_composio_connections",
            ["0296_personal_model_connections", "0299_merge_personal_composio"],
        ),
        (
            ("0296_personal_model_connections", "0298_composio_connections"),
            ["0299_merge_personal_composio"],
        ),
    ],
)
def test_deployed_personal_and_app_heads_apply_only_their_missing_upgrades(
    current: str | tuple[str, ...], remaining: list[str]
) -> None:
    # Traverse the actual scripts with Alembic's upgrade-planner options.
    # Reparenting an already-deployed revision must not replay its DDL.
    pending = list(
        _chain().iterate_revisions("0299_merge_personal_composio", current, implicit_base=True)
    )

    assert [revision.revision for revision in reversed(pending)] == remaining


def test_deployed_personal_and_app_revisions_keep_their_original_parents() -> None:
    chain = _chain()
    parents: dict[str, str | list[str] | tuple[str, ...] | None] = {}
    for revision in (
        "0296_personal_model_connections",
        "0297_agent_messages",
        "0298_composio_connections",
    ):
        script = chain.get_revision(revision)
        assert script is not None
        parents[revision] = script.down_revision
    assert parents == {
        "0296_personal_model_connections": "0295_tool_approval_consent",
        "0297_agent_messages": "0295_tool_approval_consent",
        "0298_composio_connections": "0297_agent_messages",
    }


# `alembic_version.version_num` is `VARCHAR(32)`, which alembic creates and never
# widens. A longer id is not refused when the file is written or when the chain is
# read - it is refused by the *database*, after the migration's DDL has run, on the
# UPDATE that records the new version. So the schema change lands and the version
# does not, which on a non-transactional step leaves a chain nobody can move.
_VERSION_NUM_LIMIT = 32


def test_no_revision_id_is_too_long_for_the_version_table() -> None:
    too_long = {
        revision.revision: len(revision.revision)
        for revision in _chain().walk_revisions()
        if len(revision.revision) > _VERSION_NUM_LIMIT
    }
    assert not too_long, (
        f"revision ids longer than {_VERSION_NUM_LIMIT} characters: {too_long}. "
        "`alembic_version.version_num` is VARCHAR(32), so such a migration runs "
        "its DDL and then fails writing the version. Shorten the id."
    )
