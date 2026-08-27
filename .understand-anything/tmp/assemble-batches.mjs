import fs from "node:fs";
import path from "node:path";

const root = process.cwd();
const uaDir = path.join(root, ".understand-anything");
const intermediate = path.join(uaDir, "intermediate");
const tmp = path.join(uaDir, "tmp");
const batches = JSON.parse(
  fs.readFileSync(path.join(intermediate, "batches.json"), "utf8"),
).batches;

const CATEGORY_TYPES = {
  code: "file",
  script: "file",
  markup: "file",
  config: "config",
  docs: "document",
  infra: "service",
  data: "table",
};

function basename(filePath) {
  return path.posix.basename(filePath);
}

function complexity(result) {
  if ((result.nonEmptyLines ?? 0) > 200) return "complex";
  if ((result.nonEmptyLines ?? 0) >= 50) return "moderate";
  return "simple";
}

function nodeType(result) {
  const filePath = result.path.toLowerCase();
  if (result.fileCategory === "infra") {
    if (
      filePath.startsWith(".github/workflows/") ||
      filePath.includes("gitlab-ci") ||
      basename(filePath) === "jenkinsfile"
    ) {
      return "pipeline";
    }
    if (filePath.endsWith(".tf") || filePath.endsWith(".tfvars")) return "resource";
  }
  if (result.fileCategory === "data") {
    if (
      filePath.endsWith(".graphql") ||
      filePath.endsWith(".gql") ||
      filePath.endsWith(".proto") ||
      filePath.endsWith(".prisma")
    ) {
      return "schema";
    }
    if (filePath.includes("openapi") || filePath.includes("swagger")) return "endpoint";
  }
  return CATEGORY_TYPES[result.fileCategory] ?? "file";
}

function tagsFor(result, type) {
  const filePath = result.path.toLowerCase();
  const tags = new Set(["xiaogu"]);
  if (filePath.includes("test") || filePath.includes("tests/")) tags.add("test");
  if (filePath.includes("scanner") || filePath.includes("scrapy")) tags.add("scanner");
  if (filePath.includes("runner")) tags.add("runner");
  if (filePath.includes("database") || filePath.includes("_db")) tags.add("database");
  if (filePath.includes("scheduler")) tags.add("scheduler");
  if (filePath.includes("api")) tags.add("api");
  if (filePath.includes("forward")) tags.add("forward-pipeline");
  if (type === "config") tags.add("configuration");
  if (type === "document") tags.add("documentation");
  if (type === "service" || type === "resource") tags.add("infrastructure");
  if (type === "pipeline") tags.add("ci-cd");
  if (type === "table" || type === "schema") tags.add("data-schema");
  if (tags.size < 3) tags.add(result.fileCategory);
  return [...tags];
}

function fileSummary(result, type) {
  const name = basename(result.path);
  const labels = {
    file: "实现项目运行逻辑或辅助脚本。",
    config: "定义项目配置与工具运行参数。",
    document: "记录项目说明、工作流或参考知识。",
    service: "定义运行环境、服务或基础设施资源。",
    pipeline: "定义自动化流水线与执行步骤。",
    resource: "定义基础设施资源配置。",
    table: "定义或维护项目的数据结构。",
    schema: "定义项目使用的数据模式。",
    endpoint: "定义 API 接口模式。",
  };
  return `${name}：${labels[type] ?? labels.file}`;
}

function childSummary(kind, name, filePath) {
  if (kind === "function") return `函数 ${name}，定义于 ${filePath}。`;
  if (kind === "class") return `类 ${name}，定义于 ${filePath}。`;
  return `${kind} ${name}，定义于 ${filePath}。`;
}

function addNode(nodes, node) {
  nodes.push(node);
}

function addEdge(edges, source, target, type, weight) {
  edges.push({ source, target, type, direction: "forward", weight });
}

