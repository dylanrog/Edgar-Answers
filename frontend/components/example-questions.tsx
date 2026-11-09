"use client";

import type { Example } from "@/lib/examples";

/** Example questions as chips; the featured one is first and highlighted. */
export function ExampleQuestions({
  examples,
  onPick,
  disabled,
}: {
  examples: Example[];
  onPick: (example: Example) => void;
  disabled: boolean;
}) {
  const ordered = [...examples].sort(
    (a, b) => Number(b.featured) - Number(a.featured),
  );
  return (
    <div className="flex flex-wrap gap-2">
      {ordered.map((example) => (
        <button
          key={example.id}
          type="button"
          disabled={disabled}
          onClick={() => onPick(example)}
          className={
            "rounded border px-3 py-1.5 text-left text-xs disabled:opacity-50 " +
            (example.featured
              ? "border-blue-400 bg-blue-900 text-blue-100 hover:bg-blue-800"
              : "border-slate-700 bg-slate-900 text-slate-300 hover:border-slate-500")
          }
        >
          {example.label}
        </button>
      ))}
    </div>
  );
}
