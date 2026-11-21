"""Choose the bot identity and emoji for a social automatic-routing reaction.

This module only makes the decision. The caller must pass the frozen eligible
room candidates and trusted continuity/name/history facts, then persist the
returned reaction through the agent-attributed reaction path.
"""

import math
import re
import unicodedata
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from uuid import UUID

import regex


@dataclass(frozen=True, slots=True)
class AutoReactionCandidate:
    """A currently eligible bot, in stable room-roster order."""

    agent_id: UUID
    name: str
    handle: str
    score: float = 0.0


@dataclass(frozen=True, slots=True)
class AutoReactionHistoryLine:
    """A bounded, already-authorized message from this room's readable history."""

    message_id: UUID
    ordinal: int
    thread_root_message_id: UUID | None
    created_at: datetime
    author_user_id: UUID | None
    author_agent_id: UUID | None
    body: str
    mentioned_agent_ids: tuple[UUID, ...] = ()


@dataclass(frozen=True, slots=True)
class AutoReactionSocialIntent:
    """Optional structured router choice; absence enables the deterministic emoji fallback."""

    react: bool
    emoji: str


@dataclass(frozen=True, slots=True)
class RoomAutoReaction:
    """An agent reaction to the accepted human message."""

    message_id: UUID
    agent_id: UUID
    reaction: str


_EMOJI_BASE = regex.compile(r"(?:\p{Extended_Pictographic}|\p{Emoji_Presentation})")
_FLAG = regex.compile(r"\A\p{Regional_Indicator}{2}\Z")
_KEYCAP = regex.compile(r"\A[#*0-9]\uFE0F?\u20E3\Z")
_FOR_SOMEONE = frozenset({"👍", "🙏", "❤️", "❤"})
_GENERAL_ACKNOWLEDGMENTS = frozenset(
    {
        "appreciate it",
        "all good",
        "awesome",
        "cheers",
        "cool",
        "got it",
        "great",
        "nice",
        "ok",
        "okay",
        "perfect",
        "sounds good",
        "thank you",
        "thank you so much",
        "thanks",
        "thanks again",
        "thanks so much",
        "thx",
        "ty",
        "understood",
    }
)
_ACKNOWLEDGEMENT_EMOJIS: tuple[tuple[str, frozenset[str]], ...] = (
    (
        "❤️",
        frozenset({"❤", "😍", "🥰", "💜", "💙", "💚", "🧡", "💛", "🤍", "🩷", "😘", "🫶"}),
    ),
    ("🎉", frozenset({"🎉", "🥳", "🎊", "🏆"})),
    ("🙏", frozenset({"🙏"})),
    ("🌙", frozenset({"🌙", "😴", "💤"})),
    ("☀️", frozenset({"☀", "🌞", "🌅"})),
    ("👋", frozenset({"👋"})),
    ("👍", frozenset({"👍", "👌", "🙌", "💪", "👏", "✅", "🆗"})),
)


def fallback_social_reaction(text: str) -> str | None:
    """Return the upstream deterministic reaction for an emoji-only message.

    Words in any language and questions are left to the model's structured
    social result. This fallback intentionally uses emoji only.
    """
    bare = text.replace("<3", "❤")
    bare = regex.sub(r"[\uFE0F\u200D\p{Emoji_Modifier}]", "", bare)
    if _is_question(bare):
        return None
    without_emoji = regex.sub(r"\p{Extended_Pictographic}", "", bare)
    if regex.search(r"[\p{L}\p{N}]", without_emoji):
        return None
    for emoji in regex.findall(r"\p{Extended_Pictographic}", bare):
        for reaction, matches in _ACKNOWLEDGEMENT_EMOJIS:
            if emoji in matches:
                return reaction
    return None


