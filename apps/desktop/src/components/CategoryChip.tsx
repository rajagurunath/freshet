import React from "react";
import { Badge } from "./ui/Badge";
import type { Category } from "@/lib/types";
import { cn } from "./ui/cn";

interface CategoryChipProps {
  category: Category;
  className?: string;
}

type BadgeColorType = "slate" | "success" | "accent" | "warn" | "default" | "teal";

const categoryColor: Record<Category, BadgeColorType> = {
  engineering: "slate",
  sales: "success",
  marketing: "accent",
  research: "teal",
  ops: "warn",
  other: "default",
};

const categoryLabel: Record<Category, string> = {
  engineering: "Engineering",
  sales: "Sales",
  marketing: "Marketing",
  research: "Research",
  ops: "Ops",
  other: "Other",
};

export function CategoryChip({ category, className }: CategoryChipProps) {
  return (
    <Badge color={categoryColor[category]} className={cn(className)}>
      {categoryLabel[category]}
    </Badge>
  );
}
