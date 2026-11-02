import { digestGet } from './http-digest.js';

/**
 * Read the Hik-Connect / HiLook / EZVIZ verification code from the device.
 * Requires an admin account. Response looks like:
 *   <EZVIZ><verificationCode>ABC123XYZ</verificationCode>...
 */
export async function readVerificationCode({ host, user, pass, timeoutMs = 8000 }) {
  if (!host) return null;
  try {
    const body = await digestGet(`http://${host}/ISAPI/System/Network/EZVIZ`, {
      user,
      pass,
      timeoutMs,
    });
    const xml = body.toString('utf8');
    return /<verificationCode>([^<]*)<\/verificationCode>/.exec(xml)?.[1]?.trim() || null;
  } catch {
    return null;
  }
}

/** Whether the device reports stream encryption as enabled. */
export async function isStreamEncryptionEnabled({ host, user, pass, timeoutMs = 8000 }) {
  if (!host) return null;
  try {
    const body = await digestGet(`http://${host}/ISAPI/System/Network/EZVIZ`, {
      user,
      pass,
      timeoutMs,
    });
    const value = /<streamEncrypteEnabled>([^<]*)<\/streamEncrypteEnabled>/.exec(body.toString('utf8'))?.[1];
    return value === undefined ? null : value === 'true';
  } catch {
    return null;
  }
}
