import unittest

from aldernia_runtime.openai_provider import provider_available, wif_configured


class OpenAIProviderTests(unittest.TestCase):
    def test_wif_requires_all_non_secret_identity_fields_and_github_oidc(self):
        env = {
            "OPENAI_WIF_AUDIENCE": "aud",
            "OPENAI_IDENTITY_PROVIDER_ID": "idp",
            "OPENAI_SERVICE_ACCOUNT_ID": "svc",
            "ACTIONS_ID_TOKEN_REQUEST_URL": "https://example.test/oidc",
            "ACTIONS_ID_TOKEN_REQUEST_TOKEN": "runner-token",
        }
        self.assertTrue(wif_configured(env))
        self.assertTrue(provider_available(env=env))

    def test_partial_wif_is_not_treated_as_provider(self):
        env = {
            "OPENAI_WIF_AUDIENCE": "aud",
            "OPENAI_IDENTITY_PROVIDER_ID": "idp",
            "ACTIONS_ID_TOKEN_REQUEST_URL": "https://example.test/oidc",
            "ACTIONS_ID_TOKEN_REQUEST_TOKEN": "runner-token",
        }
        self.assertFalse(wif_configured(env))
        self.assertFalse(provider_available(env=env))

    def test_bounded_api_key_remains_fallback(self):
        self.assertTrue(provider_available(api_key="test-key", env={}))
        self.assertFalse(provider_available(api_key="", env={}))


if __name__ == "__main__":
    unittest.main()
