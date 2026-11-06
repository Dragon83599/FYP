/**
 * Talk to the probe over Web Bluetooth.
 *
 * This is the only part of the phone-side path that cannot be tested on this
 * machine without hardware, so it is written to be testable with a fake
 * `navigator.bluetooth`: the class takes the Bluetooth API as a constructor
 * option, and everything it does is expressed in terms of that object.
 *
 * What it does not do: buffer audio, pre-process, or classify. It turns BLE
 * notifications into decoded 20 ms audio frames and reports link quality, which
 * is exactly the boundary the page needs.
 *
 * Browser support is narrow and that is a design constraint, not an oversight:
 * Web Bluetooth exists in Android Chrome/Edge, and nowhere else that matters
 * here (no iOS Safari, no Huawei stock browser). The page must say so instead of
 * offering a button that cannot work -- see pwa/pwa.js for how it reports that.
 */

import {
  FrameReassembler,
  decodeAudioFrame,
  LinkAProtocolError,
} from "./lka1.js";
import {
  LKA1_SERVICE_UUID,
  LKA1_AUDIO_DATA_UUID,
  LKA1_CONTROL_UUID,
  LKA1_STATUS_UUID,
  CONTROL_CMD,
  encodeControl,
  decodeStatus,
} from "./gatt.js";

/** Link quality counters the page can show while streaming. */
export function emptyStats() {
  return {
    frames: 0,
    packets: 0,
    crcErrors: 0,
    fragmentErrors: 0,
    droppedBySequence: 0,
    lastSequence: null,
  };
}

/**
 * One connection to a probe.
 *
 * Usage:
 *   const probe = new ProbeConnection({ onFrame, onStatus, onState });
 *   await probe.connect();   // must be called from a user gesture
 *   await probe.start();
 *   ...
 *   probe.disconnect();
 */
export class ProbeConnection {
  constructor({
    bluetooth = globalThis.navigator?.bluetooth,
    onFrame = () => {},
    onStatus = () => {},
    onState = () => {},
    onError = () => {},
  } = {}) {
    if (!bluetooth) {
      throw new Error("这个浏览器没有 Web Bluetooth（需要安卓 Chrome / Edge）");
    }
    this.bluetooth = bluetooth;
    this.onFrame = onFrame;
    this.onStatus = onStatus;
    this.onState = onState;
    this.onError = onError;

    this.device = null;
    this.server = null;
    this.audio = null;
    this.control = null;
    this.status = null;
    this.reassembler = new FrameReassembler();
    this.stats = emptyStats();
    this.state = "idle";
    this._onDisconnected = () => this.#handleDisconnect();
  }

  #setState(state, detail = "") {
    this.state = state;
    this.onState(state, detail);
  }

  /** Must be called from a user gesture (a click): the browser requires it. */
  async connect() {
    this.#setState("requesting");
    this.device = await this.bluetooth.requestDevice({
      // Filter by service, never by name: the name is not a unique identifier.
      filters: [{ services: [LKA1_SERVICE_UUID] }],
      optionalServices: [LKA1_SERVICE_UUID],
    });
    this.device.addEventListener("gattserverdisconnected", this._onDisconnected);

    this.#setState("connecting");
    this.server = await this.device.gatt.connect();
    const service = await this.server.getPrimaryService(LKA1_SERVICE_UUID);
    this.audio = await service.getCharacteristic(LKA1_AUDIO_DATA_UUID);
    this.control = await service.getCharacteristic(LKA1_CONTROL_UUID);
    this.status = await service.getCharacteristic(LKA1_STATUS_UUID);

    this.audio.addEventListener("characteristicvaluechanged", (event) => this.#onPacket(event));
    this.status.addEventListener("characteristicvaluechanged", (event) => this.#onStatusValue(event));
    await this.audio.startNotifications();
    await this.status.startNotifications();

    this.#setState("connected", this.device.name ?? "");
    return this.device;
  }

  /**
   * Ask the probe to start streaming.
   *
   * Web Bluetooth does not expose MTU negotiation or connection parameters: the
   * operating system decides. The 247-byte MTU the profile asks for therefore
   * cannot be requested here, only observed indirectly by whether 222-byte
   * notifications arrive intact.
   */
  async start(token = 1) {
    await this.#write(encodeControl(CONTROL_CMD.START, { token }));
  }

  async stop(token = 2) {
    await this.#write(encodeControl(CONTROL_CMD.STOP, { token }));
  }

  async #write(bytes) {
    if (!this.control) {
      throw new Error("还没有连接探头");
    }
    // Write with response where available: control commands are low-rate and
    // must not be silently dropped.
    if (this.control.properties?.write) {
      await this.control.writeValueWithResponse(bytes);
    } else {
      await this.control.writeValueWithoutResponse(bytes);
    }
  }

  #onPacket(event) {
    const view = event.target.value;
    const bytes = new Uint8Array(view.buffer, view.byteOffset, view.byteLength);
    this.stats.packets += 1;
    try {
      const result = this.reassembler.push(bytes);
      const sequence = result.packet?.frameSequence;
      if (typeof sequence === "number") {
        if (this.stats.lastSequence !== null && sequence > this.stats.lastSequence + 1) {
          this.stats.droppedBySequence += sequence - this.stats.lastSequence - 1;
        }
        this.stats.lastSequence = sequence;
      }
      if (result.status === "complete") {
        this.stats.frames += 1;
        const frame = decodeAudioFrame(result.frame);
        this.onFrame(frame, this.stats);
      }
    } catch (error) {
      if (error instanceof LinkAProtocolError && error.code === "bad_crc") {
        this.stats.crcErrors += 1;
      } else {
        this.stats.fragmentErrors += 1;
      }
      this.onError(error, this.stats);
    }
  }

  #onStatusValue(event) {
    const view = event.target.value;
    const bytes = new Uint8Array(view.buffer, view.byteOffset, view.byteLength);
    try {
      this.onStatus(decodeStatus(bytes));
    } catch (error) {
      this.onError(error, this.stats);
    }
  }

  #handleDisconnect() {
    this.#setState("disconnected", "探头断开");
    this.audio = null;
    this.control = null;
    this.status = null;
    this.server = null;
  }

  disconnect() {
    if (this.device?.gatt?.connected) {
      this.device.gatt.disconnect();
    }
    this.#handleDisconnect();
  }
}
