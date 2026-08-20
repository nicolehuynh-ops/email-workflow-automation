#!/usr/bin/env node
/**
 * Creates a campaign-analysis workbook from one or more CSV/TSV exports.
 *
 * Usage:
 *   node scripts/build_campaign_analysis.mjs [export-folder] [output-file]
 *
 * By default, reads exports from the Uncovered Issuer Email Campaign input
 * folder and writes the workbook to that campaign's output folder. With no source files, it creates an empty,
 * ready-to-fill template.
 * Source files should have one row per issuer/prospect; the script maps common
 * column aliases to the canonical Input schema and removes duplicate records.
 */
import fs from "node:fs/promises";
import path from "node:path";
import { SpreadsheetFile, Workbook } from "@oai/artifact-tool";

const root = path.resolve(path.dirname(new URL(import.meta.url).pathname), "..");
const defaultCampaignDir = path.join(root, "campaigns", "uncovered-issuer-email-campaign");
const inputFolder = process.argv[2] ? path.resolve(process.argv[2]) : path.join(defaultCampaignDir, "input");
const outputFile = process.argv[3]
  ? path.resolve(process.argv[3])
  : path.join(defaultCampaignDir, "output", "campaign_analysis.xlsx");
const MAX_ROWS = 2000;

const columns = [
  "Source File", "Sender", "Version", "Issuer ID", "Issuer Name", "Email",
  "Stop Step", "Last Activity Date", "Delivered", "Opened", "Clicked", "Replied",
  "Positive Response", "Meeting Requested", "Meeting Booked", "Outcome", "Notes",
];
const aliases = {
  "Source File": [], Sender: ["sender", "email sender", "sending account", "from"],
  Version: ["version", "variant", "email version", "ab version", "a/b test"],
  "Issuer ID": ["issuer id", "company id", "account id", "prospect id", "id"],
  "Issuer Name": ["issuer", "issuer name", "company", "company name", "account name"],
  Email: ["email", "email address", "prospect email", "contact email"],
  "Stop Step": ["stop step", "stopped at step", "sequence step", "step", "last step", "last sequence step"],
  "Last Activity Date": ["last activity date", "activity date", "reply date", "date"],
  Delivered: ["delivered", "delivery status"], Opened: ["opened", "open"], Clicked: ["clicked", "click"],
  Replied: ["replied", "reply", "has replied"],
  "Positive Response": ["positive response", "positive reply", "positive"],
  "Meeting Requested": ["meeting requested", "meeting request", "requested meeting"],
  "Meeting Booked": ["meeting booked", "booked meeting", "meeting scheduled"],
  Outcome: ["outcome", "status", "response status", "reply category"], Notes: ["notes", "note", "comment"],
};