def _normalize_reaction(value: str) -> str | None:
    """Accept one emoji grapheme and map thumbs-up/down to the shared reaction codes."""
    emoji = unicodedata.normalize("NFC", value.strip())
    if not emoji or any(character.isspace() for character in emoji):
        return None
    # Match the router transport limit, measured as UTF-16 units like JavaScript.
    if len(emoji.encode("utf-16-le")) // 2 > 16 or regex.fullmatch(r"\X", emoji) is None:
        return None
    if not (_EMOJI_BASE.search(emoji) or _FLAG.fullmatch(emoji) or _KEYCAP.fullmatch(emoji)):
        return None
    without_selector = emoji.replace("\ufe0f", "")
    if without_selector == "👍":
        return "👍"
    if without_selector == "👎":
        return "👎"
    return emoji


def _eligible_id(candidate_id: UUID | None, eligible: set[UUID]) -> UUID | None:
    return candidate_id if candidate_id in eligible else None


def _is_question(text: str) -> bool:
    return bool(re.search(r"[?\uFF1F\u061F\u00BF]", text))


def _is_general_acknowledgment(text: str) -> bool:
    """Recognize short generic acknowledgements without guessing a target."""
    plain = regex.sub(r"[\uFE0F\u200D\p{Emoji_Modifier}\p{Extended_Pictographic}]", " ", text)
    plain = regex.sub(r"[^\p{L}\p{N}]+", " ", plain).casefold().strip()
    if plain in _GENERAL_ACKNOWLEDGMENTS:
        return True
    return bool(
        re.match(
            r"^(?:appreciate it|got it|thank you|thanks|thx|ty|understood|okay|ok|"
            r"cool|great|nice|perfect|awesome|cheers)\b",
            plain,
        )
    )


def _same_thread(line: AutoReactionHistoryLine, thread_root_message_id: UUID | None) -> bool:
    return line.thread_root_message_id == thread_root_message_id or (
        thread_root_message_id is not None and line.message_id == thread_root_message_id
    )


def _thanked_agent(
    *,
    requester_id: UUID,
    thread_root_message_id: UUID | None,
    accepted_at: datetime,
    continuity_window_ms: int,
    history: Sequence[AutoReactionHistoryLine],
    thread_root: AutoReactionHistoryLine | None,
    eligible: set[UUID],
) -> UUID | None:
    """Find the agent thanked in an uninterrupted exchange, if one is provable."""
    here = [
        line
        for line in history
        if _same_thread(line, thread_root_message_id)
        and 0 <= (accepted_at - line.created_at).total_seconds() * 1000 <= continuity_window_ms
    ]
    # Router history is newest first. Sorting here makes the contract explicit
    # for callers that assemble rows from more than one source.
    here.sort(key=lambda line: (line.created_at, line.ordinal), reverse=True)
    if (
        thread_root is not None
        and thread_root_message_id is not None
        and 0
        <= (accepted_at - thread_root.created_at).total_seconds() * 1000
        <= continuity_window_ms
    ):
        here.append(thread_root)

    own_index = next(
        (index for index, line in enumerate(here) if line.author_user_id == requester_id),
        None,
    )
    if own_index is None or own_index < 1:
        return None

    since = here[:own_index]
    if not since or any(line.author_agent_id is None for line in since):
        return None

    previous_human = here[own_index]
    mentioned = [
        agent_id for agent_id in previous_human.mentioned_agent_ids if agent_id in eligible
    ]
    agent_id = mentioned[0] if len(mentioned) == 1 else here[own_index - 1].author_agent_id
    if agent_id not in eligible or not any(line.author_agent_id == agent_id for line in since):
        return None

    last_agent_message = next(
        (
            line
            for line in (*history, *((thread_root,) if thread_root is not None else ()))
            if line.author_agent_id == agent_id
            and _same_thread(line, thread_root_message_id)
            and 0 <= (accepted_at - line.created_at).total_seconds() * 1000 <= continuity_window_ms
        ),
        None,
    )
    if last_agent_message is not None:
        lines = last_agent_message.body.strip().splitlines()
        if lines and _is_question(lines[-1]):
            return None
    return agent_id


