# 项目输入与命令

Python 3.11 或以上。依赖安装在技能目录的 `.venv` 中，或使用调用环境已有依赖。Windows 示例（在技能目录执行）。

```powershell
python -m venv .venv
& .\.venv\Scripts\python.exe -m pip install -r requirements.txt
python scripts/run.py generate references/example-project.json --brief --out ./opc-demo
python scripts/run.py verify ./opc-demo
```

示例为合成场景，涉及报价的价格为虚拟测试数据。可选择 [办公](example-project.json)、[整层研发办公](example-large-floor.json)、[共创与接待](example-workplace.json) 或 [住宅](example-home.json) 输入。项目 JSON 的几何单位统一为毫米，坐标原点和方向应与已确认的平面一致。

| 字段 | 内容 |
| --- | --- |
| `schema`、`title` | 格式版本 1 和项目名称 |
| `geometry` | `unit: mm`、可追溯的 `source`、确认状态、净使用边界 `outline` |
| `voids` | 真实无楼板区，`name/polygon`。洞口在轮廓内部，互不接触 |
| `fixed` | 不可移动多边形，`name/kind/polygon`。类别为 `wall/column/service/existing` |
| `keep_clear` | 门扇扫掠 `door_swing`、家具禁占 `reserved` 或人员禁行 `no_walk` 多边形 |
| `exits`、`windows` | `name/segment`。线段在外轮廓同一条边，单段最长 15 m。门洞窄段可填 `throat_mm`，门头可填 `head_mm`，窗台和窗顶同时填 `sill_mm/head_mm` |
| `ceiling_mm` | 已知净高。缺失时留待核对 |
| `requirements.desks` | 0 至 200 个必需工位。非办公可省略，项目至少提供一项家具或使用需求 |
| `work_groups` | 最多 12 组。每组 `name/desks/polygon`，团队区互不重叠且避开洞口。总工位可由各组相加得出 |
| `meeting_seats` | 每个会议组的座位数列表。单组可选 4、6、8 |
| `pantry/storage/lounge` | 对应家具功能组数量，未指定时为 0 |
| `aisle_mm`、`exit_min_mm` | 项目确认的室内通行净宽及可选的独立出口最小净宽。省略 `exit_min_mm` 时沿用通行值，已确认的门洞窄段之后校核室内通行宽度 |
| `zones` | 用 `use/polygon` 指定 `work/meeting/pantry/storage/lounge` 或下方家具 `kind` 的允许区域 |
| `furniture` | 下方说明的定尺或实测家具，可与办公配套组合 |
| `interior` | 可选。设计概念、墙地顶元素及人视机位，见 [室内原创设计](interior-design.md) |
| `priorities` | 按填写顺序比较 `comfort` 连通通行空间、`cost` 已报价范围、`privacy` 二维入口视线、`visitor` 访客区接近程度、`focus` 专注舱至活跃功能区的最小平面净距。省略时按连通通行空间推荐 |
| `visitor_entries` | 可选。填写已确认的访客入口名称列表，用于访客比较 |
| `clear_routes` | 可选。填写 `name/points/width_mm`，沿中心线保留连续净空，宽度单位为毫米 |
| `required_approaches` | 可选。填写 `name/point/entry`。检测点放在门外可站人的位置，入口名称可省略 |
| `max_travel_mm` | 可选。使用区边缘至区域出口的二维可走路径上限 |
| `options`、`style` | 1 至 3 套。科技感、简约、绿化、自然木色、冷灰极简、暖白轻商务、自然疗愈、工业创客、当代展廊 |
| `budget_limit_cny`、`budget_mode` | 预算比较值。默认 `reference`，`cap` 要求量化项目全部有价且不超上限 |
| `prices` | `source/date/unit_prices`。键名见示例。缺价保留待报价 |

每行 `furniture` 支持 `kind/name/count/width_mm/depth_mm/height_mm/access_mm/access_sides/unit_price_cny`。床 `bed` 的使用带在床侧，展示架 `display`、服务台 `counter` 和设备 `equipment` 在前后方向使用 `access_sides: 1` 或 `2`。四座餐桌组 `dining` 的宽深是桌面尺寸，前后各有一条座椅使用带。一般家具可采用参考尺寸。设备请提供宽、深、高和使用带尺寸。填写自定义家具单价时提供 `prices.source/date`，餐桌组报价以一桌四椅为单位。禁占区供门扇或保留空地使用，实体禁行区按固定阻隔标注。

饮水、回收、入口随身物和落地植物可用 [日常配套](daily-use.md) 中的类型，正常核对取用面和养护空间。

