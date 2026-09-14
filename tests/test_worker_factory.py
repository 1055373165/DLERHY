"""All translation worker construction goes through workers.factory."""

import unittest

from book_agent.core.config import Settings
from book_agent.domain.enums import ProviderKind
from book_agent.domain.models.provider_credential import ProviderCredential
from book_agent.services.chapter_concept_autolock import (
    FallbackConceptResolver,
    HeuristicConceptResolver,
    build_default_concept_resolver,
)
from book_agent.services.secrets import encrypt_secret
from book_agent.workers.factory import build_translation_worker, build_worker_from_credential
from book_agent.workers.translator import EchoTranslationWorker


class WorkerFactoryTests(unittest.TestCase):
    def _settings(self) -> Settings:
        return Settings(
            translation_backend="openai_compatible",
            translation_model="settings-model",
            translation_openai_api_key="sk-settings",
            translation_prompt_profile="cn-native-faithful-v3",
            translation_input_cost_per_1m_tokens=0.27,
            translation_output_cost_per_1m_tokens=1.1,
        )

    def test_credential_worker_uses_settings_prompt_profile_and_prices(self) -> None:
        record = ProviderCredential(
            id="11111111-1111-4111-8111-111111111111",
            name="Stored DeepSeek",
            provider_kind=ProviderKind.OPENAI_COMPATIBLE,
            model_name="deepseek-chat",
            base_url="https://api.deepseek.com/v1",
            api_key_ciphertext=encrypt_secret("sk-stored"),
            streaming=False,
            max_output_tokens=4096,
            timeout_seconds=30,
            max_retries=1,
            retry_backoff_seconds_x10=15,
            is_active=True,
        )

        worker = build_worker_from_credential(record, self._settings())

        self.assertEqual(worker.prompt_profile, "cn-native-faithful-v3")
        self.assertEqual(worker.client.input_cost_per_1m_tokens, 0.27)
        self.assertEqual(worker.client.output_cost_per_1m_tokens, 1.1)
        metadata = worker.metadata()
        self.assertEqual(metadata.model_name, "deepseek-chat")
        self.assertEqual(metadata.runtime_config["credential_name"], "Stored DeepSeek")
        self.assertEqual(metadata.runtime_config["base_url"], "https://api.deepseek.com/v1")

    def test_settings_worker_matches_credential_worker_shape(self) -> None:
        worker = build_translation_worker(self._settings())

        self.assertEqual(worker.prompt_profile, "cn-native-faithful-v3")
        self.assertEqual(worker.client.input_cost_per_1m_tokens, 0.27)
        self.assertEqual(worker.metadata().runtime_config["provider"], "openai_compatible")


    def test_concept_resolver_reuses_the_translation_workers_provider(self) -> None:
        worker = build_translation_worker(self._settings())

        resolver = build_default_concept_resolver(translation_worker=worker)

        self.assertIsInstance(resolver, FallbackConceptResolver)
        self.assertIs(resolver.resolvers[0].client, worker.client)
        self.assertEqual(resolver.resolvers[0].model_name, "settings-model")

    def test_concept_resolver_is_heuristic_for_echo_worker(self) -> None:
        resolver = build_default_concept_resolver(translation_worker=EchoTranslationWorker())

        self.assertIsInstance(resolver, HeuristicConceptResolver)


if __name__ == "__main__":
    unittest.main()
