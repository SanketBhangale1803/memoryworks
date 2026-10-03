import { Fragment, type ReactNode } from "react";

const URL_RE = /(https?:\/\/[^\s<>()]+[^\s<>().,;:!?'"])/g;

function linked(text: string, key: number): ReactNode {
  const parts = text.split(URL_RE);
  if (parts.length === 1) return <Fragment key={key}>{text}</Fragment>;
  return (
    <Fragment key={key}>
      {parts.map((part, index) =>
        index % 2 === 1 ? (
          <a key={index} href={part} target="_blank" rel="noreferrer noopener">
            {part}
          </a>
        ) : (
          <Fragment key={index}>{part}</Fragment>
        ),
      )}
    </Fragment>
  );
}

function inlineMarkdown(value: string): ReactNode[] {
  const tokens = value.split(/(\*\*[^*]+\*\*|`[^`]+`|\*[^*]+\*)/g);
  return tokens.filter(Boolean).map((token, index) => {
    if (token.startsWith("**") && token.endsWith("**")) return <strong key={index}>{token.slice(2, -2)}</strong>;
    if (token.startsWith("`") && token.endsWith("`")) return <code key={index}>{token.slice(1, -1)}</code>;
    if (token.startsWith("*") && token.endsWith("*")) return <em key={index}>{token.slice(1, -1)}</em>;
    return linked(token, index);
  });
}

/* A diff reads by its first column: what was added, removed, or where a hunk starts. */
function diffClass(line: string) {
  if (line.startsWith("@@")) return "hunk";
  if (line.startsWith("--- ") || line.startsWith("+++ ")) return "file";
  if (line.startsWith("+")) return "add";
  if (line.startsWith("-")) return "del";
  if (line.startsWith("…")) return "more";
  return "";
}

function CodeBlock({ language, lines }: { language: string; lines: string[] }) {
  const diff = language === "diff";
  return (
    <pre className={`md-code${diff ? " diff" : ""}`}>
      <code>
        {lines.map((line, index) => (
          // Each line is its own block, so copying still yields one line per row.
          <span key={index} className={diff ? diffClass(line) : undefined}>
            {line || " "}
          </span>
        ))}
      </code>
    </pre>
  );
}

export default function MarkdownAnswer({children}:{children:string}) {
  const blocks: ReactNode[] = [];
  const lines = children.replace(/\r\n/g, "\n").split("\n");
  let list: string[] = [];
  let fence: { language: string; lines: string[]; start: number } | null = null;
  function flushList() {
    if (!list.length) return;
    blocks.push(<ul key={`list-${blocks.length}`}>{list.map((item, index) => <li key={index}>{inlineMarkdown(item)}</li>)}</ul>);
    list = [];
  }
  lines.forEach((raw, index) => {
    // Inside a fence, lines are kept exactly — indentation is the content.
    if (fence) {
      if (raw.trim().startsWith("```")) {
        blocks.push(<CodeBlock key={`code-${fence.start}`} language={fence.language} lines={fence.lines} />);
        fence = null;
      } else {
        fence.lines.push(raw);
      }
      return;
    }
    const line = raw.trim();
    if (line.startsWith("```")) {
      flushList();
      fence = { language: line.slice(3).trim().toLowerCase(), lines: [], start: index };
      return;
    }
    if (!line) { flushList(); return; }
    if (line.startsWith("- ")) { list.push(line.slice(2)); return; }
    flushList();
    const heading = line.match(/^(#{1,3})\s+(.+)$/);
    if (heading) blocks.push(<h3 key={index}>{inlineMarkdown(heading[2])}</h3>);
    else if (/^\*\*[^*]+\*\*$/.test(line)) blocks.push(<h3 key={index}>{line.slice(2, -2)}</h3>);
    else blocks.push(<p key={index}>{inlineMarkdown(line)}</p>);
  });
  flushList();
  // A fence still open — an answer streaming mid-block — renders what has arrived.
  const open = fence as { language: string; lines: string[]; start: number } | null;
  if (open) blocks.push(<CodeBlock key={`code-${open.start}`} language={open.language} lines={open.lines} />);
  return <div className="markdown-answer">{blocks}</div>;
}
