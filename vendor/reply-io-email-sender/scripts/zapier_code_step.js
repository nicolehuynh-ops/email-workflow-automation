// Paste this into a Code by Zapier JavaScript step.
//
// Required input fields:
// - REPLY_IO_API_KEY
// - contactEmail
//
// Optional input fields:
// - sequenceId
// - contactId
// - pcsIssuerId
//
// Behavior:
// If sequenceId is omitted, the code finds the contact's single active Reply.io
// sequence and uses that sequence. It errors if there are zero or multiple active
// sequences.

const API_KEY = inputData.REPLY_IO_API_KEY;
const CONTACT_EMAIL = inputData.contactEmail;
const CONTACT_ID = inputData.contactId;
const PROVIDED_SEQUENCE_ID = inputData.sequenceId;
const PROVIDED_ISSUER_ID = inputData.pcsIssuerId;
const PCS_ISSUER_FIELD_ID = "147787";
const BASE_URL = "https://api.reply.io/v3";
const PAGE_SIZE = 1000;
const WRITE_CHUNK_SIZE = 100;

if (!API_KEY) {
  throw new Error("REPLY_IO_API_KEY is required.");
}
if (!CONTACT_EMAIL && !CONTACT_ID) {
  throw new Error("contactEmail or contactId is required.");
}

async function request(method, route, body) {
  const response = await fetch(`${BASE_URL}${route}`, {
    method,
    headers: {
      Authorization: `Bearer ${API_KEY}`,
      "Content-Type": "application/json",
    },
    body: body ? JSON.stringify(body) : undefined,
  });
  const text = await response.text();
  const data = text ? JSON.parse(text) : null;
  if (!response.ok) {
    throw new Error(`Reply.io ${method} ${route} failed (${response.status}): ${JSON.stringify(data)}`);
  }
  return data;
}

async function getContactByEmail(email) {
  const data = await request("GET", `/contacts?email=${encodeURIComponent(email)}`);
  const contact = (data.items || []).find((item) => (item.email || "").toLowerCase() === email.toLowerCase());
  if (!contact) {
    throw new Error(`No Reply.io contact found for ${email}.`);
  }
  return contact;
}

async function getContact(contactId) {
  return request("GET", `/contacts/${contactId}`);
}

async function listContactSequences(contactId) {
  return request("GET", `/contacts/${contactId}/sequences`);
}

async function inferSingleActiveSequenceId(contactId) {
  const sequences = await listContactSequences(contactId);
  const activeSequences = sequences.filter((sequence) => {
    return String(sequence.statusInSequence || sequence.status || "").toLowerCase() === "active";
  });
  if (activeSequences.length === 0 && sequences.length === 1) {
    return sequences[0].sequenceId || sequences[0].id;
  }
  if (activeSequences.length !== 1) {
    throw new Error(
      `Expected exactly one active sequence for contact ${contactId}, found ${activeSequences.length}. ` +
        `Sequences: ${JSON.stringify(
          sequences.map((sequence) => ({
            sequenceId: sequence.sequenceId || sequence.id,
            sequenceName: sequence.sequenceName || sequence.name,
            statusInSequence: sequence.statusInSequence || sequence.status,
          }))
        )}`
    );
  }
  return activeSequences[0].sequenceId || activeSequences[0].id;
}

async function listSequenceContacts(sequenceId) {
  const contacts = [];
  let skip = 0;
  while (true) {
    const data = await request("GET", `/sequences/${sequenceId}/contacts?top=${PAGE_SIZE}&skip=${skip}`);
    contacts.push(...(data.items || []));
    if (!data.hasMore) {
      return contacts;
    }
    skip += PAGE_SIZE;
  }
}

function getCustomFieldValue(contact, fieldId) {
  const field = (contact.customFields || []).find((item) => {
    return String(item.key || item.id || "").toLowerCase() === String(fieldId).toLowerCase();
  });
  return field ? field.value || "" : "";
}

function chunks(items, size) {
  const result = [];
  for (let index = 0; index < items.length; index += size) {
    result.push(items.slice(index, index + size));
  }
  return result;
}

async function markFinished(sequenceId, contactIds) {
  for (const batch of chunks(contactIds, WRITE_CHUNK_SIZE)) {
    const failures = await request("POST", `/sequences/${sequenceId}/contacts/set-status-in-sequence`, {
      contactIds: batch,
      statusInSequence: "finished",
    });
    if (failures && Object.keys(failures).length) {
      throw new Error(`Failed to mark some contacts finished: ${JSON.stringify(failures)}`);
    }
  }
}

const triggerContact = CONTACT_ID ? await getContact(CONTACT_ID) : await getContactByEmail(CONTACT_EMAIL);
const triggerContactId = triggerContact.contactId || triggerContact.id;
const triggerDetails = await getContact(triggerContactId);

const sequenceId = PROVIDED_SEQUENCE_ID ? Number(PROVIDED_SEQUENCE_ID) : await inferSingleActiveSequenceId(triggerContactId);
const issuerId = PROVIDED_ISSUER_ID || getCustomFieldValue(triggerDetails, PCS_ISSUER_FIELD_ID);

if (!issuerId) {
  throw new Error(`Trigger contact ${triggerContactId} is missing PCS Issuer ID field ${PCS_ISSUER_FIELD_ID}.`);
}

const sequenceContacts = await listSequenceContacts(sequenceId);
const triggerSequenceContact = sequenceContacts.find((contact) => Number(contact.contactId) === Number(triggerContactId));
const triggerStatus = String((triggerSequenceContact || {}).statusInSequence || "")
  .toLowerCase()
  .replace(/[^a-z0-9]/g, "");
const triggerDisposition = (triggerSequenceContact || {}).emailDisposition || {};
if (
  (triggerDisposition.isBounced && !triggerDisposition.isReplied) ||
  triggerStatus === "outofoffice"
) {
  return {
    ok: true,
    ignored: true,
    reason: "Trigger contact is bounced or out of office; issuer was not suppressed.",
    sequenceId,
    issuerId,
    triggerContactId,
    triggerContactEmail: triggerContact.email || CONTACT_EMAIL,
    finishedCount: 0,
    finishedContactIds: [],
  };
}
const activeSequenceContacts = sequenceContacts.filter((contact) => {
  return String(contact.statusInSequence || "").toLowerCase() === "active";
});

const sameIssuerActiveContacts = [];
for (const sequenceContact of activeSequenceContacts) {
  const detail = await getContact(sequenceContact.contactId);
  if (detail.isOptedOut) {
    continue;
  }
  const rowIssuerId = getCustomFieldValue(detail, PCS_ISSUER_FIELD_ID);
  if (rowIssuerId === issuerId) {
    sameIssuerActiveContacts.push(sequenceContact);
  }
}

const finishedContactIds = sameIssuerActiveContacts.map((contact) => contact.contactId);
if (finishedContactIds.length) {
  await markFinished(sequenceId, finishedContactIds);
}

return {
  ok: true,
  sequenceId,
  issuerId,
  triggerContactId,
  triggerContactEmail: triggerContact.email || CONTACT_EMAIL,
  finishedCount: finishedContactIds.length,
  finishedContactIds,
};
