import { useMemo, useState } from "react";
import { ExternalLink, Pencil, Trash2, X, GitMerge, Plus } from "lucide-react";
import { Badge } from "@/components/ui/Badge";
import { Button } from "@/components/ui/Button";
import { Input } from "@/components/ui/Input";
import { Select } from "@/components/ui/Select";
import { useToast } from "@/components/ui/Toast";
import { kindColor, type GraphEdge, type GraphNode } from "@/lib/graph";
import type { makeApiClient } from "@/lib/api/client";

const KIND_OPTIONS = ["repo", "service", "feature", "person", "decision", "tool", "pr", "problem"]
  .map((k) => ({ value: k, label: k }));
const REL_OPTIONS = ["worked_on", "decided", "fixed", "uses", "depends_on", "related_to"]
  .map((r) => ({ value: r, label: r.replace(/_/g, " ") }));

export interface GraphNodePanelProps {
  node: GraphNode;
  edges: GraphEdge[];
  nodeById: Map<string, GraphNode>;
  allNodes: GraphNode[];
  localSessionIds: Set<string>;
  client: ReturnType<typeof makeApiClient>;
  onClose: () => void;
  onSelect: (id: string) => void;
  onChanged: () => void;
  onOpenSession?: (sessionId: string) => void;
}

/**
 * Node inspector + editor. View mode mirrors the old read-only panel;
 * edit mode exposes rename (rename onto an existing name = hard merge),
 * kind/summary edits, delete, explicit merge-into, edge delete, and add-edge.
 * Every mutation calls onChanged() so the page refetches the live graph.
 */
