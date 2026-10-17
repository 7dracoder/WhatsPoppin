import { useCallback, useEffect, useRef } from "react";

/** RMS level (0-1) above which the mic counts as someone speaking. */
const SPEECH_LEVEL = 0.025;
/** Stop this long after the speaker goes quiet. */
const END_SILENCE_MS = 1300;
/** A single turn never records longer than this. */
const MAX_TURN_MS = 20_000;
/** With no speech at all for this long, give up the take (hands-free keeps re-arming). */
const NO_SPEECH_MS = 9_000;

export interface Take {
  /** The recording, or null when nobody spoke (nothing worth transcribing). */
  audio: Blob | null;
}

/**
 * One microphone stream for the whole session, opened on first use and kept until `release()`.
 * `record()` captures one spoken turn and resolves when the speaker stops (silence detection),
 * or when `stop()` is called. `level` reports the live input level for the orb animation.
 */
export function useRecorder(onLevel: (level: number) => void) {
  const streamRef = useRef<MediaStream | null>(null);
  const ctxRef = useRef<AudioContext | null>(null);
  const analyserRef = useRef<AnalyserNode | null>(null);
  const recorderRef = useRef<MediaRecorder | null>(null);
  const stopRef = useRef<(() => void) | null>(null);
  const levelCb = useRef(onLevel);
  levelCb.current = onLevel;

  const ensureStream = useCallback(async () => {
    if (streamRef.current) return;
    const stream = await navigator.mediaDevices.getUserMedia({
      audio: { echoCancellation: true, noiseSuppression: true, autoGainControl: true },
    });
    const ctx = new AudioContext();
    const analyser = ctx.createAnalyser();
    analyser.fftSize = 1024;
    ctx.createMediaStreamSource(stream).connect(analyser);
    streamRef.current = stream;
    ctxRef.current = ctx;
    analyserRef.current = analyser;
  }, []);

  const release = useCallback(() => {
    stopRef.current?.();
    streamRef.current?.getTracks().forEach((t) => t.stop());
    void ctxRef.current?.close();
    streamRef.current = null;
    ctxRef.current = null;
    analyserRef.current = null;
  }, []);

  useEffect(() => release, [release]);

  const record = useCallback(async (): Promise<Take> => {
    await ensureStream();
    const stream = streamRef.current!;
    const analyser = analyserRef.current!;
    if (ctxRef.current?.state === "suspended") await ctxRef.current.resume();
    const mimeType = ["audio/webm;codecs=opus", "audio/webm", "audio/ogg;codecs=opus", "audio/mp4"].find(
      (t) => MediaRecorder.isTypeSupported(t),
    );
    const recorder = new MediaRecorder(stream, mimeType ? { mimeType } : undefined);
    recorderRef.current = recorder;
    const chunks: Blob[] = [];
    recorder.ondataavailable = (e) => e.data.size > 0 && chunks.push(e.data);

    return new Promise<Take>((resolve) => {
      const samples = new Float32Array(analyser.fftSize);
      const started = performance.now();
      let heardSpeech = false;
      let lastLoud = started;
      let frame = 0;
      let done = false;

      const finish = () => {
        if (done) return;
        done = true;
        cancelAnimationFrame(frame);
        stopRef.current = null;
        levelCb.current(0);
        recorder.onstop = () => {
          resolve({ audio: heardSpeech && chunks.length ? new Blob(chunks, { type: recorder.mimeType }) : null });
        };
        if (recorder.state !== "inactive") recorder.stop();
        else resolve({ audio: null });
      };
      stopRef.current = () => {
        heardSpeech = heardSpeech || performance.now() - started > 600; // a manual stop keeps what was said
        finish();
      };

      const tick = () => {
        analyser.getFloatTimeDomainData(samples);
        let sum = 0;
        for (const s of samples) sum += s * s;
        const rms = Math.sqrt(sum / samples.length);
        levelCb.current(Math.min(1, rms * 12));
        const now = performance.now();
        if (rms > SPEECH_LEVEL) {
          heardSpeech = true;
          lastLoud = now;
        }
        if (
          (heardSpeech && now - lastLoud > END_SILENCE_MS) ||
          now - started > MAX_TURN_MS ||
          (!heardSpeech && now - started > NO_SPEECH_MS)
        ) {
          finish();
          return;
        }
        frame = requestAnimationFrame(tick);
      };

      recorder.start(250);
      frame = requestAnimationFrame(tick);
    });
  }, [ensureStream]);

  /** End the current take now (keeps what was said). */
  const stop = useCallback(() => stopRef.current?.(), []);

  return { record, stop, release };
}
