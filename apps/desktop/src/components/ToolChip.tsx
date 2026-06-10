import React from "react";
import { Badge } from "./ui/Badge";
import type { Tool } from "@/lib/types";
import { TOOL_LABELS } from "@/lib/types";
import { cn } from "./ui/cn";

interface ToolChipProps {
  tool: Tool;
  className?: string;
}

type BadgeColorType = "orange" | "slate" | "teal" | "default";

const toolColor: Record<Tool, BadgeColorType> = {
  "claude-code": "orange",
  codex: "slate",
  "kilo-code": "teal",
};

export function ToolChip({ tool, className }: ToolChipProps) {
  return (
    <Badge color={toolColor[tool]} className={cn("font-mono", className)}>
      {TOOL_LABELS[tool]}
    </Badge>
  );
}
