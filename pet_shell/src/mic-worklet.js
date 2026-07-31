// PetMicCollector: AudioWorklet 采集处理器（16kHz 由前端降采样）
class PetMicCollector extends AudioWorkletProcessor {
  process(inputs) {
    const input = inputs[0] && inputs[0][0];
    if (input && input.length) {
      const buf = new Float32Array(input.length);
      buf.set(input);
      this.port.postMessage(buf, [buf.buffer]);
    }
    return true;
  }
}
registerProcessor("pet-mic-collector", PetMicCollector);
