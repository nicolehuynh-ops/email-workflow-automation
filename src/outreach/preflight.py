"""Non-secret readiness checks for a scoped live acceptance run."""

from typing import Callable, Dict, List, Optional

from outreach.gateway import GatewayError, HiiveGatewayClassifierClient, config_from_env
from outreach.models import Campaign


REQUIRED_ENV = ("REPLY_IO_API_KEY", "FRONT_API_TOKEN", "CALENDLY_ACCESS_TOKEN", "CALENDLY_ORGANIZATION_URI", "AI_GATEWAY_MODEL")


def live_acceptance_preflight(campaign: Campaign, environment: Dict[str, str], gateway_checker: Optional[Callable[[Dict[str, str]], Dict]] = None) -> Dict:
    missing: List[str] = []
    if not campaign.campaign_id or campaign.campaign_id.startswith("replace-with-"):
        missing.append("campaignId must identify a non-production Reply.io campaign")
    if not campaign.front_inbox_ids:
        missing.append("front.inboxIds")
    if not campaign.calendly_report_start or not campaign.calendly_event_type_uris:
        missing.append("calendly.reportStart and calendly.eventTypeUris")
    for key in REQUIRED_ENV:
        if not environment.get(key):
            missing.append(key)
    user_access = bool(environment.get("CF_ACCESS_TOKEN"))
    service_access = bool(environment.get("CF_ACCESS_CLIENT_ID") and environment.get("CF_ACCESS_CLIENT_SECRET"))
    if not user_access and not service_access:
        missing.append("Cloudflare Access credentials (CF_ACCESS_TOKEN or service token pair)")
    gateway = None
    if environment.get("AI_GATEWAY_MODEL") and (user_access or service_access):
        try:
            gateway = (gateway_checker or _check_gateway)(environment)
            if not gateway.get("ready"):
                missing.append("configured AI gateway model is unavailable")
        except GatewayError as error:
            gateway = {"ready": False, **error.diagnostics()}
            missing.append("configured AI gateway model check failed")
    return {"ready": not missing, "campaign": campaign.slug, "missing": missing, "gateway": gateway}


def _check_gateway(environment: Dict[str, str]) -> Dict:
    return HiiveGatewayClassifierClient(config_from_env(environment)).check_configured_model()
