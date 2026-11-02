import http from 'node:http';
import https from 'node:https';
import crypto from 'node:crypto';

const authCache = new Map();

const md5 = (value) => crypto.createHash('md5').update(value).digest('hex');

/** Parse a `WWW-Authenticate: Digest …` header into a plain object. */
export function parseDigestChallenge(header) {
  const params = {};
  const source = String(header).replace(/^Digest\s+/i, '');
  const re = /([a-zA-Z0-9_-]+)=(?:"([^"]*)"|([^,]*))/g;
  let match;
  while ((match = re.exec(source))) {
    params[match[1].toLowerCase()] = match[2] ?? match[3].trim();
  }
  return params;
}

/** Build an RFC 2617/7616 Digest Authorization header (MD5, qop optional). */
export function buildDigestAuth({
  user,
  pass,
  method = 'GET',
  uri,
  challenge,
  nonceCount = 1,
  cnonce = crypto.randomBytes(8).toString('hex'),
}) {
  const { realm = '', nonce = '', qop, opaque } = challenge;
  const ha1 = md5(`${user}:${realm}:${pass}`);
  const ha2 = md5(`${method}:${uri}`);

  let header;
  if (qop) {
    const selected = qop.split(',').map((entry) => entry.trim()).includes('auth')
      ? 'auth'
      : qop.split(',')[0].trim();
    const nc = nonceCount.toString(16).padStart(8, '0');
    const response = md5(`${ha1}:${nonce}:${nc}:${cnonce}:${selected}:${ha2}`);
    header = `Digest username="${user}", realm="${realm}", nonce="${nonce}", uri="${uri}", response="${response}", qop=${selected}, nc=${nc}, cnonce="${cnonce}"`;
  } else {
    const response = md5(`${ha1}:${nonce}:${ha2}`);
    header = `Digest username="${user}", realm="${realm}", nonce="${nonce}", uri="${uri}", response="${response}"`;
  }

  if (opaque) header += `, opaque="${opaque}"`;
  return header;
}

/**
 * GET a URL using HTTP Digest auth. The camera's nonce is cached and reused;
 * a 401 with a fresh challenge triggers exactly one retry.
 */
export function digestGet(target, { user, pass, timeoutMs = 8000 } = {}) {
  return new Promise((resolve, reject) => {
    const url = new URL(target);
    const lib = url.protocol === 'https:' ? https : http;
    const origin = url.origin;
    const uri = `${url.pathname}${url.search}`;
    let retried = false;

    const request = () => {
      const headers = {};
      const cached = authCache.get(origin);
      if (cached) {
        cached.count += 1;
        headers.Authorization = buildDigestAuth({
          user,
          pass,
          method: 'GET',
          uri,
          challenge: cached.challenge,
          nonceCount: cached.count,
        });
      }

      const req = lib.request(url, { method: 'GET', headers, timeout: timeoutMs }, (res) => {
        const chunks = [];
        res.on('data', (chunk) => chunks.push(chunk));
        res.on('end', () => {
          if (res.statusCode === 401 && !retried) {
            const header = res.headers['www-authenticate'];
            if (!header) {
              reject(new Error('HTTP 401 without a Digest challenge'));
              return;
            }
            const challenge = parseDigestChallenge(header);
            authCache.set(origin, { challenge, count: 1 });
            retried = true;
            request();
            return;
          }

          if (res.statusCode !== 200) {
            reject(new Error(`HTTP ${res.statusCode}`));
            return;
          }
          resolve(Buffer.concat(chunks));
        });
      });

      req.on('timeout', () => req.destroy(new Error('Snapshot request timed out')));
      req.on('error', reject);
      req.end();
    };

    request();
  });
}
