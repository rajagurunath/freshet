import { describe, it, expect } from "vitest";
import {
  parseAssetDescription,
  crc32,
  createZip,
  scanLocalAssets,
  buildAssetZip,
  type AssetFs,
  type DirEntry,
  type LocalAsset,
} from "./assetScan";

// ─── test helpers ────────────────────────────────────────────────────────────

const enc = new TextEncoder();

/** Minimal fake filesystem for scanLocalAssets. */
function makeFakeFs(
  home: string,
  dirs: Record<string, DirEntry[]>,
  texts: Record<string, string>,
): AssetFs {
  return {
    homeDir: async () => home,
    listDir: async (path: string) => dirs[path] ?? [],
    readText: async (path: string) => {
      if (!(path in texts)) throw new Error(`no such file: ${path}`);
      return texts[path];
    },
  };
}

/** Parse local-file-header entry names out of a stored ZIP (test-only reader). */
function readZipEntryNames(bytes: Uint8Array): string[] {
  const names: string[] = [];
  const view = new DataView(bytes.buffer, bytes.byteOffset, bytes.byteLength);
  let pos = 0;
  while (pos + 30 <= bytes.length && view.getUint32(pos, true) === 0x04034b50) {
    const nameLen = view.getUint16(pos + 26, true);
    const extraLen = view.getUint16(pos + 28, true);
    const dataLen = view.getUint32(pos + 18, true); // stored: comp == uncomp
    names.push(new TextDecoder().decode(bytes.slice(pos + 30, pos + 30 + nameLen)));
    pos += 30 + nameLen + extraLen + dataLen;
  }
  return names;
}

// ─── parseAssetDescription ───────────────────────────────────────────────────

describe("parseAssetDescription", () => {
  it("extracts the description field from YAML frontmatter", () => {
    const text = "---\nname: my-skill\ndescription: Does things well\n---\n# Title\nBody";
    expect(parseAssetDescription(text)).toBe("Does things well");
  });

  it("strips surrounding quotes from frontmatter values", () => {
    const text = '---\ndescription: "Browse the web"\n---\nbody';
    expect(parseAssetDescription(text)).toBe("Browse the web");
  });

  it("falls back to the first prose line when there is no frontmatter", () => {
    expect(parseAssetDescription("Deploy the app to production.\nMore detail.")).toBe(
      "Deploy the app to production.",
    );
  });

  it("skips markdown headings when falling back to prose", () => {
    expect(parseAssetDescription("# Title\n\nFirst real line.")).toBe("First real line.");
  });

  it("returns empty string for empty content", () => {
    expect(parseAssetDescription("")).toBe("");
    expect(parseAssetDescription("   \n\n")).toBe("");
  });

  it("truncates very long descriptions to 200 chars", () => {
    const long = "x".repeat(500);
    expect(parseAssetDescription(long)).toHaveLength(200);
  });
});

// ─── crc32 / createZip ───────────────────────────────────────────────────────

describe("crc32", () => {
  it("matches the standard test vector", () => {
    expect(crc32(enc.encode("123456789"))).toBe(0xcbf43926);
  });

  it("returns 0 for empty input", () => {
    expect(crc32(new Uint8Array())).toBe(0);
  });
});

describe("createZip", () => {
  it("produces a valid stored ZIP with correct signatures and counts", () => {
    const data = enc.encode("hello");
    const zip = createZip([{ name: "SKILL.md", data }]);

    const view = new DataView(zip.buffer, zip.byteOffset, zip.byteLength);
    // Local file header signature at start
    expect(view.getUint32(0, true)).toBe(0x04034b50);
    // CRC of the entry payload
    expect(view.getUint32(14, true)).toBe(crc32(data));
    // End-of-central-directory record: last 22 bytes
    const eocd = zip.length - 22;
    expect(view.getUint32(eocd, true)).toBe(0x06054b50);
    expect(view.getUint16(eocd + 10, true)).toBe(1); // total entry count
    // Payload is stored verbatim after the 30-byte header + name
    const nameLen = view.getUint16(26, true);
    expect(new TextDecoder().decode(zip.slice(30 + nameLen, 30 + nameLen + 5))).toBe("hello");
  });

  it("includes every entry name", () => {
    const zip = createZip([
      { name: "SKILL.md", data: enc.encode("a") },
      { name: "scripts/run.sh", data: enc.encode("b") },
    ]);
    expect(readZipEntryNames(zip)).toEqual(["SKILL.md", "scripts/run.sh"]);
  });
});

