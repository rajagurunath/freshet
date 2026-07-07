import React, { useMemo, useState } from "react";
import { useNavigate } from "react-router-dom";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { Check, GitPullRequest, RefreshCw, User, X } from "lucide-react";
import { Badge } from "@/components/ui/Badge";
import { Button } from "@/components/ui/Button";
import { Card } from "@/components/ui/Card";
import { EmptyState } from "@/components/ui/EmptyState";
import { Modal } from "@/components/ui/Modal";
import { Skeleton } from "@/components/ui/Skeleton";
import { Tabs } from "@/components/ui/Tabs";
import { Textarea } from "@/components/ui/Textarea";
import { useToast } from "@/components/ui/Toast";
import { cn } from "@/components/ui/cn";
import { CategoryChip } from "@/components/CategoryChip";
import { Markdown } from "@/components/Markdown";
import { useSettings } from "@/store/settings";
import { makeApiClient, type ReviewRecord, type ReviewStatus, type ReviewVerdict } from "@/lib/api/client";
import { relativeTime } from "@/lib/format";
import type { Category } from "@/lib/types";

const MAX_PREVIEW_MESSAGES = 20;

const statusBadge: Record<ReviewStatus, { color: "warn" | "success" | "danger"; label: string }> = {
  pending: { color: "warn", label: "Pending" },
  approved: { color: "success", label: "Approved" },
  rejected: { color: "danger", label: "Rejected" },
};

const visibilityBadgeColor: Record<string, "default" | "teal" | "slate"> = {
  company: "default",
  team: "teal",
  private: "slate",
};

function approvalsLabel(review: ReviewRecord): string {
  return `${review.approveCount} of ${review.approvalsRequired} approval${
    review.approvalsRequired === 1 ? "" : "s"
  }`;
}

