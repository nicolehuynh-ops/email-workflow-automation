import json
import unittest
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch

from outreach.config import load_campaign
from outreach.gateway import GatewayConfig, GatewayError, HiiveGatewayClassifierClient
from outreach.live import classify_front_signals, collect_live
from outreach.models import Signal


ROOT = Path(__file__).resolve().parents[1]


class FakeResponse:
    def __init__(self, payload=None):
        self.payload = payload or {
            "id": "answer-1",
            "choices": [{"message": {"content": json.dumps({"results": [{
                "id": "item-000000", "label": "interested", "confidence": 0.9,
                "rationale": "Asked for a meeting",
            }]})}}],
        }

    def __enter__(self):
        return self

    def __exit__(self, *_):
        return False

    def read(self):
        return json.dumps(self.payload).encode("utf-8")


class EchoBatchOpener:
    def __init__(self):
        self.requests = []

    def __call__(self, request, timeout):
        self.requests.append(request)
        body = json.loads(request.data.decode("utf-8"))
        items = json.loads(body["messages"][1]["content"])["items"]
        return FakeResponse({
            "id": f"answer-{len(self.requests)}",
            "choices": [{"message": {"content": json.dumps({"results": [
                {"id": item["id"], "label": "interested", "confidence": 0.9, "rationale": "Positive"}
                for item in items
            ]})}}],
        })


class FailingBatchOpener:
    def __call__(self, *_args, **_kwargs):
        raise GatewayError(
            "AI gateway rejected the request.", status_code=400, cf_ray="ray-safe",
            gateway_error_code="invalid_request", retryable=False,
        )