function norm(value) { return String(value ?? "").trim().toLowerCase().replace(/[^a-z0-9]/g, ""); }
function truthy(value) { return ["true", "yes", "y", "1", "delivered", "opened", "clicked", "replied"].includes(norm(value)); }
function csvRows(text, delimiter) {
  const rows = []; let row = []; let field = ""; let quoted = false;
  for (let i = 0; i < text.length; i += 1) {
    const ch = text[i]; const next = text[i + 1];
    if (ch === '"' && quoted && next === '"') { field += '"'; i += 1; }
    else if (ch === '"') quoted = !quoted;
    else if (ch === delimiter && !quoted) { row.push(field); field = ""; }
    else if ((ch === "\n" || ch === "\r") && !quoted) {
      if (ch === "\r" && next === "\n") i += 1;
      row.push(field); if (row.some((cell) => cell.trim())) rows.push(row); row = []; field = "";
    } else field += ch;
  }
  row.push(field); if (row.some((cell) => cell.trim())) rows.push(row);
  return rows;
}
function findColumn(headers, target) {
  const candidates = aliases[target].map(norm);
  return headers.findIndex((header) => candidates.includes(norm(header)));
}
function standardizeBoolean(value) { return truthy(value) ? "Yes" : "No"; }
function normalizeOutcome(row) {
  const existing = String(row["Outcome"] ?? "").trim();
  if (truthy(row["Meeting Booked"])) return "Meeting Booked";
  if (truthy(row["Meeting Requested"])) return "Meeting Requested";
  if (truthy(row["Positive Response"]) || /positive/i.test(existing)) return "Positive Response";
  if (truthy(row.Replied) || /reply/i.test(existing)) return "Replied";
  return existing || "No Response";
}
async function loadRecords(folder) {
  try { await fs.access(folder); } catch { return []; }
  const entries = await fs.readdir(folder, { withFileTypes: true });
  const files = entries.filter((entry) => entry.isFile() && /\.(csv|tsv)$/i.test(entry.name));
  const records = [];
  for (const file of files) {
    const text = await fs.readFile(path.join(folder, file.name), "utf8");
    const rows = csvRows(text, file.name.toLowerCase().endsWith(".tsv") ? "\t" : ",");
    if (!rows.length) continue;
    const headers = rows[0];
    for (const sourceRow of rows.slice(1)) {
      const row = { "Source File": file.name };
      for (const column of columns.slice(1)) {
        const index = findColumn(headers, column);
        row[column] = index >= 0 ? (sourceRow[index] ?? "").trim() : "";
      }
      ["Delivered", "Opened", "Clicked", "Replied", "Positive Response", "Meeting Requested", "Meeting Booked"].forEach((key) => { row[key] = standardizeBoolean(row[key]); });
      row.Outcome = normalizeOutcome(row);
      records.push(columns.map((column) => row[column] ?? ""));
    }
  }
  const seen = new Set();
  return records.filter((record) => {
    const key = `${norm(record[5])}|${norm(record[3])}|${norm(record[1])}|${norm(record[2])}`;
    if (!key.replace(/\|/g, "")) return true;
    if (seen.has(key)) return false;
    seen.add(key); return true;
  });
}
function styleTitle(range) { range.format = { fill: "#14324A", font: { bold: true, color: "#FFFFFF", size: 16 }, horizontalAlignment: "left", verticalAlignment: "center" }; }
function styleHeader(range) { range.format = { fill: "#1F5F7A", font: { bold: true, color: "#FFFFFF" }, horizontalAlignment: "center", verticalAlignment: "center", wrapText: true, borders: { preset: "outside", style: "thin", color: "#D6E2E8" } }; }
function styleSection(range) { range.format = { fill: "#DCECF3", font: { bold: true, color: "#14324A" }, borders: { preset: "outside", style: "thin", color: "#A7C6D4" } }; }

const records = await loadRecords(inputFolder);
const workbook = Workbook.create();
const input = workbook.worksheets.add("Input");
const output = workbook.worksheets.add("Output");
input.showGridLines = false; output.showGridLines = false;

input.mergeCells("A1:Q1"); input.getRange("A1").values = [["Campaign Analysis — Normalized Input Data"]]; styleTitle(input.getRange("A1:Q1")); input.getRange("A1:Q1").format.rowHeight = 28;
input.mergeCells("A2:Q3");
input.getRange("A2").values = [["Paste normalized issuer-level records beginning in row 6, or run this script with a folder of CSV/TSV exports. One record represents one issuer/prospect. Meeting Booked and Meeting Requested are the primary outcomes; Stop Step identifies the most effective sequence step."]];
input.getRange("A2:Q3").format = { fill: "#EDF5F8", font: { color: "#365464", italic: true }, wrapText: true, verticalAlignment: "center" };
input.getRange("A5:Q5").values = [columns]; styleHeader(input.getRange("A5:Q5")); input.getRange("A5:Q5").format.rowHeight = 34;
if (records.length) input.getRangeByIndexes(5, 0, records.length, columns.length).values = records;
input.getRange(`A6:Q${Math.max(6, records.length + 5)}`).format.borders = { preset: "insideHorizontal", style: "thin", color: "#E5EEF2" };
input.freezePanes.freezeRows(5);
input.getRange("A:A").format.columnWidth = 20; input.getRange("B:C").format.columnWidth = 15; input.getRange("D:F").format.columnWidth = 22; input.getRange("G:G").format.columnWidth = 14; input.getRange("H:H").format.columnWidth = 16; input.getRange("I:O").format.columnWidth = 13; input.getRange("P:P").format.columnWidth = 20; input.getRange("Q:Q").format.columnWidth = 30;
input.getRange(`B6:B${MAX_ROWS + 5}`).dataValidation = { rule: { type: "list", values: ["Sender 1", "Sender 2", "Sender 3"] } };
input.getRange(`C6:C${MAX_ROWS + 5}`).dataValidation = { rule: { type: "list", values: ["A", "B"] } };
input.getRange(`I6:O${MAX_ROWS + 5}`).dataValidation = { rule: { type: "list", values: ["Yes", "No"] } };

