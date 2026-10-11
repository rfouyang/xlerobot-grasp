// The task pages' voice. Recording: 16 kHz mono PCM -> BytePlus ASR -> sent as the user's turn (it shows in the chat,
// so a misheard word is seen and said again). 语音讲解: with the switch on, the page says what the robot does (one
// sentence a step, as long as the step lasts) and what it answers in the chat; BytePlus TTS through /api/voice/say.
// Only the page last used speaks (the server keeps which), so a demo has one voice.
"use strict";

// --- recording ---

const voice = { stream: null, context: null, processor: null, chunks: [], started: 0, timer: null, busy: false };
const RECORD_IDLE = "● 语音";
const RECORD_ON = "border-red-600 bg-red-600 font-semibold text-white";
// The record button's own look while it is not recording (taken off while it is, so the red one shows).
const RECORD_OFF = "border-gray-200 bg-white text-gray-800 hover:bg-gray-50";

async function startRecording() {
  if (!navigator.mediaDevices || !navigator.mediaDevices.getUserMedia) {
    chatAdd("robot", "这个浏览器不给麦克风：要用 https 地址打开（iPad 也是），或者打字告诉我。");
    return;
  }
  audioSession("play-and-record");
  try {
    voice.stream = await navigator.mediaDevices.getUserMedia({ audio: { channelCount: 1, echoCancellation: true, noiseSuppression: true } });
  } catch (error) {
    audioSession("playback");
    // The browser's own words, with its name for the error, so the cause can be told (permission, in use, session).
    chatAdd("robot", `用不了麦克风（${error.name}：${error.message}）。检查「设置 → Safari → 麦克风」是否允许这个网站，或者打字告诉我。`);
    return;
  }
  voice.context = new AudioContext({ sampleRate: 16000 });
  const source = voice.context.createMediaStreamSource(voice.stream);
  voice.processor = voice.context.createScriptProcessor(4096, 1, 1);
  voice.chunks = [];
  voice.processor.onaudioprocess = (event) => voice.chunks.push(new Float32Array(event.inputBuffer.getChannelData(0)));
  source.connect(voice.processor);
  voice.processor.connect(voice.context.destination);
  voice.started = Date.now();
  const button = byId("btn-record");
  button.classList.remove(...RECORD_OFF.split(" "));
  button.classList.add(...RECORD_ON.split(" "));
  button.textContent = "■ 停止 00:00";
  voice.timer = setInterval(() => {
    const seconds = Math.floor((Date.now() - voice.started) / 1000);
    button.textContent = `■ 停止 ${String(Math.floor(seconds / 60)).padStart(2, "0")}:${String(seconds % 60).padStart(2, "0")}`;
    if (seconds >= 30) stopRecording();
  }, 250);
}

// Float32 at the context's rate -> int16 at 16 kHz (most browsers give 16 kHz as asked; others are resampled here).
function pcm16(chunks, rate) {
  const length = chunks.reduce((sum, chunk) => sum + chunk.length, 0);
  const all = new Float32Array(length);
  let offset = 0;
  for (const chunk of chunks) { all.set(chunk, offset); offset += chunk.length; }
  const ratio = rate / 16000;
  const out = new Int16Array(Math.floor(length / ratio));
  for (let i = 0; i < out.length; i++) {
    const sample = Math.max(-1, Math.min(1, all[Math.floor(i * ratio)]));
    out[i] = sample < 0 ? sample * 0x8000 : sample * 0x7fff;
  }
  return out;
}

async function stopRecording() {
  clearInterval(voice.timer);
  const button = byId("btn-record");
  button.classList.remove(...RECORD_ON.split(" "));
  button.classList.add(...RECORD_OFF.split(" "));
  button.textContent = RECORD_IDLE;
  if (!voice.context) return;
  voice.processor.disconnect();
  voice.stream.getTracks().forEach((track) => track.stop());
  audioSession("playback");
  const rate = voice.context.sampleRate;
  await voice.context.close();
  voice.context = null;
  const pcm = pcm16(voice.chunks, rate);
  voice.busy = true;
  button.disabled = true;
  const listening = chatAdd("robot", "我在听写…", { pending: true });
  try {
    const result = await api("POST", "/api/voice/transcribe", new Blob([pcm.buffer]));
    chat.messages.splice(chat.messages.indexOf(listening), 1);
    sendChat(result.text, "🎤 ");
  } catch (error) {
    chatResolve(listening, `${error.message}，再说一次，或者打字告诉我。`);
  } finally {
    voice.busy = false;
    button.disabled = false;
  }
}

