#!/usr/bin/env node
/**
 * Iris Agent — puente de WhatsApp
 *
 * Proceso aislado que mantiene la sesión de WhatsApp Web con Baileys y habla
 * con iris-core por HTTP en loopback, autenticado con un token.
 *
 *   node server.js           # servicio normal
 *   node server.js --pair    # solo pareo: muestra el QR y sale al conectar
 *
 * Variables de entorno (todas vienen de /etc/iris/iris.env):
 *   IRIS_BRIDGE_PORT     puerto local (3003)
 *   IRIS_BRIDGE_TOKEN    token compartido con el core
 *   IRIS_CORE_URL        http://127.0.0.1:3004
 *   IRIS_PHONE           número autorizado (E.164, solo dígitos se comparan)
 *   IRIS_DATA            /var/lib/iris
 *   IRIS_WA_SESSION      carpeta de sesión (por defecto $IRIS_DATA/whatsapp-session)
 */

import express from 'express';
import pino from 'pino';
import qrcode from 'qrcode-terminal';
import path from 'node:path';
import fs from 'node:fs';
import crypto from 'node:crypto';
import { Boom } from '@hapi/boom';
import {
  makeWASocket,
  useMultiFileAuthState,
  DisconnectReason,
  downloadMediaMessage,
  fetchLatestBaileysVersion,
} from '@whiskeysockets/baileys';

// ── Configuración ───────────────────────────────────────────────────────────

const PORT = parseInt(process.env.IRIS_BRIDGE_PORT || '3003', 10);
const TOKEN = (process.env.IRIS_BRIDGE_TOKEN || '').trim();
const CORE_URL = (process.env.IRIS_CORE_URL || 'http://127.0.0.1:3004').replace(/\/+$/, '');
const PHONE = onlyDigits(process.env.IRIS_PHONE || '');
const DATA_DIR = process.env.IRIS_DATA || '/var/lib/iris';
const SESSION_DIR = process.env.IRIS_WA_SESSION || path.join(DATA_DIR, 'whatsapp-session');
const MEDIA_DIR = path.join(DATA_DIR, 'media');
const OUTBOX_DIR = process.env.IRIS_OUTBOX || path.join(DATA_DIR, 'outbox');
const SEEN_FILE = path.join(DATA_DIR, 'bridge-seen.json');
const PAIR_ONLY = process.argv.includes('--pair');
const CHANNELS = (process.env.IRIS_CHANNELS || (process.env.IRIS_PHONE ? 'whatsapp' : 'telegram')).toLowerCase();
const CHANNEL_ON = PAIR_ONLY || CHANNELS.includes('whatsapp') || CHANNELS.includes('both');
const BIND = (process.env.IRIS_BRIDGE_BIND || '127.0.0.1').trim();
const ALLOWED_HOSTS = new Set(
  (process.env.IRIS_ALLOWED_HOSTS || '127.0.0.1,localhost,::1')
    .split(',')
    .map((host) => host.trim().toLowerCase())
    .filter(Boolean)
);
const MAX_SEEN = 1000;
const MAX_PENDING = 200;
const MAX_FILE_MB = Math.max(1, parseFloat(process.env.IRIS_MAX_FILE_MB || '100') || 100);
const MAX_FILE_BYTES = MAX_FILE_MB * 1024 * 1024;

