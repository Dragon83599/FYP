import {
  AUDIO_FRAME_SAMPLES,
  AUDIO_SAMPLE_RATE_HZ,
  AUDIO_FRAME_DURATION_MS,
  decodeFrame,
  encodeFrame
} from './audio-protocol.mjs';
import {
  FrameReassembler,
  LINK_A_DEFAULT_MAX_FRAGMENT_PAYLOAD,
  fragmentFrame
} from './link-a-protocol.mjs';
import { decimate8kTo4k } from './dsp.mjs';

export function splitIntoAudioFrames(samples, {
  startTimestampMs = 0,
  padFinalFrame = true
} = {}) {
  const frames = [];
  let offset = 0;
  let sequence = 0;
  while (offset < samples.length) {
    const chunk = new Int16Array(AUDIO_FRAME_SAMPLES);
    const available = Math.min(AUDIO_FRAME_SAMPLES, samples.length - offset);
    chunk.set(samples.subarray(offset, offset + available));
    if (!padFinalFrame && available !== AUDIO_FRAME_SAMPLES) break;
    frames.push({
      samples: chunk,
      sampleRateHz: AUDIO_SAMPLE_RATE_HZ,
      timestampMs: startTimestampMs + sequence * AUDIO_FRAME_DURATION_MS,
      sequence,
      channelCount: 1
    });
    offset += available;
    sequence += 1;
  }
  return frames;
}

export function encodeAudioToLinkPackets(samples, {
  sessionId = 1,
  maxFragmentPayload = LINK_A_DEFAULT_MAX_FRAGMENT_PAYLOAD,
  startTimestampMs = 0
} = {}) {
  const frames = splitIntoAudioFrames(samples, { startTimestampMs });
  const packets = [];
  for (const frame of frames) {
    const encodedFrame = encodeFrame(frame);
    packets.push(...fragmentFrame(encodedFrame, {
      sessionId,
      frameSequence: frame.sequence,
      maxFragmentPayload
    }));
  }
  return { frames, packets };
}

export function receiveAudioPackets(packets, {
  maxPendingFrames = 8
} = {}) {
  const reassembler = new FrameReassembler({ maxPendingFrames });
  const frames = [];
  const errors = [];

  packets.forEach((packet, packetIndex) => {
    try {
      const result = reassembler.push(packet);
      if (result.status === 'complete') {
        const decoded = decodeFrame(result.frame);
        frames.push({ ...decoded.frame, linkFrameLength: result.frameLength });
      }
    } catch (error) {
      errors.push({ packetIndex, error });
    }
  });

  frames.sort((a, b) => a.sequence - b.sequence);
  const missingSequences = [];
  for (let i = 1; i < frames.length; i += 1) {
    const previous = frames[i - 1].sequence;
    const current = frames[i].sequence;
    for (let sequence = previous + 1; sequence < current; sequence += 1) {
      missingSequences.push(sequence);
    }
  }

  return {
    frames,
    samples: joinFrameSamples(frames),
    aiInput4k: decimate8kTo4k(joinFrameSamples(frames)),
    stats: {
      inputPackets: packets.length,
      decodedFrames: frames.length,
      incompleteFrames: reassembler.pending.size,
      errors: errors.length,
      missingSequences,
      firstSequence: frames[0]?.sequence ?? null,
      lastSequence: frames.at(-1)?.sequence ?? null
    },
    errors
  };
}

export function joinFrameSamples(frames) {
  const output = new Int16Array(frames.length * AUDIO_FRAME_SAMPLES);
  frames.forEach((frame, index) => {
    output.set(frame.samples, index * AUDIO_FRAME_SAMPLES);
  });
  return output;
}

export function dropPackets(packets, indexes) {
  const dropped = new Set(indexes);
  return packets.filter((_, index) => !dropped.has(index));
}

export function runSoftwareLoopback(samples, options = {}) {
  const encoded = encodeAudioToLinkPackets(samples, options);
  const deliveredPackets = options.dropPacketIndexes
    ? dropPackets(encoded.packets, options.dropPacketIndexes)
    : encoded.packets;
  const received = receiveAudioPackets(deliveredPackets, options);
  return { ...encoded, deliveredPackets, received };
}
