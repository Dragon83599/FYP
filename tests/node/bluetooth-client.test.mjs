/**
 * Tests for the Web Bluetooth client, with a fake `navigator.bluetooth`.
 *
 * The real radio cannot be exercised here (no probe, no BLE adapter), but every
 * decision the client makes can be: which service it filters on, that it enables
 * notifications on both characteristics, that notifications become decoded audio
 * frames, that a corrupted packet is counted instead of reaching the model, and
 * that START writes exactly the eight bytes the firmware expects.
 */

import { test } from "node:test";
import assert from "node:assert/strict";

import { ProbeConnection } from "../../pwa/bluetooth.js";
import {
  LKA1_SERVICE_UUID,
  LKA1_AUDIO_DATA_UUID,
  LKA1_CONTROL_UUID,
  LKA1_STATUS_UUID,
  CONTROL_CMD,
} from "../../pwa/gatt.js";
import { crc16CcittFalse } from "../../pwa/lka1.js";
import { encodeAudioToLinkPackets } from "../../tools/audio-sim/lib/pipeline.mjs";

/* ---- a tiny fake of the Web Bluetooth surface ---------------------------- */

class FakeEventTarget {
  constructor() {
    this.listeners = new Map();
  }
  addEventListener(type, handler) {
    if (!this.listeners.has(type)) this.listeners.set(type, []);
    this.listeners.get(type).push(handler);
  }
  dispatch(type, event) {
    for (const handler of this.listeners.get(type) ?? []) handler(event);
  }
}

class FakeCharacteristic extends FakeEventTarget {
  constructor(uuid, { writable = false } = {}) {
    super();
    this.uuid = uuid;
    this.value = null;
    this.properties = writable ? { write: true } : {};
    this.notifying = false;
    this.writes = [];
  }
  async startNotifications() {
    this.notifying = true;
    return this;
  }
  async writeValueWithResponse(bytes) {
    this.writes.push(Uint8Array.from(bytes));
    return undefined;
  }
  async writeValueWithoutResponse(bytes) {
    this.writes.push(Uint8Array.from(bytes));
    return undefined;
  }
  /** Deliver a notification the way the browser does: a DataView on `value`. */
  notify(bytes) {
    const copy = new Uint8Array(bytes);
    this.value = new DataView(copy.buffer, copy.byteOffset, copy.byteLength);
    this.dispatch("characteristicvaluechanged", { target: this });
  }
}

function makeFakeBluetooth({ failConnect = false } = {}) {
  const characteristics = {
    audio: new FakeCharacteristic(LKA1_AUDIO_DATA_UUID),
    control: new FakeCharacteristic(LKA1_CONTROL_UUID, { writable: true }),
    status: new FakeCharacteristic(LKA1_STATUS_UUID),
  };
  const device = new FakeEventTarget();
  device.name = "probe-sim";
  device.gatt = {
    connected: false,
    async connect() {
      if (failConnect) throw new Error("connect failed");
      this.connected = true;
      return {
        async getPrimaryService(uuid) {
          assert.equal(uuid, LKA1_SERVICE_UUID);
          return {
            async getCharacteristic(wanted) {
              if (wanted === LKA1_AUDIO_DATA_UUID) return characteristics.audio;
              if (wanted === LKA1_CONTROL_UUID) return characteristics.control;
              if (wanted === LKA1_STATUS_UUID) return characteristics.status;
              throw new Error(`unexpected characteristic ${wanted}`);
            },
          };
        },
      };
    },
    disconnect() {
      this.connected = false;
      device.dispatch("gattserverdisconnected", {});
    },
  };
  return {
    device,
    characteristics,
    requestCalls: [],
    async requestDevice(options) {
      this.requestCalls.push(options);
      return device;
    },
  };
}

/** Produce the LKA1 packet stream for a short synthetic recording. */
function packetStream(samples) {
  const pcm = Int16Array.from(samples);
  const { packets } = encodeAudioToLinkPackets(pcm, { sessionId: 9, maxFragmentPayload: 222 });
  return packets;
}

/* ---- tests ---------------------------------------------------------------- */

test("it filters by service UUID, never by device name", async () => {
  const bluetooth = makeFakeBluetooth();
  const probe = new ProbeConnection({ bluetooth });
  await probe.connect();
  assert.equal(bluetooth.requestCalls.length, 1);
  const { filters, optionalServices } = bluetooth.requestCalls[0];
  assert.deepEqual(filters, [{ services: [LKA1_SERVICE_UUID] }]);
  assert.deepEqual(optionalServices, [LKA1_SERVICE_UUID]);
});

test("it enables notifications on audio and status", async () => {
  const bluetooth = makeFakeBluetooth();
  const probe = new ProbeConnection({ bluetooth });
  await probe.connect();
  assert.equal(bluetooth.characteristics.audio.notifying, true);
  assert.equal(bluetooth.characteristics.status.notifying, true);
  assert.equal(probe.state, "connected");
});