function toggleRecording() {
  if (voice.busy) return;
  if (voice.context) stopRecording();
  else startRecording();
}

// --- 语音讲解 ---

// What each step says as it starts, about as long as the step lasts on the robot (10-10, 23:33: 准备 1 s, 规划 ~16 s,
// 到瓶子后方 4 s, 对准 2 s, 张开 3 s, 伸到瓶身 2.5 s, 闭合 3.5 s, 抬起 3.4 s, 送到上方 4.3 s, 放下 2.4 s, 松开 4.6 s,
// 退出 5.6 s, 回位 8 s with the look whether it stands): a sentence longer than its step put the voice a step or two
// behind the robot (10-11). Chinese TTS says about 4 characters a second. {arm}: 左手 / 右手.
const STAGE_SAY = {
  prepare: "开始。",
  plan: PAGE.place
    ? "我先用头上的相机看清瓶子和放的地方，再算右手怎么抓、怎么放过去。要十几秒，请稍等。"
    : "我先用头上的相机看清瓶子在哪里，再算出用哪只手、从哪个方向抓。要十几秒，请稍等。",
  approach: "规划好了，{arm}过去。", align: "对准。", open: "张开。", grasp: "伸过去。", close: "抓住。", lift: "抬起来。",
  place: "放回去。", release: "松开。", retreat: "退出来。", return: "回到起始位置，再看看瓶子立着没有。", shift: "往右移。",
  carry: "送过去。", lower: "放下。", back_out: "退出来。", home: "回到起始位置，再看看瓶子立着没有。",
};
// The names YOLOE gives, said in Chinese; others as they are.
const SAY_NAMES = { bottle: "瓶子", "water bottle": "水瓶", "spray bottle": "喷雾瓶", cup: "杯子", mug: "杯子", can: "罐子", box: "盒子", banana: "香蕉", apple: "苹果", orange: "橙子" };
const ARM_SAY = { left: "左手", right: "右手" };
const sayName = (label) => SAY_NAMES[String(label || "").toLowerCase()] || label || "东西";
const stageSay = (name, arm) => (STAGE_SAY[name] || "").replace("{arm}", ARM_SAY[arm] || "手");

// prefetched: the task whose steps' sentences are synthesised; announced: the step last said.
const narrator = { on: false, queue: [], playing: false, last: null, announced: null, prefetched: null };
// The steps' sentences, synthesised once (BytePlus took ~1 s a sentence, as long as some steps): played at once later.
const STEP_LINES = new Set();
const spoken = new Map();

// One audio context for all of the robot's speech. Safari (iPad) plays nothing that a tap did not start, so the first
// tap on the page unlocks it; later sentences, which arrive on their own, play through it. mine: this page is the one
// last used, so it is the one that speaks; each tap, click or key on it makes it the one.
const speaker = { context: null, id: Math.random().toString(36).slice(2), mine: false, claimedAt: 0 };

// A sentence to say: with the switch on (always: also off, the robot's reply to a spoken turn); a step's sentence drops
// what was not said yet of the steps before, so the voice says the step the robot is at.
function say(text, { always = false, step = false } = {}) {
  if (!text || (!narrator.on && !always) || !speaker.mine) return;
  if (step) narrator.queue = narrator.queue.filter((item) => !STEP_LINES.has(item));
  narrator.queue.push(text);
  // Behind the robot by more than a few sentences: the old ones are dropped, the narration keeps up with it.
  while (narrator.queue.length > 3) narrator.queue.shift();
  playNext();
}

async function speech(text) {
  if (spoken.has(text)) return spoken.get(text);
  const response = await fetch("/api/voice/say", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ text }) }).catch(() => null);
  const audio = response && response.ok ? await response.arrayBuffer() : null;
  if (audio && STEP_LINES.has(text)) spoken.set(text, audio);
  return audio;
}

// Every step's sentence of this task (both arms' wording: which one plans is known only after planning), synthesised
// ahead one at a time; again when the task changes (the page opened on 物体抓取's steps, then started 抓取摆放).
async function prefetchSpeech(status) {
  if (narrator.prefetched === status.action) return;
  narrator.prefetched = status.action;
  const lines = [...new Set(status.stages.flatMap((stage) => ["left", "right"].map((arm) => stageSay(stage.name, arm))).filter(Boolean))];
  lines.forEach((line) => STEP_LINES.add(line));
  for (const line of lines) await speech(line);
}

