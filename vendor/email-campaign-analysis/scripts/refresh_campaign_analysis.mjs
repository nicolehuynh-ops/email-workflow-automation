#!/usr/bin/env node
/**
 * Supported refresh workflow for updated Reply.io and tracker exports.
 * Rebuilds normalized issuer data and refreshes the existing workbook tabs.
 */
import fs from "node:fs/promises";
import path from "node:path";
import { FileBlob, SpreadsheetFile, Workbook } from "@oai/artifact-tool";

const root = path.resolve(path.dirname(new URL(import.meta.url).pathname), "..");
const campaignId = process.argv[2] ?? "uncovered-issuer-email-campaign";
if (!/^[a-z0-9][a-z0-9-]*$/.test(campaignId)) throw new Error("Campaign must be a lowercase slug using letters, numbers, and hyphens.");
const campaignDir = path.join(root, "campaigns", campaignId);
const inputDir = path.join(campaignDir, "input");
const outputDir = path.join(campaignDir, "output");
const workbookPath = path.join(outputDir, "campaign_analysis.xlsx");
const normalizedPath = path.join(outputDir, "issuer_breakdown.csv");
const contactPath = path.join(outputDir, "contact_breakdown.csv");
const overridePath = path.join(campaignDir, "config", "issuer-overrides.json");
const issuerHeaders = ["Source File", "Sender", "Version", "Issuer ID", "Issuer Name", "Email", "Stop Step", "Last Activity Date", "Delivered", "Opened", "Clicked", "Replied", "Positive Response", "Meeting Requested", "Meeting Booked", "Outcome", "Notes"];

