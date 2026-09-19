import unittest

from book_agent.services.chapter_concept_autolock import ConceptResolutionPayload, FallbackConceptResolver


class _FailingResolver:
    def resolve(self, **_kwargs):
        raise RuntimeError("HTTP 402: Insufficient Balance")


class _StaticResolver:
    def resolve(self, *, source_term, **_kwargs):
        return ConceptResolutionPayload(source_term=source_term, canonical_zh="智能体"), None


class FallbackConceptResolverTests(unittest.TestCase):
    def test_provider_error_falls_through_to_next_resolver(self) -> None:
        resolver = FallbackConceptResolver(resolvers=(_FailingResolver(), _StaticResolver()))

        with self.assertLogs("book_agent.services.chapter_concept_autolock", level="WARNING"):
            resolution, usage = resolver.resolve(
                source_term="agent",
                chapter_title="Chapter One",
                chapter_brief=None,
                examples=[],
            )

        self.assertIsNotNone(resolution)
        self.assertEqual(resolution.canonical_zh, "智能体")
        self.assertIsNone(usage)


if __name__ == "__main__":
    unittest.main()
