/* Ported from homebridge-hikvision-dvr (MIT) - https://github.com/matthewdeaves/homebridge-hikvision-dvr */
import crypto from 'node:crypto';

/**
 * Hikvision / HiLook / EZVIZ "stream encryption" (video encryption with the device verification code).
 *
 * Encrypted NAL units are marked by a Hikvision RTP header extension. Their layout is:
 *   [original NAL header byte] + AES-128-ECB(original NAL, first min(4096, floor(len/16)*16) bytes) + plaintext rest
 * The key is the verification code, zero-padded to 16 bytes. Decrypting the first block yields the original NAL
 * header again, which we use as a sanity check so unencrypted streams are never touched.
 */
const MAX_ENCRYPTED = 4096;

class HikDecryptor {
  constructor(verificationCode, { headerSize = 1 } = {}) {
    this.key = Buffer.alloc(16);
    if (verificationCode) Buffer.from(String(verificationCode), 'utf8').copy(this.key, 0, 0, 16);
    this.enabled = Boolean(verificationCode);
    this.sawFlag = false;
    this.decrypted = 0;
    // H.264 has a 1-byte NAL header, HEVC/H.265 has 2.
    this.headerSize = headerSize;
  }

  _decrypt(buf) {
    const d = crypto.createDecipheriv('aes-128-ecb', this.key, null);
    d.setAutoPadding(false);
    return Buffer.concat([d.update(buf), d.final()]);
  }

  /**
   * @param {Buffer} nal NAL unit without start code
   * @param {boolean} flagged whether the RTP packet carried the Hikvision extension
   * @returns {Buffer} plaintext NAL unit
   */
  process(nal, flagged) {
    const headerSize = this.headerSize;
    if (!this.enabled || nal.length < 16 + headerSize) return nal;
    if (flagged) this.sawFlag = true;
    // Only trust the extension once we've seen one; without it fall back to the self-check alone.
    if (this.sawFlag && !flagged) return nal;
    const body = nal.subarray(headerSize);
    const first = this._decrypt(body.subarray(0, 16));
    for (let i = 0; i < headerSize; i += 1) {
      if (first[i] !== nal[i]) return nal;
    }
    const n = Math.min(Math.floor(body.length / 16) * 16, MAX_ENCRYPTED);
    this.decrypted++;
    return Buffer.concat([first, this._decrypt(body.subarray(16, n)), body.subarray(n)]);
  }
}

export { HikDecryptor };