function parseCsv(text) {
  const output = []; let row = []; let field = ""; let quoted = false;
  for (let i = 0; i < text.length; i += 1) {
    const char = text[i]; const next = text[i + 1];
    if (char === '"' && quoted && next === '"') { field += '"'; i += 1; }
    else if (char === '"') quoted = !quoted;
    else if (char === ',' && !quoted) { row.push(field); field = ""; }
    else if ((char === "\n" || char === "\r") && !quoted) {
      if (char === "\r" && next === "\n") i += 1;
      row.push(field); if (row.some((value) => value.trim())) output.push(row); row = []; field = "";
    } else field += char;
  }
  row.push(field); if (row.some((value) => value.trim())) output.push(row); return output;
}
function normalized(value) { return String(value ?? "").trim().toLowerCase().replace(/^www\./, "").replace(/[^a-z0-9]/g, ""); }
function sender(value) { return String(value ?? "").trim().replace(/^daragh$/i, "Darragh"); }
function sequenceSender(sequence, fallback) { return sender((String(sequence ?? "").match(/-\s*(Frankie|Darragh|Daragh|Prab)\s*$/i) || [])[1] ?? fallback); }
function version(sequence) { return (String(sequence ?? "").match(/version\s+([ab])/i) || [])[1]?.toUpperCase() || ""; }
function yes(value) { return String(value ?? "").trim() === "1" || /^yes$/i.test(String(value ?? "")); }
function outcome(response) { const value = String(response ?? "").toLowerCase(); if (/meeting\s*booked|scheduled call|schedule call|scheduling call/.test(value)) return "Meeting Booked"; if (/meeting\s*requested/.test(value)) return "Meeting Requested"; return "Positive Response"; }
function rank(row) { return row[14] === "Yes" ? 4 : row[13] === "Yes" ? 3 : row[12] === "Yes" ? 2 : 1; }
function sequenceStep(row) { return Number(row[6]) || 0; }
function escapeCsv(value) { const text = String(value ?? ""); return /[",\n\r]/.test(text) ? `"${text.replaceAll('"', '""')}"` : text; }
function requireColumns(headers, columns, file) { const missing = columns.filter((name) => !headers.includes(name)); if (missing.length) throw new Error(`${file} is missing: ${missing.join(", ")}`); }
function count(rows, predicate) { return rows.reduce((total, row) => total + (predicate(row) ? 1 : 0), 0); }
function rate(numerator, denominator) { return denominator ? numerator / denominator : 0; }

const overrides = JSON.parse(await fs.readFile(overridePath, "utf8"));
const contactFile = path.join(inputDir, "Reply.io_Contact_Report.csv");
const contactCsv = parseCsv(await fs.readFile(contactFile, "utf8"));
const contactHeaders = contactCsv[0].map((value) => value.replace(/^\uFEFF/, ""));
requireColumns(contactHeaders, ["Contact Id", "PCS Issuer ID", "Contact email", "Sequence", "Sequence step", "Delivered", "Opened", "Clicked", "Replied", "OptedOut"], "Reply.io_Contact_Report.csv");
const contacts = contactCsv.slice(1).map((row) => Object.fromEntries(contactHeaders.map((header, index) => [header, row[index] ?? ""])));
const trackerFile = (await fs.readdir(inputDir)).find((name) => /positive_response.*\.xlsx$/i.test(name));
if (!trackerFile) throw new Error("Missing the positive-response tracker XLSX in input/");
const trackerWorkbook = await SpreadsheetFile.importXlsx(await FileBlob.load(path.join(inputDir, trackerFile)));
const trackerRows = trackerWorkbook.worksheets.getItem("Positive Responses").getRange("A1:I5000").values.filter((row) => row.some((value) => value !== null && value !== ""));
const trackerHeaders = trackerRows[0];
requireColumns(trackerHeaders, ["Issuer Name", "Domain", "PCS Sender", "Email Version", "Response"], trackerFile);
const tracker = trackerRows.slice(1).map((row) => Object.fromEntries(trackerHeaders.map((header, index) => [header, row[index] ?? ""])));

const trackerMap = new Map();
for (const row of tracker) {
  const override = overrides[normalized(row.Domain)] ?? {};
  for (const domain of [row.Domain, override.issuerId].filter(Boolean)) trackerMap.set(`${normalized(domain)}|${normalized(sender(row["PCS Sender"]))}|${row["Email Version"]}`, row);
}
const usedTracker = new Set();
const issuerRows = contacts.map((contact) => {
  const campaignVersion = version(contact.Sequence);
  const campaignSender = sequenceSender(contact.Sequence, contact["PCS Sender"]);
  const trackerRow = trackerMap.get(`${normalized(contact["PCS Issuer ID"])}|${normalized(campaignSender)}|${campaignVersion}`);
  if (trackerRow) usedTracker.add(`${normalized(trackerRow.Domain)}|${normalized(sender(trackerRow["PCS Sender"]))}|${trackerRow["Email Version"]}`);
  const override = trackerRow ? (overrides[normalized(trackerRow.Domain)] ?? {}) : {};
  const result = trackerRow ? outcome(trackerRow.Response) : "No Response";
  return ["Reply.io Contact Report", campaignSender, campaignVersion, contact["PCS Issuer ID"], override.issuerName ?? contact.IssuerName, contact["Contact email"], contact["Sequence step"], contact["Delivery date"], yes(contact.Delivered) ? "Yes" : "No", yes(contact.Opened) ? "Yes" : "No", yes(contact.Clicked) ? "Yes" : "No", yes(contact.Replied) ? "Yes" : "No", trackerRow ? "Yes" : "No", result === "Meeting Requested" ? "Yes" : "No", result === "Meeting Booked" ? "Yes" : "No", result, trackerRow?.Response ?? ""];
});
for (const row of tracker) {
  const trackerKey = `${normalized(row.Domain)}|${normalized(sender(row["PCS Sender"]))}|${row["Email Version"]}`;
  if (usedTracker.has(trackerKey)) continue;
  const override = overrides[normalized(row.Domain)] ?? {};
  const result = outcome(row.Response);
  issuerRows.push(["Positive-response tracker (unmatched)", sender(row["PCS Sender"]), row["Email Version"], override.issuerId ?? row.Domain, override.issuerName ?? row["Issuer Name"], "", "Unmatched", "", "No", "No", "No", "No", "Yes", result === "Meeting Requested" ? "Yes" : "No", result === "Meeting Booked" ? "Yes" : "No", result, row.Response]);
}
const deduped = new Map();
for (const row of issuerRows) {
  const key = `${normalized(row[3])}|${normalized(row[1])}|${row[2]}`;
  const current = deduped.get(key);
  if (!current || rank(row) > rank(current) || (rank(row) === rank(current) && sequenceStep(row) > sequenceStep(current))) deduped.set(key, row);
}
const issuerData = [...deduped.values()].sort((a, b) => a[3].localeCompare(b[3]) || a[1].localeCompare(b[1]) || a[2].localeCompare(b[2]));
const contactData = contacts.map((contact) => [sequenceSender(contact.Sequence, contact["PCS Sender"]), version(contact.Sequence), contact["Contact email"], contact["PCS Issuer ID"], yes(contact.Delivered) ? "Yes" : "No", yes(contact.Opened) ? "Yes" : "No", yes(contact.Clicked) ? "Yes" : "No", yes(contact.Replied) ? "Yes" : "No", yes(contact.OptedOut) ? "Yes" : "No"]);

await fs.mkdir(outputDir, { recursive: true });
await fs.writeFile(normalizedPath, [issuerHeaders.join(","), ...issuerData.map((row) => row.map(escapeCsv).join(","))].join("\n"));
await fs.writeFile(contactPath, [["Sender", "Version", "Contact Email", "Issuer ID", "Delivered", "Opened", "Clicked", "Replied", "Opted Out"].join(","), ...contactData.map((row) => row.map(escapeCsv).join(","))].join("\n"));

const workbook = Workbook.create();
const issuerSummary = workbook.worksheets.add("Issuer Metrics Summary");
const contactSummary = workbook.worksheets.add("Contact Metrics Summary");
for (const sheet of [issuerSummary, contactSummary]) sheet.showGridLines = false;
const title = (sheet, range, text) => { sheet.mergeCells(range); sheet.getRange(range.split(":")[0]).values = [[text]]; sheet.getRange(range).format = { fill: "#14324A", font: { bold: true, color: "#FFFFFF", size: 16 }, verticalAlignment: "center" }; sheet.getRange(range).format.rowHeight = 28; };
const section = (sheet, range) => { sheet.getRange(range).format = { fill: "#DCECF3", font: { bold: true, color: "#14324A" }, wrapText: true, verticalAlignment: "center", borders: { preset: "outside", style: "thin", color: "#A7C6D4" } }; };
const percent = (sheet, range) => { sheet.getRange(range).format.numberFormat = "0.0%"; };
const styleData = (sheet, range) => { sheet.getRange(range).format.borders = { preset: "outside", style: "thin", color: "#B9CFD8" }; };

const totalIssuer = issuerData.length;
const issuerPositive = count(issuerData, (row) => row[12] === "Yes");
const issuerRequested = count(issuerData, (row) => row[13] === "Yes");
const issuerBooked = count(issuerData, (row) => row[14] === "Yes");
const issuerOutcomes = issuerRequested + issuerBooked;
const senderRows = ["Frankie", "Darragh", "Prab"].map((name) => { const audience = count(issuerData, (row) => row[1] === name); const outcomes = count(issuerData, (row) => row[1] === name && (row[13] === "Yes" || row[14] === "Yes")); return [name, audience, outcomes, rate(outcomes, audience)]; });
const versionRows = ["A", "B"].map((name) => { const audience = count(issuerData, (row) => row[2] === name); const positive = count(issuerData, (row) => row[2] === name && row[12] === "Yes"); const requested = count(issuerData, (row) => row[2] === name && row[13] === "Yes" && row[14] === "No"); const booked = count(issuerData, (row) => row[2] === name && row[14] === "Yes"); return [name, audience, positive, rate(positive, audience), requested, booked, rate(requested + booked, audience)]; });
const emailRows = [["Initial email", 1], ["Chase 1", 3], ["Chase 2", 5]].map(([label, step]) => { const make = (version) => { const reached = count(issuerData, (row) => row[2] === version && Number(row[6]) >= step); const positive = count(issuerData, (row) => row[2] === version && Number(row[6]) === step && row[12] === "Yes"); const meetings = count(issuerData, (row) => row[2] === version && Number(row[6]) === step && (row[13] === "Yes" || row[14] === "Yes")); return [reached, positive, rate(positive, reached), meetings, rate(meetings, reached)]; }; return [label, step, ...make("A"), ...make("B")]; });

title(issuerSummary, "A1:L1", `${campaignId} — Campaign Effectiveness Dashboard`);
issuerSummary.mergeCells("A2:L2"); issuerSummary.getRange("A2").values = [["Issuer-level results are calculated from this campaign's output/issuer_breakdown.csv."]]; issuerSummary.getRange("A2:L2").format = { fill: "#EDF5F8", font: { color: "#365464", italic: true } };
issuerSummary.getRange("A4:D4").values = [["Issuer conversion", "Value", "Rate", "Definition"]]; section(issuerSummary, "A4:D4");
issuerSummary.getRange("A5:D9").values = [["Issuer campaign records", totalIssuer, 1, "Normalized issuer × sender × version"], ["Positive responses", issuerPositive, rate(issuerPositive, totalIssuer), "Issuer-level positive rate"], ["Meeting requested", issuerRequested, rate(issuerRequested, totalIssuer), "Issuer-level request rate"], ["Meeting booked", issuerBooked, rate(issuerBooked, totalIssuer), "Issuer-level booked rate"], ["Meeting outcomes", issuerOutcomes, rate(issuerOutcomes, totalIssuer), "Issuer-level meeting outcome rate"]]; percent(issuerSummary, "C5:C9"); styleData(issuerSummary, "A4:D9");
issuerSummary.getRange("G4:J4").values = [["Sender comparison", "Audience", "Meeting outcomes", "Outcome rate"]]; section(issuerSummary, "G4:J4"); issuerSummary.getRange("G5:J7").values = senderRows; percent(issuerSummary, "J5:J7"); styleData(issuerSummary, "G4:J7");
issuerSummary.getRange("A12:G12").values = [["Version comparison", "Audience", "Positive responses", "Positive-response rate", "Requested only", "Meeting booked", "Meeting outcome rate"]]; section(issuerSummary, "A12:G12"); issuerSummary.getRange("A13:G14").values = versionRows; percent(issuerSummary, "D13:D14"); percent(issuerSummary, "G13:G14"); styleData(issuerSummary, "A12:G14");
issuerSummary.getRange("A17:L17").values = [["Email effectiveness by version", "Stop step", "A: reached", "A: positive", "A: positive rate", "A: meetings", "A: meeting rate", "B: reached", "B: positive", "B: positive rate", "B: meetings", "B: meeting rate"]]; section(issuerSummary, "A17:L17"); issuerSummary.getRange("A18:L20").values = emailRows; percent(issuerSummary, "E18:E20"); percent(issuerSummary, "G18:G20"); percent(issuerSummary, "J18:J20"); percent(issuerSummary, "L18:L20"); styleData(issuerSummary, "A17:L20");
issuerSummary.getRange("A:L").format.columnWidth = 16; issuerSummary.getRange("A:A").format.columnWidth = 24;

const contactTotal = contactData.length;
const delivered = count(contactData, (row) => row[4] === "Yes");
const opened = count(contactData, (row) => row[5] === "Yes");
const clicked = count(contactData, (row) => row[6] === "Yes" && row[8] === "No");
const activeContacts = count(contactData, (row) => row[8] === "No");
const contactVersionRows = ["A", "B"].map((name) => { const rows = contactData.filter((row) => row[1] === name); const active = rows.filter((row) => row[8] === "No"); return [name, rows.length, rate(count(rows, (row) => row[4] === "Yes"), rows.length), rate(count(rows, (row) => row[5] === "Yes"), rows.length), rate(count(active, (row) => row[6] === "Yes"), active.length)]; });
title(contactSummary, "A1:J1", `${campaignId} — Contact Metrics Summary`);
contactSummary.mergeCells("A2:J2"); contactSummary.getRange("A2").values = [["Engagement metrics are calculated from this campaign's output/contact_breakdown.csv. Click rates exclude opted-out contacts."]]; contactSummary.getRange("A2:J2").format = { fill: "#EDF5F8", font: { color: "#365464", italic: true } };
contactSummary.getRange("A4:D4").values = [["Contact engagement", "Value", "Rate", "Definition"]]; section(contactSummary, "A4:D4"); contactSummary.getRange("A5:D8").values = [["Contacts", contactTotal, 1, "Reply.io Contact Report"], ["Delivered", delivered, rate(delivered, contactTotal), "All contacts"], ["Opened", opened, rate(opened, contactTotal), "All contacts"], ["Clicked (excl. opt-outs)", clicked, rate(clicked, activeContacts), "Opted-out contacts excluded"]]; percent(contactSummary, "C5:C8"); styleData(contactSummary, "A4:D8");
contactSummary.getRange("F4:J4").values = [["Contact engagement by version", "Contacts", "Delivery rate", "Open rate", "Click rate (excl. opt-outs)"]]; section(contactSummary, "F4:J4"); contactSummary.getRange("F5:J6").values = contactVersionRows; percent(contactSummary, "H5:J6"); styleData(contactSummary, "F4:J6");
contactSummary.getRange("A:J").format.columnWidth = 18; contactSummary.getRange("A:A").format.columnWidth = 24; contactSummary.getRange("F:F").format.columnWidth = 28;

const xlsx = await SpreadsheetFile.exportXlsx(workbook);
await xlsx.save(workbookPath);
const errorScan = await workbook.inspect({ kind: "match", searchTerm: "#REF!|#DIV/0!|#VALUE!|#NAME\\?|#N/A", options: { useRegex: true, maxResults: 100 } });
console.log(errorScan.ndjson);
console.log(`Refresh complete: ${contacts.length} contact rows, ${issuerData.length} issuer records, ${tracker.length} tracker rows.`);
