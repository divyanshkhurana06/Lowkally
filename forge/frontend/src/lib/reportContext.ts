export type ReportSeed = {
  subject?: string;
  body?: string;
  repo_url?: string;
  contact?: string;
};

let pending: ReportSeed | null = null;

export function queueReport(seed: ReportSeed) {
  pending = seed;
  if (typeof window !== "undefined") {
    window.dispatchEvent(new CustomEvent("lowkally:report", { detail: seed }));
  }
}

export function takeReportSeed(): ReportSeed | null {
  const seed = pending;
  pending = null;
  return seed;
}