test("notifications become decoded audio frames", async () => {
  const bluetooth = makeFakeBluetooth();
  const frames = [];
  const probe = new ProbeConnection({
    bluetooth,
    onFrame: (frame, stats) => frames.push({ frame, stats: { ...stats } }),
  });
  await probe.connect();

  const samples = Array.from({ length: 160 * 4 }, (_, i) => (i * 37) % 1000 - 500);
  for (const packet of packetStream(samples)) {
    bluetooth.characteristics.audio.notify(packet);
  }

  assert.equal(frames.length, 4, "four 20 ms frames");
  const first = frames[0].frame;
  assert.equal(first.sampleCount, 160);
  assert.equal(first.sampleRateHz, 8000);
  assert.deepEqual(Array.from(first.samples), samples.slice(0, 160));
  // normalisation matches the rest of the page: int16 / 32768
  assert.equal(first.normalized[0], samples[0] / 32768);
  assert.equal(frames[0].stats.packets, 2);
  assert.equal(frames[0].stats.frames, 1);
});

test("a corrupted notification is counted, not delivered", async () => {
  const bluetooth = makeFakeBluetooth();
  const frames = [];
  const errors = [];
  const probe = new ProbeConnection({
    bluetooth,
    onFrame: (frame) => frames.push(frame),
    onError: (error, stats) => errors.push({ code: error.code, stats: { ...stats } }),
  });
  await probe.connect();

  const packets = packetStream(Array.from({ length: 320 }, () => 7));
  const broken = Uint8Array.from(packets[0]);
  broken[25] ^= 0xff;
  bluetooth.characteristics.audio.notify(broken);

  assert.equal(frames.length, 0);
  assert.equal(errors.length, 1);
  assert.equal(errors[0].code, "bad_crc");
  assert.equal(errors[0].stats.crcErrors, 1);

  // the good packet of the same frame must not complete it either
  bluetooth.characteristics.audio.notify(packets[1]);
  assert.equal(frames.length, 0, "a frame with a lost fragment is not delivered");
});

test("a gap in frame sequence is counted as a dropped frame", async () => {
  const bluetooth = makeFakeBluetooth();
  const probe = new ProbeConnection({ bluetooth, onFrame: () => {} });
  await probe.connect();

  const packets = packetStream(Array.from({ length: 160 * 3 }, (_, i) => i));
  // deliver frames 0 and 2, skip frame 1 entirely
  bluetooth.characteristics.audio.notify(packets[0]);
  bluetooth.characteristics.audio.notify(packets[1]);
  bluetooth.characteristics.audio.notify(packets[4]);
  bluetooth.characteristics.audio.notify(packets[5]);

  assert.equal(probe.stats.droppedBySequence, 1);
});

test("START writes exactly the eight control bytes", async () => {
  const bluetooth = makeFakeBluetooth();
  const probe = new ProbeConnection({ bluetooth });
  await probe.connect();
  await probe.start(7);
  const written = bluetooth.characteristics.control.writes;
  assert.equal(written.length, 1);
  assert.deepEqual(Array.from(written[0]), [CONTROL_CMD.START, 0, 0, 0, 7, 0, 0, 0]);
});

test("STOP writes its own command id", async () => {
  const bluetooth = makeFakeBluetooth();
  const probe = new ProbeConnection({ bluetooth });
  await probe.connect();
  await probe.stop(8);
  assert.equal(bluetooth.characteristics.control.writes[0][0], CONTROL_CMD.STOP);
});

test("a status notification is decoded", async () => {
  const bluetooth = makeFakeBluetooth();
  const seen = [];
  const probe = new ProbeConnection({ bluetooth, onStatus: (status) => seen.push(status) });
  await probe.connect();
  bluetooth.characteristics.status.notify(Uint8Array.from([1, 2, 5, 0]));
  assert.equal(seen.length, 1);
  assert.equal(seen[0].stateName, "STREAMING");
  assert.equal(seen[0].errorName, "BLE_CONGEST");
  assert.equal(seen[0].dropFramesLow8, 5);
});

test("a disconnect is reported and clears the characteristics", async () => {
  const bluetooth = makeFakeBluetooth();
  const states = [];
  const probe = new ProbeConnection({ bluetooth, onState: (state) => states.push(state) });
  await probe.connect();
  await probe.start();
  bluetooth.device.gatt.disconnect();
  assert.deepEqual(states, ["requesting", "connecting", "connected", "disconnected"]);
  assert.equal(probe.control, null);
});

test("without Web Bluetooth it refuses with an explanation", () => {
  assert.throws(() => new ProbeConnection({ bluetooth: null }),
    /Web Bluetooth（需要安卓 Chrome \/ Edge）/);
});

test("a failed connection propagates instead of leaving a half state", async () => {
  const bluetooth = makeFakeBluetooth({ failConnect: true });
  const probe = new ProbeConnection({ bluetooth });
  await assert.rejects(() => probe.connect(), /connect failed/);
  assert.equal(probe.state, "connecting");
});