// El agente solo envía archivos que el core dejó en el outbox compartido.
const ALLOWED_EXT = new Set([
  'pdf', 'txt', 'md', 'markdown', 'csv', 'json', 'xml', 'html', 'htm',
  'rtf', 'epub', 'doc', 'docx', 'xls', 'xlsx', 'ppt', 'pptx', 'odt',
  'ods', 'odp', 'png', 'jpg', 'jpeg', 'webp', 'gif', 'bmp', 'svg',
  'heic', 'heif', 'mp3', 'm4a', 'ogg', 'opus', 'wav', 'aac', 'flac',
  'mp4', 'mov', 'm4v', 'webm', 'mkv', '3gp', 'zip', 'tar', 'gz',
  'tgz', '7z', 'rar', 'py', 'js', 'mjs', 'cjs', 'ts', 'tsx', 'jsx',
  'sh', 'sql', 'yaml', 'yml', 'toml', 'ini', 'cfg', 'ipynb',
]);
const MIME_BY_EXT = {
  pdf: 'application/pdf',
  txt: 'text/plain',
  md: 'text/markdown',
  csv: 'text/csv',
  json: 'application/json',
  xml: 'application/xml',
  html: 'text/html',
  htm: 'text/html',
  rtf: 'application/rtf',
  epub: 'application/epub+zip',
  doc: 'application/msword',
  docx: 'application/vnd.openxmlformats-officedocument.wordprocessingml.document',
  xls: 'application/vnd.ms-excel',
  xlsx: 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
  ppt: 'application/vnd.ms-powerpoint',
  pptx: 'application/vnd.openxmlformats-officedocument.presentationml.presentation',
  odt: 'application/vnd.oasis.opendocument.text',
  ods: 'application/vnd.oasis.opendocument.spreadsheet',
  odp: 'application/vnd.oasis.opendocument.presentation',
  png: 'image/png',
  jpg: 'image/jpeg',
  jpeg: 'image/jpeg',
  webp: 'image/webp',
  gif: 'image/gif',
  bmp: 'image/bmp',
  svg: 'image/svg+xml',
  heic: 'image/heic',
  heif: 'image/heif',
  mp3: 'audio/mpeg',
  m4a: 'audio/mp4',
  ogg: 'audio/ogg',
  opus: 'audio/opus',
  wav: 'audio/wav',
  aac: 'audio/aac',
  flac: 'audio/flac',
  mp4: 'video/mp4',
  mov: 'video/quicktime',
  m4v: 'video/x-m4v',
  webm: 'video/webm',
  mkv: 'video/x-matroska',
  '3gp': 'video/3gpp',
  zip: 'application/zip',
  tar: 'application/x-tar',
  gz: 'application/gzip',
  tgz: 'application/gzip',
  '7z': 'application/x-7z-compressed',
  rar: 'application/vnd.rar',
  py: 'text/x-python',
  js: 'text/javascript',
  mjs: 'text/javascript',
  cjs: 'text/javascript',
  ts: 'text/x-typescript',
  tsx: 'text/x-typescript',
  jsx: 'text/javascript',
  sh: 'application/x-sh',
  sql: 'application/sql',
  yaml: 'application/yaml',
  yml: 'application/yaml',
  toml: 'application/toml',
  ini: 'text/plain',
  cfg: 'text/plain',
  ipynb: 'application/x-ipynb+json',
};

if (!CHANNEL_ON) {
  console.log(`[iris-bridge] WhatsApp inactivo (IRIS_CHANNELS=${CHANNELS}). Saliendo.`);
  process.exit(0);
}

if (!TOKEN && !PAIR_ONLY) {
  console.error('[iris-bridge] Falta IRIS_BRIDGE_TOKEN. Abortando.');
  process.exit(2);
}
if (!PHONE && !PAIR_ONLY) {
  console.warn('[iris-bridge] Aviso: IRIS_PHONE está vacío; nadie podrá escribir.');
}

fs.mkdirSync(SESSION_DIR, { recursive: true, mode: 0o700 });
fs.mkdirSync(MEDIA_DIR, { recursive: true, mode: 0o700 });
fs.mkdirSync(OUTBOX_DIR, { recursive: true, mode: 0o700 });
const OUTBOX_REAL = fs.realpathSync(OUTBOX_DIR);

const logger = pino({ level: process.env.IRIS_BRIDGE_LOG_LEVEL || 'warn' });

function onlyDigits(value) {
  return String(value || '').replace(/\D+/g, '');
}

function log(...args) {
  console.log(new Date().toISOString(), '[iris-bridge]', ...args);
}

// ── Estado ──────────────────────────────────────────────────────────────────

let sock = null;
let connectionState = 'disconnected';
let reconnectTimer = null;
let shuttingDown = false;
const pending = []; // mensajes que no pudieron entregarse al core
const seen = loadSeen();
let sendChain = Promise.resolve();

function loadSeen() {
  try {
    const raw = JSON.parse(fs.readFileSync(SEEN_FILE, 'utf8'));
    return new Set(Array.isArray(raw) ? raw.slice(-MAX_SEEN) : []);
  } catch {
    return new Set();
  }
}

function persistSeen(id) {
  seen.add(id);
  if (seen.size > MAX_SEEN) {
    const keep = Array.from(seen).slice(-MAX_SEEN);
    seen.clear();
    keep.forEach((v) => seen.add(v));
  }
  try {
    fs.writeFileSync(SEEN_FILE, JSON.stringify(Array.from(seen).slice(-MAX_SEEN)), { mode: 0o600 });
  } catch (err) {
    log('no pude guardar el registro de vistos:', err.message);
  }
}

