# 室内原创设计

从空间使用方式提出设计。先用一句话说清来访者进入后看到什么，日常员工如何使用，空间里哪里需要相对安静。再画动线、决定主要看点和背景，最后才确定色材。风格偏好相同也可比较不同的活动组织。

沿动线考虑墙、地、顶。墙面用作品墙、可换展示、格栅或吸声面形成节奏。地面可用材质分界组织使用区域。顶面用工作灯、重点照明和吸声片标出不同活动。设计构件应服务于使用、识别或氛围，位置、范围、相邻关系要能在图面上找到。保留已经确认的外轮廓、洞口、门窗、柱和原结构。

## 从设计概念到同坐标效果

`requirements.interior` 的 `concept` 与 `description` 用简短文字表达设计。 `elements` 是可定位的设计构件。

- `slats` 格栅、`acoustic` 吸声面、`art` 墙面艺术、`display_wall` 展示墙和 `notice_board` 壁报用 `name/kind/material/segment/depth_mm/bottom_mm/top_mm`。线段取已核实的室内实体墙面，厚度向室内。避免门窗、门扇及通道。跨门窗时分段或换墙。
- `floor_finish` 地面饰面用 `name/kind/material/polygon`。贴在楼板上，不覆盖洞口，不与另一个饰面重叠。
- `ceiling_light` 灯具和 `ceiling_baffle` 吊挂吸声片用 `name/kind/material/polygon/bottom_mm/top_mm`。用现场净高核对。 `clearance_mm` 是本方案采用的吊挂净空目标，示例用 2200 mm，按项目改填。
- `view` 可填写 `eye_mm` 和 `target_mm`，都是 `[x,y,z]` 毫米坐标。机位应在可用楼面，避开家具、固定结构和吊挂件。

材料预设有 `oak` 橡木、`walnut` 胡桃木、`sage` 灰绿、`cobalt` 钴蓝、`graphite` 石墨灰、`clay` 陶土、`ivory` 米白、`terrazzo` 水磨石、`warm_light` 暖光和 `cork` 软木色。饰面为方案表达，实物选样后确认样品、做法及报价。光源表现供比较，照度、眩光、空调、喷淋、电气及吊装节点按专业深化。

可用 `interior.materials` 增加用户指定的材质名与方案色，详见 [风格选择](style-choices.md)。

用 `generate --brief --quality studio` 生成一页图、人视、轴测和清单。先看机位所见是否能表达主要设计，再和全层平面对照，调整构件或机位后重跑。两种设计概念可以各用一份项目 JSON 保持同一底图，分别比选。需要按实际样品调整时，可复用已校核坐标，更新对应材质或构件。 [原创室内示例](example-interior.json)