// ─── scanLocalAssets ─────────────────────────────────────────────────────────

const HOME = "/home/u";
const SKILLS = `${HOME}/.claude/skills`;
const COMMANDS = `${HOME}/.claude/commands`;

function fixtureFs(): AssetFs {
  return makeFakeFs(
    HOME,
    {
      [SKILLS]: [
        { name: "my-skill", isDirectory: true },
        { name: "notes.txt", isDirectory: false }, // loose file → ignored
      ],
      [`${SKILLS}/my-skill`]: [
        { name: "SKILL.md", isDirectory: false },
        { name: "reference.md", isDirectory: false },
        { name: "scripts", isDirectory: true },
      ],
      [`${SKILLS}/my-skill/scripts`]: [{ name: "run.sh", isDirectory: false }],
      [COMMANDS]: [
        { name: "deploy.md", isDirectory: false },
        { name: "gstack", isDirectory: true },
      ],
      [`${COMMANDS}/gstack`]: [{ name: "browse.md", isDirectory: false }],
      // ~/.claude/agents intentionally missing
    },
    {
      [`${SKILLS}/my-skill/SKILL.md`]:
        "---\nname: my-skill\ndescription: Does things well\n---\n# My Skill\nBody",
      [`${COMMANDS}/deploy.md`]: "Deploy the app to production.",
      [`${COMMANDS}/gstack/browse.md`]: '---\ndescription: "Browse the web"\n---\nbody',
    },
  );
}

describe("scanLocalAssets", () => {
  it("finds skills as directory assets with descriptions from SKILL.md", async () => {
    const assets = await scanLocalAssets(fixtureFs());
    const skill = assets.find((a) => a.source === "skills");
    expect(skill).toBeDefined();
    expect(skill!.kind).toBe("skill");
    expect(skill!.name).toBe("my-skill");
    expect(skill!.description).toBe("Does things well");
    expect(skill!.path).toBe(`${SKILLS}/my-skill`);
    expect(skill!.files.sort()).toEqual([
      `${SKILLS}/my-skill/SKILL.md`,
      `${SKILLS}/my-skill/reference.md`,
      `${SKILLS}/my-skill/scripts/run.sh`,
    ]);
  });

  it("finds commands as single-file prompt assets, namespaced by subdirectory", async () => {
    const assets = await scanLocalAssets(fixtureFs());
    const commands = assets.filter((a) => a.source === "commands");
    expect(commands.map((c) => c.name).sort()).toEqual(["deploy", "gstack/browse"]);
    const browse = commands.find((c) => c.name === "gstack/browse")!;
    expect(browse.kind).toBe("prompt");
    expect(browse.description).toBe("Browse the web");
    expect(browse.files).toEqual([`${COMMANDS}/gstack/browse.md`]);
  });

  it("ignores loose files in the skills root and missing roots", async () => {
    const assets = await scanLocalAssets(fixtureFs());
    expect(assets.filter((a) => a.source === "skills")).toHaveLength(1);
    expect(assets.filter((a) => a.source === "agents")).toHaveLength(0);
  });

  it("returns [] when home is unknown (browser mode)", async () => {
    const assets = await scanLocalAssets(makeFakeFs("", {}, {}));
    expect(assets).toEqual([]);
  });
});

// ─── buildAssetZip ───────────────────────────────────────────────────────────

describe("buildAssetZip", () => {
  it("zips a skill directory with paths relative to the skill dir", async () => {
    const asset: LocalAsset = {
      id: "skills/my-skill",
      source: "skills",
      kind: "skill",
      name: "my-skill",
      description: "",
      path: `${SKILLS}/my-skill`,
      files: [`${SKILLS}/my-skill/SKILL.md`, `${SKILLS}/my-skill/scripts/run.sh`],
    };
    const zip = await buildAssetZip(asset, async (p) => enc.encode(`content of ${p}`));
    expect(readZipEntryNames(zip)).toEqual(["SKILL.md", "scripts/run.sh"]);
  });

  it("zips a single-file command using its basename", async () => {
    const asset: LocalAsset = {
      id: "commands/gstack/browse",
      source: "commands",
      kind: "prompt",
      name: "gstack/browse",
      description: "",
      path: `${COMMANDS}/gstack/browse.md`,
      files: [`${COMMANDS}/gstack/browse.md`],
    };
    const zip = await buildAssetZip(asset, async () => enc.encode("body"));
    expect(readZipEntryNames(zip)).toEqual(["browse.md"]);
  });
});