- `focus_pod` 是单人专注舱，预留一处正面进入带。舱门开启方向、电源、通风和声学参数用选型资料确认。
- `workshop` 是两侧可使用的共创台与白板组。宽深填写台面，台面两条长边各预留 `access_mm`，一侧短边额外预留等深的白板操作带及 70 mm 板体。白板高 1650 mm，报价按整组填。重型设备单列实测。
- `planter` 是绿植隔断，宽深应覆盖选型的最大展开范围，使用带供养护。渗水、照度和维护由选型确认。
- `gallery` 是展陈装置，使用带预留给观看和更换展品。需要停留、排队或活动座位时按运营人数另核所需空间。

新品类有概念参考尺寸，可通过各自字段覆盖。风格选择控制材质、色彩与表现。面积、人数、设备与活动方式决定家具清单、功能分区和布局。优先项 `focus` 比较专注舱至会议区、工作台、展陈和休息区使用边界的最短平面距离，供布局取舍使用。声学指标由项目深化。

有定制柜或设备必须在指定位置时，在该家具行填写 `"placement":{"x":3530,"y":3790,"angle":0}`。该行数量为 1。坐标是旋转后的使用范围包络左下角，含取用、开门或坐人的预留区。角度可用 0、90、180、270 度。先在图纸上标明要从哪一侧使用，再核对包络与实体的相对位置。其他自由布置家具仍会搜索位置。

用 `clear_routes` 约束必须保持的一段通道。填写一条连续中心线和净宽，软件检查整条净空带是否越界、撞固定结构或被新家具使用带侵占。实际门前通行另填 `required_approaches`，按项目通行净宽检查对应入口至该点的连接。端点可落在图示入口边界，门前检测点放在轮廓内。两项均可画在平面图和 DXF 中。像素描图中的中心线和检测点可随图标定，固定家具位置须在毫米图上录入。

活动预留地面用 `reserved`，模型允许行人通过。背景板后、操作设备危险区等已确认不让人员走的区域用 `no_walk`，会从通行计算中排除，并在平面中标红。实体墙和柜体用 `fixed`，才会在三维中作为物体出现。检测点验证门前可达，门扇开启、门洞实际净宽和室内外疏散仍按现场复核。

二维通行采用由项目净宽计算的可走区域，保留已确认出口、各工位及会议座位所在的使用区边缘。会议组两侧屏风留出前后开口，两排座位均验证各自的外部通行连接。家具包内部的座椅拉出和净距在深化时另核。提供路径长度上限时，按可通过的路线验证保守上界。整栋楼疏散、楼梯、消防分区、无障碍、结构和施工做法需要项目专业复核。中国项目的消防依据见[住建部公告](https://www.mohurd.gov.cn/gongkai/zc/wjk/art/2024/art_17339_770016.html)。

生成的三类布局按家具布置实际差异选择。半围合方案使用屏风及开口。找不到足够可行方案时报告实际数量，搜索结果只描述当前工具找到的候选。

`python scripts/run.py recommend <方案包目录> --priorities privacy comfort` 比较同一批方案。结果报告并列、实际使用的优先项和缺证据的项。访客指标是直线距离代理，屏风指标基于二维采样点。人流排队、员工工作区和专业场地条件在相应项目中另行核对。

默认先发 `方案一页比选.png` 或单页 PDF。使用 `--brief` 时 ZIP 含每套方案的 DXF、平面 PNG、双向轴测、CSV、布局数据及版本摘要。省略 `--brief` 时另有 Word/PDF 说明和各方案平面 PDF。当前材料量化覆盖家具、屏风和地面净面积。柜体内部、墙顶、安装、机电和损耗另行计量。

已有经过设计调整的坐标时，可用 `generate <项目.json> --layouts <候选.json> --brief --out <新目录>`。候选文件为列表，每项含 `strategy`（`open/hybrid/focus`）和 `placements`（每条 `type/x/y/angle`），可从 `analyze` 结果或方案 `layout.json` 提取。所有候选仍按同一份项目条件校核，有冲突的候选不会进入推荐。新家具类型须与项目输入中的顺序对应，例如第一项自定义家具为 `furniture_1`。

改版文件例如 `{"requirements":{"desks":18}}`。执行 `python scripts/run.py revise <旧方案目录> --option B --changes <改版.json> --out <新目录>`。几何、报价、风格及数量从当前输入重新核对。命令退出码 2 表示输入/校验错误，3 表示求解完成但未找到可行候选。
