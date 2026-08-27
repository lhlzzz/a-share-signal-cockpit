#!/usr/bin/env node
import fs from "node:fs";
import path from "node:path";
import { execFileSync } from "node:child_process";
import {
  GraphBuilder,
  detectLayers,
  generateHeuristicTour,
} from "/root/.claude/plugins/cache/understand-anything/understand-anything/2.7.6/packages/core/dist/index.js";

const root = process.cwd();
const understandDir = path.join(root, ".understand-anything");
const scan = JSON.parse(fs.readFileSync(path.join(understandDir, "tmp/ua-scan-files-latest.json"), "utf8"));
const importOutput = JSON.parse(fs.readFileSync(path.join(understandDir, "tmp/ua-import-map-output-latest.json"), "utf8"));
const structure = JSON.parse(fs.readFileSync(path.join(understandDir, "tmp/ua-structure-output-latest.json"), "utf8"));
const currentCommit = execFileSync("git", ["rev-parse", "HEAD"], { encoding: "utf8" }).trim();
const statusOutput = execFileSync("git", ["status", "--porcelain=v1", "-z"], { encoding: "utf8" });

function normalize(filePath) {
  return filePath.replaceAll("\\", "/").replace(/^\.\//, "");
}

function parseStatuses() {
  const statuses = new Map();
  let cursor = 0;
  while (cursor < statusOutput.length) {
    const end = statusOutput.indexOf("\0", cursor);
    if (end < 0) break;
    const record = statusOutput.slice(cursor, end);
    cursor = end + 1;
    if (record.length < 4) continue;
    const index = record[0];
    const worktree = record[1];
    let filePath = record.slice(3);
    if (index === "R" || index === "C") {
      const renamedEnd = statusOutput.indexOf("\0", cursor);
      if (renamedEnd < 0) break;
      filePath = statusOutput.slice(cursor, renamedEnd);
      cursor = renamedEnd + 1;
    }
    const status = index === "?" || worktree === "?"
      ? "untracked"
      : index === "D" || worktree === "D"
        ? "deleted"
        : index !== " " || worktree !== " "
          ? "modified"
          : "tracked";
    statuses.set(normalize(filePath), status);
  }
  return statuses;
}

function complexity(result) {
  if (result.nonEmptyLines > 200 || (result.metrics?.functionCount ?? 0) > 20 || (result.metrics?.classCount ?? 0) > 5) return "complex";
  if (result.nonEmptyLines > 50 || (result.metrics?.functionCount ?? 0) > 5 || (result.metrics?.classCount ?? 0) > 1) return "moderate";
  return "simple";
}

function nodeType(file) {
  if (file.fileCategory === "docs") return "document";
  if (file.fileCategory === "config") return "config";
  if (file.fileCategory === "infra") {
    if (file.path.includes(".github/workflows") || file.path.includes(".gitlab-ci") || file.path.includes("Jenkinsfile")) return "pipeline";
    if (/Dockerfile|docker-compose|compose\.ya?ml/i.test(file.path)) return "service";
    return "resource";
  }
  if (file.fileCategory === "data") return "table";
  return "file";
}

function fileSummary(file, result, status) {
  const metrics = result.metrics || {};
  const parts = [`当前文件：${file.path}`, `状态：${status}`];
  if (metrics.functionCount) parts.push(`函数 ${metrics.functionCount}`);
  if (metrics.classCount) parts.push(`类 ${metrics.classCount}`);
  if (metrics.importCount) parts.push(`内部导入 ${metrics.importCount}`);
  if (metrics.definitionCount) parts.push(`定义 ${metrics.definitionCount}`);
  return parts.join("；") + "。";
}

function tags(file, result, status) {
  const out = ["xiaogu", `status:${status}`, file.fileCategory];
  if (file.path.includes("test") || file.path.includes("pytest")) out.push("test");
  if (file.path.includes("api") || file.path.includes("route")) out.push("api");
  if (file.path.includes("db") || file.path.includes("database") || file.fileCategory === "data") out.push("database");
  if (file.path.includes("runner") || file.path.includes("scheduler") || file.path.includes("pipeline")) out.push("entry-point");
  if ((result.metrics?.functionCount ?? 0) > 0) out.push("functions");
  return [...new Set(out)];
}

function toAnalysis(result) {
  return {
    functions: (result.functions || []).map((fn) => ({ name: fn.name, lineRange: [fn.startLine, fn.endLine], params: fn.params || [] })),
    classes: (result.classes || []).map((cls) => ({ name: cls.name, lineRange: [cls.startLine, cls.endLine], methods: cls.methods || [], properties: cls.properties || [] })),
    imports: [],
    exports: (result.exports || []).map((item) => ({ name: item.name, lineNumber: item.line, isDefault: item.isDefault })),
    definitions: (result.definitions || []).map((item) => ({ name: item.name, kind: item.kind, fields: item.fields || [], lineRange: [item.startLine, item.endLine] })),
    services: (result.services || []).map((item) => ({ name: item.name, image: item.image, ports: item.ports || [], lineRange: [item.startLine || 1, item.endLine || item.startLine || 1] })),
    endpoints: (result.endpoints || []).map((item) => ({ method: item.method, path: item.path, lineRange: [item.startLine, item.endLine] })),
    steps: (result.steps || []).map((item) => ({ name: item.name, lineRange: [item.startLine, item.endLine] })),
    resources: (result.resources || []).map((item) => ({ name: item.name, kind: item.kind, lineRange: [item.startLine, item.endLine] })),
  };
}

const resultByPath = new Map(structure.results.map((result) => [normalize(result.path), result]));
const statuses = parseStatuses();
const builder = new GraphBuilder("xiaogu", `${currentCommit}+working-tree`);

for (const file of scan.files) {
  const filePath = normalize(file.path);
  const result = resultByPath.get(filePath) || { path: filePath, nonEmptyLines: 0, metrics: {} };
  const status = statuses.get(filePath) || "tracked";
  const meta = {
    summary: fileSummary(file, result, status),
    fileSummary: fileSummary(file, result, status),
    summaries: Object.fromEntries([
      ...(result.functions || []).map((fn) => [fn.name, `函数 ${fn.name}，位于 ${filePath}。`]),
      ...(result.classes || []).map((cls) => [cls.name, `类 ${cls.name}，位于 ${filePath}。`]),
    ]),
    tags: tags(file, result, status),
    complexity: complexity(result),
    nodeType: nodeType(file),
  };
  if (["config", "docs", "infra", "data"].includes(file.fileCategory)) {
    builder.addNonCodeFileWithAnalysis(filePath, { ...meta, ...toAnalysis(result) });
  } else {
    builder.addFileWithAnalysis(filePath, toAnalysis(result), meta);
  }
}

for (const [from, targets] of Object.entries(importOutput.importMap || {})) {
  for (const target of targets) builder.addImportEdge(normalize(from), normalize(target));
}

for (const result of structure.results) {
  const filePath = normalize(result.path);
  const functionNames = new Set((result.functions || []).map((fn) => fn.name));
  const callGraph = result.callGraph || [];
  for (const call of callGraph) {
    if (functionNames.has(call.caller) && functionNames.has(call.callee)) {
      builder.addCallEdge(filePath, call.caller, filePath, call.callee);
    }
  }
}

const graph = builder.build();
const nodeById = new Map(graph.nodes.map((node) => [node.id, node]));
for (const node of graph.nodes) {
  if (!node.filePath) continue;
  const relativePath = normalize(node.filePath);
  const file = scan.files.find((item) => normalize(item.path) === relativePath);
  if (!file) continue;
  const result = resultByPath.get(relativePath) || { nonEmptyLines: 0, metrics: {} };
  const status = statuses.get(relativePath) || "tracked";
  node.tags = [...new Set([...(node.tags || []), ...tags(file, result, status)])];
}

const layerRules = [
  ["入口与编排", "负责扫描、调度和驱动运行链路。", (p) => /runner|scheduler|pipeline|daily_pipeline|start_api/.test(p)],
  ["数据与持久化", "负责数据库、记录、回填和结果存储。", (p) => /db|database|recorder|filler|persistence|sql|snapshot|bundle_io/.test(p)],
  ["研究与决策", "负责特征、评分、资格判断、组合决策和评估。", (p) => /alpha|feature|eligibility|portfolio|decision|research|evaluation|backtest|ranking|score/.test(p)],
  ["扫描与外部数据", "负责市场扫描、外部数据和集成适配。", (p) => /scrapy|scanner|integration|adapter|eastmoney|external/.test(p)],
  ["测试", "负责回归测试和行为验证。", (p) => /(^|\/)tests?(\/|_)|test_|\.test\./.test(p)],
  ["配置与文档", "负责配置、规则、说明和运行记录。", (p) => /README|AGENTS|CLAUDE|\.json$|\.yaml$|\.yml$|\.md$|\.txt$|config|rule_freeze/.test(p)],
];
const layerNodes = new Map(layerRules.map(([name]) => [name, []]));
layerNodes.set("核心组件", []);
for (const node of graph.nodes) {
  const p = node.filePath || "";
  const match = layerRules.find(([, , predicate]) => predicate(p));
  const layerName = match ? match[0] : "核心组件";
  layerNodes.get(layerName).push(node.id);
}
graph.layers = [...layerNodes.entries()]
  .filter(([, nodeIds]) => nodeIds.length > 0)
  .map(([name, nodeIds]) => ({ id: `layer:${name}`, name, description: layerRules.find(([n]) => n === name)?.[1] || "项目核心文件和结构节点。", nodeIds }));
graph.tour = [
  { order: 1, title: "项目入口与编排", description: "从 runner、scheduler 和 pipeline 文件开始，了解当前工作树的主运行路径。", nodeIds: graph.layers.find((x) => x.name === "入口与编排")?.nodeIds || [] },
  { order: 2, title: "扫描与外部数据", description: "查看扫描器、外部数据接入和适配器，理解输入如何进入系统。", nodeIds: graph.layers.find((x) => x.name === "扫描与外部数据")?.nodeIds || [] },
  { order: 3, title: "研究与决策", description: "跟随特征、资格判断、alpha 和组合决策模块，理解候选如何形成决策。", nodeIds: graph.layers.find((x) => x.name === "研究与决策")?.nodeIds || [] },
  { order: 4, title: "数据与持久化", description: "检查数据库、记录、回填和快照文件，理解结果如何保存和验证。", nodeIds: graph.layers.find((x) => x.name === "数据与持久化")?.nodeIds || [] },
  { order: 5, title: "测试与当前状态", description: "使用测试、配置和状态标签检查当前工作树中的修改、删除与未跟踪文件。", nodeIds: [...(graph.layers.find((x) => x.name === "测试")?.nodeIds || []), ...(graph.layers.find((x) => x.name === "配置与文档")?.nodeIds || [])] },
].filter((step) => step.nodeIds.length > 0);

graph.project.description = "xiaogu 当前工作树的实时 A 股研究与纸面交易系统图谱，包含文件、函数、类、配置、文档、数据库定义及内部依赖关系。";
graph.project.languages = [...new Set(scan.files.map((file) => file.language))].sort((a, b) => a.localeCompare(b));
graph.project.frameworks = [];
graph.project.analyzedAt = new Date().toISOString();

const ids = new Set(graph.nodes.map((node) => node.id));
graph.edges = graph.edges.filter((edge) => ids.has(edge.source) && ids.has(edge.target));
fs.writeFileSync(path.join(understandDir, "knowledge-graph.json"), `${JSON.stringify(graph, null, 2)}\n`);
fs.writeFileSync(path.join(understandDir, "meta.json"), `${JSON.stringify({ lastAnalyzedAt: graph.project.analyzedAt, gitCommitHash: graph.project.gitCommitHash, version: "1.0.0", analyzedFiles: scan.totalFiles }, null, 2)}\n`);

console.log(JSON.stringify({
  analyzedFiles: scan.totalFiles,
  nodes: graph.nodes.length,
  edges: graph.edges.length,
  layers: graph.layers.length,
  tour: graph.tour.length,
  commit: graph.project.gitCommitHash,
  analyzedAt: graph.project.analyzedAt,
}, null, 2));
