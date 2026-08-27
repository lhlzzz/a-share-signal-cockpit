#!/usr/bin/env node
import fs from "node:fs";
import path from "node:path";
import { execFileSync } from "node:child_process";

const workspace = "/workspace/hermes-workspaces";
const outputDir = path.join(workspace, ".understand-anything");
const graphPath = path.join(outputDir, "knowledge-graph.json");
const metaPath = path.join(outputDir, "meta.json");
const configPath = path.join(outputDir, "config.json");

const repoDirs = fs.readdirSync(workspace, { withFileTypes: true })
  .filter((entry) => entry.isDirectory())
  .map((entry) => path.join(workspace, entry.name))
  .filter((repoPath) => fs.existsSync(path.join(repoPath, ".git")))
  .sort();

const nodeMap = new Map();
const edgeMap = new Map();
const repoStats = [];

function runGit(repoPath, args) {
  return execFileSync("git", ["-C", repoPath, ...args], { encoding: "utf8" });
}

function normalizeRelative(filePath) {
  return filePath.replaceAll("\\", "/").replace(/^\.\//, "");
}

function classifyFile(filePath) {
  const lower = filePath.toLowerCase();
  if (/\.(md|mdx|rst|txt)$/.test(lower) || lower.startsWith("docs/")) return "document";
  if (
    /(^|\/)(\.env|.*\.ya?ml|.*\.json|.*\.toml|.*\.ini|.*\.cfg|.*\.conf|dockerfile|makefile|.*\.sql)$/.test(lower) ||
    ["package.json", "pyproject.toml", "requirements.txt", "go.mod", "cargo.toml"].includes(lower)
  ) return "config";
  return "file";
}

function statusLabel(indexCode, worktreeCode) {
  if (indexCode === "?" || worktreeCode === "?") return "untracked";
  if (indexCode === "D" || worktreeCode === "D") return "deleted";
  if (indexCode !== " " || worktreeCode !== " ") return "modified";
  return "tracked";
}

function parseStatus(repoPath) {
  const status = new Map();
  const raw = runGit(repoPath, ["status", "--porcelain=v1", "-z"]);
  let cursor = 0;
  while (cursor < raw.length) {
    const recordEnd = raw.indexOf("\0", cursor);
    if (recordEnd < 0) break;
    const record = raw.slice(cursor, recordEnd);
    cursor = recordEnd + 1;
    if (record.length < 4) continue;
    const indexCode = record[0];
    const worktreeCode = record[1];
    let filePath = record.slice(3);
    if (indexCode === "R" || indexCode === "C") {
      const renamedToEnd = raw.indexOf("\0", cursor);
      if (renamedToEnd < 0) break;
      filePath = raw.slice(cursor, renamedToEnd);
      cursor = renamedToEnd + 1;
    }
    status.set(normalizeRelative(filePath), { indexCode, worktreeCode, status: statusLabel(indexCode, worktreeCode) });
  }
  return status;
}

function addNode(node) {
  if (!node || typeof node.id !== "string") return;
  nodeMap.set(node.id, {
    ...node,
    tags: Array.isArray(node.tags) ? [...new Set(node.tags)] : [],
    complexity: ["simple", "moderate", "complex"].includes(node.complexity) ? node.complexity : "simple",
  });
}

function addEdge(edge) {
  if (!edge || typeof edge.source !== "string" || typeof edge.target !== "string") return;
  const key = `${edge.source}\0${edge.target}\0${edge.type}`;
  edgeMap.set(key, {
    source: edge.source,
    target: edge.target,
    type: edge.type,
    direction: edge.direction || "forward",
    description: edge.description,
    weight: typeof edge.weight === "number" ? edge.weight : 1,
  });
}

for (const repoPath of repoDirs) {
  const repoName = path.basename(repoPath);
  const statuses = parseStatus(repoPath);
  const currentFiles = new Set(
    runGit(repoPath, ["ls-files", "-co", "--exclude-standard"])
      .split("\n")
      .filter(Boolean)
      .map(normalizeRelative),
  );
  const deletedFiles = new Set(
    runGit(repoPath, ["ls-files", "--deleted"])
      .split("\n")
      .filter(Boolean)
      .map(normalizeRelative),
  );
  for (const filePath of deletedFiles) currentFiles.add(filePath);

  const oldGraphPath = path.join(repoPath, ".understand-anything", "knowledge-graph.json");
  const oldGraph = fs.existsSync(oldGraphPath) ? JSON.parse(fs.readFileSync(oldGraphPath, "utf8")) : null;
  const idMap = new Map();
  const fileNodeByPath = new Map();

  if (oldGraph) {
    for (const node of oldGraph.nodes || []) {
      const type = node.id.includes(":") ? node.id.slice(0, node.id.indexOf(":")) : node.type || "node";
      const suffix = node.id.includes(":") ? node.id.slice(node.id.indexOf(":") + 1) : node.id;
      const newId = `${type}:${repoName}/${suffix}`;
      idMap.set(node.id, newId);
      const copied = { ...node, id: newId, tags: [...new Set([...(node.tags || []), `repo:${repoName}`])] };
      if (copied.filePath) {
        const relativePath = path.isAbsolute(copied.filePath)
          ? normalizeRelative(path.relative(repoPath, copied.filePath))
          : normalizeRelative(copied.filePath);
        copied.filePath = `${repoName}/${relativePath}`;
        const repoStatus = statuses.get(relativePath) || { status: deletedFiles.has(relativePath) ? "deleted" : "tracked" };
        if (["file", "config", "document"].includes(copied.type)) {
          copied.tags = [...new Set([...copied.tags, `status:${repoStatus.status}`])];
          copied.tags = [...new Set([...copied.tags, "workspace-file"])]
          copied.summary = `${repoName}/${relativePath} [${repoStatus.status}]`;
          if (copied.type === "file") fileNodeByPath.set(relativePath, copied);
        }
      }
      addNode(copied);
    }
    for (const edge of oldGraph.edges || []) {
      const source = idMap.get(edge.source);
      const target = idMap.get(edge.target);
      if (source && target) addEdge({ ...edge, source, target });
    }
  }

  for (const relativePath of currentFiles) {
    const repoStatus = statuses.get(relativePath) || { status: deletedFiles.has(relativePath) ? "deleted" : "tracked" };
    const existing = fileNodeByPath.get(relativePath);
    if (existing) {
      existing.tags = [...new Set([...(existing.tags || []), `status:${repoStatus.status}`])];
      existing.summary = `${repoName}/${relativePath} [${repoStatus.status}]`;
      nodeMap.set(existing.id, existing);
      continue;
    }
    const type = classifyFile(relativePath);
    const id = `${type}:${repoName}/${relativePath}`;
    const node = {
      id,
      type,
      name: path.basename(relativePath),
      filePath: `${repoName}/${relativePath}`,
      summary: `${repoName}/${relativePath} [${repoStatus.status}]`,
      tags: [`repo:${repoName}`, `status:${repoStatus.status}`, "workspace-file"],
      complexity: "simple",
    };
    addNode(node);
    fileNodeByPath.set(relativePath, node);
  }

  const repoModuleId = `module:repo/${repoName}`;
  addNode({
    id: repoModuleId,
    type: "module",
    name: repoName,
    summary: `${repoName} repository: ${currentFiles.size} files, ${[...statuses.values()].filter((x) => x.status !== "tracked").length} changed files`,
    tags: [`repo:${repoName}`, "repository"],
    complexity: "moderate",
  });
  for (const fileNode of fileNodeByPath.values()) {
    addEdge({ source: repoModuleId, target: fileNode.id, type: "contains", direction: "forward", weight: 1 });
  }

  const statusCounts = { tracked: 0, modified: 0, deleted: 0, untracked: 0 };
  for (const relativePath of currentFiles) {
    const value = statuses.get(relativePath) || { status: deletedFiles.has(relativePath) ? "deleted" : "tracked" };
    statusCounts[value.status] = (statusCounts[value.status] || 0) + 1;
  }
  repoStats.push({ repoName, files: currentFiles.size, ...statusCounts, graphNodes: oldGraph?.nodes?.length || 0 });
}

const nodes = [...nodeMap.values()].sort((a, b) => a.id.localeCompare(b.id));
const nodeIds = new Set(nodes.map((node) => node.id));
const edges = [...edgeMap.values()]
  .filter((edge) => nodeIds.has(edge.source) && nodeIds.has(edge.target))
  .sort((a, b) => `${a.source}\0${a.target}\0${a.type}`.localeCompare(`${b.source}\0${b.target}\0${b.type}`));

const layers = repoStats.map(({ repoName, files, modified, deleted, untracked }) => ({
  id: `layer:repo-${repoName}`,
  name: repoName,
  description: `${repoName} repository: ${files} files (${modified} modified, ${deleted} deleted, ${untracked} untracked).`,
  nodeIds: nodes.filter((node) => node.tags?.includes(`repo:${repoName}`)).map((node) => node.id),
}));
const tour = [
  {
    order: 1,
    title: "Workspace Overview",
    description: "Start with the repository modules, then use the status tags to find modified, deleted, and untracked files.",
    nodeIds: repoStats.map(({ repoName }) => `module:repo/${repoName}`),
  },
  ...repoStats.map(({ repoName }, index) => ({
    order: index + 2,
    title: `${repoName} repository`,
    description: `Inspect the ${repoName} file tree and its available code relationships.`,
    nodeIds: [`module:repo/${repoName}`],
  })),
];

const totalFiles = repoStats.reduce((sum, stats) => sum + stats.files, 0);
const graph = {
  version: "1.0.0",
  kind: "codebase",
  project: {
    name: "hermes-workspaces",
    languages: [...new Set(nodes.map((node) => node.languageNotes).filter(Boolean))].sort(),
    frameworks: [],
    description: "Interactive multi-repository view of all Git repositories under /workspace/hermes-workspaces. File nodes carry repository and Git working-tree status labels.",
    analyzedAt: new Date().toISOString(),
    gitCommitHash: "multi-repository-working-tree",
  },
  nodes,
  edges,
  layers,
  tour,
};

fs.mkdirSync(outputDir, { recursive: true });
fs.writeFileSync(graphPath, `${JSON.stringify(graph, null, 2)}\n`);
fs.writeFileSync(metaPath, `${JSON.stringify({
  lastAnalyzedAt: graph.project.analyzedAt,
  gitCommitHash: graph.project.gitCommitHash,
  version: graph.version,
  analyzedFiles: totalFiles,
}, null, 2)}\n`);
fs.writeFileSync(configPath, `${JSON.stringify({ autoUpdate: false, outputLanguage: "zh" }, null, 2)}\n`);

console.log(JSON.stringify({
  repositories: repoStats,
  totalFiles,
  nodes: nodes.length,
  edges: edges.length,
  layers: layers.length,
  tour: tour.length,
  graphPath,
}, null, 2));
