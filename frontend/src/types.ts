export type State = "idle" | "listening" | "thinking" | "speaking";
export type Emotion = "neutral" | "joy" | "thinking" | "surprise" | "sad" | "angry";

export interface HistoryEntry {
  role: "user" | "assistant";
  text: string;
  source: string;
  ts: number;
}

export interface AvatarConfig {
  kind: "hud" | "vrm" | "live2d";
  path: string | null;
}

export interface VoiceConfig {
  engine: string;
  voice: string;
  rate: number;
  pitch: number;
  volume: number;
  effect: string;
  reference_wav: string | null;
}

export interface Profile {
  gender: "male" | "female";
  name: string;
  aliases: string[];
  voice: VoiceConfig;
  avatar: AvatarConfig;
  persona: string;
}

export interface Config {
  assistant: {
    profile: string;
    user_address: string;
    language: string;
    follow_up_seconds: number;
  };
  profiles: Record<string, Profile>;
  audio: {
    input_device: string | number | null;
    output_device: string | number | null;
    vad: { backend: string; threshold: number; min_silence_ms: number; max_utterance_s: number };
    barge_in: string;
  };
  wakeword: {
    backend: string;
    sensitivity: number;
    prefix_seconds: number;
    openwakeword_model: string;
  };
  stt: { backend: string; whisper_model: string; device: string; vosk_model: string };
  updates: { auto_check: boolean };
  llm: {
    provider: string;
    temperature: number;
    max_tokens: number;
    history_turns: number;
    tools: boolean;
    ollama: { base_url: string; model: string };
    anthropic: { model: string; api_key_env: string; effort: string };
    openai: { base_url: string; model: string; api_key_env: string };
  };
  initiative: {
    enabled: boolean;
    talkativeness: number;
    use_llm: boolean;
    llm_timeout: number;
  };
  plugins: { dirs: string[]; disabled: string[]; fuzzy_threshold: number; hot_reload: boolean };
  ui: {
    push_to_talk: string | null;
    autostart: boolean;
    tray: boolean;
    window: boolean;
    desktop_scale: number;
  };
}

export interface SettingSpec {
  type: "string" | "text" | "int" | "float" | "bool" | "enum" | "secret";
  title: string;
  description: string;
  default: unknown;
  options: string[] | null;
  min: number | null;
  max: number | null;
}

export interface PluginInfo {
  name: string;
  title: string;
  version: string;
  author: string;
  description: string;
  permissions: string[];
  dangerous_permissions: string[];
  settings_schema: Record<string, SettingSpec>;
  settings?: Record<string, unknown>;
  builtin: boolean;
  path: string;
  status: "loaded" | "disabled" | "error" | "unloaded";
  error: string | null;
  commands: { name: string; patterns: string[]; dangerous: boolean }[];
  tools: { name: string; description: string }[];
}

export interface VoiceInfo {
  id: string;
  name: string;
  language: string;
  gender: string;
  installed: boolean;
}

export interface Device {
  index: number;
  name: string;
  hostapi: string;
  inputs: number;
  outputs: number;
  is_default_input: boolean;
  is_default_output: boolean;
}

export interface LogItem {
  ts: number;
  level: string;
  module: string;
  message: string;
}

/** The character's mood, 0..1 each (see core/mood.py). */
export interface Mood {
  energy: number;
  affection: number;
  boredom: number;
}

export interface Snapshot {
  type: "snapshot";
  state: State;
  name: string;
  profile: string;
  avatar: AvatarConfig;
  voice_enabled: boolean;
  muted: boolean;
  llm: string | null;
  history: HistoryEntry[];
  /** Desktop mascot mode is on (only in the desktop app). */
  desktop: boolean;
  desktop_available: boolean;
  mood?: Mood;
  /** "dev" (private repo) or "public" (installer build without the log viewer). */
  edition?: Edition;
  version?: string;
  /** A newer release found by the background check. */
  update?: string | null;
}

export type Edition = "dev" | "public";

/** A background download (update installer, CUDA libraries). */
export interface Job {
  state: "running" | "done" | "error";
  label: string;
  done: number;
  total: number;
  error?: string;
}

export interface UpdateStatus {
  edition: Edition;
  version: string;
  supported: boolean;
  latest: { version: string; notes: string; url: string; size: number } | null;
  checked_at: number | null;
  error: string | null;
  job: Job | null;
}

export interface CudaStatus {
  gpu: boolean;
  available: boolean;
  bundled: boolean;
  size: number;
  job: Job | null;
}

/** Any event from the backend bus: {type: "wake_detected", ...fields}. */
export interface BusEvent {
  type: string;
  [key: string]: unknown;
}
