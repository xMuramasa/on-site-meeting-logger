/* Independently recoverable PCM16 blocks, averaged to mono at the context sample rate. */
class RecoveryProcessor extends AudioWorkletProcessor {
  constructor() {
    super(); this.buffer = new ArrayBuffer(sampleRate * 5 * 2); this.offset = 0;
    this.port.onmessage = event => { if (event.data === "flush") { this.flush(); this.port.postMessage({ flushed: true }); } };
  }
  flush() {
    if (this.offset) { const data = this.buffer.slice(0, this.offset * 2); this.port.postMessage({ data }, [data]); this.offset = 0; }
  }
  process(inputs) {
    const channels = inputs[0];
    if (!channels?.length) return true;
    const view = new DataView(this.buffer);
    for (let i = 0; i < channels[0].length; i++) {
      let sample = 0; for (const channel of channels) sample += channel[i] / channels.length;
      sample = Math.max(-1, Math.min(1, sample));
      view.setInt16(this.offset++ * 2, Math.round(sample * (sample < 0 ? 32768 : 32767)), true);
      if (this.offset * 2 === this.buffer.byteLength) this.flush();
    }
    return true;
  }
}
registerProcessor("meeting-recovery", RecoveryProcessor);