function ReviewDetailModal({
  sessionId,
  onClose,
}: {
  sessionId: string;
  onClose: () => void;
}) {
  const settings = useSettings();
  const queryClient = useQueryClient();
  const { success: toastSuccess, error: toastError } = useToast();
  const [comment, setComment] = useState("");
  const [voting, setVoting] = useState<ReviewVerdict | null>(null);

  const client = makeApiClient(settings.apiBaseUrl, settings.apiKey ?? "");

  const { data: detail, isLoading } = useQuery({
    queryKey: ["review", sessionId],
    queryFn: () => client.getReview(sessionId),
    enabled: Boolean(settings.apiBaseUrl),
  });

  const review = detail?.review;
  const isOwn = Boolean(review && settings.author.id && review.authorId === settings.author.id);

  const vote = async (verdict: ReviewVerdict) => {
    setVoting(verdict);
    try {
      await client.voteReview(sessionId, verdict, comment.trim() || undefined);
      toastSuccess(
        verdict === "approve"
          ? "Approved — the session joins the company brain once it has enough approvals."
          : "Rejected — the session will not be indexed.",
      );
      await queryClient.invalidateQueries({ queryKey: ["reviews"] });
      await queryClient.invalidateQueries({ queryKey: ["review", sessionId] });
      await queryClient.invalidateQueries({ queryKey: ["review-stats"] });
      onClose();
    } catch (e) {
      toastError(e instanceof Error ? e.message : "Vote failed.");
    } finally {
      setVoting(null);
    }
  };

  const messages = detail?.messages ?? [];
  const shown = messages.slice(0, MAX_PREVIEW_MESSAGES);

  return (
    <Modal open onClose={onClose} title={review?.title ?? "Review"} size="lg">
      {isLoading || !review ? (
        <div className="space-y-3">
          <Skeleton className="h-6 w-2/3 rounded-[8px]" />
          <Skeleton className="h-24 w-full rounded-card" />
          <Skeleton className="h-40 w-full rounded-card" />
        </div>
      ) : (
        <div className="space-y-5">
          {/* Meta row */}
          <div className="flex items-center gap-2 flex-wrap">
            <Badge color={statusBadge[review.status].color}>
              {statusBadge[review.status].label}
            </Badge>
            <CategoryChip category={review.category as Category} />
            <Badge color={visibilityBadgeColor[review.visibility] ?? "default"}>
              {review.visibility}
            </Badge>
            <span className="flex items-center gap-1 text-micro text-ink-faint">
              <User size={11} />
              {review.authorName ?? review.authorId}
            </span>
            <span className="text-micro text-ink-faint">· {relativeTime(review.createdAt)}</span>
            <span className="ml-auto text-micro font-medium text-ink-soft">
              {approvalsLabel(review)}
            </span>
          </div>

          {/* Summary */}
          {review.summary && (
            <div className="rounded-card border border-border bg-bg-sunken px-4 py-3">
              <span className="block text-micro font-semibold uppercase tracking-wide text-ink-faint mb-1.5">
                Summary
              </span>
              <Markdown>{review.summary}</Markdown>
            </div>
          )}

          {/* Transcript preview */}
          <div>
            <span className="block text-micro font-semibold uppercase tracking-wide text-ink-faint mb-1.5">
              Transcript preview
            </span>
            {shown.length === 0 ? (
              <p className="text-small text-ink-faint">Transcript unavailable.</p>
            ) : (
              <div className="rounded-card border border-border bg-bg-elevated divide-y divide-border max-h-[320px] overflow-y-auto">
                {shown.map((m) => (
                  <div key={m.id} className="px-4 py-2.5">
                    <span
                      className={cn(
                        "block text-micro font-semibold uppercase tracking-wide mb-0.5",
                        m.role === "user" ? "text-accent-ink" : "text-ink-faint",
                      )}
                    >
                      {m.role}
                      {m.toolName ? ` · ${m.toolName}` : ""}
                    </span>
                    <p className="text-small text-ink whitespace-pre-wrap break-words line-clamp-6">
                      {m.text}
                    </p>
                  </div>
                ))}
                {messages.length > shown.length && (
                  <p className="px-4 py-2 text-micro text-ink-faint">
                    +{messages.length - shown.length} more messages
                  </p>
                )}
              </div>
            )}
          </div>

          {/* Votes */}
          {review.votes.length > 0 && (
            <div>
              <span className="block text-micro font-semibold uppercase tracking-wide text-ink-faint mb-1.5">
                Votes
              </span>
              <ul className="space-y-1.5">
                {review.votes.map((v) => (
                  <li key={v.id} className="flex items-start gap-2 text-small">
                    {v.verdict === "approve" ? (
                      <Check size={14} className="text-success shrink-0 mt-0.5" />
                    ) : (
                      <X size={14} className="text-danger shrink-0 mt-0.5" />
                    )}
                    <span className="text-ink font-medium">{v.reviewerName ?? v.reviewerId}</span>
                    {v.comment && <span className="text-ink-soft">— {v.comment}</span>}
                    <span className="ml-auto text-micro text-ink-faint shrink-0">
                      {relativeTime(v.createdAt)}
                    </span>
                  </li>
                ))}
              </ul>
            </div>
          )}

          {/* Actions */}
          {review.status === "pending" && (
            <div className="space-y-3 border-t border-border pt-4">
              <Textarea
                label="Comment (optional)"
                placeholder="Why are you approving or rejecting this session?"
                value={comment}
                onChange={(e) => setComment(e.target.value)}
                rows={2}
              />
              {isOwn && (
                <p className="text-small text-ink-faint">
                  You pushed this session — someone else has to review it.
                </p>
              )}
              <div className="flex items-center justify-end gap-2">
                <Button
                  variant="danger"
                  onClick={() => void vote("reject")}
                  loading={voting === "reject"}
                  disabled={isOwn || voting !== null}
                >
                  <X size={14} />
                  Reject
                </Button>
                <Button
                  variant="primary"
                  onClick={() => void vote("approve")}
                  loading={voting === "approve"}
                  disabled={isOwn || voting !== null}
                >
                  <Check size={14} />
                  Approve
                </Button>
              </div>
            </div>
          )}
        </div>
      )}
    </Modal>
  );
}

