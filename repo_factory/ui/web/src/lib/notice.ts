// The notice on the first screen: said once, as a quiet band, until it is dismissed (remembered in
// this browser). The words are the README's, exactly.
import { PRODUCT } from "./product";

export const NOTICE =
  `${PRODUCT} is an experiment. It runs code a model wrote and can publish under your GitHub account. ` +
  "It costs real money or plan usage: about $10 and an hour a build on our record. " +
  "Its own checks are not proof. Read what it builds.";

export const NOTICE_KEY = `${PRODUCT}.notice.dismissed`;

export function noticeDismissed(): boolean {
  try {
    return localStorage.getItem(NOTICE_KEY) === "1";
  } catch {
    return false;
  }
}

export function dismissNotice(): void {
  try {
    localStorage.setItem(NOTICE_KEY, "1");
  } catch {
    /* storage off: dismissed for this page only */
  }
}
