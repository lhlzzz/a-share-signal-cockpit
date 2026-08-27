import fs from "node:fs";
import path from "node:path";
import { execFileSync } from "node:child_process";

const root = process.cwd();
const ua = path.join(root, ".understand-anything");
const intermediate = path.join(ua, "intermediate");
const graph = JSON.parse(
  fs.readFileSync(path.join(intermediate, "assembled-graph.json"), "utf8"),
);
const fileTypes = new Set([
  "file",
  "config",
  "document",
  "service",
  "pipeline",
  "table",
  "schema",
  "resource",
  "endpoint",
]);
const fileNodes = graph.nodes.filter((n) => fileTypes.has(n.type));
const layerMap = new Map();
const definitions = [
  ["layer:入口与编排", "入口与编排层", "负责扫描、调度和驱动研究交易流水线。", (n) =>
    /runner|scheduler|daily_pipeline|main|app|run/i.test(n.filePath ?? "")],
  ["layer:数据与持久化", "数据与持久化层", "负责数据库访问、数据修复、回测数据与结果持久化。", (n) =>
    /db|database|backtest|data|schema|migration|persistence|recorder|filler/i.test(n.filePath ?? "") ||
    ["table", "schema"].includes(n.type)],
  ["layer:研究与决策", "研究与决策层", "负责特征、评分、资格判断、组合决策与研究评估。", (n) =>
    /forward|scoring|portfolio|research|horizon|eligibility|feature|alpha|decision|regime|ranking/i.test(n.filePath ?? "")],
  ["layer:扫描与外部数据", "扫描与外部数据层", "负责市场扫描、外部数据接入和证据采集。", (n) =>
    /scanner|eastmoney|integration|source|evidence|snapshot/i.test(n.filePath ?? "")],
  ["layer:接口与基础设施", "接口与基础设施层", "负责 API、配置、运行环境、脚本和部署支持。", (n) =>
    ["config", "service", "pipeline", "resource", "endpoint"].includes(n.type) ||
    /api|scripts|\.claude|\.github|docker|deploy|config|settings|\.sql$/i.test(n.filePath ?? "")],
  ["layer:文档与测试", "文档与测试层", "负责项目说明、运行规则、技能资料和回归验证。", (n) =>
    n.type === "document" || /test|tests|spec/i.test(n.filePath ?? "")],
  ["layer:核心模块", "核心模块层", "承载未归入专项层的通用业务模块。", () => true],
];
for (const [id, name, description] of definitions) layerMap.set(id, { id, name, description, nodeIds: [] });
for (const node of fileNodes) {
  const def = definitions.find(([, , , predicate]) => predicate(node));
  layerMap.get(def[0]).nodeIds.push(node.id);
}
const layers = [...layerMap.values()].filter((layer) => layer.nodeIds.length > 0);

const nodeIds = new Set(graph.nodes.map((n) => n.id));
const byPath = (pattern) =>
  fileNodes
    .filter((n) => pattern.test(n.filePath ?? ""))
    .slice(0, 12)
    .map((n) => n.id);
const first = (patterns) => {
  for (const p of patterns) {
    const found = fileNodes.find((n) => p.test(n.filePath ?? ""));
    if (found) return found.id;
  }
  return fileNodes[0]?.id;
};
const tour = [
  {
    order: 1,
    title: "项目概览与规则",
    description: "先阅读项目说明和工作规则，了解 xiaogu 的目标、数据流和 PAPER_PICK 边界。",
    nodeIds: byPath(/README|AGENTS|CLAUDE|constitution/i),
  },
  {
    order: 2,
    title: "扫描与市场数据入口",
    description: "从扫描器和快照模块开始，理解市场数据如何采集、规范化并形成候选输入。",
    nodeIds: byPath(/scanner|snapshot|eastmoney/i),
  },
  {
    order: 3,
    title: "Runner 与决策编排",
    description: "跟随 runner 和 scheduler，理解候选如何经过资格判断、评分与最终决策。",
    nodeIds: byPath(/runner|scheduler|eligibility|scoring/i),
  },
  {
    order: 4,
    title: "研究特征与组合选择",
    description: "查看特征、研究上下文和组合决策模块，理解信号如何转化为可解释的选择结果。",
    nodeIds: byPath(/feature|research|portfolio|alpha|ranking|regime/i),
  },
  {
    order: 5,
    title: "记录、回填与收益评估",
    description: "检查 recorder、filler 和回测评估路径，理解 PAPER_PICK 记录如何与后续收益验证闭环。",
    nodeIds: byPath(/recorder|filler|backtest|horizon|return/i),
  },
  {
    order: 6,
    title: "数据库与 API",
    description: "从数据库初始化、持久化和 API 文件了解状态如何保存，以及外部查询如何读取结果。",
    nodeIds: byPath(/db|database|api|sql|schema/i),
  },
  {
    order: 7,
    title: "测试与运行保障",
    description: "最后查看测试和健康检查脚本，确认关键链路的回归验证与运行诊断方式。",
    nodeIds: byPath(/test|health|diagnostic|check/i),
  },
].map((step) => ({
  ...step,
  nodeIds: step.nodeIds.filter((id) => nodeIds.has(id)),
}));

graph.layers = layers;
graph.tour = tour;
graph.project = {
  ...graph.project,
  name: "xiaogu",
  languages: JSON.parse(
    fs.readFileSync(path.join(intermediate, "scan-result.json"), "utf8"),
  ).languages,
  frameworks: [],
  description:
    "A-share autonomous research and paper trading platform that scans market data, evaluates candidates, records PAPER_PICK decisions, and backfills returns.",
  analyzedAt: new Date().toISOString(),
  gitCommitHash: execFileSync("git", ["rev-parse", "HEAD"], { cwd: root, encoding: "utf8" })
    .trim(),
};
fs.writeFileSync(
  path.join(intermediate, "assembled-graph.json"),
  JSON.stringify(graph, null, 2),
);
fs.writeFileSync(path.join(intermediate, "layers.json"), JSON.stringify(layers, null, 2));
fs.writeFileSync(path.join(intermediate, "tour.json"), JSON.stringify(tour, null, 2));
console.log(`layers=${layers.length} tourSteps=${tour.length}`);
