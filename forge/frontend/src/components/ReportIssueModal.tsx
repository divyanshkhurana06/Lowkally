"use client";

import { useEffect, useState } from "react";
import { reportIssue, type ReportIssueResult } from "@/lib/api";
import type { ReportSeed } from "@/lib/reportContext";

export function ReportIssueModal({
  open,
  onClose,
  issuesUrl,
  seed,
}: {
  open: boolean;
  onClose: () => void;
  issuesUrl?: string;
  seed?: ReportSeed | null;
}) {
  const [subject, setSubject] = useState("");
  const [body, setBody] = useState("");
  const [contact, setContact] = useState("");
  const [repoUrl, setRepoUrl] = useState("");
  const [sending, setSending] = useState(false);
  const [result, setResult] = useState<ReportIssueResult | null>(null);
  const [error, setError] = useState("");

  useEffect(() => {
    if (!open) return;
    setResult(null);
    setError("");
    setSubject(seed?.subject || "");
    setBody(seed?.body || "");
    setContact(seed?.contact || "");
    setRepoUrl(seed?.repo_url || "");
  }, [open, seed]);

  if (!open) return null;

  const onSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    setError("");
    setSending(true);
    try {
      const res = await reportIssue({
        subject: subject.trim(),
        body: body.trim(),
        contact: contact.trim() || undefined,
        repo_url: repoUrl.trim() || undefined,
      });
      setResult(res);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Could not send report. Try again.");
    } finally {
      setSending(false);
    }
  };

  const close = () => {
    setResult(null);
    setSubject("");
    setBody("");
    setContact("");
    setRepoUrl("");
    setError("");
    onClose();
  };

  const issueLink = result?.github?.url || result?.issues_url || issuesUrl;

  return (
    <div className="modal-backdrop" onClick={close}>
      <div className="modal-card" onClick={(e) => e.stopPropagation()}>
        <h2>Report an issue</h2>
        {result ? (
          <>
            <p className="hint">
              {result.delivered
                ? "Thanks — your report was filed on GitHub and the maintainer will see it."
                : "Thanks — your report was saved. GitHub delivery is not configured on this server yet."}
            </p>
            {issueLink && (
              <p className="mt-2">
                <a href={issueLink} target="_blank" rel="noopener noreferrer">
                  {result.delivered ? "View GitHub issue" : "Open GitHub Issues"}
                </a>
              </p>
            )}
            {!result.delivered && result.github?.error && (
              <p className="hint mt-2">Delivery note: {result.github.error}</p>
            )}
            <button type="button" className="btn btn-solid mt-3" onClick={close}>
              Close
            </button>
          </>
        ) : (
          <form onSubmit={onSubmit}>
            <p className="hint modal-lead">
              Describe what went wrong. Reports go to the Lowkally maintainer on GitHub when configured.
            </p>
            <label className="env-field">
              <span>Subject</span>
              <input
                value={subject}
                onChange={(e) => setSubject(e.target.value)}
                placeholder="e.g. Bootstrap failed on my repo"
                required
                minLength={3}
              />
            </label>
            <label className="env-field mt-2">
              <span>Details</span>
              <textarea
                rows={5}
                value={body}
                onChange={(e) => setBody(e.target.value)}
                placeholder="What happened? Include repo URL, error message, or steps to reproduce."
                required
                minLength={5}
              />
            </label>
            <label className="env-field mt-2">
              <span>Repo URL (optional)</span>
              <input
                value={repoUrl}
                onChange={(e) => setRepoUrl(e.target.value)}
                placeholder="https://github.com/you/project"
              />
            </label>
            <label className="env-field mt-2">
              <span>Contact (optional)</span>
              <input
                value={contact}
                onChange={(e) => setContact(e.target.value)}
                placeholder="email or @github — so we can follow up"
              />
            </label>
            {error && <p className="banner-err mt-2">{error}</p>}
            <div className="modal-actions">
              <button type="button" className="btn btn-ghost" onClick={close} disabled={sending}>
                Cancel
              </button>
              <button type="submit" className="btn btn-solid" disabled={sending}>
                {sending ? "Sending…" : "Submit"}
              </button>
            </div>
          </form>
        )}
      </div>
    </div>
  );
}
