import React, { useCallback, useEffect, useMemo, useState } from "react";
import { useNavigate } from "react-router-dom";
import {
  Package,
  Sparkles,
  TerminalSquare,
  Bot,
  Search,
  RefreshCw,
  UploadCloud,
  Download,
  FileArchive,
} from "lucide-react";
import { Badge } from "@/components/ui/Badge";
import { Button } from "@/components/ui/Button";
import { EmptyState } from "@/components/ui/EmptyState";
import { Input } from "@/components/ui/Input";
import { Modal } from "@/components/ui/Modal";
import { Select } from "@/components/ui/Select";
import { Skeleton } from "@/components/ui/Skeleton";
import { Tabs } from "@/components/ui/Tabs";
import { useToast } from "@/components/ui/Toast";
import { cn } from "@/components/ui/cn";
import { useSettings } from "@/store/settings";
import { makeApiClient, type AssetRecord } from "@/lib/api/client";
import {
  scanLocalAssets,
  buildAssetZip,
  type AssetSource,
  type LocalAsset,
} from "@/lib/assetScan";
import { homeDirPath, isTauri, listDir, readBinary, readText } from "@/lib/tauri";
import { relativeTime } from "@/lib/format";
import { CATEGORIES, type Category, type Visibility } from "@/lib/types";

type AssetTab = "local" | "hub";

const SOURCE_META: Record<AssetSource, { label: string; icon: React.ElementType }> = {
  skills: { label: "Skills", icon: Sparkles },
  commands: { label: "Commands", icon: TerminalSquare },
  agents: { label: "Agents", icon: Bot },
};

const KIND_FILTERS = [
  { value: "", label: "All kinds" },
  { value: "skill", label: "Skills" },
  { value: "prompt", label: "Prompts" },
  { value: "script", label: "Scripts" },
  { value: "config", label: "Configs" },
];

