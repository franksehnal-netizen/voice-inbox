// Voice Inbox – upload endpoint (Apps Script web app, execute as Frank).
// Web appka (../app, Content-Type text/plain kvůli CORS) POSTuje JSON {token, recording_id, ext?, audio (base64)} → My Drive/voice_inbox/incoming/<recording_id>.<ext>
// Token se v kódu neuchovává, jen jeho SHA-256. Plán: ../voice_inbox_plan.md

const TOKEN_SHA256 = 'e3c897956f254ea216678a254a516a037de7ac650d158cbb78e0a75b9d8f6503';
const RECORDING_ID_RE = /^\d{8}-\d{6}(-[a-z0-9]{1,8})?$/;
const MIME_BY_EXT = { m4a: 'audio/mp4', mp4: 'audio/mp4', aac: 'audio/aac', wav: 'audio/wav', mp3: 'audio/mpeg', webm: 'audio/webm' };
const MAX_BYTES = 40 * 1024 * 1024;

function doPost(e) {
  try {
    const body = JSON.parse((e && e.postData && e.postData.contents) || '{}');
    if (!tokenOk_(body.token)) return json_({ ok: false, error: 'unauthorized' });

    const id = String(body.recording_id || '');
    if (!RECORDING_ID_RE.test(id)) return json_({ ok: false, error: 'bad recording_id' });
    const ext = String(body.ext || 'm4a').toLowerCase();
    if (!MIME_BY_EXT[ext]) return json_({ ok: false, error: 'bad ext' });
    if (!body.audio) return json_({ ok: false, error: 'missing audio' });

    const bytes = Utilities.base64Decode(String(body.audio).replace(/\s/g, ''));
    if (!bytes.length || bytes.length > MAX_BYTES) return json_({ ok: false, error: 'bad audio size' });

    const name = id + '.' + ext;
    const lock = LockService.getScriptLock();
    lock.waitLock(20000);
    try {
      const folderId = incomingFolderId_();
      // Idempotence: opakovaný upload téže nahrávky (retry ze Zkratky) nic nepřepíše ani nezdvojí.
      if (findChild_(folderId, name)) return json_({ ok: true, status: 'duplicate', name: name });
      Drive.Files.create({ name: name, parents: [folderId] }, Utilities.newBlob(bytes, MIME_BY_EXT[ext], name));
      return json_({ ok: true, status: 'saved', name: name, bytes: bytes.length });
    } finally {
      lock.releaseLock();
    }
  } catch (err) {
    return json_({ ok: false, error: String((err && err.message) || err) });
  }
}

// Spustit jednou ručně v editoru: udělí oprávnění (drive.file) a založí složky.
// Drive advanced service (API v3), ne DriveApp: DriveApp.createFolder vyžaduje plný scope drive.
function authorize() {
  Logger.log('incoming folder: ' + incomingFolderId_());
}

const FOLDER_MIME = 'application/vnd.google-apps.folder';

function incomingFolderId_() {
  const props = PropertiesService.getScriptProperties();
  const id = props.getProperty('INCOMING_FOLDER_ID');
  if (id) {
    try {
      if (!Drive.Files.get(id, { fields: 'trashed' }).trashed) return id;
    } catch (ignored) {
      // složka smazaná / nedostupná → založit znovu
    }
  }
  const root = Drive.Files.create({ name: 'voice_inbox', mimeType: FOLDER_MIME });
  const incoming = Drive.Files.create({ name: 'incoming', mimeType: FOLDER_MIME, parents: [root.id] });
  props.setProperty('INCOMING_FOLDER_ID', incoming.id);
  return incoming.id;
}

function findChild_(folderId, name) {
  const q = "'" + folderId + "' in parents and name = '" + name + "' and trashed = false";
  const res = Drive.Files.list({ q: q, fields: 'files(id)', pageSize: 1 });
  return res.files && res.files.length ? res.files[0].id : null;
}

function tokenOk_(token) {
  if (!token) return false;
  const digest = Utilities.computeDigest(Utilities.DigestAlgorithm.SHA_256, String(token), Utilities.Charset.UTF_8);
  const hex = digest.map(function (b) { return ('0' + (b & 0xff).toString(16)).slice(-2); }).join('');
  return hex === TOKEN_SHA256;
}

function json_(obj) {
  return ContentService.createTextOutput(JSON.stringify(obj)).setMimeType(ContentService.MimeType.JSON);
}