for (const batch of batches) {
  const raw = JSON.parse(
    fs.readFileSync(
      path.join(tmp, `ua-file-extract-results-${batch.batchIndex}.json`),
      "utf8",
    ),
  );
  const nodes = [];
  const edges = [];
  const nodeIds = new Set();

  for (const result of raw.results) {
    const type = nodeType(result);
    const fileId = `${type}:${result.path}`;
    const resultComplexity = complexity(result);
    nodeIds.add(fileId);
    addNode(nodes, {
      id: fileId,
      type,
      name: basename(result.path),
      filePath: result.path,
      summary: fileSummary(result, type),
      tags: tagsFor(result, type),
      complexity: resultComplexity,
    });

    const significantFunctions = (result.functions ?? []).filter(
      (fn) => (fn.endLine ?? 0) - (fn.startLine ?? 0) + 1 >= 10,
    );
    for (const fn of significantFunctions) {
      const id = `function:${result.path}:${fn.name}`;
      nodeIds.add(id);
      addNode(nodes, {
        id,
        type: "function",
        name: fn.name,
        filePath: result.path,
        lineRange: [fn.startLine, fn.endLine],
        summary: childSummary("function", fn.name, result.path),
        tags: ["function", "xiaogu", result.fileCategory],
        complexity: complexity({
          nonEmptyLines: (fn.endLine ?? 0) - (fn.startLine ?? 0) + 1,
        }),
      });
      addEdge(edges, fileId, id, "contains", 1);
    }

    for (const cls of result.classes ?? []) {
      const lines = (cls.endLine ?? 0) - (cls.startLine ?? 0) + 1;
      if ((cls.methods?.length ?? 0) < 2 && lines < 20) continue;
      const id = `class:${result.path}:${cls.name}`;
      nodeIds.add(id);
      addNode(nodes, {
        id,
        type: "class",
        name: cls.name,
        filePath: result.path,
        lineRange: [cls.startLine, cls.endLine],
        summary: childSummary("class", cls.name, result.path),
        tags: ["class", "xiaogu", result.fileCategory],
        complexity: complexity({ nonEmptyLines: lines }),
      });
      addEdge(edges, fileId, id, "contains", 1);
    }

    for (const definition of result.definitions ?? []) {
      const typeByKind = {
        table: "table",
        view: "table",
        index: "table",
        message: "schema",
        enum: "schema",
        route: "endpoint",
      };
      const childType = typeByKind[definition.kind] ?? "schema";
      const id = `${childType}:${result.path}:${definition.name}`;
      nodeIds.add(id);
      addNode(nodes, {
        id,
        type: childType,
        name: definition.name,
        filePath: result.path,
        lineRange: definition.lineRange,
        summary: childSummary(childType, definition.name, result.path),
        tags: [childType, "xiaogu", "definition"],
        complexity: resultComplexity,
      });
      addEdge(edges, fileId, id, "contains", 1);
    }
    for (const service of result.services ?? []) {
      const id = `service:${result.path}:${service.name}`;
      nodeIds.add(id);
      addNode(nodes, {
        id,
        type: "service",
        name: service.name,
        filePath: result.path,
        summary: childSummary("service", service.name, result.path),
        tags: ["service", "infrastructure", "xiaogu"],
        complexity: resultComplexity,
      });
      addEdge(edges, fileId, id, "contains", 1);
    }
    for (const endpoint of result.endpoints ?? []) {
      const name = `${endpoint.method ?? ""} ${endpoint.path}`.trim();
      const id = `endpoint:${result.path}:${name}`;
      nodeIds.add(id);
      addNode(nodes, {
        id,
        type: "endpoint",
        name,
        filePath: result.path,
        lineRange: endpoint.lineRange,
        summary: childSummary("endpoint", name, result.path),
        tags: ["endpoint", "api", "xiaogu"],
        complexity: resultComplexity,
      });
      addEdge(edges, fileId, id, "contains", 1);
    }
  }

  for (const [from, targets] of Object.entries(batch.batchImportData ?? {})) {
    for (const target of targets) {
      addEdge(edges, `file:${from}`, `file:${target}`, "imports", 0.7);
    }
  }

  for (const result of raw.results) {
    const funcs = new Set(
      (result.functions ?? [])
        .filter((fn) => (fn.endLine ?? 0) - (fn.startLine ?? 0) + 1 >= 10)
        .map((fn) => fn.name),
    );
    for (const call of result.callGraph ?? []) {
      if (!funcs.has(call.caller) || !funcs.has(call.callee)) continue;
      const source = `function:${result.path}:${call.caller}`;
      const target = `function:${result.path}:${call.callee}`;
      if (nodeIds.has(source) && nodeIds.has(target)) {
        addEdge(edges, source, target, "calls", 0.8);
      }
    }
  }

  fs.writeFileSync(
    path.join(intermediate, `batch-${batch.batchIndex}.json`),
    JSON.stringify({ nodes, edges }, null, 2),
  );
}