export function ReviewsPage() {
  const navigate = useNavigate();
  const settings = useSettings();
  const [tab, setTab] = useState<ReviewStatus>("pending");
  const [selected, setSelected] = useState<string | null>(null);

  const hasApi = Boolean(settings.apiBaseUrl);
  const client = makeApiClient(settings.apiBaseUrl, settings.apiKey ?? "");

  const { data: stats } = useQuery({
    queryKey: ["review-stats"],
    queryFn: () => client.reviewStats(),
    enabled: hasApi,
    refetchInterval: 30_000,
  });

  const {
    data: page,
    isLoading,
    isError,
    refetch,
    isFetching,
  } = useQuery({
    queryKey: ["reviews", tab],
    queryFn: () => client.listReviews({ status: tab, limit: 200 }),
    enabled: hasApi,
    refetchInterval: 30_000,
  });

  const items = page?.items ?? [];

  const tabItems = useMemo(
    () => [
      { value: "pending" as ReviewStatus, label: "Pending", count: stats?.pending ?? 0 },
      { value: "approved" as ReviewStatus, label: "Approved", count: stats?.approved ?? 0 },
      { value: "rejected" as ReviewStatus, label: "Rejected", count: stats?.rejected ?? 0 },
    ],
    [stats],
  );

  return (
    <div className="flex flex-col h-full">
      {/* Page header */}
      <div className="flex items-center justify-between gap-4 px-6 py-5 border-b border-border bg-bg-elevated shrink-0">
        <div>
          <h1 className="text-h2 font-semibold text-ink">Review Queue</h1>
          <p className="text-small text-ink-faint mt-0.5">
            Pushed sessions need approval before they join the company brain
          </p>
        </div>
        <button
          type="button"
          onClick={() => void refetch()}
          aria-label="Refresh reviews"
          className="h-9 w-9 flex items-center justify-center rounded-[8px] border border-border text-ink-soft hover:bg-bg-sunken hover:text-ink transition-colors duration-120"
        >
          <RefreshCw size={14} className={cn(isFetching && "animate-spin")} />
        </button>
      </div>

      {/* Body */}
      <div className="flex-1 min-h-0 overflow-y-auto px-6 py-6 space-y-5">
        {!hasApi ? (
          <EmptyState
            icon={<GitPullRequest size={40} strokeWidth={1.25} />}
            headline="Connect a hub first"
            body="Add your Freshet API URL and key in Settings to review your team's pushed sessions."
            cta={
              <button
                className="text-small text-accent hover:text-accent-ink transition-colors duration-120"
                onClick={() => navigate("/settings")}
              >
                Go to Settings
              </button>
            }
          />
        ) : (
          <>
            <Tabs items={tabItems} value={tab} onChange={setTab} className="w-fit" />

            {isLoading ? (
              <div className="space-y-3 max-w-[860px]">
                {[0, 1, 2].map((i) => (
                  <Skeleton key={i} className="h-28 w-full rounded-card" />
                ))}
              </div>
            ) : isError ? (
              <EmptyState
                icon={<GitPullRequest size={40} strokeWidth={1.25} />}
                headline="Could not load reviews"
                body="Check your connection settings and try again."
                cta={
                  <button
                    className="text-small text-accent hover:text-accent-ink transition-colors duration-120"
                    onClick={() => void refetch()}
                  >
                    Retry
                  </button>
                }
              />
            ) : items.length === 0 ? (
              <EmptyState
                icon={<GitPullRequest size={40} strokeWidth={1.25} />}
                headline={
                  tab === "pending" ? "Nothing waiting for review" : `No ${tab} sessions`
                }
                body={
                  tab === "pending"
                    ? "When teammates push sessions, they land here for approval before being indexed."
                    : "Decided reviews will show up here."
                }
              />
            ) : (
              <div className="space-y-3 max-w-[860px]">
                {items.map((r) => (
                  <Card
                    key={r.sessionId}
                    hoverable
                    className="p-4 flex flex-col gap-2.5"
                    onClick={() => setSelected(r.sessionId)}
                  >
                    <div className="flex items-center gap-2 flex-wrap">
                      <h3 className="text-body font-semibold text-ink line-clamp-1 min-w-0">
                        {r.title}
                      </h3>
                      <CategoryChip category={r.category as Category} />
                      <Badge color={visibilityBadgeColor[r.visibility] ?? "default"}>
                        {r.visibility}
                      </Badge>
                      {tab !== "pending" && (
                        <Badge color={statusBadge[r.status].color}>
                          {statusBadge[r.status].label}
                        </Badge>
                      )}
                    </div>
                    {r.summary && (
                      <p className="text-small text-ink-faint line-clamp-2 leading-relaxed">
                        {r.summary}
                      </p>
                    )}
                    <div className="flex items-center gap-2 text-micro text-ink-faint">
                      <User size={11} />
                      <span>{r.authorName ?? r.authorId}</span>
                      <span className="text-border-strong">·</span>
                      <span>{relativeTime(r.createdAt)}</span>
                      <span className="ml-auto font-medium text-ink-soft">
                        {r.status === "pending"
                          ? approvalsLabel(r)
                          : r.status === "approved"
                          ? `Approved with ${approvalsLabel(r).toLowerCase()}`
                          : "Rejected"}
                      </span>
                      {r.myVote && (
                        <Badge color={r.myVote === "approve" ? "success" : "danger"}>
                          you: {r.myVote}
                        </Badge>
                      )}
                    </div>
                  </Card>
                ))}
              </div>
            )}
          </>
        )}
      </div>

      {selected && (
        <ReviewDetailModal sessionId={selected} onClose={() => setSelected(null)} />
      )}
    </div>
  );
}