// ── Permitido ──────────────────────────────────────────────────────────────

function readLidMapping(identifier, suffix = '') {
  try {
    const file = path.join(SESSION_DIR, `lid-mapping-${identifier}${suffix}.json`);
    if (!fs.existsSync(file)) return null;
    const raw = JSON.parse(fs.readFileSync(file, 'utf8'));
    const digits = onlyDigits(String(raw));
    return digits || null;
  } catch {
    return null;
  }
}

/** Todos los identificadores (teléfono/LID) que apuntan a la misma persona. */
function expandAliases(identifier) {
  const start = onlyDigits(String(identifier || '').split('@')[0].split(':')[0]);
  const seen = new Set();
  const queue = start ? [start] : [];
  while (queue.length > 0) {
    const current = queue.shift();
    if (!current || seen.has(current)) continue;
    seen.add(current);
    for (const suffix of ['', '_reverse']) {
      const mapped = readLidMapping(current, suffix);
      if (mapped && !seen.has(mapped)) queue.push(mapped);
    }
  }
  return seen;
}

function isAllowedSender(ids) {
  if (PHONE === '*') return true;
  if (!PHONE) return false;
  return ids.some((id) => expandAliases(id).has(PHONE));
}

function phoneJidFor(ids) {
  const direct = ids.find((id) => {
    const digits = onlyDigits(String(id || '').split('@')[0].split(':')[0]);
    return digits === PHONE;
  });
  if (direct) return direct;
  if (ids.some((id) => expandAliases(id).has(PHONE))) {
    return `${PHONE}@s.whatsapp.net`;
  }
  return ids.find((id) => String(id).endsWith('@s.whatsapp.net')) || null;
}

/**
 * Baileys exige un JID. Acepta un número suelto, un JID de teléfono o uno de
 * grupo, y cuando hay un LID conocido en la sesión envía por ese chat.
 */
function resolveChatId(chatId) {
  const value = String(chatId || '').trim();
  if (!value) return value;
  const at = value.indexOf('@');
  const user = at >= 0 ? value.slice(0, at) : value;
  const server = at >= 0 ? value.slice(at + 1) : '';
  if (server && server !== 's.whatsapp.net') return value;
  const digits = onlyDigits(user);
  if (!digits) return value;
  const lid = readLidMapping(digits);
  if (lid) return `${lid}@lid`;
  return at >= 0 ? value : `${digits}@s.whatsapp.net`;
}

// ── HTTP al core ────────────────────────────────────────────────────────────

async function postToCore(payload) {
  const body = JSON.stringify(payload);
  const res = await fetch(`${CORE_URL}/inbox`, {
    method: 'POST',
    headers: {
      'Content-Type': 'application/json',
      Authorization: `Bearer ${TOKEN}`,
    },
    body,
    signal: AbortSignal.timeout(15000),
  });
  if (!res.ok) {
    throw new Error(`core respondió HTTP ${res.status}: ${(await res.text()).slice(0, 200)}`);
  }
  return res.json().catch(() => ({ ok: true }));
}

async function deliver(payload) {
  try {
    await postToCore(payload);
    log('mensaje entregado al core', payload.id);
  } catch (err) {
    log('core no disponible, en cola:', err.message);
    pending.push(payload);
    if (pending.length > MAX_PENDING) pending.shift();
  }
}

async function flushPending() {
  while (pending.length > 0) {
    const payload = pending[0];
    try {
      await postToCore(payload);
      pending.shift();
      log('mensaje pendiente entregado', payload.id);
    } catch {
      return; // el core sigue caído
    }
  }
}

setInterval(() => {
  if (pending.length > 0) flushPending().catch(() => {});
}, 5000);

// ── Envío ───────────────────────────────────────────────────────────────────

function enqueueSend(fn) {
  const task = sendChain.then(fn, fn);
  sendChain = task.catch(() => {});
  return task;
}

function splitText(text, max = 4000) {
  const clean = String(text ?? '');
  if (clean.length <= max) return [clean];
  const chunks = [];
  let rest = clean;
  while (rest.length > max) {
    let cut = rest.lastIndexOf('\n', max);
    if (cut < Math.floor(max / 2)) cut = rest.lastIndexOf(' ', max);
    if (cut < 1) cut = max;
    chunks.push(rest.slice(0, cut).trimEnd());
    rest = rest.slice(cut).trimStart();
  }
  if (rest) chunks.push(rest);
  return chunks;
}