export function AssetsPage() {
  const navigate = useNavigate();
  const settings = useSettings();
  const { success: toastSuccess, error: toastError } = useToast();
  const hasApi = Boolean(settings.apiBaseUrl);

  const [tab, setTab] = useState<AssetTab>("local");

  // ── local assets ──────────────────────────────────────────────────────────
  const [localAssets, setLocalAssets] = useState<LocalAsset[]>([]);
  const [localLoading, setLocalLoading] = useState(true);
  const [pushedIds, setPushedIds] = useState<Set<string>>(new Set());

  const scanLocal = useCallback(async () => {
    setLocalLoading(true);
    try {
      const assets = await scanLocalAssets({
        homeDir: homeDirPath,
        listDir,
        readText,
      });
      setLocalAssets(assets);
    } catch {
      setLocalAssets([]);
    } finally {
      setLocalLoading(false);
    }
  }, []);

  useEffect(() => {
    void scanLocal();
  }, [scanLocal]);

  const localBySource = useMemo(() => {
    const groups: Record<AssetSource, LocalAsset[]> = {
      skills: [],
      commands: [],
      agents: [],
    };
    for (const a of localAssets) groups[a.source].push(a);
    return groups;
  }, [localAssets]);

  // ── push modal ────────────────────────────────────────────────────────────
  const [pushTarget, setPushTarget] = useState<LocalAsset | null>(null);
  const [pushCategory, setPushCategory] = useState<Category>(settings.defaultCategory);
  const [pushVisibility, setPushVisibility] = useState<Visibility>(
    settings.defaultVisibility,
  );
  const [pushing, setPushing] = useState(false);

  const openPush = (asset: LocalAsset) => {
    setPushCategory(settings.defaultCategory);
    setPushVisibility(settings.defaultVisibility);
    setPushTarget(asset);
  };

  const client = useCallback(
    () => makeApiClient(settings.apiBaseUrl, settings.apiKey ?? ""),
    [settings.apiBaseUrl, settings.apiKey],
  );

  const pushAsset = async () => {
    if (!pushTarget) return;
    setPushing(true);
    try {
      const zip = await buildAssetZip(pushTarget, readBinary);
      await client().uploadAsset(
        {
          kind: pushTarget.kind,
          name: pushTarget.name,
          description: pushTarget.description,
          category: pushCategory,
          visibility: pushVisibility,
        },
        zip,
      );
      setPushedIds((prev) => new Set(prev).add(pushTarget.id));
      toastSuccess(`Pushed "${pushTarget.name}" to the hub.`);
      setPushTarget(null);
    } catch (e) {
      toastError(e instanceof Error ? e.message : "Could not push the asset.");
    } finally {
      setPushing(false);
    }
  };

  // ── hub assets ────────────────────────────────────────────────────────────
  const [hubAssets, setHubAssets] = useState<AssetRecord[]>([]);
  const [hubTotal, setHubTotal] = useState(0);
  const [hubLoading, setHubLoading] = useState(false);
  const [hubError, setHubError] = useState<string | null>(null);
  const [hubQuery, setHubQuery] = useState("");
  const [hubKind, setHubKind] = useState("");
  const [downloading, setDownloading] = useState<string | null>(null);

  const loadHub = useCallback(async () => {
    if (!settings.apiBaseUrl) return;
    setHubLoading(true);
    setHubError(null);
    try {
      const page = await client().listAssets({
        q: hubQuery.trim() || undefined,
        kind: hubKind || undefined,
        limit: 200,
      });
      setHubAssets(page.items);
      setHubTotal(page.total);
    } catch {
      setHubError("Could not load assets. Check your connection settings.");
    } finally {
      setHubLoading(false);
    }
  }, [settings.apiBaseUrl, client, hubQuery, hubKind]);

  // Load hub list when switching to the tab; debounce search input.
  useEffect(() => {
    if (tab !== "hub") return;
    const t = setTimeout(() => void loadHub(), 250);
    return () => clearTimeout(t);
  }, [tab, loadHub]);

  const downloadAsset = async (asset: AssetRecord) => {
    setDownloading(asset.id);
    try {
      const blob = await client().downloadAsset(asset.id);
      const url = URL.createObjectURL(blob);
      const link = document.createElement("a");
      link.href = url;
      link.download = `${asset.name.replace(/\//g, "__")}.zip`;
      link.click();
      URL.revokeObjectURL(url);
      toastSuccess(`Downloaded "${asset.name}".`);
    } catch (e) {
      toastError(e instanceof Error ? e.message : "Could not download the asset.");
    } finally {
      setDownloading(null);
    }
  };

  // ── render ────────────────────────────────────────────────────────────────
  return (
    <div className="flex flex-col h-full">
      {/* Page header */}
      <div className="flex items-center justify-between gap-4 px-6 py-5 border-b border-border bg-bg-elevated shrink-0">
        <div>
          <h1 className="text-h2 font-semibold text-ink">Assets</h1>
          <p className="text-small text-ink-faint mt-0.5">
            Skills, commands and agents — push yours to the hub, pull your team&apos;s
          </p>
        </div>
        <div className="flex items-center gap-2 shrink-0">
          <button
            type="button"
            onClick={() => (tab === "local" ? void scanLocal() : void loadHub())}
            aria-label="Refresh assets"
            className="h-9 w-9 flex items-center justify-center rounded-[8px] border border-border text-ink-soft hover:bg-bg-sunken hover:text-ink transition-colors duration-120"
          >
            <RefreshCw size={14} className={cn((localLoading || hubLoading) && "animate-spin")} />
          </button>
          <Tabs<AssetTab>
            items={[
              { value: "local", label: "On this machine", count: localAssets.length },
              { value: "hub", label: "Company hub", count: tab === "hub" ? hubTotal : undefined },
            ]}
            value={tab}
            onChange={setTab}
          />
        </div>
      </div>

      {/* Body */}
      <div className="flex-1 min-h-0 overflow-y-auto">
        {tab === "local" ? (
          !isTauri() ? (
            <EmptyState
              icon={<Package size={40} strokeWidth={1.25} />}
              headline="Local scanning needs the desktop app"
              body="Run Freshet as the Tauri desktop app to scan ~/.claude/skills, commands and agents."
            />
          ) : localLoading ? (
            <div className="px-6 py-6 space-y-3 max-w-[860px]">
              <Skeleton className="h-16 w-full rounded-card" />
              <Skeleton className="h-16 w-full rounded-card" />
              <Skeleton className="h-16 w-full rounded-card" />
            </div>
          ) : localAssets.length === 0 ? (
            <EmptyState
              icon={<Package size={40} strokeWidth={1.25} />}
              headline="No local assets found"
              body="Nothing in ~/.claude/skills, ~/.claude/commands or ~/.claude/agents yet. Create a skill or slash command and it shows up here."
            />
          ) : (
            <div className="px-6 py-6 space-y-8 max-w-[860px]">
              {(Object.keys(SOURCE_META) as AssetSource[]).map((source) => {
                const assets = localBySource[source];
                if (assets.length === 0) return null;
                const { label, icon: Icon } = SOURCE_META[source];
                return (
                  <section key={source}>
                    <div className="flex items-center gap-2 mb-3">
                      <Icon size={15} className="text-ink-soft" />
                      <h2 className="text-h3 font-semibold text-ink">{label}</h2>
                      <Badge color="default">{assets.length}</Badge>
                    </div>
                    <ul className="rounded-card border border-border bg-bg-elevated divide-y divide-border">
                      {assets.map((asset) => (
                        <li
                          key={asset.id}
                          className="flex items-center gap-3 px-4 py-3"
                        >
                          <div className="min-w-0 flex-1">
                            <div className="flex items-center gap-2">
                              <span className="text-body font-medium text-ink truncate">
                                {asset.name}
                              </span>
                              {asset.files.length > 1 && (
                                <span className="text-micro font-mono text-ink-faint shrink-0">
                                  {asset.files.length} files
                                </span>
                              )}
                            </div>
                            {asset.description && (
                              <p className="text-small text-ink-faint truncate mt-0.5">
                                {asset.description}
                              </p>
                            )}
                          </div>
                          {pushedIds.has(asset.id) ? (
                            <Badge color="success">pushed</Badge>
                          ) : (
                            <Button
                              size="sm"
                              variant="secondary"
                              onClick={() => openPush(asset)}
                              disabled={!hasApi}
                              title={hasApi ? undefined : "Connect a hub in Settings first"}
                            >
                              <UploadCloud size={13} />
                              Push to hub
                            </Button>
                          )}
                        </li>
                      ))}
                    </ul>
                  </section>
                );
              })}
            </div>
          )
        ) : /* hub tab */ !hasApi ? (
          <EmptyState
            icon={<Package size={40} strokeWidth={1.25} />}
            headline="Connect a hub first"
            body="Add your Freshet API URL and key in Settings to browse company assets."
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
          <div className="px-6 py-6 space-y-4 max-w-[860px]">
            {/* Search + kind filter */}
            <div className="flex items-center gap-3">
              <Input
                placeholder="Search assets…"
                value={hubQuery}
                onChange={(e) => setHubQuery(e.target.value)}
                leading={<Search size={14} />}
              />
              <div className="w-[160px] shrink-0">
                <Select
                  aria-label="Filter by kind"
                  options={KIND_FILTERS}
                  value={hubKind}
                  onChange={(e) => setHubKind(e.target.value)}
                />
              </div>
            </div>

            {hubLoading ? (
              <div className="space-y-3">
                <Skeleton className="h-16 w-full rounded-card" />
                <Skeleton className="h-16 w-full rounded-card" />
              </div>
            ) : hubError ? (
              <EmptyState
                icon={<Package size={40} strokeWidth={1.25} />}
                headline="Could not load assets"
                body={hubError}
                cta={
                  <button
                    className="text-small text-accent hover:text-accent-ink transition-colors duration-120"
                    onClick={() => void loadHub()}
                  >
                    Retry
                  </button>
                }
              />
            ) : hubAssets.length === 0 ? (
              <EmptyState
                icon={<Package size={40} strokeWidth={1.25} />}
                headline="No assets on the hub yet"
                body='Push a local skill or command from the "On this machine" tab — it shows up here for the whole team.'
              />
            ) : (
              <ul className="rounded-card border border-border bg-bg-elevated divide-y divide-border">
                {hubAssets.map((asset) => (
                  <li key={asset.id} className="flex items-center gap-3 px-4 py-3">
                    <FileArchive size={16} className="text-ink-faint shrink-0" />
                    <div className="min-w-0 flex-1">
                      <div className="flex items-center gap-2">
                        <span className="text-body font-medium text-ink truncate">
                          {asset.name}
                        </span>
                        <Badge color="accent">{asset.kind}</Badge>
                        <Badge color="default">{asset.category}</Badge>
                      </div>
                      <p className="text-small text-ink-faint truncate mt-0.5">
                        {asset.description || "No description"}
                        {asset.author && ` — ${asset.author}`}
                        {asset.createdAt && `, ${relativeTime(asset.createdAt)}`}
                      </p>
                    </div>
                    <span className="text-micro font-mono text-ink-faint shrink-0">
                      v{asset.version}
                    </span>
                    <Button
                      size="sm"
                      variant="secondary"
                      onClick={() => void downloadAsset(asset)}
                      loading={downloading === asset.id}
                    >
                      <Download size={13} />
                      Download
                    </Button>
                  </li>
                ))}
              </ul>
            )}
          </div>
        )}
      </div>

      {/* Push modal */}
      <Modal
        open={pushTarget !== null}
        onClose={() => !pushing && setPushTarget(null)}
        title={pushTarget ? `Push "${pushTarget.name}"` : undefined}
        description={
          pushTarget
            ? `${pushTarget.files.length} file${pushTarget.files.length === 1 ? "" : "s"} will be zipped and uploaded to the hub.`
            : undefined
        }
        size="sm"
      >
        <div className="space-y-4">
          <Select
            label="Category"
            options={CATEGORIES.map((c) => ({ value: c, label: c }))}
            value={pushCategory}
            onChange={(e) => setPushCategory(e.target.value as Category)}
          />
          <Select
            label="Visibility"
            options={[
              { value: "company", label: "Company — everyone on the hub" },
              { value: "team", label: "Team — your team only" },
              { value: "private", label: "Private — just you" },
            ]}
            value={pushVisibility}
            onChange={(e) => setPushVisibility(e.target.value as Visibility)}
          />
          <div className="flex justify-end gap-2 pt-1">
            <Button variant="ghost" onClick={() => setPushTarget(null)} disabled={pushing}>
              Cancel
            </Button>
            <Button variant="primary" onClick={() => void pushAsset()} loading={pushing}>
              <UploadCloud size={14} />
              Push
            </Button>
          </div>
        </div>
      </Modal>
    </div>
  );
}
