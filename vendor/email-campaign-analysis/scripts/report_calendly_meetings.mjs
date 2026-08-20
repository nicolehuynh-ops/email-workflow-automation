#!/usr/bin/env node
/**
 * Reports Calendly meetings as upcoming, completed, or canceled.
 * Usage: node scripts/report_calendly_meetings.mjs [campaign-slug]
 */
import fs from "node:fs/promises";
import path from "node:path";
import { execFile } from "node:child_process";
import { promisify } from "node:util";

const root = path.resolve(path.dirname(new URL(import.meta.url).pathname), "..");
const requestedCampaign = process.argv[2];
const envPath = path.join(root, ".env");
const linksPath = path.join(root, "booking-links.json");
const execFileAsync = promisify(execFile);
let authHeaderPath;

function parseEnv(text) {
  return Object.fromEntries(text.split(/\r?\n/).map((line) => line.trim()).filter((line) => line && !line.startsWith("#")).map((line) => {
    const index = line.indexOf("=");
    return [line.slice(0, index), line.slice(index + 1)];
  }));
}
function csv(value) { const text = String(value ?? ""); return /[",\n\r]/.test(text) ? `"${text.replaceAll('"', '""')}"` : text; }
function requireValue(value, label) { if (!value) throw new Error(`Missing ${label}.`); return value; }
function trackingFromLink(link) {
  const url = new URL(link);
  if (url.protocol !== "https:" || url.hostname !== "calendly.com") throw new Error(`Invalid Calendly link: ${link}`);
  const tracking = Object.fromEntries([...url.searchParams].filter(([key, value]) => key.startsWith("utm_") && value));
  for (const key of ["utm_source", "utm_medium", "utm_campaign"]) requireValue(tracking[key], `${key} in ${link}`);
  return tracking;
}
function eventMatches(event, tracking) {
  const actual = event.tracking ?? {};
  return Object.entries(tracking).every(([key, value]) => actual[key] === value);
}
function classification(event, now) {
  if (event.status === "canceled") return "Canceled";
  return new Date(event.start_time) > now ? "Upcoming" : "Completed";
}
async function apiGet(url) {
  // curl uses the desktop environment's trusted certificate store.
  try {
    const { stdout } = await execFileAsync("curl", ["--silent", "--show-error", "--fail", "--header", `@${authHeaderPath}`, url.toString()], { maxBuffer: 10 * 1024 * 1024 });
    return JSON.parse(stdout);
  } catch (error) {
    throw new Error(`Calendly API request failed (${error.code ?? "unknown error"}).`);
  }
}
async function listEvents(scopeKey, scopeUri, status, startTime) {
  const events = []; let nextPage = null;
  do {
    const url = nextPage ? new URL(nextPage) : new URL("https://api.calendly.com/scheduled_events");
    if (!nextPage) {
      url.searchParams.set(scopeKey, scopeUri);
      url.searchParams.set("status", status);
      url.searchParams.set("min_start_time", startTime);
      url.searchParams.set("count", "100");
    }
    const page = await apiGet(url);
    events.push(...(page.collection ?? []));
    nextPage = page.pagination?.next_page ?? null;
  } while (nextPage);
  return events;
}

const env = parseEnv(await fs.readFile(envPath, "utf8"));
const token = requireValue(env.CALENDLY_ACCESS_TOKEN, "CALENDLY_ACCESS_TOKEN in .env");
authHeaderPath = path.join("/private/tmp", `calendly-auth-${process.pid}.txt`);
await fs.writeFile(authHeaderPath, `Authorization: Bearer ${token}\n`, { mode: 0o600 });
process.on("exit", () => { fs.unlink(authHeaderPath).catch(() => {}); });
const config = JSON.parse(await fs.readFile(linksPath, "utf8"));
let campaigns = config.campaigns ?? [];
if (requestedCampaign) campaigns = campaigns.filter((campaign) => campaign.id === requestedCampaign);
if (!campaigns.length) throw new Error(`No campaign configuration found${requestedCampaign ? ` for ${requestedCampaign}` : ""}.`);

const me = await apiGet("https://api.calendly.com/users/me");
const scopeUri = env.CALENDLY_ORGANIZATION_URI || me.resource?.uri;
const scopeKey = env.CALENDLY_ORGANIZATION_URI ? "organization" : "user";
const now = new Date();

for (const campaign of campaigns) {
  const reportStart = requireValue(campaign.reportStart, `reportStart for ${campaign.id}`);
  const active = await listEvents(scopeKey, scopeUri, "active", reportStart);
  const canceled = await listEvents(scopeKey, scopeUri, "canceled", reportStart);
  const seen = new Set(); const rows = [];
  for (const sequence of campaign.sequences ?? []) {
    for (const bookingLink of sequence.bookingLinks ?? []) {
      if (bookingLink.url?.startsWith("PASTE_")) continue;
      const tracking = trackingFromLink(bookingLink.url);
      for (const event of [...active, ...canceled]) {
        if (!eventMatches(event, tracking)) continue;
        const eventKey = event.uri ?? `${event.start_time}|${event.name}|${event.status}`;
        if (seen.has(eventKey)) continue;
        seen.add(eventKey);
        rows.push([campaign.id, campaign.name, sequence.id, sequence.name, classification(event, now), event.status, event.name, event.start_time, event.end_time, event.created_at, event.updated_at]);
      }
    }
  }
  rows.sort((a, b) => a[7].localeCompare(b[7]));
  const count = (category) => rows.filter((row) => row[4] === category).length;
  const outputDir = path.join(root, "campaigns", campaign.id, "output");
  await fs.mkdir(outputDir, { recursive: true });
  const summary = [["Campaign ID", "Campaign Name", "Upcoming Booked Meetings", "Completed Meetings", "Canceled Meetings", "Total Attributed Meetings"], [campaign.id, campaign.name, count("Upcoming"), count("Completed"), count("Canceled"), rows.length]];
  const details = [["Campaign ID", "Campaign Name", "Sequence ID", "Sequence Name", "Meeting Classification", "Calendly Status", "Event Name", "Start Time", "End Time", "Created At", "Updated At"], ...rows];
  await fs.writeFile(path.join(outputDir, "calendly-meeting-summary.csv"), summary.map((row) => row.map(csv).join(",")).join("\n") + "\n");
  await fs.writeFile(path.join(outputDir, "calendly-meeting-details.csv"), details.map((row) => row.map(csv).join(",")).join("\n") + "\n");
  console.log(`${campaign.id}: ${count("Upcoming")} upcoming, ${count("Completed")} completed, ${count("Canceled")} canceled.`);
}
