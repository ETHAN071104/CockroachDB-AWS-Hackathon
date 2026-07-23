from __future__ import annotations

import unittest

from backend.memory.service import MemorySearchResult
from backend.rag.chat_intent import classify_chat_intent, route_suggestion
from backend.rag.chat_service import _validate_grounded_answer
from backend.rag.rag_service import RetrievedSource, format_memory_context


class ChatFeatureRoutingTest(unittest.TestCase):
    def setUp(self) -> None:
        self.source = RetrievedSource(
            index=1,
            filename="lesson.pdf",
            page_number=1,
            chunk_index=0,
            distance=0.1,
            text="Grounded lesson evidence.",
            document_id=1,
            mime_type="application/pdf",
        )

    def test_deterministic_intent_examples(self) -> None:
        cases = {
            "How does photosynthesis store energy?": "document_question",
            "What are my weaknesses?": "weakness_analysis",
            "What do I always get wrong?": "weakness_analysis",
            "Which topic has my lowest mastery?": "weakness_analysis",
            "What should I learn first based on my mistakes?": "coaching_request",
            "What should I revise next?": "coaching_request",
            "Analyse my quiz performance.": "weakness_analysis",
            "Create a study plan based on my history.": "study_plan_request",
            "Build a schedule for me.": "study_plan_request",
            "\u6211\u7684\u5f31\u70b9\u662f\u4ec0\u4e48\uff1f": "weakness_analysis",
            "\u6211\u5e94\u8be5\u5148\u590d\u4e60\u4ec0\u4e48\uff1f": "coaching_request",
            "\u521b\u5efa\u4e00\u4e2a\u5b66\u4e60\u8ba1\u5212": "study_plan_request",
            "Help me": "unsupported_or_ambiguous",
        }
        for question, expected in cases.items():
            with self.subTest(question=question):
                self.assertEqual(classify_chat_intent(question), expected)

    def test_route_suggestion_keeps_original_prompt_without_interpreting_it(self) -> None:
        prompt = 'What are my weaknesses? <img src=x onerror="alert(1)">'
        suggestion = route_suggestion("weakness_analysis", prompt)
        self.assertIsNotNone(suggestion)
        assert suggestion is not None
        self.assertEqual(suggestion.target, "coaching")
        self.assertEqual(suggestion.original_prompt, prompt)
        self.assertNotIn("<img", suggestion.message)

    def test_grounding_states_are_distinct(self) -> None:
        status, _ = _validate_grounded_answer("anything", [])
        self.assertEqual(status, "no_relevant_chunks")

        status, _ = _validate_grounded_answer(
            "I could not find sufficient information in the indexed files.",
            [self.source],
        )
        self.assertEqual(status, "retrieved_chunks_insufficient")

        status, _ = _validate_grounded_answer(
            "A factual answer without a source marker.",
            [self.source],
        )
        self.assertEqual(status, "citation_validation_failed")

        status, _ = _validate_grounded_answer(
            "Supported material is explained here [1].\n\n"
            "This additional paragraph makes several factual claims without any citation marker.",
            [self.source],
        )
        self.assertEqual(status, "unsupported_claims")

        status, answer = _validate_grounded_answer(
            "The source supports this explanation [1].",
            [self.source],
        )
        self.assertEqual(status, "grounded")
        self.assertEqual(answer, "The source supports this explanation [1].")

    def test_learner_memory_is_not_formatted_as_document_evidence(self) -> None:
        context = format_memory_context(
            [
                MemorySearchResult(
                    memory_id=7,
                    memory_type="learning_state",
                    content="The learner finds recursion difficult.",
                    confidence=0.8,
                    importance=0.9,
                    distance=0.1,
                )
            ]
        )
        self.assertIn("learner", context)
        self.assertNotIn("[1]", context)
        self.assertNotIn("File:", context)


if __name__ == "__main__":
    unittest.main()
