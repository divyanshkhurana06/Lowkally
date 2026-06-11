"use client";

import Link from "next/link";
import { useSearchParams } from "next/navigation";
import { Suspense, useCallback, useEffect, useState } from "react";
import { getSavedSites, type SavedSite } from "@/lib/api";

function CompareInner() {
  const params = useSearchParams();
  const [sites, setSites] = useState<SavedSite[]>([]);
  const [leftId, setLeftId] = useState(params.get("left") || "");
  const [rightId, setRightId] = useState(params.get("right") || "");

  const load = useCallback(async () => {
    try {
      const res = await getSavedSites();
      setSites(res.sites);
    } catch (e) {
      console.error(e);
      setSites([]);
    }
  }, []);

  useEffect(() => {
    load();
  }, [load]);

  const left = sites.find((s) => s.id === leftId);
  const right = sites.find((s) => s.id === rightId);

  const frameUrl = (site: SavedSite | undefined) => {
    if (!site) return "";
    if (site.success_url?.startsWith("http")) return site.success_url;
    return "";
  };

  return (
    <div className="page-wrap compare-page">
      <div className="page-head">
        <div className="page-head-row">
          <div className="page-head-copy">
            <h1>Split compare</h1>
            <p className="hint">
              View two saved sites side by side. Localhost previews only work on the machine that ran them —
              use public URLs or re-run from Library.
            </p>
          </div>
          <Link href="/library" className="btn btn-ghost page-head-action">
            ← Library
          </Link>
        </div>
      </div>

      <div className="compare-pickers">
        <label>
          Left
          <select value={leftId} onChange={(e) => setLeftId(e.target.value)}>
            <option value="">— pick —</option>
            {sites.map((s) => (
              <option key={s.id} value={s.id}>
                {s.title || s.repo_url}
              </option>
            ))}
          </select>
        </label>
        <label>
          Right
          <select value={rightId} onChange={(e) => setRightId(e.target.value)}>
            <option value="">— pick —</option>
            {sites.map((s) => (
              <option key={s.id} value={s.id}>
                {s.title || s.repo_url}
              </option>
            ))}
          </select>
        </label>
      </div>

      <div className="compare-split">
        <div className="compare-pane">
          <div className="compare-pane-title">{left?.title || "Left"}</div>
          {frameUrl(left) ? (
            <iframe title="left" src={frameUrl(left)} className="compare-frame" />
          ) : left ? (
            <div className="compare-placeholder">
              <p>No live URL — re-run from library.</p>
              <Link href={`/?repo=${encodeURIComponent(left.repo_url)}`} className="btn btn-solid">
                Re-run
              </Link>
            </div>
          ) : (
            <div className="compare-placeholder">Select a site</div>
          )}
        </div>
        <div className="compare-pane">
          <div className="compare-pane-title">{right?.title || "Right"}</div>
          {frameUrl(right) ? (
            <iframe title="right" src={frameUrl(right)} className="compare-frame" />
          ) : right ? (
            <div className="compare-placeholder">
              <p>No live URL — re-run from library.</p>
              <Link href={`/?repo=${encodeURIComponent(right.repo_url)}`} className="btn btn-solid">
                Re-run
              </Link>
            </div>
          ) : (
            <div className="compare-placeholder">Select a site</div>
          )}
        </div>
      </div>
    </div>
  );
}

export default function ComparePage() {
  return (
    <Suspense fallback={<div className="page-wrap"><p className="hint">Loading…</p></div>}>
      <CompareInner />
    </Suspense>
  );
}
