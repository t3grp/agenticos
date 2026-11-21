"""Social routing reactions are limited to a trusted bot and one emoji."""

from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest

from app.services.room_auto_reaction import (
    AutoReactionCandidate,
    AutoReactionHistoryLine,
    AutoReactionSocialIntent,
    choose_room_auto_reaction,
    fallback_social_reaction,
)


def test_explicit_name_selects_the_named_bot_and_normalizes_thumbs_up():
    requester_id, first_id, second_id, message_id = uuid4(), uuid4(), uuid4(), uuid4()

    reaction = choose_room_auto_reaction(
        message_id=message_id,
        requester_id=requester_id,
        thread_root_message_id=None,
        accepted_at=datetime.now(UTC),
        text="Thank you",
        eligible_candidates=(
            AutoReactionCandidate(first_id, "Ada", "ada", score=0.1),
            AutoReactionCandidate(second_id, "Theo", "theo", score=0.9),
        ),
        router_social=AutoReactionSocialIntent(react=True, emoji="👍️"),
        named_agent_ids=(second_id,),
    )

    assert reaction is not None
    assert (reaction.message_id, reaction.agent_id, reaction.reaction) == (
        message_id,
        second_id,
        "👍",
    )


def test_multiple_named_agents_suppress_automatic_reaction():
    first_id, second_id = uuid4(), uuid4()

    reaction = choose_room_auto_reaction(
        message_id=uuid4(),
        requester_id=uuid4(),
        thread_root_message_id=None,
        accepted_at=datetime.now(UTC),
        text="Good night",
        eligible_candidates=(
            AutoReactionCandidate(first_id, "Ada", "ada"),
            AutoReactionCandidate(second_id, "Theo", "theo"),
        ),
        router_social=AutoReactionSocialIntent(react=True, emoji="🌙"),
        named_agent_ids=(first_id, second_id),
    )

    assert reaction is None


def test_conflicting_named_and_server_owned_targets_suppress_reaction():
    named_id, held_id = uuid4(), uuid4()

    reaction = choose_room_auto_reaction(
        message_id=uuid4(),
        requester_id=uuid4(),
        thread_root_message_id=None,
        accepted_at=datetime.now(UTC),
        text="Good night",
        eligible_candidates=(
            AutoReactionCandidate(named_id, "Ada", "ada"),
            AutoReactionCandidate(held_id, "Theo", "theo"),
        ),
        router_social=AutoReactionSocialIntent(react=True, emoji="🌙"),
        named_agent_ids=(named_id,),
        held_for_agent_id=held_id,
    )

    assert reaction is None


def test_missing_model_social_uses_emoji_fallback_and_unique_named_bot():
    requester_id, agent_id, message_id = uuid4(), uuid4(), uuid4()

    reaction = choose_room_auto_reaction(
        message_id=message_id,
        requester_id=requester_id,
        thread_root_message_id=None,
        accepted_at=datetime.now(UTC),
        text="👋",
        eligible_candidates=(AutoReactionCandidate(agent_id, "Ada", "ada"),),
        named_agent_ids=(agent_id,),
    )

    assert reaction is not None
    assert reaction.agent_id == agent_id
    assert reaction.reaction == "👋"


def test_explicit_no_reaction_suppresses_emoji_fallback():
    reaction = choose_room_auto_reaction(
        message_id=uuid4(),
        requester_id=uuid4(),
        thread_root_message_id=None,
        accepted_at=datetime.now(UTC),
        text="👍",
        eligible_candidates=(AutoReactionCandidate(uuid4(), "Ada", "ada"),),
        router_social=AutoReactionSocialIntent(react=False, emoji=""),
    )

    assert reaction is None


def test_empty_positive_social_emoji_uses_deterministic_fallback():
    agent_id = uuid4()

    reaction = choose_room_auto_reaction(
        message_id=uuid4(),
        requester_id=uuid4(),
        thread_root_message_id=None,
        accepted_at=datetime.now(UTC),
        text="👋",
        eligible_candidates=(AutoReactionCandidate(agent_id, "Ada", "ada"),),
        router_social=AutoReactionSocialIntent(react=True, emoji=""),
    )

    assert reaction is not None
    assert reaction.reaction == "👋"


def test_someone_directed_heart_needs_a_proven_target():
    only_candidate = AutoReactionCandidate(uuid4(), "Ada", "ada", score=1.0)

    reaction = choose_room_auto_reaction(
        message_id=uuid4(),
        requester_id=uuid4(),
        thread_root_message_id=None,
        accepted_at=datetime.now(UTC),
        text="Thanks",
        eligible_candidates=(only_candidate,),
        router_social=AutoReactionSocialIntent(react=True, emoji="❤️"),
    )

    assert reaction is None


def test_room_emoji_uses_server_ranked_eligible_bot():
    first_id, second_id = uuid4(), uuid4()

    reaction = choose_room_auto_reaction(
        message_id=uuid4(),
        requester_id=uuid4(),
        thread_root_message_id=None,
        accepted_at=datetime.now(UTC),
        text="Good night",
        eligible_candidates=(
            AutoReactionCandidate(first_id, "Ada", "ada", score=0.2),
            AutoReactionCandidate(second_id, "Theo", "theo", score=0.8),
        ),
        router_social=AutoReactionSocialIntent(react=True, emoji="🌙"),
    )

    assert reaction is not None
    assert reaction.agent_id == second_id
    assert reaction.reaction == "🌙"


