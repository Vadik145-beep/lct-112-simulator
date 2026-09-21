// Мост между тестом и звуком страницы. Ставится как init-скрипт в каждый документ.
//
// 1. «Микрофон»: navigator.mediaDevices.getUserMedia({audio}) отдаёт поток из AudioContext,
//    в который тест подмешивает заранее озвученные реплики диспетчера (window.__voice.say).
//    Так Vapi (и локальное распознавание) слышат «человека», а не тишину.
// 2. Запись: все входящие WebRTC-дорожки (голос заявителя из облака) и реплики диспетчера
//    сводятся в один поток и пишутся MediaRecorder-ом; куски уходят в тест через
//    window.__pushAudioChunk (exposeFunction), где склеиваются в out/audio.webm.
(() => {
  if (window.__voice) return;

  const state = {
    ctx: null,
    micDest: null,
    mixDest: null,
    recorder: null,
    remote: new Set(),
    playing: 0,
    started: 0,
    log: [],
  };

  function ensure() {
    if (state.ctx) return state;
    const ctx = new AudioContext({ sampleRate: 48000 });
    state.ctx = ctx;
    state.micDest = ctx.createMediaStreamDestination();
    state.mixDest = ctx.createMediaStreamDestination();
    // Постоянная тишина держит дорожку микрофона «живой» между репликами.
    const silence = ctx.createConstantSource();
    silence.offset.value = 0;
    silence.connect(state.micDest);
    silence.connect(state.mixDest);
    silence.start();
    return state;
  }

  function note(msg) {
    state.log.push(`${new Date().toISOString()} ${msg}`);
  }

  // ---- микрофон -------------------------------------------------------------------------
  const media = navigator.mediaDevices;
  const originalGetUserMedia = media.getUserMedia.bind(media);
  media.getUserMedia = async (constraints) => {
    const wantsAudio = constraints && constraints.audio;
    const wantsVideo = constraints && constraints.video;
    if (!wantsAudio || wantsVideo) return originalGetUserMedia(constraints);
    const s = ensure();
    await s.ctx.resume();
    const track = s.micDest.stream.getAudioTracks()[0].clone();
    note("getUserMedia → синтетический микрофон");
    return new MediaStream([track]);
  };

  // ---- входящий звук (заявитель) ---------------------------------------------------------
  function addRemote(track) {
    if (!track || track.kind !== "audio" || state.remote.has(track.id)) return;
    state.remote.add(track.id);
    const s = ensure();
    try {
      const source = s.ctx.createMediaStreamSource(new MediaStream([track]));
      source.connect(s.mixDest);
      note(`удалённая дорожка ${track.id} → запись`);
    } catch (error) {
      note(`удалённая дорожка не подключена: ${error}`);
    }
  }

  const OriginalPC = window.RTCPeerConnection;
  if (OriginalPC) {
    window.RTCPeerConnection = new Proxy(OriginalPC, {
      construct(target, args) {
        const pc = new target(...args);
        pc.addEventListener("track", (event) => addRemote(event.track));
        return pc;
      },
    });
  }
  // Daily проигрывает удалённый звук через <audio srcObject=…>: ловим и этот путь.
  const srcObject = Object.getOwnPropertyDescriptor(HTMLMediaElement.prototype, "srcObject");
  if (srcObject && srcObject.set) {
    Object.defineProperty(HTMLMediaElement.prototype, "srcObject", {
      get: srcObject.get,
      set(stream) {
        if (stream && typeof stream.getAudioTracks === "function") {
          stream.getAudioTracks().forEach(addRemote);
        }
        return srcObject.set.call(this, stream);
      },
      configurable: true,
    });
  }

  // ---- API для теста --------------------------------------------------------------------
  function base64ToBuffer(b64) {
    const bin = atob(b64);
    const bytes = new Uint8Array(bin.length);
    for (let i = 0; i < bin.length; i += 1) bytes[i] = bin.charCodeAt(i);
    return bytes.buffer;
  }

  window.__voice = {
    /** Проигрывает WAV (base64) в микрофон и в запись; возвращает длительность, с. */
    async say(b64, label) {
      const s = ensure();
      await s.ctx.resume();
      const buffer = await s.ctx.decodeAudioData(base64ToBuffer(b64));
      const source = s.ctx.createBufferSource();
      source.buffer = buffer;
      const gain = s.ctx.createGain();
      gain.gain.value = 1.0;
      source.connect(gain);
      gain.connect(s.micDest);
      gain.connect(s.mixDest);
      state.playing += 1;
      source.onended = () => {
        state.playing -= 1;
      };
      source.start();
      note(`диспетчер: ${label || ""} (${buffer.duration.toFixed(1)} с)`);
      return buffer.duration;
    },
    async startRecording() {
      const s = ensure();
      await s.ctx.resume();
      if (state.recorder) return;
      const recorder = new MediaRecorder(s.mixDest.stream, { mimeType: "audio/webm;codecs=opus" });
      recorder.ondataavailable = async (event) => {
        if (!event.data || event.data.size === 0 || !window.__pushAudioChunk) return;
        const bytes = new Uint8Array(await event.data.arrayBuffer());
        let bin = "";
        for (let i = 0; i < bytes.length; i += 1) bin += String.fromCharCode(bytes[i]);
        await window.__pushAudioChunk(btoa(bin));
      };
      recorder.start(1000);
      state.recorder = recorder;
      state.started = Date.now();
      note("запись начата");
      return state.started;
    },
    async stopRecording() {
      const recorder = state.recorder;
      if (!recorder) return;
      state.recorder = null;
      await new Promise((resolve) => {
        recorder.onstop = resolve;
        recorder.stop();
      });
      note("запись остановлена");
    },
    status() {
      return {
        remoteTracks: state.remote.size,
        playing: state.playing,
        recording: Boolean(state.recorder),
        contextState: state.ctx ? state.ctx.state : "none",
        log: state.log.slice(-20),
      };
    },
  };
})();
