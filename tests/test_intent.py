"""Tests for the multi-category intent classifier."""

import pytest
from nadirclaw.intent import (
    INTENT_CATEGORIES,
    PIPELINE_INTENTS,
    IntentClassifier,
    IntentResult,
    get_intent_classifier,
)


class TestIntentClassifier:
    """Tests for IntentClassifier."""

    @pytest.fixture(autouse=True)
    def classifier(self):
        self.clf = get_intent_classifier()

    def test_returns_intent_result(self):
        result = self.clf.classify("Write a Python function")
        assert isinstance(result, IntentResult)
        assert result.intent in INTENT_CATEGORIES
        assert isinstance(result.confidence, float)
        assert isinstance(result.needs_pipeline, bool)

    def test_code_generation_intent(self):
        result = self.clf.classify("Write a REST API endpoint for user authentication")
        assert result.intent == "code_generation"
        assert result.needs_pipeline is True

    def test_architecture_intent(self):
        result = self.clf.classify("Design a microservices architecture for an e-commerce platform")
        assert result.intent == "architecture"
        assert result.needs_pipeline is True

    def test_debugging_intent(self):
        result = self.clf.classify("Debug why the API returns 500 errors under load")
        assert result.intent == "debugging"
        assert result.needs_pipeline is True

    def test_security_analysis_intent(self):
        result = self.clf.classify("Perform a security audit of the authentication system")
        assert result.intent == "security_analysis"
        assert result.needs_pipeline is True

    def test_documentation_intent(self):
        result = self.clf.classify("Write API documentation for the user management endpoints")
        assert result.intent == "documentation"
        assert result.needs_pipeline is True

    def test_simple_qa_no_pipeline(self):
        result = self.clf.classify("What is the capital of France?")
        assert result.intent == "simple_qa"
        assert result.needs_pipeline is False

    def test_general_qa_intent(self):
        result = self.clf.classify("What is the difference between REST and GraphQL APIs?")
        assert result.intent == "general_qa"

    def test_scores_dict_populated(self):
        result = self.clf.classify("Write a function to sort a list")
        assert isinstance(result.scores, dict)
        assert len(result.scores) == len(INTENT_CATEGORIES)

    def test_latency_recorded(self):
        result = self.clf.classify("Hello world")
        assert result.latency_ms >= 0

    def test_keyword_boost_applied(self):
        """Keyword boost should increase score for matching patterns."""
        result = self.clf.classify("Write a Python function to validate email")
        # code_generation keyword pattern should match "Write a...function"
        assert result.keyword_boost is not None or result.intent == "code_generation"

    def test_all_categories_have_centroids(self):
        """All 8 intent categories should have loaded centroids."""
        assert len(self.clf._centroids) == 8
        for cat in INTENT_CATEGORIES:
            assert cat in self.clf._centroids, f"Missing centroid for {cat}"

    def test_pipeline_intents_subset_of_categories(self):
        """PIPELINE_INTENTS should be a subset of INTENT_CATEGORIES."""
        for intent in PIPELINE_INTENTS:
            assert intent in INTENT_CATEGORIES


class TestIntentSingleton:
    def test_singleton_returns_same_instance(self):
        c1 = get_intent_classifier()
        c2 = get_intent_classifier()
        assert c1 is c2
