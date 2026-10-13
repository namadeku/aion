// One «Ход работы» for the whole window: it listens from the start of the app, so a run that
// began on one page (a command test) is still visible after switching to another.

import { store } from "../store";
import { bindWorkLog, WorkLog } from "./worklog";

let shared: WorkLog | null = null;

export function sharedWorkLog(): WorkLog {
  if (!shared) {
    shared = new WorkLog();
    bindWorkLog(shared, store);
  }
  return shared;
}
