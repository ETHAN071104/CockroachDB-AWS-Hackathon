from __future__ import annotations

import re
import unicodedata

from dataclasses import dataclass
from typing import Literal, TypeAlias


ChatIntent: TypeAlias = Literal[
    "document_question",
    "weakness_analysis",
    "coaching_request",
    "study_plan_request",
    "unsupported_or_ambiguous",
]
FeatureRedirectTarget: TypeAlias = Literal["coaching", "study-plan"]


@dataclass(frozen=True)
class FeatureRedirect:
    target: FeatureRedirectTarget
    title: str
    message: str
    action_label: str
    original_prompt: str
    suggested_prompt: str | None = None


_PLAN_PATTERNS = tuple(
    re.compile(pattern)
    for pattern in (
        r"\b(?:study|learning|revision|revision study) plan\b",
        r"\b(?:build|create|make|organize|organise|draft)\b.*\b(?:plan|schedule|timetable)\b",
        r"\b(?:schedule|timetable)\b.*\b(?:study|revision|learning)\b",
        r"\b(?:in what order|learning order|study order)\b",
        r"\bhow (?:long|much time)\b.*\b(?:study|revise|spend)\b",
        r"学习计划|复习计划|学习时间表|复习时间表|学习顺序|复习顺序|安排.*(?:学习|复习).*(?:时间|顺序)",
    )
)

_PERSONAL_PERFORMANCE_PATTERNS = tuple(
    re.compile(pattern)
    for pattern in (
        r"\bmy weak(?:ness|nesses|est)\b",
        r"\bwhat (?:am i|i am) weak (?:at|in)\b",
        r"\bwhat do i (?:always|usually|often|keep) get wrong\b",
        r"\b(?:my )?(?:mistakes|errors|wrong answers)\b",
        r"\b(?:analy[sz]e|review|assess) (?:my )?(?:quiz )?performance\b",
        r"\b(?:lowest|worst) mastery\b",
        r"\bwhat do i struggle with\b",
        r"\bperformance history\b",
        r"我的弱点|我的弱项|最薄弱|总是答错|经常答错|错题|测验表现|考试表现|掌握度最低",
    )
)

_COACHING_PATTERNS = tuple(
    re.compile(pattern)
    for pattern in (
        r"\b(?:coach|coaching)\b",
        r"\bwhat should i (?:review|revise|practice|relearn|learn|focus on)(?: first| next)?\b",
        r"\bwhat (?:should|do) i (?:study|revise|review) next\b",
        r"\bwhich topic should i (?:review|revise|practice|focus on)\b",
        r"\bwhat to (?:review|revise|practice) (?:first|next)\b",
        r"我应该先(?:复习|学习)什么|接下来应该(?:复习|学习)什么|我该重点复习什么|辅导我",
    )
)

_AMBIGUOUS_PATTERNS = tuple(
    re.compile(pattern)
    for pattern in (
        r"^(?:help|help me|what now|what next|hello|hi|hey|thanks|thank you)[.!? ]*$",
        r"^(?:帮我|帮助我|你好|谢谢|接下来呢)[。！？ ]*$",
    )
)


def classify_chat_intent(question: str) -> ChatIntent:
    """Classify only the current submitted text using local deterministic rules."""
    normalized = _normalize(question)
    if not normalized:
        return "unsupported_or_ambiguous"

    if _matches(_PLAN_PATTERNS, normalized):
        return "study_plan_request"

    coaching_request = _matches(_COACHING_PATTERNS, normalized)
    performance_request = _matches(_PERSONAL_PERFORMANCE_PATTERNS, normalized)
    if coaching_request:
        return "coaching_request"
    if performance_request:
        return "weakness_analysis"

    if _matches(_AMBIGUOUS_PATTERNS, normalized):
        return "unsupported_or_ambiguous"

    word_count = len(re.findall(r"\w+", normalized, flags=re.UNICODE))
    if word_count < 3 and len(normalized) < 8:
        return "unsupported_or_ambiguous"
    return "document_question"


def route_suggestion(
    intent: ChatIntent,
    original_prompt: str,
) -> FeatureRedirect | None:
    """Build a user-controlled route suggestion for misplaced Chat requests."""
    cleaned_prompt = original_prompt.strip()
    if intent in {"weakness_analysis", "coaching_request"}:
        return FeatureRedirect(
            target="coaching",
            title="This question needs your learning history",
            message=(
                "Coaching uses your quiz mistakes, Learning Signals, and Learner "
                "Memories to identify what you should review."
            ),
            action_label="Open Coaching",
            original_prompt=cleaned_prompt,
            suggested_prompt=(
                "What should I review first based on my recent mistakes?"
            ),
        )
    if intent == "study_plan_request":
        return FeatureRedirect(
            target="study-plan",
            title="This question is better suited to Study Plan",
            message=(
                "Study Plan can organize your weaknesses and available materials "
                "into a learning order and time budget."
            ),
            action_label="Create Study Plan",
            original_prompt=cleaned_prompt,
            suggested_prompt=None,
        )
    return None


def _normalize(value: str) -> str:
    normalized = unicodedata.normalize("NFKC", value).casefold().strip()
    return re.sub(r"\s+", " ", normalized)


def _matches(patterns: tuple[re.Pattern[str], ...], value: str) -> bool:
    return any(pattern.search(value) is not None for pattern in patterns)
