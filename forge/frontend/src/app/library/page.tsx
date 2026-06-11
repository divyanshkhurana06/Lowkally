"use client";

import Link from "next/link";
import { useCallback, useEffect, useState } from "react";
import {
  deleteSaved,
  getSavedSites,
  normalizeInsightLabels,
  toggleFavorite,
  type SavedSite,
} from "@/lib/api";

export default function LibraryPage() {
  const [sites, setSites] = useState<SavedSite[]>([]);
  const [favOnly, setFavOnly] = useState(false);
  const [error, setError] = useState("");

  const load = useCallback(async () => {
    try {
      const res = await getSavedSites(favOnly);
      setSites(res.sites);
      setError("");
    } catch (e) {
      const msg = e instanceof Error ? e.message : "Failed to load";
      setError(
        msg.includes("404")
          ? "Agent API outdated — run: bash scripts/start.sh"
          : msg,
      );
    }
  }, [favOnly]);

  useEffect(() => {
    load();
  }, [load]);

  const onFav = async (id: string) => {
    await toggleFavorite(id);
    load();
  };

  const onDelete = async (id: string) => {
    await deleteSaved(id);
    load();
  };

  const openSite = (site: SavedSite) => {
    if (site.success_url?.startsWith("http")) {
      window.open(site.success_url, "_blank");
      return;
    }
    window.location.href = `/?repo=${encodeURIComponent(site.repo_url)}`;
  };

  return (
    <div className="page-wrap">
      <div className="page-head">
        <h1>Your library</h1>
        <p className="hint">Saved sites are private to your account — one click to open or re-run.</p>
      </div>

      <div className="library-toolbar">
        <button
          type="button"
          className={`btn ${favOnly ? "btn-solid" : "btn-ghost"}`}
          onClick={() => setFavOnly(false)}
        >
          All
        </button>
        <button
          type="button"
          className={`btn ${favOnly ? "btn-solid" : "btn-ghost"}`}
          onClick={() => setFavOnly(true)}
        >
          Favorites
        </button>
        <Link href="/compare" className="btn btn-ghost">
          Split compare →
        </Link>
      </div>

      {error && <div className="banner-err">{error}</div>}

      <ul className="library-grid">
        {sites.map((site) => (
          <li key={site.id} className="library-card">
            <div className="library-card-head">
              <h3>{site.title || site.repo_url.replace(/^https?:\/\//, "")}</h3>
              <button
                type="button"
                className={`fav-btn ${site.is_favorite ? "fav-on" : ""}`}
                onClick={() => onFav(site.id)}
                title="Favorite"
              >
                ★
              </button>
            </div>
            {site.summary && <p className="insight-summary">{site.summary}</p>}
            <div className="insight-labels">
              {normalizeInsightLabels(site.labels).map((label, i) => (
                <span key={`${label}-${i}`} className={`insight-label insight-label-${i % 3}`}>
                  {label}
                </span>
              ))}
            </div>
            <p className="hint hist-url">{site.repo_url}</p>
            <div className="library-actions">
              <button type="button" className="btn btn-solid" onClick={() => openSite(site)}>
                Open
              </button>
              <Link
                href={`/compare?left=${site.id}`}
                className="btn btn-ghost"
              >
                Compare
              </Link>
              <button type="button" className="btn btn-ghost" onClick={() => onDelete(site.id)}>
                Remove
              </button>
            </div>
          </li>
        ))}
      </ul>

      {!sites.length && !error && (
        <p className="hint">No saved sites yet. Run a repo and click Save on the Run page.</p>
      )}
    </div>
  );
}
