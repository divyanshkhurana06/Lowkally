"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import { useCallback, useEffect, useState } from "react";
import {
  getAuthMe,
  getSetup,
  logout,
  type AuthUser,
  type Setup,
} from "@/lib/api";
import type { ReportSeed } from "@/lib/reportContext";
import { takeReportSeed } from "@/lib/reportContext";
import { ReportIssueModal } from "./ReportIssueModal";

export function AppHeader() {
  const path = usePathname();
  const [user, setUser] = useState<AuthUser | null>(null);
  const [setup, setSetup] = useState<Setup | null>(null);
  const [issueOpen, setIssueOpen] = useState(false);
  const [reportSeed, setReportSeed] = useState<ReportSeed | null>(null);
  const [menuOpen, setMenuOpen] = useState(false);

  const refresh = useCallback(async () => {
    try {
      const [s, auth] = await Promise.all([getSetup(), getAuthMe()]);
      setSetup(s);
      setUser(auth.user);
    } catch {
      /* offline */
    }
  }, []);

  useEffect(() => {
    refresh();
  }, [refresh]);

  useEffect(() => {
    const onReport = (e: Event) => {
      const detail = (e as CustomEvent<ReportSeed>).detail;
      setReportSeed(detail || takeReportSeed());
      setIssueOpen(true);
    };
    window.addEventListener("lowkally:report", onReport);
    return () => window.removeEventListener("lowkally:report", onReport);
  }, []);

  const onLogout = async () => {
    await logout();
    setUser(null);
    setMenuOpen(false);
    refresh();
  };

  const oauth = setup?.oauth;
  const showLogin = oauth?.google || oauth?.github || oauth?.gitlab;

  return (
    <>
      <header className="app-topbar">
        <nav className="app-nav">
          <Link href="/" className="brand-link">
            Lowkally
          </Link>
          <Link href="/" className={`nav-link ${path === "/" ? "active" : ""}`}>
            Run
          </Link>
          <Link href="/library" className={`nav-link ${path === "/library" ? "active" : ""}`}>
            Library
          </Link>
          <Link href="/compare" className={`nav-link ${path === "/compare" ? "active" : ""}`}>
            Compare
          </Link>
        </nav>
        <div className="app-topbar-actions">
          <button
            type="button"
            className="btn btn-ghost"
            onClick={() => {
              setReportSeed(takeReportSeed());
              setIssueOpen(true);
            }}
          >
            Report issue
          </button>
          {user ? (
            <div className="user-menu">
              <button
                type="button"
                className="user-btn"
                aria-label={`Account: ${user.username}`}
                onClick={() => setMenuOpen((o) => !o)}
              >
                {user.avatar_url ? (
                  <img src={user.avatar_url} alt="" className="user-avatar" />
                ) : (
                  <span className="user-avatar user-avatar-fallback">
                    {user.username.slice(0, 1).toUpperCase()}
                  </span>
                )}
              </button>
              {menuOpen && (
                <div className="user-dropdown">
                  <span className="hint">@{user.username}</span>
                  <span className="hint">{user.provider}</span>
                  <button type="button" onClick={onLogout}>
                    Log out
                  </button>
                </div>
              )}
            </div>
          ) : showLogin ? (
            <div className="login-btns">
              {oauth?.google && (
                <a className="btn btn-ghost" href="/api/auth/google/login">
                  Google
                </a>
              )}
              {oauth?.github && (
                <a className="btn btn-ghost" href="/api/auth/github/login">
                  GitHub
                </a>
              )}
              {oauth?.gitlab && (
                <a className="btn btn-solid" href="/api/auth/gitlab/login">
                  GitLab
                </a>
              )}
            </div>
          ) : (
            <span className="hint">dev mode</span>
          )}
        </div>
      </header>
      <ReportIssueModal
        open={issueOpen}
        onClose={() => {
          setIssueOpen(false);
          setReportSeed(null);
        }}
        issuesUrl={setup?.issues_url}
        seed={reportSeed}
      />
    </>
  );
}
