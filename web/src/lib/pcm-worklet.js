class PCMCapture extends AudioWorkletProcessor {
  constructor() {
    super();
    this.buffer = new Float32Array(2048);
    this.offset = 0;
    this.recording = true;
    this.port.onmessage = ({ data }) => {
      if (data === 'stop') {
        this.recording = false;
        if (this.offset) this.port.postMessage({ samples: this.buffer.slice(0, this.offset) });
        this.port.postMessage({ stopped: true });
      }
    };
  }
  process(inputs) {
    const samples = inputs[0]?.[0];
    if (this.recording && samples) {
      for (const sample of samples) {
        this.buffer[this.offset++] = sample;
        if (this.offset === this.buffer.length) {
          this.port.postMessage({ samples: this.buffer }, [this.buffer.buffer]);
          this.buffer = new Float32Array(2048);
          this.offset = 0;
        }
      }
    }
    return true;
  }
}
registerProcessor('pcm-capture', PCMCapture);
