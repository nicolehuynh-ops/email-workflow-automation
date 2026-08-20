"""Small read-only client for the Calendly API v2."""

import json
import urllib.error
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor


class CalendlyClient:
    base_url = "https://api.calendly.com"

    def __init__(self, access_token):
        self.access_token = access_token

    def request(self, path_or_url, params=None):
        if path_or_url.startswith("http"):
            url = path_or_url
        else:
            url = f"{self.base_url}{path_or_url}"
        if params:
            separator = "&" if "?" in url else "?"
            url = f"{url}{separator}{urllib.parse.urlencode(params)}"

        request = urllib.request.Request(
            url,
            headers={
                "Authorization": f"Bearer {self.access_token}",
                "User-Agent": "pcs-campaign-tracker/1.0",
                "Accept": "application/json",
            },
        )
        try:
            with urllib.request.urlopen(request, timeout=60) as response:
                return json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            body = exc.read().decode("utf-8")
            raise RuntimeError(f"Calendly {path_or_url} failed ({exc.code}): {body}") from exc

    def current_user(self):
        return (self.request("/users/me").get("resource") or {})

    def list_active_invitees(self, tracking_links, min_start_time=None):
        """Return invitees on active events belonging to the supplied tracking links."""
        user = self.current_user()
        scope = {"organization": user.get("current_organization")} if user.get("current_organization") else {"user": user.get("uri")}
        event_types = self.list_collection("/event_types", {**scope, "count": 100})
        groups_by_event_type = {}
        unresolved_links = []
        for group, link in tracking_links.items():
            matching_type = next(
                (event_type for event_type in event_types if comparable_link(event_type.get("scheduling_url")) == comparable_link(link)),
                None,
            )
            if not matching_type:
                unresolved_links.append(link)
                continue
            groups_by_event_type.setdefault(matching_type.get("uri"), set()).add(group)
        if unresolved_links:
            raise RuntimeError(
                "Calendly tracking link(s) did not match an event type visible to this token: "
                + ", ".join(unresolved_links)
            )
        invitees = []
        tracked_events = []
        for event_type_uri, groups in groups_by_event_type.items():
            event_filters = {
                **scope,
                "event_type": event_type_uri,
                "status": "active",
                "count": 100,
            }
            if min_start_time:
                event_filters["min_start_time"] = min_start_time
            events = self.list_collection(
                "/scheduled_events",
                event_filters,
            )
            for event in events:
                tracked_events.append((event, groups))
        with ThreadPoolExecutor(max_workers=4) as executor:
            for event_invitees in executor.map(self.event_invitees, tracked_events):
                invitees.extend(event_invitees)
        return invitees

    def event_invitees(self, event_and_groups):
        event, groups = event_and_groups
        event_id = str(event.get("uri") or "").rstrip("/").split("/")[-1]
        if not event_id:
            return []
        return [
            {
                "email": invitee.get("email") or "",
                "eventName": event.get("name") or "",
                "eventStartTime": event.get("start_time") or "",
                "bookedAt": invitee.get("created_at") or "",
                "eventUri": event.get("uri") or "",
                "inviteeUri": invitee.get("uri") or "",
                "trackingGroups": sorted(groups),
            }
            for invitee in self.list_collection(f"/scheduled_events/{event_id}/invitees", {"count": 100})
        ]

    def list_collection(self, path, params=None):
        items = []
        next_page = path
        next_params = params
        while next_page:
            payload = self.request(next_page, next_params)
            items.extend(payload.get("collection") or [])
            next_page = (payload.get("pagination") or {}).get("next_page")
            next_params = None
        return items


def comparable_link(value):
    """Compare Calendly scheduling links without query-string tracking parameters."""
    parsed = urllib.parse.urlparse(str(value or "").strip())
    return f"{parsed.netloc.lower()}{parsed.path.rstrip('/').lower()}"