output.mergeCells("A1:J1"); output.getRange("A1").values = [["Campaign Effectiveness Dashboard"]]; styleTitle(output.getRange("A1:J1")); output.getRange("A1:J1").format.rowHeight = 30;
output.mergeCells("A2:J2"); output.getRange("A2").values = [["Primary objective: meeting booked or requested. Populate the Input tab, then use this dashboard to compare variants, senders, and stopping steps."]]; output.getRange("A2:J2").format = { fill: "#EDF5F8", font: { color: "#365464", italic: true } };
output.getRange("A4:E4").values = [["Overall funnel", "Value", "Rate", "Definition", ""]]; styleSection(output.getRange("A4:E4"));
output.getRange("A5:A10").values = [["Issuers / prospects"], ["Delivered"], ["Replied"], ["Positive responses"], ["Meeting requested"], ["Meeting booked"]];
output.getRange("B5:B10").formulas = [[`=COUNTA('Input'!$F$6:$F$${MAX_ROWS + 5})`], [`=COUNTIF('Input'!$I$6:$I$${MAX_ROWS + 5},"Yes")`], [`=COUNTIF('Input'!$L$6:$L$${MAX_ROWS + 5},"Yes")`], [`=COUNTIF('Input'!$M$6:$M$${MAX_ROWS + 5},"Yes")`], [`=COUNTIF('Input'!$N$6:$N$${MAX_ROWS + 5},"Yes")`], [`=COUNTIF('Input'!$O$6:$O$${MAX_ROWS + 5},"Yes")`]];
output.getRange("C5:C10").formulas = [["=IFERROR(B5/B5,0)"], ["=IFERROR(B6/B5,0)"], ["=IFERROR(B7/B5,0)"], ["=IFERROR(B8/B5,0)"], ["=IFERROR(B9/B5,0)"], ["=IFERROR(B10/B5,0)"]];
output.getRange("D5:D10").values = [["Unique normalized rows"], ["Delivery rate"], ["Reply rate"], ["Positive-response rate"], ["Meeting-request rate"], ["Meeting-booked rate"]];
output.getRange("B5:B10").format = { fill: "#EAF4EE", font: { bold: true, color: "#174A2A" }, numberFormat: "#,##0" };
output.getRange("C5:C10").format.numberFormat = "0.0%";
output.getRange("A4:D10").format.borders = { preset: "outside", style: "thin", color: "#B9CFD8" };

output.getRange("A13:E13").values = [["Version comparison", "Audience", "Requested only", "Meeting booked", "Meeting outcome rate"]]; styleSection(output.getRange("A13:E13"));
output.getRange("A14:A15").values = [["A"], ["B"]];
output.getRange("B14:B15").formulas = [[`=COUNTIF('Input'!$C$6:$C$${MAX_ROWS + 5},A14)`], [`=COUNTIF('Input'!$C$6:$C$${MAX_ROWS + 5},A15)`]];
output.getRange("C14:C15").formulas = [[`=COUNTIFS('Input'!$C$6:$C$${MAX_ROWS + 5},A14,'Input'!$N$6:$N$${MAX_ROWS + 5},"Yes",'Input'!$O$6:$O$${MAX_ROWS + 5},"No")`], [`=COUNTIFS('Input'!$C$6:$C$${MAX_ROWS + 5},A15,'Input'!$N$6:$N$${MAX_ROWS + 5},"Yes",'Input'!$O$6:$O$${MAX_ROWS + 5},"No")`]];
output.getRange("D14:D15").formulas = [[`=COUNTIFS('Input'!$C$6:$C$${MAX_ROWS + 5},A14,'Input'!$O$6:$O$${MAX_ROWS + 5},"Yes")`], [`=COUNTIFS('Input'!$C$6:$C$${MAX_ROWS + 5},A15,'Input'!$O$6:$O$${MAX_ROWS + 5},"Yes")`]];
output.getRange("E14:E15").formulas = [["=IFERROR((C14+D14)/B14,0)"], ["=IFERROR((C15+D15)/B15,0)"]]; output.getRange("E14:E15").format.numberFormat = "0.0%";
output.getRange("A13:E15").format.borders = { preset: "outside", style: "thin", color: "#B9CFD8" };