class LiveWorkflowTests(unittest.TestCase):
    def test_front_reply_is_classified_without_retaining_message_content_in_decision(self):
        client = HiiveGatewayClassifierClient(
            GatewayConfig("https://ai-gateway.hiive.network/compat", "openai/gpt-5.6-luna", access_token="test"),
            lambda *_, **__: FakeResponse(),
        )
        signals = classify_front_signals([
            Signal("front", "message-1", "reply_received", "alex@example.com", "example.com", "sender@example.com", "Please book time.", matched_reply_contact=True)
        ], client)
        self.assertEqual("interested", signals[0].classifier_label)
        self.assertEqual(0.9, signals[0].classifier_confidence)
        self.assertEqual("succeeded", signals[0].gateway_status)
        self.assertEqual("answer-1", signals[0].gateway_response_id)
        self.assertEqual(1, signals[0].gateway_attempts)

    def test_multiple_front_replies_are_batched_and_paced(self):
        opener = EchoBatchOpener()
        sleeps = []
        client = HiiveGatewayClassifierClient(
            GatewayConfig(
                "https://ai-gateway.hiive.network/compat", "openai/gpt-5.6-luna", access_token="test",
                batch_size=2, batch_pause_seconds=0.5,
            ),
            opener,
            sleeper=sleeps.append,
        )
        signals = classify_front_signals([
            Signal("front", f"message-{index}", "reply_received", f"user{index}@example.com", "example.com", content=f"Reply {index}", matched_reply_contact=True)
            for index in range(5)
        ], client)
        self.assertEqual(3, len(opener.requests))
        self.assertEqual([0.5, 0.5], sleeps)
        self.assertTrue(all(signal.gateway_status == "succeeded" for signal in signals))
        self.assertEqual([2, 2, 1], [signals[index].gateway_batch_size for index in (0, 2, 4)])
        self.assertEqual(3, len({signal.gateway_batch_id for signal in signals}))

    def test_failed_batch_marks_every_message_review_only_with_audit_metadata(self):
        client = HiiveGatewayClassifierClient(
            GatewayConfig(
                "https://ai-gateway.hiive.network/compat", "openai/gpt-5.6-luna", access_token="test",
                batch_size=2,
            ),
            FailingBatchOpener(),
            sleeper=lambda _seconds: self.fail("Permanent errors must not be retried"),
        )
        signals = classify_front_signals([
            Signal("front", f"message-{index}", "reply_received", f"user{index}@example.com", "example.com", content="Reply", matched_reply_contact=True)
            for index in range(2)
        ], client)
        self.assertTrue(all(signal.classifier_label == "unclear" for signal in signals))
        self.assertTrue(all(signal.gateway_status == "failed" for signal in signals))
        self.assertTrue(all(signal.gateway_http_status == 400 for signal in signals))
        self.assertTrue(all(signal.gateway_error_code == "invalid_request" for signal in signals))
        self.assertEqual(1, len({signal.gateway_batch_id for signal in signals}))

    def test_missing_reply_body_is_held_for_review(self):
        client = HiiveGatewayClassifierClient(
            GatewayConfig("https://ai-gateway.hiive.network/compat", "openai/gpt-5.6-luna", access_token="test"),
            lambda *_, **__: self.fail("Gateway must not be called"),
        )
        signal = classify_front_signals([
            Signal("front", "message-1", "reply_received", "alex@example.com", "example.com", "sender@example.com", matched_reply_contact=True)
        ], client)[0]
        self.assertEqual("unclear", signal.classifier_label)
        self.assertEqual(0.0, signal.classifier_confidence)

    def test_unmapped_messages_require_an_exact_campaign_allowlist(self):
        campaign, _ = load_campaign(ROOT / "config/campaigns/example-campaign.json")
        campaign = replace(
            campaign,
            gateway_allowed_unmapped_emails=("allowed@example.com",),
            gateway_allowed_front_conversation_ids=("cnv-allowed",),
            gateway_allowed_front_message_ids=("message-allowed",),
        )
        opener = EchoBatchOpener()
        client = HiiveGatewayClassifierClient(
            GatewayConfig("https://ai-gateway.hiive.network/compat", "openai/gpt-5.6-luna", access_token="test"),
            opener,
            sleeper=lambda _seconds: None,
        )
        signals = classify_front_signals([
            Signal("front", "mapped", "reply_received", "mapped@example.com", "example.com", content="Mapped", matched_reply_contact=True),
            Signal("front", "author", "reply_received", "allowed@example.com", "example.com", content="Author"),
            Signal("front", "conversation", "reply_received", "forwarder@example.net", "example.net", content="Forwarded", conversation_id="cnv-allowed"),
            Signal("front", "message-allowed", "reply_received", "bounce@example.net", "example.net", content="Bounce"),
            Signal("front", "unrelated", "reply_received", "unknown@example.org", "example.org", content="Private"),
        ], client, campaign)
        self.assertEqual(1, len(opener.requests))
        self.assertEqual(
            ["matched_reply_contact", "allowlisted_author", "allowlisted_conversation", "allowlisted_message"],
            [signal.gateway_scope_basis for signal in signals[:4]],
        )
        self.assertEqual("skipped_scope", signals[4].gateway_status)
        self.assertEqual("unclear", signals[4].classifier_label)

    def test_run_limit_fails_closed_without_sending_a_partial_batch(self):
        campaign, _ = load_campaign(ROOT / "config/campaigns/example-campaign.json")
        campaign = replace(campaign, gateway_max_messages_per_run=2)
        client = HiiveGatewayClassifierClient(
            GatewayConfig("https://ai-gateway.hiive.network/compat", "openai/gpt-5.6-luna", access_token="test"),
            lambda *_, **__: self.fail("No gateway request is allowed when the run limit is exceeded"),
        )
        signals = classify_front_signals([
            Signal("front", f"message-{index}", "reply_received", f"user{index}@example.com", "example.com", content="Reply", matched_reply_contact=True)
            for index in range(3)
        ], client, campaign)
        self.assertTrue(all(signal.gateway_status == "skipped_run_limit" for signal in signals))
        self.assertTrue(all(signal.classifier_label == "unclear" for signal in signals))

    def test_invalid_gateway_configuration_prevents_vendor_reads(self):
        environment = {
            "AI_GATEWAY_BASE_URL": "https://api.openai.com/v1",
            "AI_GATEWAY_MODEL": "openai/gpt-5.6-luna",
            "CF_ACCESS_TOKEN": "token",
        }
        with patch("outreach.live.ReplyReader") as reply_reader:
            with self.assertRaisesRegex(ValueError, "provider-direct"):
                collect_live(None, environment)
            reply_reader.assert_not_called()
