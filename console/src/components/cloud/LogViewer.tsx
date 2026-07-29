import { useEffect, useMemo, useRef } from "react";
import { useVirtualizer } from "@tanstack/react-virtual";
import { cn } from "@/lib/utils";

export function LogViewer({ text, className }: { text: string; className?: string }) {
  const parentRef = useRef<HTMLDivElement>(null);
  const stickToBottomRef = useRef(true);

  const lines = useMemo(() => {
    if (!text) return ["(no output)"];
    const arr = text.split("\n");
    if (arr.length && arr[arr.length - 1] === "") arr.pop();
    return arr.length ? arr : ["(no output)"];
  }, [text]);

  const rowVirtualizer = useVirtualizer({
    count: lines.length,
    getScrollElement: () => parentRef.current,
    estimateSize: () => 18,
    overscan: 12,
  });

  // Track whether the user is pinned to the bottom; only auto-scroll if so.
  useEffect(() => {
    const el = parentRef.current;
    if (!el) return;
    const onScroll = () => {
      const dist = el.scrollHeight - el.scrollTop - el.clientHeight;
      stickToBottomRef.current = dist < 40;
    };
    el.addEventListener("scroll", onScroll, { passive: true });
    return () => el.removeEventListener("scroll", onScroll);
  }, []);

  useEffect(() => {
    if (!stickToBottomRef.current) return;
    rowVirtualizer.scrollToIndex(lines.length - 1, { align: "end" });
  }, [lines.length, rowVirtualizer]);

  return (
    <div
      ref={parentRef}
      className={cn(
        "rounded-md bg-zinc-950 text-zinc-100 font-mono text-xs leading-[18px] overflow-auto max-h-[600px]",
        className
      )}
    >
      <div style={{ height: rowVirtualizer.getTotalSize(), position: "relative", width: "100%" }}>
        {rowVirtualizer.getVirtualItems().map((vi) => (
          <div
            key={vi.key}
            data-index={vi.index}
            className="px-4 whitespace-pre"
            style={{
              position: "absolute",
              top: 0,
              left: 0,
              width: "100%",
              height: 18,
              transform: `translateY(${vi.start}px)`,
            }}
          >
            {lines[vi.index] || "\u00A0"}
          </div>
        ))}
      </div>
    </div>
  );
}