output.getRange("G4:J4").values = [["Sender comparison", "Audience", "Meeting outcomes", "Outcome rate"]]; styleSection(output.getRange("G4:J4"));
output.getRange("G5:G7").values = [["Sender 1"], ["Sender 2"], ["Sender 3"]];
output.getRange("H5:H7").formulas = [[`=COUNTIF('Input'!$B$6:$B$${MAX_ROWS + 5},G5)`], [`=COUNTIF('Input'!$B$6:$B$${MAX_ROWS + 5},G6)`], [`=COUNTIF('Input'!$B$6:$B$${MAX_ROWS + 5},G7)`]];
output.getRange("I5:I7").formulas = [[`=COUNTIFS('Input'!$B$6:$B$${MAX_ROWS + 5},G5,'Input'!$N$6:$N$${MAX_ROWS + 5},"Yes",'Input'!$O$6:$O$${MAX_ROWS + 5},"No")+COUNTIFS('Input'!$B$6:$B$${MAX_ROWS + 5},G5,'Input'!$O$6:$O$${MAX_ROWS + 5},"Yes")`], [`=COUNTIFS('Input'!$B$6:$B$${MAX_ROWS + 5},G6,'Input'!$N$6:$N$${MAX_ROWS + 5},"Yes",'Input'!$O$6:$O$${MAX_ROWS + 5},"No")+COUNTIFS('Input'!$B$6:$B$${MAX_ROWS + 5},G6,'Input'!$O$6:$O$${MAX_ROWS + 5},"Yes")`], [`=COUNTIFS('Input'!$B$6:$B$${MAX_ROWS + 5},G7,'Input'!$N$6:$N$${MAX_ROWS + 5},"Yes",'Input'!$O$6:$O$${MAX_ROWS + 5},"No")+COUNTIFS('Input'!$B$6:$B$${MAX_ROWS + 5},G7,'Input'!$O$6:$O$${MAX_ROWS + 5},"Yes")`]];
output.getRange("J5:J7").formulas = [["=IFERROR(I5/H5,0)"], ["=IFERROR(I6/H6,0)"], ["=IFERROR(I7/H7,0)"]]; output.getRange("J5:J7").format.numberFormat = "0.0%";
output.getRange("G4:J7").format.borders = { preset: "outside", style: "thin", color: "#B9CFD8" };

output.getRange("G10:J10").values = [["Step effectiveness", "Meeting requested", "Meeting booked", "Meeting outcome"]]; styleSection(output.getRange("G10:J10"));
output.getRange("G11:G16").values = [["Step 1"], ["Step 2"], ["Step 3"], ["Step 4"], ["Step 5"], ["Step 6+"]];
for (let row = 11; row <= 16; row += 1) {
  output.getRange(`H${row}`).formulas = [[`=COUNTIFS('Input'!$G$6:$G$${MAX_ROWS + 5},G${row},'Input'!$N$6:$N$${MAX_ROWS + 5},"Yes",'Input'!$O$6:$O$${MAX_ROWS + 5},"No")`]];
  output.getRange(`I${row}`).formulas = [[`=COUNTIFS('Input'!$G$6:$G$${MAX_ROWS + 5},G${row},'Input'!$O$6:$O$${MAX_ROWS + 5},"Yes")`]];
  output.getRange(`J${row}`).formulas = [[`=H${row}+I${row}`]];
}
output.getRange("G10:J16").format.borders = { preset: "outside", style: "thin", color: "#B9CFD8" };
output.mergeCells("A18:J19"); output.getRange("A18").values = [["Interpretation guide: compare the meeting outcome rate for Version A vs B, then validate whether the stronger version remains stronger across individual senders. The Stop Step table identifies where prospects who requested or booked meetings were most often stopped. Rename the Sender 1–3 and Step 1–6+ labels to match your export values."]]; output.getRange("A18:J19").format = { fill: "#FFF8E6", font: { color: "#69521B", italic: true }, wrapText: true, verticalAlignment: "center" };
output.getRange("A:A").format.columnWidth = 23; output.getRange("B:E").format.columnWidth = 18; output.getRange("F:F").format.columnWidth = 4; output.getRange("G:G").format.columnWidth = 20; output.getRange("H:J").format.columnWidth = 18;
output.freezePanes.freezeRows(2);

await fs.mkdir(path.dirname(outputFile), { recursive: true });
const file = await SpreadsheetFile.exportXlsx(workbook);
await file.save(outputFile);
console.log(`Created ${outputFile} with ${records.length} normalized records.`);
