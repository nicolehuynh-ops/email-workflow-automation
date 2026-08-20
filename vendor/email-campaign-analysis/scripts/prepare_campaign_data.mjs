#!/usr/bin/env node
/** Creates a campaign's output/normalized_campaign.csv from Reply.io exports and the positive-response tracker. */
import fs from "node:fs/promises";
import path from "node:path";
import { FileBlob, SpreadsheetFile } from "@oai/artifact-tool";

const root = path.resolve(path.dirname(new URL(import.meta.url).pathname), "..");
const campaignId = process.argv[2] ?? "uncovered-issuer-email-campaign";
if (!/^[a-z0-9][a-z0-9-]*$/.test(campaignId)) throw new Error("Campaign must be a lowercase slug using letters, numbers, and hyphens.");
const campaignDir = path.join(root, "campaigns", campaignId);
const input = path.join(campaignDir, "input");
const output = path.join(campaignDir, "output", "normalized_campaign.csv");
const headers = ["Source File","Sender","Version","Issuer ID","Issuer Name","Email","Stop Step","Last Activity Date","Delivered","Opened","Clicked","Replied","Positive Response","Meeting Requested","Meeting Booked","Outcome","Notes"];
function csv(text) { const out=[]; let row=[], field="", quote=false; for(let i=0;i<text.length;i+=1){const ch=text[i], next=text[i+1]; if(ch==='"'&&quote&&next==='"'){field+='"';i+=1;}else if(ch==='"')quote=!quote;else if(ch===','&&!quote){row.push(field);field="";}else if((ch==='\n'||ch==='\r')&&!quote){if(ch==='\r'&&next==='\n')i+=1;row.push(field);if(row.some(x=>x.trim()))out.push(row);row=[];field="";}else field+=ch;}row.push(field);if(row.some(x=>x.trim()))out.push(row);return out; }
function key(value) { return String(value??"").trim().toLowerCase().replace(/^www\./,"").replace(/[^a-z0-9]/g,""); }
function sender(value) { return String(value??"").trim().replace(/^daragh$/i,"Darragh"); }
function version(sequence) { return (String(sequence??"").match(/version\s+([ab])/i)||[])[1]?.toUpperCase()||""; }
function yes(value) { return String(value??"").trim()==="1"||/^yes$/i.test(String(value??"")); }
function outcome(response) { const text=String(response??"").toLowerCase(); if(/meeting\s*booked|scheduled call|schedule call|scheduling call/.test(text))return "Meeting Booked"; if(/meeting\s*requested/.test(text))return "Meeting Requested"; return "Positive Response"; }
function escape(value) { const text=String(value??""); return /[",\n\r]/.test(text)?`"${text.replaceAll('"','""')}"`:text; }

const contactRows=csv(await fs.readFile(path.join(input,"Reply.io_Contact_Report.csv"),"utf8"));
const contactHeaders=contactRows[0].map(x=>x.replace(/^\uFEFF/,""));
const contacts=contactRows.slice(1).map(row=>Object.fromEntries(contactHeaders.map((header,index)=>[header,row[index]??""])));
const trackerFile=(await fs.readdir(input)).find(name=>/positive_response.*\.xlsx$/i.test(name));
if(!trackerFile) throw new Error("Missing positive-response tracker XLSX in input/");
const trackerBook=await SpreadsheetFile.importXlsx(await FileBlob.load(path.join(input,trackerFile)));
const trackerValues=trackerBook.worksheets.getItem("Positive Responses").getRange("A1:I1000").values.filter(row=>row.some(value=>value!==null&&value!==""));
const trackerHeaders=trackerValues[0];
const tracker=trackerValues.slice(1).map(row=>Object.fromEntries(trackerHeaders.map((header,index)=>[header,row[index]??""])));
const trackerMap=new Map(tracker.map(row=>[`${key(row.Domain)}|${key(sender(row["PCS Sender"]))}|${row["Email Version"]}`,row]));
const rows=contacts.map(contact=>{
  const campaignVersion=version(contact.Sequence); const campaignSender=sender(contact["PCS Sender"]);
  const trackerRow=trackerMap.get(`${key(contact["PCS Issuer ID"])}|${key(campaignSender)}|${campaignVersion}`);
  const result=trackerRow?outcome(trackerRow.Response):"No Response";
  return ["Reply.io Contact Report",campaignSender,campaignVersion,contact["PCS Issuer ID"],contact.IssuerName,contact["Contact email"],contact["Sequence step"],contact["Delivery date"],yes(contact.Delivered)?"Yes":"No",yes(contact.Opened)?"Yes":"No",yes(contact.Clicked)?"Yes":"No",yes(contact.Replied)?"Yes":"No",trackerRow?"Yes":"No",result==="Meeting Requested"?"Yes":"No",result==="Meeting Booked"?"Yes":"No",result,trackerRow?.Response??""];
});
const matched=new Set(rows.filter(row=>row[12]==="Yes").map(row=>`${key(row[3])}|${key(row[1])}|${row[2]}`));
for(const row of tracker){const matchKey=`${key(row.Domain)}|${key(sender(row["PCS Sender"]))}|${row["Email Version"]}`; if(!matched.has(matchKey)){const result=outcome(row.Response); rows.push(["Positive-response tracker (unmatched)",sender(row["PCS Sender"]),row["Email Version"],row.Domain,row["Issuer Name"],"","Unmatched","","No","No","No","No","Yes",result==="Meeting Requested"?"Yes":"No",result==="Meeting Booked"?"Yes":"No",result,row.Response]);}}
await fs.mkdir(path.dirname(output),{recursive:true});
await fs.writeFile(output,[headers.join(","),...rows.map(row=>row.map(escape).join(","))].join("\n"));
console.log(`Created ${output}: ${contacts.length} Reply.io contacts, ${tracker.length} tracked positive outcomes.`);