function isInside(child, root) {
  const rel = path.relative(root, child);
  return Boolean(rel) && !rel.startsWith('..') && !path.isAbsolute(rel);
}

/** Valida que el archivo esté en el outbox, no sea symlink y respete el límite. */
function resolveDocument(raw) {
  const value = typeof raw === 'string' ? raw.trim() : '';
  if (!value) throw new Error('path es obligatorio');

  const absolute = path.resolve(value);
  const rel = path.relative(OUTBOX_REAL, absolute);
  if (!rel || rel.startsWith('..') || path.isAbsolute(rel)) {
    throw new Error('ruta fuera del outbox');
  }

  let current = OUTBOX_REAL;
  for (const part of rel.split(path.sep)) {
    current = path.join(current, part);
    let stats;
    try {
      stats = fs.lstatSync(current);
    } catch {
      throw new Error('el archivo no existe');
    }
    if (stats.isSymbolicLink()) throw new Error('no se permiten symlinks');
  }

  const real = fs.realpathSync(absolute);
  if (!isInside(real, OUTBOX_REAL)) throw new Error('ruta fuera del outbox');
  const stats = fs.statSync(real);
  if (!stats.isFile()) throw new Error('no es un archivo');

  const ext = path.extname(real).slice(1).toLowerCase();
  if (!ALLOWED_EXT.has(ext)) {
    throw new Error(`extensión no permitida: .${ext || '?'}`);
  }
  if (stats.size > MAX_FILE_BYTES) {
    throw new Error(`archivo demasiado grande (máximo ${MAX_FILE_MB} MB)`);
  }

  return {
    file: real,
    name: path.basename(real),
    size: stats.size,
    mimetype: MIME_BY_EXT[ext] || 'application/octet-stream',
  };
}

async function sendDocument(chatId, rawPath, caption) {
  if (!sock || connectionState !== 'connected') {
    throw new Error('WhatsApp no está conectado');
  }
  const doc = resolveDocument(rawPath);
  const buffer = fs.readFileSync(doc.file);
  const payload = {
    document: buffer,
    fileName: doc.name,
    mimetype: doc.mimetype,
  };
  if (caption) payload.caption = String(caption).slice(0, 1024);
  const target = resolveChatId(chatId);
  const sent = await enqueueSend(() => sock.sendMessage(target, payload));
  return sent?.key?.id ? [sent.key.id] : [];
}

async function sendText(chatId, text) {
  if (!sock || connectionState !== 'connected') {
    throw new Error('WhatsApp no está conectado');
  }
  const ids = [];
  const target = resolveChatId(chatId);
  for (const chunk of splitText(text)) {
    const sent = await enqueueSend(() => sock.sendMessage(target, { text: chunk }));
    if (sent?.key?.id) ids.push(sent.key.id);
  }
  return ids;
}

// ── WhatsApp ────────────────────────────────────────────────────────────────

function extractText(message) {
  if (!message) return '';
  return (
    message.conversation ||
    message.extendedTextMessage?.text ||
    message.imageMessage?.caption ||
    message.videoMessage?.caption ||
    message.documentMessage?.caption ||
    ''
  );
}

function mediaKind(message) {
  if (message?.imageMessage) return 'image';
  if (message?.videoMessage) return 'video';
  if (message?.audioMessage || message?.pttMessage) return 'audio';
  if (message?.documentMessage) return 'document';
  return null;
}

function mediaExtension(message, kind) {
  const mime =
    message?.imageMessage?.mimetype ||
    message?.videoMessage?.mimetype ||
    message?.audioMessage?.mimetype ||
    message?.pttMessage?.mimetype ||
    message?.documentMessage?.mimetype ||
    '';
  const docName = message?.documentMessage?.fileName || '';
  const docExt = path.extname(docName).replace('.', '');
  if (docExt) return docExt;
  const byMime = {
    'image/jpeg': 'jpg',
    'image/png': 'png',
    'image/webp': 'webp',
    'video/mp4': 'mp4',
    'audio/ogg': 'ogg',
    'audio/mpeg': 'mp3',
    'audio/mp4': 'm4a',
    'application/pdf': 'pdf',
  };
  return byMime[mime] || (kind === 'image' ? 'jpg' : kind === 'video' ? 'mp4' : kind === 'audio' ? 'ogg' : 'bin');
}