def _best_placed_agent(
    *,
    candidates: Sequence[AutoReactionCandidate],
    history: Sequence[AutoReactionHistoryLine],
    eligible: set[UUID],
) -> UUID | None:
    if not candidates:
        return None

    recent_order: dict[UUID, int] = {}
    for index, line in enumerate(history):
        author_agent_id = line.author_agent_id
        if (
            author_agent_id is not None
            and author_agent_id in eligible
            and author_agent_id not in recent_order
        ):
            recent_order[author_agent_id] = index
    history_length = len(history)
    ranked = list(enumerate(candidates))
    ranked.sort(
        key=lambda pair: (
            -(pair[1].score if math.isfinite(pair[1].score) and 0 <= pair[1].score <= 1 else 0.0),
            recent_order.get(pair[1].agent_id, history_length),
            pair[0],
        )
    )
    return ranked[0][1].agent_id


def choose_room_auto_reaction(
    *,
    message_id: UUID,
    requester_id: UUID,
    thread_root_message_id: UUID | None,
    accepted_at: datetime,
    text: str,
    eligible_candidates: Sequence[AutoReactionCandidate],
    selected_agent_ids: Sequence[UUID] = (),
    router_social: AutoReactionSocialIntent | None = None,
    named_agent_ids: Sequence[UUID] = (),
    held_for_agent_id: UUID | None = None,
    talking_with_agent_id: UUID | None = None,
    author_is_agent: bool = False,
    person_directed: bool = False,
    people_talking: bool = False,
    history: Sequence[AutoReactionHistoryLine] = (),
    thread_root: AutoReactionHistoryLine | None = None,
    continuity_window_ms: int = 30 * 60 * 1000,
) -> RoomAutoReaction | None:
    """Select the one eligible bot that gives a social reaction, or return none.

    The candidate list and history are runtime-filtered room data. Actor identity
    comes from a single consistent named agent in the accepted human text,
    continuous exchange, or server-owned hold/conversation state. Conflicting
    proof suppresses the reaction. The model cannot assign a reaction to an
    agent with an addressee or best-candidate hint.
    """
    if (
        author_is_agent
        or person_directed
        or people_talking
        or selected_agent_ids
        or not eligible_candidates
        or continuity_window_ms < 0
        or _is_question(text)
    ):
        return None

    candidate_ids = [candidate.agent_id for candidate in eligible_candidates]
    if len(candidate_ids) != len(set(candidate_ids)):
        return None
    eligible = set(candidate_ids)

    if router_social is None:
        raw_reaction = fallback_social_reaction(text)
    elif router_social.react:
        raw_reaction = router_social.emoji or fallback_social_reaction(text)
    else:
        return None
    reaction = None if raw_reaction is None else _normalize_reaction(raw_reaction)
    if reaction is None:
        return None

    named = list(dict.fromkeys(agent_id for agent_id in named_agent_ids if agent_id in eligible))
    if len(named) > 1:
        return None
    thanked = _thanked_agent(
        requester_id=requester_id,
        thread_root_message_id=thread_root_message_id,
        accepted_at=accepted_at,
        continuity_window_ms=continuity_window_ms,
        history=history,
        thread_root=thread_root,
        eligible=eligible,
    )
    # Only message content, the trusted continuity scan, or server-owned
    # routing state proves a person as the target.
    proven_targets = list(
        dict.fromkeys(
            target
            for target in (
                named[0] if len(named) == 1 else None,
                thanked,
                _eligible_id(held_for_agent_id, eligible),
                _eligible_id(talking_with_agent_id, eligible),
            )
            if target is not None
        )
    )
    if len(proven_targets) > 1:
        return None
    agent_id = proven_targets[0] if proven_targets else None

    # Acknowledgements for a person need an explicit or proven conversation
    # target. Model-selected addressees and generic thanks are not proof.
    if agent_id is None and reaction not in _FOR_SOMEONE:
        if _is_general_acknowledgment(text):
            return None
        agent_id = _best_placed_agent(
            candidates=eligible_candidates,
            history=history,
            eligible=eligible,
        )
    if agent_id is None:
        return None
    return RoomAutoReaction(message_id=message_id, agent_id=agent_id, reaction=reaction)
