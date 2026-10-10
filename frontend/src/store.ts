// Live connection to the assistant: WebSocket events + a tiny reactive store.

import { api, token } from "./api";
import type { BusEvent, Edition, Emotion, HistoryEntry, Mood, Snapshot, State } from "./types";

type Listener = () => void;

class Store {
  connected = false;
  /** The first snapshot has arrived (name, avatar, history are known). */
  loaded = false;
  state: State = "idle";
  name = "Aion";
  emotion: Emotion = "neutral";
  mood: Mood = { energy: 0.6, affection: 0.5, boredom: 0 };
  voiceEnabled = false;
  muted = false;
  llm: string | null = null;
  avatar: Snapshot["avatar"] = { kind: "hud", path: null };
  history: HistoryEntry[] = [];
  /** The character is out on the Windows desktop (the main window is hidden). */
  desktop = false;
  desktopAvailable = false;
  /** Unknown until the first snapshot: dev-only pages stay hidden meanwhile. */
  edition: Edition | null = null;
  version = "";
  /** Version of an available update, if any. */
  update: string | null = null;
  /** Smoothed audio levels 0..1, updated ~30 times a second. */
  outputLevel = 0;
  inputLevel = 0;

  private listeners = new Set<Listener>();
  private eventListeners = new Map<string, Set<(e: BusEvent) => void>>();
  private socket: WebSocket | null = null;
  private retry = 0;
  private openReply: HistoryEntry | null = null;

  subscribe(fn: Listener): () => void {
    this.listeners.add(fn);
    return () => this.listeners.delete(fn);
  }

  /** Listen to raw backend events of one type ("*" for all). */
  on(type: string, fn: (e: BusEvent) => void): () => void {
    const set = this.eventListeners.get(type) ?? new Set();
    set.add(fn);
    this.eventListeners.set(type, set);
    return () => set.delete(fn);
  }

  private notify(): void {
    for (const fn of this.listeners) fn();
  }

  connect(): void {
    const proto = location.protocol === "https:" ? "wss" : "ws";
    const ws = new WebSocket(`${proto}://${location.host}/ws?token=${encodeURIComponent(token)}`);
    this.socket = ws;
    ws.onopen = () => {
      this.connected = true;
      this.retry = 0;
      this.notify();
    };
    ws.onclose = () => {
      this.connected = false;
      this.notify();
      const delay = Math.min(10_000, 500 * 2 ** this.retry++);
      setTimeout(() => this.connect(), delay);
    };
    ws.onmessage = (msg) => {
      try {
        this.handle(JSON.parse(msg.data) as BusEvent);
      } catch (err) {
        console.warn("bad message", err);
      }
    };
  }

  send(message: Record<string, unknown>): void {
    if (this.socket?.readyState === WebSocket.OPEN) this.socket.send(JSON.stringify(message));
  }

  /** Send binary data unless more than `maxBuffered` bytes are still queued (drops the frame). */
  sendBinary(data: ArrayBuffer, maxBuffered: number): void {
    const ws = this.socket;
    if (ws?.readyState === WebSocket.OPEN && ws.bufferedAmount <= maxBuffered) ws.send(data);
  }

  private handle(e: BusEvent): void {
    switch (e.type) {
      case "snapshot": {
        const s = e as unknown as Snapshot;
        Object.assign(this, {
          state: s.state,
          name: s.name,
          voiceEnabled: s.voice_enabled,
          muted: s.muted,
          llm: s.llm,
          avatar: s.avatar,
          history: s.history,
          desktop: Boolean(s.desktop),
          desktopAvailable: Boolean(s.desktop_available),
          edition: s.edition ?? "dev",
          version: s.version ?? "",
          update: s.update ?? null,
        });
        if (s.mood) this.mood = s.mood;
        this.openReply = null;
        this.loaded = true;
        break;
      }
      case "state_changed":
        this.state = e.new as State;
        break;
      case "audio_level":
        if (e.channel === "output") this.outputLevel = e.level as number;
        else this.inputLevel = e.level as number;
        this.emit(e);
        return; // high-frequency: no full re-render
      case "speech_recognized":
        this.openReply = null;
        this.history.push({
          role: "user",
          text: e.text as string,
          source: e.source as string,
          ts: e.ts as number,
        });
        break;
      case "assistant_reply": {
        const text = e.text as string;
        if (text) {
          if (this.openReply) this.openReply.text = `${this.openReply.text} ${text}`.trim();
          else {
            this.openReply = { role: "assistant", text, source: "", ts: e.ts as number };
            this.history.push(this.openReply);
          }
        }
        if (e.final) this.openReply = null;
        break;
      }
      case "emotion_changed":
        this.emotion = e.emotion as Emotion;
        break;
      case "mood_changed":
        this.mood = {
          energy: e.energy as number,
          affection: e.affection as number,
          boredom: e.boredom as number,
        };
        break;
      case "muted":
        this.muted = Boolean(e.value);
        break;
      case "desktop_mode":
        this.desktop = Boolean(e.value);
        break;
      case "update_available":
        this.update = String(e.version);
        break;
      case "config_changed":
        // name/avatar may have changed: refresh the snapshot-like fields lazily
        this.emit(e);
        void this.refreshProfile();
        return;
    }
    if (this.history.length > 300) this.history.splice(0, this.history.length - 300);
    this.emit(e);
    this.notify();
  }

  private emit(e: BusEvent): void {
    for (const key of [e.type, "*"]) {
      for (const fn of this.eventListeners.get(key) ?? []) fn(e);
    }
  }

  async refreshProfile(): Promise<void> {
    try {
      const cfg = await api.get<{
        assistant: { profile: string };
        profiles: Record<string, { name: string; avatar: Snapshot["avatar"] }>;
      }>("/api/config");
      const profile = cfg.profiles[cfg.assistant.profile];
      if (profile) {
        this.name = profile.name;
        this.avatar = profile.avatar;
        this.notify();
      }
    } catch {
      /* offline */
    }
  }
}

export const store = new Store();