export function GraphNodePanel({
  node, edges, nodeById, allNodes, localSessionIds, client,
  onClose, onSelect, onChanged, onOpenSession,
}: GraphNodePanelProps) {
  const { info: toastInfo, error: toastError } = useToast();
  const [editing, setEditing] = useState(false);
  const [name, setName] = useState(node.name);
  const [kind, setKind] = useState(node.kind);
  const [summary, setSummary] = useState(node.summary ?? "");
  const [mergeTarget, setMergeTarget] = useState("");
  const [linkTarget, setLinkTarget] = useState("");
  const [linkRel, setLinkRel] = useState("related_to");
  const [busy, setBusy] = useState(false);

  const mergeCandidates = useMemo(
    () => allNodes.filter((n) => n.kind === node.kind && n.id !== node.id),
    [allNodes, node],
  );
  const linkCandidates = useMemo(
    () => allNodes.filter((n) => n.id !== node.id),
    [allNodes, node],
  );

  const run = async (fn: () => Promise<unknown>, okMsg: string) => {
    setBusy(true);
    try {
      await fn();
      toastInfo(okMsg);
      onChanged();
    } catch (e) {
      toastError(e instanceof Error ? e.message : "Graph update failed.");
    } finally {
      setBusy(false);
    }
  };

  const save = () =>
    run(async () => {
      const patch: { name?: string; kind?: string; summary?: string } = {};
      if (name.trim() && name.trim() !== node.name) patch.name = name.trim();
      if (kind !== node.kind) patch.kind = kind;
      if (summary !== (node.summary ?? "")) patch.summary = summary;
      if (Object.keys(patch).length) await client.updateGraphNode(node.id, patch);
      setEditing(false);
    }, "Node updated — future extractions follow the new name.");

  const mergeInto = () => {
    const target = nodeById.get(mergeTarget);
    if (!target) return;
    return run(
      () => client.updateGraphNode(node.id, { name: target.name }),
      `Merged into "${target.name}".`,
    );
  };

  const remove = () => {
    if (!window.confirm(`Delete "${node.name}"? It won't be re-extracted.`)) return;
    return run(async () => {
      await client.deleteGraphNode(node.id);
      onClose();
    }, "Node deleted (tombstoned).");
  };

  const addEdge = () => {
    if (!linkTarget) return;
    return run(
      () => client.createGraphEdge({ src: node.id, dst: linkTarget, rel: linkRel }),
      "Link added.",
    );
  };

  const removeEdge = (edgeId: string) =>
    run(() => client.deleteGraphEdge(edgeId), "Link removed (tombstoned).");

  return (
    <aside className="w-[320px] shrink-0 border-l border-border bg-bg-elevated overflow-y-auto">
      <div className="flex items-start justify-between gap-2 px-4 py-4 border-b border-border">
        <div className="min-w-0">
          <Badge className="mb-1.5" color="default">
            <span
              className="w-2 h-2 rounded-full"
              style={{ backgroundColor: kindColor(node.kind).ink }}
            />
            {kindColor(node.kind).label}
            {node.generic ? " · common" : ""}
          </Badge>
          <h2 className="text-h3 font-semibold text-ink break-words">{node.name}</h2>
        </div>
        <div className="flex items-center gap-1 shrink-0">
          <button
            onClick={() => setEditing((v) => !v)}
            aria-label="Edit node"
            className="p-1 rounded-[6px] text-ink-faint hover:text-ink hover:bg-bg-sunken transition-colors duration-120"
          >
            <Pencil size={14} />
          </button>
          <button
            onClick={onClose}
            aria-label="Close panel"
            className="p-1 rounded-[6px] text-ink-faint hover:text-ink hover:bg-bg-sunken transition-colors duration-120"
          >
            <X size={14} />
          </button>
        </div>
      </div>

      <div className="px-4 py-4 space-y-5">
        {editing ? (
          <div className="space-y-3">
            <Input value={name} onChange={(e) => setName(e.target.value)} aria-label="Node name" />
            <Select
              options={KIND_OPTIONS}
              value={kind}
              onChange={(e) => setKind(e.target.value)}
              aria-label="Node kind"
            />
            <textarea
              value={summary}
              onChange={(e) => setSummary(e.target.value)}
              rows={3}
              placeholder="Summary"
              aria-label="Node summary"
              className="w-full rounded-[8px] border border-border bg-bg px-3 py-2 text-small text-ink"
            />
            <div className="flex gap-2">
              <Button variant="primary" size="sm" loading={busy} onClick={() => void save()}>
                Save
              </Button>
              <Button variant="secondary" size="sm" onClick={() => setEditing(false)}>
                Cancel
              </Button>
              <Button variant="secondary" size="sm" onClick={() => void remove()}>
                <Trash2 size={13} /> Delete
              </Button>
            </div>

            {mergeCandidates.length > 0 && (
              <div className="pt-2 border-t border-border space-y-2">
                <span className="text-micro uppercase tracking-wide text-ink-faint flex items-center gap-1">
                  <GitMerge size={11} /> Merge into
                </span>
                <Select
                  options={[{ value: "", label: "Pick a node…" }].concat(
                    mergeCandidates.map((n) => ({ value: n.id, label: n.name })),
                  )}
                  value={mergeTarget}
                  onChange={(e) => setMergeTarget(e.target.value)}
                  aria-label="Merge target"
                />
                <Button
                  variant="secondary" size="sm" loading={busy}
                  onClick={() => void mergeInto()}
                >
                  Merge (moves links + sessions)
                </Button>
              </div>
            )}

            <div className="pt-2 border-t border-border space-y-2">
              <span className="text-micro uppercase tracking-wide text-ink-faint flex items-center gap-1">
                <Plus size={11} /> Link to
              </span>
              <Select
                options={[{ value: "", label: "Pick a node…" }].concat(
                  linkCandidates.map((n) => ({ value: n.id, label: `${n.kind}: ${n.name}` })),
                )}
                value={linkTarget}
                onChange={(e) => setLinkTarget(e.target.value)}
                aria-label="Link target"
              />
              <Select
                options={REL_OPTIONS}
                value={linkRel}
                onChange={(e) => setLinkRel(e.target.value)}
                aria-label="Link relation"
              />
              <Button variant="secondary" size="sm" loading={busy} onClick={() => void addEdge()}>
                Add link
              </Button>
            </div>
          </div>
        ) : (
          <>
            {node.summary && (
              <p className="text-small text-ink-soft leading-relaxed">{node.summary}</p>
            )}

            {edges.length > 0 && (
              <div>
                <h3 className="text-micro font-semibold uppercase tracking-wide text-ink-faint mb-2">
                  Relations
                </h3>
                <ul className="space-y-1.5">
                  {edges.map((e) => {
                    const otherId = e.src === node.id ? e.dst : e.src;
                    const other = nodeById.get(otherId);
                    if (!other) return null;
                    return (
                      <li key={e.id} className="flex items-center gap-2">
                        <button
                          onClick={() => onSelect(other.id)}
                          className="flex-1 min-w-0 flex items-center gap-2 text-left text-small text-ink-soft hover:text-ink transition-colors duration-120"
                        >
                          <span
                            className="w-2 h-2 rounded-full shrink-0 border"
                            style={{
                              backgroundColor: kindColor(other.kind).fill,
                              borderColor: kindColor(other.kind).stroke,
                            }}
                          />
                          <span className="truncate">{other.name}</span>
                          <span className="ml-auto text-micro text-ink-faint shrink-0">
                            {e.rel.replace(/_/g, " ")}
                          </span>
                        </button>
                        <button
                          onClick={() => void removeEdge(e.id)}
                          aria-label={`Remove link to ${other.name}`}
                          className="p-0.5 text-ink-faint hover:text-ink shrink-0"
                        >
                          <X size={11} />
                        </button>
                      </li>
                    );
                  })}
                </ul>
              </div>
            )}

            <div>
              <h3 className="text-micro font-semibold uppercase tracking-wide text-ink-faint mb-2">
                Linked sessions ({node.sessionIds.length})
              </h3>
              {node.sessionIds.length === 0 ? (
                <p className="text-small text-ink-faint">No session provenance recorded.</p>
              ) : (
                <ul className="space-y-1">
                  {node.sessionIds.map((sid) => (
                    <li key={sid}>
                      {localSessionIds.has(sid) && onOpenSession ? (
                        <button
                          onClick={() => onOpenSession(sid)}
                          className="flex items-center gap-1.5 text-small font-mono text-accent hover:text-accent-ink transition-colors duration-120 max-w-full"
                        >
                          <span className="truncate">{sid}</span>
                          <ExternalLink size={11} className="shrink-0" />
                        </button>
                      ) : (
                        <span className="block text-small font-mono text-ink-faint truncate">
                          {sid}
                        </span>
                      )}
                    </li>
                  ))}
                </ul>
              )}
            </div>
          </>
        )}
      </div>
    </aside>
  );
}
