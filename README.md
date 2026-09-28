# BioBench

在本地 NVIDIA 硬件上运行生物分子 AI 模型，并通过 Web 查看器看到结果。

一个 Agent（Claude Code、GitHub Copilot、DeepSeek Harness，或任何能读
`SKILL.md` 并执行 shell 命令的 harness）读取技能说明，在本地 GPU 上运行模型，
把结构发布到查看器。

## 目录结构

```
biobench/
├── SKILL.md                  入口：环境检查、路由、编排
├── skills/
│   ├── viewer-launch/        启动查看器；引导环境
│   │   └── scripts/
│   │       └── bootstrap.sh  环境检查（--fix 修复）
│   ├── structure-viewer/     发布结构与进度
│   │   └── scripts/
│   │       └── publish.py    原子发布（结构 + 状态）
│   ├── boltzgen-deploy/      安装 BoltzGen 及权重
│   │   └── scripts/
│   │       ├── preflight.py  GPU/CUDA/权重就绪检查
│   │       └── fetch_weights.sh
│   ├── binder-design/        运行 BoltzGen 结合剂设计
│   ├── proteinmpnn-deploy/   安装 ProteinMPNN
│   │   └── scripts/
│   │       └── preflight.py  环境/导入/权重就绪检查
│   ├── sequence-design/      为固定骨架设计序列
│   ├── genmol-deploy/        安装 GenMol 及权重
│   │   └── scripts/
│   │       └── preflight.py  环境/导入/权重/检查点就绪检查
│   ├── molecule-generation/  用 GenMol 生成小分子
│   │   └── scripts/
│   │       └── generate.py   全新生成 / 片段补全 / 连接
│   ├── foldseek-deploy/      安装 Foldseek
│   │   └── scripts/
│   │       └── preflight.py  二进制/版本/冒烟搜索就绪检查
│   └── structure-comparison/ 用 Foldseek 比较结构
├── webapp/
│   ├── serve.py              查看器服务端（Agent 面板 + 静态服务）
│   ├── agent.py              opencode 客户端：会话、流式、权限、任务监控
│   ├── run_viewer.sh         启动查看器
│   └── static/
│       ├── index.html        查看器与 Agent 面板（3Dmol.js）
│       ├── 3Dmol-min.js
│       ├── current.cif       ← 发布的结构
│       ├── current.json      ← 发布的元数据
│       └── status.json       ← 发布的进度
├── vendor/                   内置的 opencode 及项目配置
└── structures/               本地缓存的 PDB 条目
```

## 快速开始

```bash
# 1. 检查环境（加 --fix 修复）
bash skills/viewer-launch/scripts/bootstrap.sh

# 2. 启动查看器
bash webapp/run_viewer.sh --port 8282
#    → http://localhost:8282

# 3. 向 Agent 提问：
#    "为 NK1R 设计一个结合剂"
```

查看器启动后，右侧是 Agent 面板。直接用自然语言描述要做什么；在结构上选中残基
或原子，选中的内容会连同问题一起发给模型。

## 工作原理

```
        Agent（任意 harness）
              │  读 SKILL.md，执行命令
              ▼
      ┌───────────────┐
      │  biobench/    │  入口：环境检查 + 路由
      │  SKILL.md     │
      └───────┬───────┘
              │ 路由到
    ┌─────────┼──────────────┐
    ▼         ▼              ▼
viewer-    structure-    boltzgen-
launch     viewer        deploy
    │         │              │
    │         │              │ 在本地 GPU 上运行 BoltzGen
    │         │              │
    └─────────┴──────────────┘
              │ 发布
              ▼
      webapp/static/current.cif + current.json + status.json
              │ 2 秒轮询
              ▼
        BioBench 查看器
```

查看器本身没有 LLM，渲染结构也不需要后端——它是一个静态页面，渲染技能发布的
内容。这让它极易运行，也让任何 Agent harness 都能驱动它。

`webapp/serve.py` 只做三件事：提供静态文件、把 Agent 面板的请求转给 opencode、
读写查看器的状态文件。API key 存放在 `webapp/agent.key`，从不发到浏览器。

## 技能

| 技能 | 职责 |
|------|------|
| `biobench`（根） | 入口 — 环境检查、路由、编排 |
| `viewer-launch` | 启动/重启查看器；环境引导 |
| `structure-viewer` | 结构与进度的发布契约 |

**部署** — 安装工具并验证可用：

| 技能 | 安装 |
|------|------|
| `vina-deploy` | AutoDock Vina + meeko/RDKit |
| `boltzgen-deploy` | BoltzGen 及权重 |
| `proteinmpnn-deploy` | ProteinMPNN |
| `genmol-deploy` | GenMol 及权重 |
| `resgen-deploy` | ResGen 及检查点 |
| `foldseek-deploy` | Foldseek（静态二进制） |
| `mmseqs-deploy` | MMseqs2 |
| `molecular-dynamics-deploy` | OpenMM + AmberTools |

**运行** — 完成工作并发布结果：

| 技能 | 作用 |
|------|------|
| `molecular-docking` | 用 Vina 把配体对接到受体 |
| `molecular-dynamics` | 用 OpenMM 模拟复合物 |
| `pocket-detection` | 用 fpocket 查找并排序结合口袋 |
| `binder-design` | 用 BoltzGen 设计蛋白结合剂 |
| `ligand-binding-design` | 为小分子配体设计结合剂 |
| `pocket-molecule-design` | 用 ResGen 设计适配口袋的分子 |
| `molecule-generation` | 用 GenMol 生成小分子 |
| `molecule-editing` | 从查看器就地替换选中的基团 |
| `sequence-design` | 为固定骨架设计序列 |
| `sequence-search` | 用 MMseqs2 搜索序列 |
| `structure-comparison` | 用 Foldseek 搜索并聚类结构 |
| `conformational-analysis` | 用 RDKit 扫描扭转角 |
| `admet-prediction` | 预测类药性与 ADMET 性质 |

`skills/_shared/` 存放所有技能共用的东西：`biobench_paths.py`（东西放在哪）、
`pdb_format.py`（PDB 列布局）、`silent-failures.md`（这里踩过的坑）、
`pytorch-cuda.md`（哪个 wheel 匹配驱动）。

## 把技能装到别处

技能遵循开放的 Agent Skills 规范，所以用标准 CLI 或直接复制即可安装：

```bash
# 软链接到 Agent 的技能目录
ln -s "$PWD/skills/binder-design" ~/.claude/skills/binder-design

# 或复制
cp -r skills/binder-design ~/.claude/skills/
```

## 硬件

在 NVIDIA DGX Spark（GB10，aarch64，CUDA 13.0，121 GB 统一内存）上开发和测试。
一次典型的 BoltzGen 设计需要其中约 40 GB 内存。

## 相关文档

- [项目介绍书](docs/项目介绍书.md) — 设计动机、架构决策、以及为什么是现在
- [英文版 README](README.en.md)