async function saveMedia(msg, kind) {
  try {
    const buffer = await downloadMediaMessage(
      msg,
      'buffer',
      {},
      { logger, reuploadRequest: sock.updateMediaMessage },
    );
    const ext = mediaExtension(msg.message, kind);
    const name = `${Date.now()}_${msg.key.id.replace(/[^\w.-]/g, '')}.${ext}`;
    const file = path.join(MEDIA_DIR, name);
    fs.writeFileSync(file, buffer, { mode: 0o600 });
    return { path: file, kind };
  } catch (err) {
    log('no pude descargar el adjunto:', err.message);
    return null;
  }
}

async function handleIncoming(msg) {
  if (!msg?.message) return;
  if (msg.key?.fromMe) return;
  if (msg.key?.remoteJid?.endsWith('@g.us')) return; // grupos fuera de v1
  if (msg.key?.remoteJid?.includes('status')) return;
  if (msg.key?.remoteJid?.endsWith('@broadcast')) return;
  if (msg.key?.remoteJid?.endsWith('@newsletter')) return;

  const senderIds = [
    msg.key?.remoteJid,
    msg.key?.remoteJidAlt,
    msg.key?.participant,
    msg.key?.participantAlt,
  ].filter(Boolean);

  if (!isAllowedSender(senderIds)) {
    log('ignorado: remitente no autorizado', senderIds.join(', '));
    return;
  }

  const id = msg.key?.id;
  if (!id || seen.has(id)) return;
  persistSeen(id);

  const kind = mediaKind(msg.message);
  const text = extractText(msg.message);
  const media = kind ? await saveMedia(msg, kind) : null;

  if (!text && !media) return;

  const preferred = phoneJidFor(senderIds) || msg.key.remoteJid;

  await deliver({
    id,
    chatId: msg.key.remoteJid,
    senderId: preferred,
    senderName: msg.pushName || '',
    text,
    media,
    ts: Math.floor(Date.now() / 1000),
  });
}

let cachedVersion = null;

async function getWaVersion() {
  if (cachedVersion) return cachedVersion;
  try {
    const { version } = await fetchLatestBaileysVersion();
    if (Array.isArray(version) && version.length === 3) {
      cachedVersion = version;
      log('versión de WhatsApp Web:', version.join('.'));
    }
  } catch (err) {
    log('no pude consultar la versión de WhatsApp Web:', err.message);
  }
  return cachedVersion;
}

async function startSocket() {
  const { state, saveCreds } = await useMultiFileAuthState(SESSION_DIR);
  const version = await getWaVersion();

  sock = makeWASocket({
    ...(version ? { version } : {}),
    auth: state,
    logger,
    printQRInTerminal: false,
    browser: ['Iris Agent', 'Chrome', '120.0'],
    syncFullHistory: false,
    markOnlineOnConnect: false,
    connectTimeoutMs: 60000,
    defaultQueryTimeoutMs: 60000,
    getMessage: async () => ({ conversation: '' }),
  });

  sock.ev.on('creds.update', saveCreds);

  sock.ev.on('connection.update', (update) => {
    const { connection, lastDisconnect, qr } = update;

    if (qr) {
      // Baileys emite un QR nuevo cada ~20 s mientras no haya sesión. Se
      // imprime siempre en los logs (Coolify → app → Logs → iris-bridge),
      // así nunca queda uno caducado.
      console.log('\n📱 Escanea este QR con WhatsApp (Dispositivos vinculados → Vincular dispositivo):\n');
      qrcode.generate(qr, { small: true }, (code) => process.stdout.write(`${code}\n`));
      console.log('\nEsperando escaneo…\n');
    }

    if (connection === 'open') {
      connectionState = 'connected';
      log('WhatsApp conectado como', sock?.user?.id || 'desconocido');
      if (PAIR_ONLY) {
        setTimeout(() => process.exit(0), 1500);
      }
    }

    if (connection === 'close') {
      connectionState = 'disconnected';
      if (shuttingDown) return;
      const status = new Boom(lastDisconnect?.error)?.output?.statusCode;

      if (status === DisconnectReason.loggedOut) {
        // Sin esto, Baileys reintenta con credenciales inválidas y nunca vuelve a
        // mostrar el QR. Borramos las credenciales para forzar un pareo limpio.
        try {
          fs.unlinkSync(path.join(SESSION_DIR, 'creds.json'));
        } catch {}
        log('sesión cerrada (logged out). Credenciales borradas; ejecuta: iris pair');
        process.exit(1);
      }

      const delay = status === 515 ? 1000 : 3000;
      log(`conexión cerrada (código ${status}). Reconectando en ${delay / 1000}s`);
      if (!reconnectTimer) {
        reconnectTimer = setTimeout(() => {
          reconnectTimer = null;
          startSocket().catch((err) => {
            log('fallo al reconectar:', err.message);
            if (!reconnectTimer) {
              reconnectTimer = setTimeout(() => {
                reconnectTimer = null;
                startSocket().catch(() => {});
              }, 5000);
            }
          });
        }, delay);
      }
    }
  });

  sock.ev.on('messages.upsert', ({ messages, type }) => {
    if (type !== 'notify' && type !== 'append') return;
    for (const msg of messages || []) {
      handleIncoming(msg).catch((err) => log('error procesando mensaje:', err.message));
    }
  });
}

