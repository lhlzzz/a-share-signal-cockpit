#!/bin/bash
# AgentMemory 日常使用脚本

cd /workspace/hermes-workspaces/xiaogu

# 保存当前状态
echo "保存当前状态..."
cat > .agentmemory/current_state.json << STATE
{
  "timestamp": "$(date -Iseconds)",
  "project": "xiaogu",
  "git_branch": "$(git branch --show-current)",
  "git_status": "$(git status --short)",
  "recent_commits": "$(git log --oneline -5)"
}
STATE

# 搜索相关记忆
echo "搜索相关记忆..."
if [ -f ".agentmemory/memories.json" ]; then
    echo "已有记忆: $(cat .agentmemory/memories.json | wc -l) 条"
else
    echo "暂无记忆"
fi

echo "✅ AgentMemory 日常使用完成"
