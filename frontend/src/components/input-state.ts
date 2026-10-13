// Input bar state shared by the composer and whoever needs it later (command editor, code
// mode handling). UI-only for now: the backend receives plain text messages either way.

import { getVoiceMode, setVoiceMode, type InputMode } from "../prefs";
import { store } from "../store";

type Listener = () => void;

class InputState {
  /** `</>` toggle: the text is a command or code snippet (monospace, multi-line). */
  codeMode = false;
  private listeners = new Set<Listener>();

  /** Current mode. "text" means the microphone is muted (the only mode without voice). */
  get mode(): InputMode {
    if (!store.voiceEnabled || store.muted) return "text";
    return getVoiceMode();
  }

  /** Switch mode: "text" mutes the microphone, voice modes unmute it. */
  setMode(mode: InputMode): void {
    if (mode === "text") {
      if (!store.muted) store.send({ type: "mute", value: true });
    } else {
      setVoiceMode(mode);
      if (store.muted) store.send({ type: "mute", value: false });
    }
    this.notify();
  }

  setCodeMode(value: boolean): void {
    this.codeMode = value;
    this.notify();
  }

  subscribe(fn: Listener): () => void {
    this.listeners.add(fn);
    return () => this.listeners.delete(fn);
  }

  private notify(): void {
    for (const fn of this.listeners) fn();
  }
}

export const inputState = new InputState();
