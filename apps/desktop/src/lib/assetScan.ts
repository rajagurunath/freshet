/**
 * Asset hub local scanning + ZIP packing (Task 15, desktop half).
 *
 * Scans the user's local agent asset directories —
 *   ~/.claude/skills/**    (one asset per skill directory)
 *   ~/.claude/commands/**  (one asset per markdown file, namespaced by subdir)
 *   ~/.claude/agents/**    (one asset per markdown file)
 * — and packs an asset's files into a stored (uncompressed) ZIP for the hub's
 * `POST /v1/assets` multipart upload.
 *
 * All filesystem access goes through the injected `AssetFs` interface so the
 * scanner is pure and testable in node (the real implementation wires it to
 * the Tauri fs bridge in `tauri.ts`).
 */

// ─── types ───────────────────────────────────────────────────────────────────

export type AssetSource = "skills" | "commands" | "agents";

/** Mirrors the API's asset kinds: skill | script | config | prompt. */
export type LocalAssetKind = "skill" | "script" | "config" | "prompt";

export interface DirEntry {
  name: string;
  isDirectory: boolean;
}

/** Injected filesystem dependencies (Tauri in prod, fakes in tests). */
export interface AssetFs {
  /** Absolute home directory, or "" when unknown (browser mode). */
  homeDir(): Promise<string>;
  /** List a directory; returns [] when it does not exist. */
  listDir(path: string): Promise<DirEntry[]>;
  readText(path: string): Promise<string>;
}

export interface LocalAsset {
  /** Stable id: `<source>/<name>`. */
  id: string;
  source: AssetSource;
  kind: LocalAssetKind;
  /** Skill dir name, or command/agent name namespaced by subdir ("gstack/browse"). */
  name: string;
  description: string;
  /** Absolute path: the directory (skills) or the file (commands/agents). */
  path: string;
  /** Absolute paths of every file included when pushing to the hub. */
  files: string[];
}

const MAX_DESCRIPTION = 200;
const MAX_DEPTH = 6;

// ─── description parsing ─────────────────────────────────────────────────────

function firstProseLine(text: string): string {
  for (const raw of text.split("\n")) {
    const line = raw.trim();
    if (!line || line.startsWith("#") || line === "---") continue;
    return line.slice(0, MAX_DESCRIPTION);
  }
  return "";
}

/**
 * Extract a one-line description from a markdown asset file: the
 * `description:` field of YAML frontmatter when present, otherwise the first
 * prose (non-heading) line of the body. Always ≤ 200 chars.
 */
export function parseAssetDescription(text: string): string {
  const trimmed = text.trimStart();
  if (trimmed.startsWith("---")) {
    const end = trimmed.indexOf("\n---", 3);
    if (end !== -1) {
      const fm = trimmed.slice(trimmed.indexOf("\n") + 1, end);
      const m = fm.match(/^description:\s*(.*)$/m);
      if (m) {
        let v = m[1].trim();
        if (
          (v.startsWith('"') && v.endsWith('"') && v.length >= 2) ||
          (v.startsWith("'") && v.endsWith("'") && v.length >= 2)
        ) {
          v = v.slice(1, -1);
        }
        // Folded/literal block scalars (> or |) carry no inline value.
        if (v && v !== ">" && v !== "|") return v.slice(0, MAX_DESCRIPTION);
      }
      const bodyStart = end + "\n---".length;
      return firstProseLine(trimmed.slice(bodyStart));
    }
  }
  return firstProseLine(text);
}

// ─── scanning ────────────────────────────────────────────────────────────────

async function safeList(fs: AssetFs, path: string): Promise<DirEntry[]> {
  try {
    return await fs.listDir(path);
  } catch {
    return [];
  }
}

/** Recursively collect file paths under `dir` (sorted, depth-limited). */
async function collectFiles(fs: AssetFs, dir: string, depth = 0): Promise<string[]> {
  if (depth > MAX_DEPTH) return [];
  const files: string[] = [];
  for (const entry of await safeList(fs, dir)) {
    const path = `${dir}/${entry.name}`;
    if (entry.isDirectory) {
      files.push(...(await collectFiles(fs, path, depth + 1)));
    } else {
      files.push(path);
    }
  }
  return files.sort();
}

async function describeFile(fs: AssetFs, path: string): Promise<string> {
  try {
    return parseAssetDescription(await fs.readText(path));
  } catch {
    return "";
  }
}

/**
 * Scan `~/.claude/{skills,commands,agents}` for local assets.
 *
 * Skills are directories (described by their SKILL.md); commands and agents
 * are individual markdown files mapped to the hub's `prompt` kind. Missing
 * roots are silently skipped; returns [] in browser mode (no home dir).
 */
export async function scanLocalAssets(fs: AssetFs): Promise<LocalAsset[]> {
  const home = (await fs.homeDir()).replace(/\/+$/, "");
  if (!home) return [];
  const assets: LocalAsset[] = [];

  // Skills: each subdirectory of ~/.claude/skills is one asset.
  const skillsRoot = `${home}/.claude/skills`;
  for (const entry of await safeList(fs, skillsRoot)) {
    if (!entry.isDirectory) continue;
    const dir = `${skillsRoot}/${entry.name}`;
    const files = await collectFiles(fs, dir);
    if (files.length === 0) continue;
    const skillMd = files.find((f) => f.endsWith("/SKILL.md"));
    assets.push({
      id: `skills/${entry.name}`,
      source: "skills",
      kind: "skill",
      name: entry.name,
      description: skillMd ? await describeFile(fs, skillMd) : "",
      path: dir,
      files,
    });
  }

  // Commands & agents: each markdown file is one asset (namespaced by subdir).
  for (const source of ["commands", "agents"] as const) {
    const root = `${home}/.claude/${source}`;
    const mdFiles = (await collectFiles(fs, root)).filter((f) => f.endsWith(".md"));
    for (const file of mdFiles) {
      const name = file.slice(root.length + 1).replace(/\.md$/, "");
      assets.push({
        id: `${source}/${name}`,
        source,
        kind: "prompt",
        name,
        description: await describeFile(fs, file),
        path: file,
        files: [file],
      });
    }
  }

  return assets;
}

