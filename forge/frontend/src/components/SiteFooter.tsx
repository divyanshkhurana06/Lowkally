const CREATOR_SITE = "https://divyanshkhurana.vercel.app";
const PROJECT_GITHUB = "https://github.com/divyanshkhurana06/Lowkally";

export function SiteFooter() {
  return (
    <footer className="site-credit" aria-label="Project and creator">
      <a
        href={PROJECT_GITHUB}
        target="_blank"
        rel="noopener noreferrer"
        className="site-credit-link"
      >
        GitHub
      </a>
      <span className="site-credit-dot" aria-hidden>
        ·
      </span>
      <a
        href={CREATOR_SITE}
        target="_blank"
        rel="noopener noreferrer"
        className="site-credit-link"
      >
        My Website
      </a>
    </footer>
  );
}