// ── Servidor HTTP ───────────────────────────────────────────────────────────

const app = express();
app.use(express.json({ limit: '1mb' }));

// Solo loopback (defensa extra además del bind).
app.use((req, res, next) => {
  const host = (req.headers.host || '').split(':')[0].replace(/^\[|\]$/g, '').toLowerCase();
  if (!ALLOWED_HOSTS.has(host)) {
    return res.status(400).json({ error: 'Host no permitido' });
  }
  next();
});

function requireToken(req, res, next) {
  const header = req.headers.authorization || '';
  const given = header.startsWith('Bearer ') ? header.slice(7) : '';
  const a = Buffer.from(given);
  const b = Buffer.from(TOKEN);
  if (a.length === b.length && TOKEN && crypto.timingSafeEqual(a, b)) return next();
  return res.status(401).json({ error: 'No autorizado' });
}

app.get('/health', (_req, res) => {
  res.json({
    status: connectionState,
    pending: pending.length,
    session: path.resolve(SESSION_DIR),
    version: 1,
  });
});

app.post('/send', requireToken, async (req, res) => {
  const { chatId, text } = req.body || {};
  if (!chatId || typeof text !== 'string') {
    return res.status(400).json({ error: 'chatId y text son obligatorios' });
  }
  try {
    const ids = await sendText(chatId, text);
    return res.json({ ok: true, messageIds: ids });
  } catch (err) {
    return res.status(503).json({ error: err.message });
  }
});

app.post('/send_document', requireToken, async (req, res) => {
  const { chatId, path: rawPath, caption } = req.body || {};
  if (!chatId || typeof chatId !== 'string' || typeof rawPath !== 'string') {
    return res.status(400).json({ error: 'chatId y path son obligatorios' });
  }
  try {
    resolveDocument(rawPath);
  } catch (err) {
    return res.status(400).json({ error: err.message });
  }
  try {
    const ids = await sendDocument(chatId, rawPath, caption);
    return res.json({ ok: true, messageIds: ids });
  } catch (err) {
    return res.status(503).json({ error: err.message });
  }
});

app.post('/typing', requireToken, async (req, res) => {
  const { chatId } = req.body || {};
  if (!chatId) return res.status(400).json({ error: 'chatId es obligatorio' });
  try {
    if (sock && connectionState === 'connected') {
      await sock.sendPresenceUpdate('composing', resolveChatId(chatId));
    }
    return res.json({ ok: true });
  } catch (err) {
    return res.json({ ok: false, error: err.message });
  }
});

app.post('/read', requireToken, async (req, res) => {
  try {
    if (sock && connectionState === 'connected' && req.body?.key) {
      await sock.readMessages([req.body.key]);
    }
    return res.json({ ok: true });
  } catch (err) {
    return res.json({ ok: false, error: err.message });
  }
});

// ── Arranque ────────────────────────────────────────────────────────────────

if (PAIR_ONLY) {
  log('modo pareo. Sesión:', SESSION_DIR);
  startSocket().catch((err) => {
    log('error de pareo:', err.message);
    process.exit(1);
  });
} else {
  app.listen(PORT, BIND, () => {
    log(`escuchando en ${BIND}:${PORT}`);
    startSocket().catch((err) => {
      log('error al conectar con WhatsApp:', err.message);
      process.exit(1);
    });
  });
}

for (const signal of ['SIGINT', 'SIGTERM']) {
  process.on(signal, () => {
    shuttingDown = true;
    log('apagando…');
    try {
      sock?.end?.(undefined);
    } catch {}
    process.exit(0);
  });
}
