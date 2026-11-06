/**
 * Link A GATT contract, as code.
 *
 * The prose lives in `docs/link-a-gatt-profile.md` (status: candidate, waiting
 * for the probe side to confirm). These constants are the same values, so the
 * firmware and the PWA cannot disagree about a UUID or a control byte without a
 * test failing: `tests/node/gatt-profile.test.mjs` parses the table in the
 * document and compares it with what is exported here.
 *
 * Nothing here talks to a radio. It is the data model the Web Bluetooth layer
 * will use: which service to filter on, how to encode START/STOP, how to read
 * the status bitfield.
 */

/** 128-bit base: `4c4b4131` is "LKA1" in ASCII. Last 16 bits are the member id. */
export const LKA1_UUID_BASE = "4c4b4131-0000-4000-8000-0000000000";

export const LKA1_SERVICE_UUID = `${LKA1_UUID_BASE}01`;
export const LKA1_AUDIO_DATA_UUID = `${LKA1_UUID_BASE}02`;   // Notify
export const LKA1_CONTROL_UUID = `${LKA1_UUID_BASE}03`;      // Write (phone -> probe)
export const LKA1_STATUS_UUID = `${LKA1_UUID_BASE}04`;       // Notify + Read
export const LKA1_CCCD_UUID = "00002902-0000-1000-8000-00805f9b34fb";  // standard

/** Connection parameters the phone (central) asks for. */
export const LKA1_PREFERRED_MTU = 247;
export const LKA1_PHY = "2M";                  // falls back to 1M, must be logged
export const LKA1_CONN_INTERVAL_MS = 15;       // 11.25 min, 30 max
export const LKA1_SLAVE_LATENCY = 0;
export const LKA1_SUPERVISION_TIMEOUT_MS = 4000;

/** Control command ids (docs/link-a-gatt-profile.md §5.1). */
export const CONTROL_CMD = Object.freeze({
  START: 1,
  STOP: 2,
  SET_GAIN: 3,
  GET_STATUS: 4,
});

/** Probe state values in the Status characteristic, byte 0. */
export const STATUS_STATE = Object.freeze({
  IDLE: 0,
  STREAMING: 1,
  ERROR: 2,
});

/** Error codes in the Status characteristic, byte 1. */
export const STATUS_ERROR = Object.freeze({
  OK: 0,
  ADC_OVERRUN: 1,
  BLE_CONGEST: 2,
  BUFFER_FULL: 3,
});

/** The Status characteristic is exactly four bytes. */
export const STATUS_BYTES = 4;

/** The Control characteristic value is exactly eight bytes. */
export const CONTROL_BYTES = 8;

/**
 * Encode a control command for the Control characteristic.
 *
 * The 8-byte value is deliberately identical to the payload of the optional
 * `type = 4` LKA1 COMMAND packet, so a firmware that prefers the wrapped form
 * and one that prefers the raw characteristic carry the same bytes.
 */
export function encodeControl(command, { arg = 0, token = 0, status = 0 } = {}) {
  if (!Object.values(CONTROL_CMD).includes(command)) {
    throw new RangeError(`unknown control command ${command}`);
  }
  if (!Number.isInteger(arg) || arg < 0 || arg > 0xffff) {
    throw new RangeError(`arg must be a uint16, got ${arg}`);
  }
  if (!Number.isInteger(token) || token < 0 || token > 0xffffffff) {
    throw new RangeError(`token must be a uint32, got ${token}`);
  }
  const bytes = new Uint8Array(CONTROL_BYTES);
  bytes[0] = command;
  bytes[1] = status;
  bytes[2] = arg & 0xff;
  bytes[3] = (arg >>> 8) & 0xff;
  bytes[4] = token & 0xff;
  bytes[5] = (token >>> 8) & 0xff;
  bytes[6] = (token >>> 16) & 0xff;
  bytes[7] = (token >>> 24) & 0xff;
  return bytes;
}

/** Decode a probe's reply on the Control characteristic. */
export function decodeControl(bytes) {
  if (!(bytes instanceof Uint8Array) || bytes.length !== CONTROL_BYTES) {
    throw new TypeError(`control value must be ${CONTROL_BYTES} bytes`);
  }
  return {
    command: bytes[0],
    status: bytes[1],
    arg: bytes[2] | (bytes[3] << 8),
    token: ((bytes[4] | (bytes[5] << 8) | (bytes[6] << 16) | (bytes[7] << 24)) >>> 0),
  };
}

/** Decode the four-byte Status value. */
export function decodeStatus(bytes) {
  if (!(bytes instanceof Uint8Array) || bytes.length < STATUS_BYTES) {
    throw new TypeError(`status value must be at least ${STATUS_BYTES} bytes`);
  }
  return {
    state: bytes[0],
    stateName: Object.keys(STATUS_STATE).find((key) => STATUS_STATE[key] === bytes[0]) ?? "UNKNOWN",
    errorCode: bytes[1],
    errorName: Object.keys(STATUS_ERROR).find((key) => STATUS_ERROR[key] === bytes[1]) ?? "UNKNOWN",
    dropFramesLow8: bytes[2],
    reserved: bytes[3],
  };
}
