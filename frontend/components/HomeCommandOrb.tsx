"use client";

import { FormEvent, useEffect, useRef, useState } from "react";
import { useRouter } from "next/navigation";

const EXAMPLE_QUESTIONS = [
  "Why did the last deploy roll back?",
  "What changed in the auth service this week?",
  "Who owns the ingestion pipeline?",
];

export default function HomeCommandOrb() {
  const router = useRouter();
  const inputRef = useRef<HTMLTextAreaElement>(null);
  const [question, setQuestion] = useState("");

  useEffect(() => {
    function focusCommand(event: KeyboardEvent) {
      if ((event.metaKey || event.ctrlKey) && event.key.toLowerCase() === "k") {
        event.preventDefault();
        inputRef.current?.focus();
      }
    }
    window.addEventListener("keydown", focusCommand);
    return () => window.removeEventListener("keydown", focusCommand);
  }, []);

  function ask(event: FormEvent) {
    event.preventDefault();
    const nextQuestion = question.trim();
    if (nextQuestion) {
      window.sessionStorage.setItem("memoryworks.pending-question", nextQuestion);
    }
    router.push("/workspace");
  }

  return (
    <div className="entry-command-wrap">
      <form className="entry-command" onSubmit={ask}>
        <span className="entry-command-orb" aria-hidden="true"><i />✦</span>
        <textarea
          ref={inputRef}
          rows={1}
          value={question}
          onChange={(event) => setQuestion(event.target.value)}
          onKeyDown={(event) => {
            if (event.key === "Enter" && !event.shiftKey) {
              event.preventDefault();
              ask(event);
            }
          }}
          aria-label="Ask MemoryWorks"
          placeholder="Ask MemoryWorks anything…"
        />
        <span className="entry-command-key">⌘K</span>
        <button type="submit" aria-label="Open MemoryWorks workspace">
          <span>{question.trim() ? "Ask" : "Open"}</span>
          <i aria-hidden="true">↗</i>
        </button>
      </form>
      <div className="entry-examples" aria-label="Example questions">
        <span>Try</span>
        {EXAMPLE_QUESTIONS.map((example) => (
          <button key={example} type="button" onClick={() => {
            setQuestion(example);
            inputRef.current?.focus();
          }}>
            {example}
          </button>
        ))}
      </div>
    </div>
  );
}
