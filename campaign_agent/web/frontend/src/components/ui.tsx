import type { ReactNode } from "react";

export function Pill({ tone = "neutral", children }: { tone?: "good" | "bad" | "warn" | "accent" | "neutral"; children: ReactNode }) {
  return <span className={`pill pill-${tone}`}>{children}</span>;
}

export function Empty({ children }: { children: ReactNode }) {
  return <div className="empty">{children}</div>;
}

export function Section({ title, aside, children }: { title: string; aside?: ReactNode; children: ReactNode }) {
  return (
    <section className="section">
      <header className="section-head">
        <h3>{title}</h3>
        {aside}
      </header>
      {children}
    </section>
  );
}

export function Metrics({ items }: { items: [string, string][] }) {
  return (
    <dl className="metrics">
      {items.map(([k, v]) => (
        <div key={k} className="metric">
          <dt>{k}</dt>
          <dd>{v}</dd>
        </div>
      ))}
    </dl>
  );
}

/** Safe, minimal markdown: **bold** and "- " bullets. Text is never injected as HTML. */
export function Markdown({ text }: { text: string }) {
  const blocks: ReactNode[] = [];
  let items: ReactNode[] = [];
  const inline = (s: string) =>
    s.split(/(\*\*[^*]+\*\*)/).map((part, i) =>
      /^\*\*[^*]+\*\*$/.test(part) ? <strong key={i}>{part.slice(2, -2)}</strong> : part);
  const flush = () => {
    if (items.length) blocks.push(<ul key={`ul${blocks.length}`}>{items}</ul>);
    items = [];
  };
  text.split("\n").forEach((line, i) => {
    const m = /^\s*-\s+(.*)$/.exec(line);
    if (m) items.push(<li key={i}>{inline(m[1])}</li>);
    else {
      flush();
      if (line.trim()) blocks.push(<p key={i}>{inline(line)}</p>);
    }
  });
  flush();
  return <div className="md">{blocks}</div>;
}

const PALETTE = ["#0b6e69", "#7a4fb3", "#b5531f", "#2f6db5", "#a1336a", "#4f7a28", "#8a6a14", "#376b80"];
export function Avatar({ name }: { name: string }) {
  let h = 0;
  for (const c of name) h = (h * 31 + c.charCodeAt(0)) >>> 0;
  const initials = name.split(/[\s/]+/).filter(Boolean).slice(0, 2).map((w) => w[0]).join("").toUpperCase();
  return <span className="avatar" style={{ background: PALETTE[h % PALETTE.length] }} aria-hidden>{initials}</span>;
}
