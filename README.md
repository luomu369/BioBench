# BioBench

在本地 NVIDIA 硬件上运行生物分子 AI 模型，并通过 Web 查看器看到结果。

一个 Agent（opencode + StepFun `step-5-preview`）读取 23 个 Agent Skills，在
本地 GPU 上运行模型，把结构发布到查看器。你对它说"在这个口袋设计一个 12 氨基酸
的环肽"，它就去跑 BoltzGen。

- **完整文档**：[docs/项目说明.md](docs/项目说明.md) — 作品特点、技术实现、
  架构设计、部署说明、技术栈
- **仓库**：https://gitee.com/LRC-bit/biobench
- **硬件**：NVIDIA DGX Spark（GB10，aarch64，CUDA 13.0，121 GB 统一内存）
- **许可**：MIT

## 快速开始

```bash
git clone https://gitee.com/LRC-bit/biobench.git
cd biobench

# 检查环境（加 --fix 修复）
bash skills/viewer-launch/scripts/bootstrap.sh

# 配置模型 key
echo 'STEPFUN_API_KEY=sk-xxx' > webapp/agent.key

# 一条命令启动全部：查看器 + Agent
bash start.sh
#    → http://localhost:8282
```

启动后右侧是 Agent 面板。直接用自然语言描述要做什么；在结构上选中残基或原子，
选中的内容会连同问题一起发给模型。

```bash
bash stop.sh                # 停止
bash start.sh --no-agent    # 只起查看器
```

## 它由什么组成

```
biobench/
├── SKILL.md          入口：环境检查、路由、编排
├── start.sh          一键启动（查看器 + Agent）
├── skills/           23 个技能 = SKILL.md（知识）+ scripts/（操作）
│   └── _shared/      pdb_format.py, selection.py, silent-failures.md ...
├── webapp/
│   ├── serve.py      查看器服务端（静态服务 + Agent 面板）
│   ├── agent.py      opencode 客户端：会话、流式、权限、任务监控
│   └── static/       index.html（3Dmol.js）+ 发布契约的三个文件
├── vendor/           内置的 opencode 二进制与项目配置
└── docs/             项目说明（完整文档）
```

**查看器里没有 LLM，也没有后端逻辑** — 它是一个静态页面，渲染技能发布到磁盘上
的文件。Agent 在命令行工作，查看器只负责显示。这个划分是整件事能跑起来的原因。

## 技能

**部署（8）**：`vina-deploy`、`boltzgen-deploy`、`proteinmpnn-deploy`、
`genmol-deploy`、`resgen-deploy`、`foldseek-deploy`、`mmseqs-deploy`、
`molecular-dynamics-deploy`

**运行（13）**：`molecular-docking`、`molecular-dynamics`、`pocket-detection`、
`binder-design`、`ligand-binding-design`、`pocket-molecule-design`、
`molecule-generation`、`molecule-editing`、`sequence-design`、`sequence-search`、
`structure-comparison`、`conformational-analysis`、`admet-prediction`

**发布（1）**：`structure-viewer`

技能遵循开放的 Agent Skills 规范，可单独安装：

```bash
ln -s "$PWD/skills/binder-design" ~/.claude/skills/binder-design
```

## 相关文档

- [项目说明](docs/项目说明.md) — 完整文档（4256 字，含技术栈与部署）
- [英文版 README](README.en.md)
