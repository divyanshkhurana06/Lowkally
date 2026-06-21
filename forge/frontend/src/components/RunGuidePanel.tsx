import type { RunGuide } from "@/lib/api";

export function RunGuidePanel({ guide }: { guide: RunGuide }) {
  return (
    <div className="run-guide">
      <p className="run-guide-lead">{guide.headline}</p>

      {guide.showing.length > 0 && (
        <div className="run-guide-block">
          <h4>What this preview shows</h4>
          <ul>
            {guide.showing.map((line) => (
              <li key={line}>{line}</li>
            ))}
          </ul>
        </div>
      )}

      {guide.missing.length > 0 && (
        <div className="run-guide-block">
          <h4>What is missing in cloud preview</h4>
          <ul>
            {guide.missing.map((line) => (
              <li key={line}>{line}</li>
            ))}
          </ul>
        </div>
      )}

      <div className="run-guide-block">
        <h4>Run the full app on your computer</h4>
        {guide.local_steps.map((step) => (
          <div key={step.title} className="run-guide-step">
            <p className="run-guide-step-title">{step.title}</p>
            {step.note && <p className="hint">{step.note}</p>}
            {step.commands.map((cmd) => (
              <pre key={cmd} className="run-guide-cmd">
                {cmd}
              </pre>
            ))}
          </div>
        ))}
      </div>
    </div>
  );
}
