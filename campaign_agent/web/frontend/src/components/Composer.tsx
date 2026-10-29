import { forwardRef, useImperativeHandle, useRef, useState } from "react";
import type { RunView } from "../api";

const SUGGEST: Record<string, string[]> = {
  understand: ["yes", "the coffee one", "objective grow", "window 4 weeks from 2026-11-30"],
  plan: ["Plan looks right, go", "add sentiment_scan", "why is kantar_share in the plan?"],
  diagnose: ["go", "What is SPC for light shoppers only?", "What is the lapsed pool?", "Which PSGs are excluded?"],
  discuss: ["approve", "Drop the heavy cohort", "include medium", "keep the brand", "Try the competitor brand instead"],
  brief: ["approve", "note: check supplier funding before draft", "What is the dissent?"],
};

export interface ComposerHandle { prefill: (t: string) => void }

export const Composer = forwardRef<ComposerHandle, { view: RunView | null; busy: boolean; onSend: (t: string) => void }>(
  function Composer({ view, busy, onSend }, ref) {
    const [text, setText] = useState("");
    const box = useRef<HTMLTextAreaElement>(null);
    useImperativeHandle(ref, () => ({ prefill: (t) => { setText(t); requestAnimationFrame(() => box.current?.focus()); } }));

    const stage = view && !view.done ? view.pending?.stage : undefined;
    const placeholder = !view || view.done ? "Describe the campaign — brand, objective, window…"
      : view.pending?.awaiting === "clarification" ? "Answer the question above…"
        : "Approve, ask, edit, send back or pause…";
    const submit = () => {
      if (!text.trim() || busy) return;
      onSend(text);
      setText("");
    };
    return (
      <div className="composer">
        {stage && SUGGEST[stage] && (
          <div className="suggestions" aria-label="Suggested replies">
            {SUGGEST[stage].map((s) => (
              <button key={s} className="chip" disabled={busy} onClick={() => onSend(s)}>{s}</button>
            ))}
          </div>
        )}
        <form className="composer-row" onSubmit={(e) => { e.preventDefault(); submit(); }}>
          <textarea
            ref={box}
            rows={1}
            value={text}
            disabled={busy}
            placeholder={busy ? "Agents are working…" : placeholder}
            onChange={(e) => setText(e.target.value)}
            onKeyDown={(e) => { if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); submit(); } }}
            aria-label="Message"
          />
          <button type="submit" className="btn btn-primary" disabled={busy || !text.trim()}>Send</button>
        </form>
      </div>
    );
  });