// ─── ZIP packing (stored, no compression — no deps) ──────────────────────────

const CRC_TABLE = (() => {
  const t = new Uint32Array(256);
  for (let n = 0; n < 256; n++) {
    let c = n;
    for (let k = 0; k < 8; k++) c = c & 1 ? 0xedb88320 ^ (c >>> 1) : c >>> 1;
    t[n] = c >>> 0;
  }
  return t;
})();

/** Standard CRC-32 (poly 0xEDB88320) over `data`. */
export function crc32(data: Uint8Array): number {
  let c = 0xffffffff;
  for (let i = 0; i < data.length; i++) {
    c = CRC_TABLE[(c ^ data[i]) & 0xff] ^ (c >>> 8);
  }
  return (c ^ 0xffffffff) >>> 0;
}

export interface ZipEntry {
  name: string;
  data: Uint8Array;
}

function toDosDateTime(date: Date): { dosTime: number; dosDate: number } {
  const year = Math.max(date.getFullYear(), 1980);
  return {
    dosTime:
      (date.getHours() << 11) | (date.getMinutes() << 5) | (date.getSeconds() >> 1),
    dosDate: ((year - 1980) << 9) | ((date.getMonth() + 1) << 5) | date.getDate(),
  };
}

/**
 * Build a ZIP archive with stored (uncompressed) entries. Hand-rolled to
 * avoid a dependency; skills/commands are small text files, so compression
 * buys little.
 */
export function createZip(entries: ZipEntry[], date: Date = new Date()): Uint8Array {
  const enc = new TextEncoder();
  const { dosTime, dosDate } = toDosDateTime(date);
  const localParts: Uint8Array[] = [];
  const centralParts: Uint8Array[] = [];
  let offset = 0;

  for (const entry of entries) {
    const nameBytes = enc.encode(entry.name);
    const crc = crc32(entry.data);
    const size = entry.data.length;

    // Local file header
    const local = new Uint8Array(30 + nameBytes.length);
    const lv = new DataView(local.buffer);
    lv.setUint32(0, 0x04034b50, true); // signature
    lv.setUint16(4, 20, true); // version needed
    lv.setUint16(6, 0x0800, true); // UTF-8 names
    lv.setUint16(8, 0, true); // method: stored
    lv.setUint16(10, dosTime, true);
    lv.setUint16(12, dosDate, true);
    lv.setUint32(14, crc, true);
    lv.setUint32(18, size, true); // compressed
    lv.setUint32(22, size, true); // uncompressed
    lv.setUint16(26, nameBytes.length, true);
    lv.setUint16(28, 0, true); // extra length
    local.set(nameBytes, 30);
    localParts.push(local, entry.data);

    // Central directory header
    const central = new Uint8Array(46 + nameBytes.length);
    const cv = new DataView(central.buffer);
    cv.setUint32(0, 0x02014b50, true); // signature
    cv.setUint16(4, 20, true); // version made by
    cv.setUint16(6, 20, true); // version needed
    cv.setUint16(8, 0x0800, true); // UTF-8 names
    cv.setUint16(10, 0, true); // method: stored
    cv.setUint16(12, dosTime, true);
    cv.setUint16(14, dosDate, true);
    cv.setUint32(16, crc, true);
    cv.setUint32(20, size, true);
    cv.setUint32(24, size, true);
    cv.setUint16(28, nameBytes.length, true);
    // extra/comment/disk/internal-attr fields (30..38) stay 0
    cv.setUint32(38, 0, true); // external attributes
    cv.setUint32(42, offset, true); // local header offset
    central.set(nameBytes, 46);
    centralParts.push(central);

    offset += local.length + size;
  }

  const cdSize = centralParts.reduce((s, p) => s + p.length, 0);
  const eocd = new Uint8Array(22);
  const ev = new DataView(eocd.buffer);
  ev.setUint32(0, 0x06054b50, true); // EOCD signature
  ev.setUint16(8, entries.length, true); // entries on this disk
  ev.setUint16(10, entries.length, true); // total entries
  ev.setUint32(12, cdSize, true);
  ev.setUint32(16, offset, true); // central directory offset

  const out = new Uint8Array(offset + cdSize + 22);
  let pos = 0;
  for (const part of [...localParts, ...centralParts, eocd]) {
    out.set(part, pos);
    pos += part.length;
  }
  return out;
}

/**
 * Pack a local asset's files into a ZIP for upload. Skill entries are named
 * relative to the skill directory ("SKILL.md", "scripts/run.sh"); single-file
 * commands/agents use their basename.
 */
export async function buildAssetZip(
  asset: LocalAsset,
  readBinary: (path: string) => Promise<Uint8Array>,
): Promise<Uint8Array> {
  const baseDir =
    asset.source === "skills" ? asset.path : asset.path.slice(0, asset.path.lastIndexOf("/"));
  const entries: ZipEntry[] = [];
  for (const file of asset.files) {
    const name = file.startsWith(`${baseDir}/`)
      ? file.slice(baseDir.length + 1)
      : file.split("/").pop() ?? file;
    entries.push({ name, data: await readBinary(file) });
  }
  return createZip(entries);
}