async function playNext() {
  if (narrator.playing || !narrator.queue.length) return;
  narrator.playing = true;
  const text = narrator.queue.shift();
  try {
    const audio = await speech(text);
    if (audio) {
      if (!speaker.context) unlockSpeaker();
      // decodeAudioData takes the buffer over: a copy, the cached one stays.
      const sound = await speaker.context.decodeAudioData(audio.slice(0));
      await new Promise((done) => {
        const source = speaker.context.createBufferSource();
        source.buffer = sound;
        source.connect(speaker.context.destination);
        source.onended = done;
        source.start(0);
        // A context still locked never ends the sound: go on after its length.
        setTimeout(done, sound.duration * 1000 + 500);
      });
    }
  } catch { /* the voice service is down: the page goes on silently */ }
  narrator.playing = false;
  playNext();
}

// From each status: a step that starts, the end, a failure. Only this page's task, and only what changes (opening the
// page in the middle of a run says nothing about what already happened).
function narrate(status) {
  const now = { action: status.action, next: status.next, running: busy(status), failed: status.failed };
  const before = narrator.last;
  narrator.last = now;
  if (!before || !ours(status) || before.action !== now.action) return;
  const stages = status.stages;
  if (now.next < before.next) narrator.announced = null;
  if (narrator.on) prefetchSpeech(status);
  // The step it is at (one the poll missed is not said afterwards).
  if (now.running && now.next < stages.length && narrator.announced !== now.next) {
    narrator.announced = now.next;
    const stage = stages[now.next];
    const line = stageSay(stage.name, status.arm) || stage.label;
    STEP_LINES.add(line);
    say(line, { step: true });
  }
  if (now.failed && !before.failed) say(`这一步没有成功：${failureReason(status) || stages[now.next].label}。机器人停在原地。`);
  if (before.next < stages.length && now.next >= stages.length && !now.failed) say(PAGE.place ? "摆放完成。" : "抓取完成。");
}

function setNarration(on) {
  narrator.on = on;
  remember("xlerobot.narrate", on);
  if (!on) narrator.queue.length = 0;
  // Said at once: switching it on is the click the browser needs before it plays sound, and it tells the speaker works.
  else say("语音讲解已打开。");
}

// --- which page speaks ---

function claimSpeaker() {
  speaker.mine = true;
  // At most once a second: a burst of taps is one claim.
  if (Date.now() - speaker.claimedAt < 1000) return;
  speaker.claimedAt = Date.now();
  fetch("/api/voice/speaker", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ client: speaker.id }) }).catch(() => {});
}

async function checkSpeaker() {
  const response = await fetch("/api/voice/speaker").catch(() => null);
  if (!response || !response.ok) return;
  const { client } = await response.json();
  speaker.mine = client === speaker.id;
  // Not the one any more: what it still had to say is dropped.
  if (!speaker.mine) narrator.queue.length = 0;
}

// iOS's audio session: "playback" (not silenced by the mute switch) refuses the microphone, so "play-and-record" while
// recording, "playback" again after.
function audioSession(type) {
  try { if (navigator.audioSession) navigator.audioSession.type = type; } catch { /* not iOS */ }
}

function unlockSpeaker() {
  try {
    if (!voice.context) audioSession("playback");
    if (!speaker.context) speaker.context = new (window.AudioContext || window.webkitAudioContext)();
    if (speaker.context.state !== "running") speaker.context.resume();
    // A moment of silence, played inside the tap: what makes iOS let the context play later.
    const silence = speaker.context.createBuffer(1, 1, 22050);
    const source = speaker.context.createBufferSource();
    source.buffer = silence;
    source.connect(speaker.context.destination);
    source.start(0);
  } catch { /* no Web Audio: the page goes on silently */ }
}

function initVoice() {
  ["pointerdown", "keydown"].forEach((type) => document.addEventListener(type, claimSpeaker, { capture: true }));
  ["pointerdown", "touchend", "keydown"].forEach((type) => document.addEventListener(type, unlockSpeaker, { capture: true }));
  setInterval(checkSpeaker, 1000);
  byId("btn-record").addEventListener("click", toggleRecording);
  byId("narrate").checked = remembered("xlerobot.narrate");
  narrator.on = byId("narrate").checked;
  byId("narrate").addEventListener("change", () => setNarration(byId("narrate").checked));
}
