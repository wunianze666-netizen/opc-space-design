# 宿主兼容说明

本技能按 Agent Skills 通用目录编排。核心入口为 `SKILL.md`，项目资料和 Python 脚本随目录一起分发。模型负责理解和沟通，尺寸、约束和文件输出由脚本执行。相同的已确认输入可以在有文件和命令执行能力的宿主中复用。 [Agent Skills 规范](https://agentskills.io/specification)

## 在本机宿主加载

| 宿主 | 项目内或本机用户级目录 | 启用方式 |
| --- | --- | --- |
| Codex | `.agents/skills/` 或 `~/.agents/skills/` | 在会话里输入 `$spatial-design`，或描述匹配任务。 [Codex 文档](https://developers.openai.com/codex/skills) |
| Claude Code | `.claude/skills/` 或 `~/.claude/skills/` | 在会话里输入 `/spatial-design`，或描述匹配任务。 [Claude Code 文档](https://code.claude.com/docs/en/skills) |
| Gemini CLI | `.gemini/skills/`、`~/.gemini/skills/`，也支持对应的 `.agents/skills/` | 用 `gemini skills list` 检查发现情况，在会话中点名技能并描述任务。 [Gemini CLI 文档](https://geminicli.com/docs/cli/using-agent-skills/) |
| Cursor | `.cursor/skills/`、`~/.cursor/skills/`，也支持对应的 `.agents/skills/` | 在 Agent 会话里选用已加载的技能，或描述匹配任务。 [Cursor 文档](https://cursor.com/docs/skills) |

将解压后的完整 `spatial-design` 目录放入对应位置。需要计算和导出的环境还需具备 [项目输入](project-input.md) 所列的 Python 和依赖。中文出图依赖可用的中文字体，必要时将 `OPC_FONT` 指向已安装的 TTF/TTC。安装、权限、文件可见范围和执行能力以所用客户端及其版本为准。云端和远程会话需要将技能、依赖和图纸放在该会话能访问的环境中。

## 根据当前能力交付

- 有本地脚本、文件及依赖访问时，运行 `suggest`、几何校核和 `overview`。生成的 HTML 可在支持 JavaScript 的浏览器离线打开，点选平面、三维、物件与饰面试铺。宿主只允许看图片时，交付整区 PNG 和所需的试铺图。
- 宿主支持语音时，以宿主传入的转写作为需求输入。手头已有转写文本时可以直接粘贴。宿主若无法把音频转成可读内容，请用户发文字或转写。模型按 [设计对话](design-dialogue.md) 核对会影响布局的歧义。
- 有视觉能力且图纸可见时，可以辅助读图或描图。只有文字时，依赖用户提供的净轮廓、尺寸和固定设施信息。图纸语义和尺寸确认流程见 [图纸读入](drawings.md)。
- 仅有聊天能力时，可上传 [设计对话](design-dialogue.md) 和 [设计元素库](design-library.json)。请模型按实际用途、保留物件、已知限制选出做法，给出墙地纹样说明和安装待核项。把 `space/style/area_m2/needs/avoid/keep/conditions` 需求摘要和已确认的底图交给可运行脚本的宿主校核并导出。
- 有生图时可生成 [饰面单元图](material-patterns.md)。只有脚本能力时，可用本地自绘 `artwork` 或导入授权小样，按同一坐标生成带纹样的平面和剖切三维。只有聊天能力时交付纹样说明和目标尺寸，待可执行宿主出图。

实际复核环境为 Windows 上的 Codex 和 Python，以及 Chromium 对离线页的加载与点击。其他宿主使用上表说明移植，模型的理解能力、工具和文件权限须在各自环境中复测。

企业 API 与参考图生图按 [模型与表现图](model-and-visuals.md) 接入。宿主的模型接入方式与此技能的本地计算是不同配置，配置前核对当前宿主支持的接口、文件访问和权限。
