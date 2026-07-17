from __future__ import annotations
from rag.config import LLM_PROVIDER
from functools import lru_cache

from langchain_core.output_parsers import PydanticOutputParser
from langchain_core.prompts import ChatPromptTemplate

from llm.factory import create_chat_model
from memory.models import MemoryCandidate

NON_MEMORY_MESSAGES = {
    "hi",
    "hello",
    "hey",
    "thanks",
    "thank you",
    "ok",
    "okay",
}

PARSER = PydanticOutputParser(
    pydantic_object=MemoryCandidate
)


MEMORY_EXTRACTION_PROMPT = ChatPromptTemplate.from_messages(
    [
        (
            "system",
            """
You analyze one interaction and decide whether it contains one
durable learner memory worth proposing.

Use only the user's message as evidence about the learner.
The assistant answer is not evidence of the learner's knowledge,
preferences, or difficulties.

Possible memory types:

- profile
- learning_state
- episodic
- procedural

Do not store:

- ordinary factual questions
- temporary instructions
- greetings
- facts copied from documents
- API keys, passwords, or credentials
- complete local file paths
- sensitive personal information
- assumptions based only on the assistant answer

When no durable memory exists:

- should_store must be false
- memory_type must be "none"
- content must be an empty string
- confidence must be 0
- importance must be 0

Return only the requested JSON object.
Do not use Markdown or code fences.

{format_instructions}
""".strip(),
        ),
        (
            "human",
            """
User message:

{user_message}

Assistant answer:

{assistant_answer}
""".strip(),
        ),
    ]
).partial(
    format_instructions=PARSER.get_format_instructions()
)


@lru_cache(maxsize=1)
def get_memory_extractor():
    model = create_chat_model(
        max_tokens=700,
        temperature=0,
        max_retries=2,
    )

    if LLM_PROVIDER == "groq":
        model = model.bind(
            response_format={
                "type": "json_object",
            }
        )

    return model


def extract_response_text(response) -> str:
    content = getattr(response, "content", response)

    if isinstance(content, str):
        return content.strip()

    if isinstance(content, list):
        parts: list[str] = []

        for item in content:
            if isinstance(item, str):
                parts.append(item)
            elif isinstance(item, dict):
                text = item.get("text")

                if isinstance(text, str):
                    parts.append(text)

        return "\n".join(parts).strip()

    return str(content).strip()


def propose_memory_candidate(
    user_message: str,
    assistant_answer: str,
) -> MemoryCandidate:
    cleaned_user_message = user_message.strip()

    if not cleaned_user_message:
        raise ValueError(
            "User message cannot be empty."
        )

    messages = MEMORY_EXTRACTION_PROMPT.format_messages(
        user_message=cleaned_user_message[:4000],
        assistant_answer=assistant_answer.strip()[:4000],
    )

    response = get_memory_extractor().invoke(messages)
    raw_text = extract_response_text(response)

    if not raw_text:
        raise RuntimeError(
            "Memory extractor returned an empty response."
        )

    try:
        return PARSER.parse(raw_text)

    except Exception as error:
        raise RuntimeError(
            "Memory extractor returned invalid JSON.\n"
            f"Raw model output:\n{raw_text}"
        ) from error