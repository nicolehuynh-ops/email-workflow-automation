import json
import io
import unittest
import urllib.error
from email.message import Message

from outreach.gateway import GatewayConfig, GatewayError, HiiveGatewayClassifierClient, config_from_env


class FakeResponse:
    def __init__(self, value):
        self.value = value

    def __enter__(self):
        return self

    def __exit__(self, *_):
        return False

    def read(self):
        return json.dumps(self.value).encode("utf-8")


class FakeOpener:
    def __init__(self, responses):
        self.responses = list(responses)
        self.requests = []

    def __call__(self, request, timeout):
        self.requests.append((request, timeout))
        return FakeResponse(self.responses.pop(0))


class RaisingOpener:
    def __init__(self, error):
        self.error = error

    def __call__(self, *_, **__):
        raise self.error


class SequenceOpener:
    def __init__(self, outcomes):
        self.outcomes = list(outcomes)
        self.requests = []

    def __call__(self, request, timeout):
        self.requests.append((request, timeout))
        outcome = self.outcomes.pop(0)
        if isinstance(outcome, BaseException):
            raise outcome
        return FakeResponse(outcome)


class GatewayTests(unittest.TestCase):
    def test_service_token_model_discovery_uses_access_headers(self):
        opener = FakeOpener([{"data": [{"id": "openai/gpt-5.6-luna"}]}])
        client = HiiveGatewayClassifierClient(GatewayConfig(
            "https://ai-gateway.hiive.network/compat", "openai/gpt-5.6-luna", service_client_id="client", service_client_secret="secret"
        ), opener)
        client.validate_configured_model()
        request = opener.requests[0][0]
        self.assertEqual("https://ai-gateway.hiive.network/compat/models", request.full_url)
        headers = dict(request.header_items())
        self.assertEqual("client", headers["Cf-access-client-id"])
        self.assertEqual("secret", headers["Cf-access-client-secret"])
        self.assertIsNone(request.get_header("Authorization"))

    def test_user_token_classification_parses_structured_json(self):
        opener = FakeOpener([{
            "id": "completion-1",
            "choices": [{"message": {"content": "```json\n{\"label\": \"interested\", \"confidence\": 0.95, \"rationale\": \"Requested a meeting\"}\n```"}}],
        }])
        client = HiiveGatewayClassifierClient(GatewayConfig(
            "https://ai-gateway.hiive.network/compat", "openai/gpt-5.6-luna", access_token="user-token"
        ), opener)
        result = client.classify("I'd like to book a meeting.")
        self.assertEqual("interested", result.label)
        self.assertEqual(0.95, result.confidence)
        request = opener.requests[0][0]
        self.assertEqual("user-token", request.get_header("Cf-access-token"))
        self.assertEqual("POST", request.get_method())
        self.assertIn(b"chat/completions", request.full_url.encode())
        body = json.loads(request.data.decode("utf-8"))
        self.assertEqual(1024, body["max_completion_tokens"])
        self.assertNotIn("max_tokens", body)
        self.assertNotIn("temperature", body)

    def test_invalid_classifier_result_is_rejected(self):
        opener = FakeOpener([{"choices": [{"message": {"content": "{\"label\": \"unknown\", \"confidence\": 1, \"rationale\": \"x\"}"}}]}])
        client = HiiveGatewayClassifierClient(GatewayConfig(
            "https://ai-gateway.hiive.network/compat", "openai/gpt-5.6-luna", access_token="token", max_attempts=1
        ), opener)
        with self.assertRaises(GatewayError):
            client.classify("Hello")

    def test_batch_classification_uses_one_request_and_exact_item_ids(self):
        opener = FakeOpener([{
            "id": "batch-response-1",
            "choices": [{"message": {"content": json.dumps({"results": [
                {"id": "item-a", "label": "interested", "confidence": 0.95, "rationale": "Positive"},
                {"id": "item-b", "label": "unsubscribe", "confidence": 1.0, "rationale": "Opt out"},
            ]})}}],
        }])
        client = HiiveGatewayClassifierClient(GatewayConfig(
            "https://ai-gateway.hiive.network/compat", "openai/gpt-5.6-luna", access_token="token", batch_size=10
        ), opener)
        result = client.classify_batch([("item-a", "Yes, let's talk."), ("item-b", "Unsubscribe me.")])
        self.assertEqual(1, len(opener.requests))
        self.assertEqual("interested", result.results["item-a"].label)
        self.assertEqual("unsubscribe", result.results["item-b"].label)
        self.assertEqual("batch-response-1", result.response_id)
        request_body = json.loads(opener.requests[0][0].data.decode("utf-8"))
        prompt = json.loads(request_body["messages"][1]["content"])
        self.assertEqual(["item-a", "item-b"], [item["id"] for item in prompt["items"]])

    def test_transient_rate_limit_retries_with_retry_after(self):
        headers = Message()
        headers["Retry-After"] = "2.5"
        rate_limit = urllib.error.HTTPError(
            "https://gateway/compat/chat/completions", 429, "Too Many Requests", headers, io.BytesIO(b"{}")
        )
        success = {
            "id": "completion-2",
            "choices": [{"message": {"content": '{"label":"unclear","confidence":0.5,"rationale":"Ambiguous"}'}}],
        }
        opener = SequenceOpener([rate_limit, success])
        sleeps = []
        client = HiiveGatewayClassifierClient(GatewayConfig(
            "https://ai-gateway.hiive.network/compat", "openai/gpt-5.6-luna", access_token="token",
            max_attempts=3, retry_base_seconds=1, retry_max_seconds=10,
        ), opener, sleeper=sleeps.append)
        result = client.classify("Maybe later.")
        self.assertEqual(2, result.attempts)
        self.assertEqual([2.5], sleeps)
        self.assertEqual(2, len(opener.requests))

    def test_incomplete_batch_response_is_retried_as_a_whole(self):
        incomplete = {
            "id": "incomplete",
            "choices": [{"message": {"content": json.dumps({"results": [
                {"id": "item-a", "label": "interested", "confidence": 0.9, "rationale": "Positive"},
            ]})}}],
        }
        complete = {
            "id": "complete",
            "choices": [{"message": {"content": json.dumps({"results": [
                {"id": "item-a", "label": "interested", "confidence": 0.9, "rationale": "Positive"},
                {"id": "item-b", "label": "objection", "confidence": 0.8, "rationale": "Uses competitor"},
            ]})}}],
        }
        opener = SequenceOpener([incomplete, complete])
        sleeps = []
        client = HiiveGatewayClassifierClient(GatewayConfig(
            "https://ai-gateway.hiive.network/compat", "openai/gpt-5.6-luna", access_token="token",
            batch_size=2, retry_base_seconds=0.25,
        ), opener, sleeper=sleeps.append)
        result = client.classify_batch([("item-a", "Yes"), ("item-b", "No")])
        self.assertEqual(2, result.attempts)
        self.assertEqual("complete", result.response_id)
        self.assertEqual([0.25], sleeps)

    def test_permanent_bad_request_is_not_retried(self):
        error = urllib.error.HTTPError(
            "https://gateway/compat/chat/completions", 400, "Bad Request", Message(), io.BytesIO(b"{}")
        )
        opener = SequenceOpener([error])
        sleeps = []
        client = HiiveGatewayClassifierClient(GatewayConfig(
            "https://ai-gateway.hiive.network/compat", "openai/gpt-5.6-luna", access_token="token"
        ), opener, sleeper=sleeps.append)
        with self.assertRaises(GatewayError) as raised:
            client.classify("Hello")
        self.assertEqual(1, raised.exception.attempts)
        self.assertEqual([], sleeps)
        self.assertEqual(1, len(opener.requests))

    def test_environment_requires_a_complete_access_method(self):
        with self.assertRaises(ValueError):
            config_from_env({"AI_GATEWAY_MODEL": "openai/gpt-5.6-luna"})
        config = config_from_env({"AI_GATEWAY_MODEL": "openai/gpt-5.6-luna", "CF_ACCESS_TOKEN": "token"})
        self.assertEqual("token", config.access_token)
        self.assertEqual("https://ai-gateway.hiive.network/compat", config.base_url)

    def test_provider_direct_and_legacy_routes_are_rejected(self):
        with self.assertRaisesRegex(ValueError, "provider-direct"):
            GatewayConfig("https://api.openai.com/v1", "openai/gpt-5.6-luna", access_token="token")
        with self.assertRaisesRegex(ValueError, "/compat"):
            GatewayConfig("https://ai-gateway.hiive.network/v1", "openai/gpt-5.6-luna", access_token="token")

    def test_http_error_exposes_only_safe_diagnostics(self):
        headers = Message()
        headers["CF-Ray"] = "ray-123"
        headers["Retry-After"] = "60"
        error = urllib.error.HTTPError("https://gateway/compat/models", 400, "Bad Request", headers, io.BytesIO(b'{"secret":"never expose"}'))
        client = HiiveGatewayClassifierClient(GatewayConfig("https://ai-gateway.hiive.network/compat", "model", access_token="token"), RaisingOpener(error))
        with self.assertRaises(GatewayError) as raised:
            client.list_models()
        self.assertEqual(400, raised.exception.status_code)
        self.assertEqual("ray-123", raised.exception.cf_ray)
        self.assertNotIn("secret", str(raised.exception))

    def test_cloudflare_gateway_error_exposes_only_allowlisted_diagnostics(self):
        body = b'{"name":"AiGatewayError","internalCode":2008,"message":"Invalid provider","secret":"never expose"}'
        error = urllib.error.HTTPError("https://gateway/compat/models", 400, "Bad Request", Message(), io.BytesIO(body))
        client = HiiveGatewayClassifierClient(GatewayConfig("https://ai-gateway.hiive.network/compat", "model", access_token="token"), RaisingOpener(error))
        with self.assertRaises(GatewayError) as raised:
            client.list_models()
        self.assertEqual("2008", raised.exception.gateway_error_code)
        self.assertEqual("AiGatewayError", raised.exception.gateway_error_name)
        self.assertIn("Invalid provider", str(raised.exception))
        self.assertNotIn("secret", str(raised.exception))

    def test_provider_error_exposes_bounded_diagnostics(self):
        body = b'{"error":{"message":"Unsupported parameter: max_tokens","type":"invalid_request_error","param":"max_tokens","code":"unsupported_parameter"},"secret":"never expose"}'
        error = urllib.error.HTTPError("https://gateway/compat/chat/completions", 400, "Bad Request", Message(), io.BytesIO(body))
        client = HiiveGatewayClassifierClient(GatewayConfig("https://ai-gateway.hiive.network/compat", "openai/gpt-5.6-luna", access_token="token"), RaisingOpener(error))
        with self.assertRaises(GatewayError) as raised:
            client.classify("Hello")
        self.assertEqual("unsupported_parameter", raised.exception.gateway_error_code)
        self.assertEqual("invalid_request_error", raised.exception.gateway_error_name)
        self.assertIn("Unsupported parameter", str(raised.exception))
        self.assertNotIn("secret", str(raised.exception))

    def test_gateway_check_reports_model_availability(self):
        client = HiiveGatewayClassifierClient(GatewayConfig("https://ai-gateway.hiive.network/compat", "model-a", access_token="token"), FakeOpener([{"data": [{"id": "model-a"}, {"id": "model-b"}]}]))
        self.assertEqual({"ready": True, "model": "model-a", "available_models": 2}, client.check_configured_model())