def test_interrupted_thanks_do_not_get_assigned_to_an_agent():
    requester_id, other_person_id, agent_id = uuid4(), uuid4(), uuid4()
    now = datetime.now(UTC)
    history = (
        AutoReactionHistoryLine(
            message_id=uuid4(),
            ordinal=3,
            thread_root_message_id=None,
            created_at=now - timedelta(seconds=1),
            author_user_id=other_person_id,
            author_agent_id=None,
            body="One more thing",
        ),
        AutoReactionHistoryLine(
            message_id=uuid4(),
            ordinal=2,
            thread_root_message_id=None,
            created_at=now - timedelta(seconds=2),
            author_user_id=None,
            author_agent_id=agent_id,
            body="Here is the answer.",
        ),
        AutoReactionHistoryLine(
            message_id=uuid4(),
            ordinal=1,
            thread_root_message_id=None,
            created_at=now - timedelta(seconds=3),
            author_user_id=requester_id,
            author_agent_id=None,
            body="Can you check this?",
            mentioned_agent_ids=(agent_id,),
        ),
    )

    reaction = choose_room_auto_reaction(
        message_id=uuid4(),
        requester_id=requester_id,
        thread_root_message_id=None,
        accepted_at=now,
        text="👍",
        eligible_candidates=(AutoReactionCandidate(agent_id, "Ada", "ada"),),
        history=history,
    )

    assert reaction is None


def test_uninterrupted_thanks_react_as_the_answering_bot():
    requester_id, agent_id, message_id = uuid4(), uuid4(), uuid4()
    now = datetime.now(UTC)
    history = (
        AutoReactionHistoryLine(
            message_id=uuid4(),
            ordinal=2,
            thread_root_message_id=None,
            created_at=now - timedelta(seconds=1),
            author_user_id=None,
            author_agent_id=agent_id,
            body="Here is the answer.",
        ),
        AutoReactionHistoryLine(
            message_id=uuid4(),
            ordinal=1,
            thread_root_message_id=None,
            created_at=now - timedelta(seconds=2),
            author_user_id=requester_id,
            author_agent_id=None,
            body="Can you check this?",
            mentioned_agent_ids=(agent_id,),
        ),
    )

    reaction = choose_room_auto_reaction(
        message_id=message_id,
        requester_id=requester_id,
        thread_root_message_id=None,
        accepted_at=now,
        text="👍",
        eligible_candidates=(AutoReactionCandidate(agent_id, "Ada", "ada"),),
        history=history,
    )

    assert reaction is not None
    assert reaction.agent_id == agent_id
    assert reaction.reaction == "👍"


@pytest.mark.parametrize(
    "flags",
    [
        {"selected_agent_ids": (uuid4(),)},
        {"author_is_agent": True},
        {"person_directed": True},
        {"people_talking": True},
    ],
)
def test_routing_reply_or_human_conversation_suppresses_social_reaction(flags):
    reaction = choose_room_auto_reaction(
        message_id=uuid4(),
        requester_id=uuid4(),
        thread_root_message_id=None,
        accepted_at=datetime.now(UTC),
        text="👋",
        eligible_candidates=(AutoReactionCandidate(uuid4(), "Ada", "ada"),),
        **flags,
    )

    assert reaction is None


def test_invalid_model_emoji_does_not_fall_back_to_a_different_reaction():
    candidate = AutoReactionCandidate(uuid4(), "Ada", "ada")

    reaction = choose_room_auto_reaction(
        message_id=uuid4(),
        requester_id=uuid4(),
        thread_root_message_id=None,
        accepted_at=datetime.now(UTC),
        text="👍",
        eligible_candidates=(candidate,),
        router_social=AutoReactionSocialIntent(react=True, emoji="hello"),
    )

    assert reaction is None


@pytest.mark.parametrize(
    ("text", "emoji"),
    [
        ("Thanks", "🙏"),
        ("Thank you", "🎉"),
        ("Can you check this?", "👍"),
    ],
)
def test_generic_acknowledgments_and_questions_do_not_trigger_router_reactions(text, emoji):
    agent_id = uuid4()

    reaction = choose_room_auto_reaction(
        message_id=uuid4(),
        requester_id=uuid4(),
        thread_root_message_id=None,
        accepted_at=datetime.now(UTC),
        text=text,
        eligible_candidates=(AutoReactionCandidate(agent_id, "Ada", "ada"),),
        router_social=AutoReactionSocialIntent(react=True, emoji=emoji),
    )

    assert reaction is None


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("👍🎉", "👍"),
        ("🎉👋", "🎉"),
        ("🌙", "🌙"),
        ("Thank you 🙏", None),
        ("👍?", None),
    ],
)
def test_fallback_social_emoji_mapping_is_stable(text, expected):
    assert fallback_social_reaction(text) == expected
