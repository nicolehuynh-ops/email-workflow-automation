import argparse
import json
from pathlib import Path

from outreach.config import load_campaign
from outreach.database import Database
from outreach.decisions import build_decisions
from outreach.env import load_dotenv
from outreach.live import collect_live
from outreach.reply.apply import apply_approved_decisions
from outreach.reply.client import ReplyWriteClient
from outreach.analytics import materialize_analytics_inputs
from outreach.suppression_export import write_suppression_lists
from outreach.preflight import live_acceptance_preflight
from outreach.gateway import GatewayError, HiiveGatewayClassifierClient, config_from_env
from outreach.snapshot import load_snapshot


ROOT = Path(__file__).resolve().parents[2]


def database() -> Database:
    return Database(ROOT / "data" / "outreach.db", ROOT / "migrations")


def campaign_path(slug: str) -> Path:
    directory = ROOT / "config" / "campaigns"
    # A local campaign definition contains vendor identifiers and is preferred
    # over the tracked example/configuration of the same slug.
    for path in (directory / f"{slug}.local.json", directory / f"{slug}.json"):
        if path.exists():
            return path
    # Campaign workspaces use a human-readable name/date directory while the
    # CLI continues to use the stable campaign slug.  Search only the two
    # supported configuration filenames and match their declared slug.
    for workspace in sorted(path for path in directory.iterdir() if path.is_dir()):
        for filename in ("campaign.local.json", "campaign.json"):
            path = workspace / filename
            if not path.exists():
                continue
            try:
                if json.loads(path.read_text(encoding="utf-8")).get("slug") == slug:
                    return path
            except (OSError, json.JSONDecodeError):
                continue
    raise ValueError(f"Campaign configuration does not exist: {directory / (slug + '.json')}")


def run(args: argparse.Namespace) -> None:
    if args.mode == "apply":
        apply(args)
        return
    campaign, digest = load_campaign(campaign_path(args.campaign))
    if args.live:
        contacts, signals = collect_live(campaign, load_dotenv(ROOT / ".env"))
    else:
        contacts, signals = load_snapshot(Path(args.snapshot))
    db = database()
    try:
        run_id = db.create_run(campaign.slug, digest, args.mode)
        db.persist_inputs(run_id, contacts, signals)
        decisions = build_decisions(campaign, contacts, signals)
        status = "dry_run" if args.mode == "dry_run" else "pending_review"
        db.persist_decisions(run_id, decisions, status)
        summary = {"contacts": len(contacts), "signals": len(signals), "decisions": len(decisions), "decision_status": status}
        db.complete_run(run_id, "completed", summary)
        print(json.dumps({"run_id": run_id, **summary}, indent=2))
    finally:
        db.close()


def review(args: argparse.Namespace) -> None:
    db = database()
    try:
        if args.review_action == "list":
            rows = [dict(row) for row in db.list_decisions(args.campaign)]
            print(json.dumps(rows, indent=2))
        else:
            db.decide(args.decision_id, args.reviewer, args.review_action, args.note)
            print(f"{args.review_action.capitalize()} {args.decision_id}")
    finally:
        db.close()


def apply(args: argparse.Namespace) -> None:
    db = database()
    try:
        if not db.apply_candidates_for_campaign(args.campaign):
            print("No approved decisions to apply.")
            return
        campaign, digest = load_campaign(campaign_path(args.campaign))
        environment = load_dotenv(ROOT / ".env")
        results = apply_approved_decisions(
            db, campaign, ReplyWriteClient(environment.get("REPLY_IO_API_KEY", "")), args.campaign, digest
        )
        summary = {"applied": 0, "failed": 0, "skipped": 0}
        for result in results:
            if result.status == "applied":
                summary["applied"] += 1
            elif result.status.startswith("skipped"):
                summary["skipped"] += 1
            else:
                summary["failed"] += 1
        print(json.dumps({"summary": summary, "results": [result.__dict__ for result in results]}, indent=2))
    finally:
        db.close()


def export(args: argparse.Namespace) -> None:
    db = database()
    try:
        if args.export_action == "suppression-list":
            result = write_suppression_lists(db, args.campaign, ROOT / "artifacts")
        else:
            campaign, _ = load_campaign(campaign_path(args.campaign))
            result = materialize_analytics_inputs(db, campaign, ROOT / "vendor" / "email-campaign-analysis" / "campaigns", load_dotenv(ROOT / ".env"))
        print(json.dumps(result, indent=2))
    finally:
        db.close()


def preflight(args: argparse.Namespace) -> None:
    campaign, _ = load_campaign(campaign_path(args.campaign))
    result = live_acceptance_preflight(campaign, load_dotenv(ROOT / ".env"))
    print(json.dumps(result, indent=2))
    if not result["ready"]:
        raise SystemExit(2)


def gateway_check(args: argparse.Namespace) -> None:
    try:
        environment = load_dotenv(ROOT / ".env")
        result = HiiveGatewayClassifierClient(config_from_env(environment)).check_configured_model()
        print(json.dumps(result, indent=2))
        if not result["ready"]:
            raise SystemExit(2)
    except GatewayError as error:
        print(json.dumps({"ready": False, **error.diagnostics()}, indent=2))
        raise SystemExit(2)
    except ValueError as error:
        print(json.dumps({"ready": False, "message": str(error)}, indent=2))
        raise SystemExit(2)


def main() -> None:
    parser = argparse.ArgumentParser(prog="python -m outreach")
    subparsers = parser.add_subparsers(dest="command", required=True)
    run_parser = subparsers.add_parser("run")
    run_parser.add_argument("--campaign", required=True)
    run_parser.add_argument("--mode", choices=("dry_run", "review", "apply"), required=True)
    source_group = run_parser.add_mutually_exclusive_group()
    source_group.add_argument("--snapshot", help="Local JSON source fixture.")
    source_group.add_argument("--live", action="store_true", help="Read Reply.io, Front, Calendly, and Hiive AI Gateway; never applies Reply.io actions.")
    review_parser = subparsers.add_parser("review")
    review_subparsers = review_parser.add_subparsers(dest="review_action", required=True)
    list_parser = review_subparsers.add_parser("list")
    list_parser.add_argument("--campaign", required=True)
    for action in ("approve", "reject"):
        action_parser = review_subparsers.add_parser(action)
        action_parser.add_argument("decision_id")
        action_parser.add_argument("--reviewer", required=True)
        action_parser.add_argument("--note", default="")
    export_parser = subparsers.add_parser("export")
    export_subparsers = export_parser.add_subparsers(dest="export_action", required=True)
    for action in ("suppression-list", "analytics-inputs"):
        action_parser = export_subparsers.add_parser(action)
        action_parser.add_argument("--campaign", required=True)
    preflight_parser = subparsers.add_parser("preflight")
    preflight_parser.add_argument("--campaign", required=True)
    gateway_parser = subparsers.add_parser("gateway")
    gateway_subparsers = gateway_parser.add_subparsers(dest="gateway_action", required=True)
    gateway_subparsers.add_parser("check")
    args = parser.parse_args()
    if args.command == "run" and args.mode != "apply" and not args.snapshot and not args.live:
        parser.error("--snapshot or --live is required for dry_run and review.")
    if args.command == "run":
        run(args)
    elif args.command == "review":
        review(args)
    elif args.command == "export":
        export(args)
    elif args.command == "preflight":
        preflight(args)
    else:
        gateway_check(args)
