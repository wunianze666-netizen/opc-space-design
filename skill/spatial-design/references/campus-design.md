# 园区设计与设施

先走一遍不同使用者的路线，从外部到入口、楼栋、活动区和安静角落，再考虑保洁、维护和补给。根据场地用途和现有条件提出适合的空间焦点与设施，说明保留、合并、增加和待核的原因。夜间活动看照明和视线，生态园区看树冠、土壤和养护，社区学习看等候、活动信息和休息。先把活动落到平面，再选择色彩、立面、材质和构件。

图纸没画出设施时，把要保留的既有设施和建议新增的设施分别问清或标注。依实际需求检查门前等候、路径转弯、边角、雨天停留、垃圾清运和可达性。缺少植物、排水、供电或结构资料时标为待核。每个建议设施进入同一份园区输入，给足外廓、操作点、高度和使用说明。

## 数据表达

根对象可选 `surfaces`，写 `name/polygon/color/note` 表达草坪、广场或种植区。铺装与道路交叉时道路覆盖地面，图案及材料做法按项目深化。

根对象可选 `features`，每件填写唯一 `name`、`kind`、`polygon`、`height_mm`、`color`、`note`。类型支持 `bench/tree/planter/lamp/bin/sculpture/shelter/sign/equipment`。长椅可用 `facing` 选择 `north/south/east/west`，座面和靠背方向跟随该值。树木外廓覆盖示意冠幅。用户给出的特殊设施可使用 `equipment` 并填写 `components`，每个部件写 `name/polygon/base_mm/top_mm/color`。部件不得越过整件外廓或高度。

可填 `access_point`，点应位于地块中、设施外且距设施不超过 2 m。检查会寻找其到已连通道路最近点的平面直线，距离不超过 3 m 且没有穿过建筑和设施时标为「平面直线可达」。这是概念阶段的点对线检查，通道净宽、地形、地面材料、轮椅转弯和设备开门需单独深化。缺少操作点标为待确认。

建筑可选 `facade`，写 `glass_color/floors/sill_mm/head_mm/bay_mm` 控制体量上的概念玻璃分格。楼层和窗台窗顶相对高度须匹配建筑高度，入口附近留出分格。图中分格是设计表现，建筑开口和门窗需依据实测和立面方案深化。

`campus` 输出的总平面用 E 编号关联 `户外设施定位.csv`、`户外设施局部-01.png` 等分页、DXF 的 `FEATURES/FEATURE_PARTS/FEATURE_ACCESS` 图层和核对 JSON。`园区三维几何.json` 保存同版三维面的坐标与所属对象。位置使用输入图纸坐标，不规则场地也要先确认现场坐标基准。局部图是按设备外廓生成的体块表达，定制连接、灯具光学、植物品种、无障碍、机电和施工节点需另行深化。

## 交叉案例

[科技展研](../examples/campus/styles/tech.json)、[生态疗愈](../examples/campus/styles/nature.json)、[社区学习](../examples/campus/styles/community.json)采用不同面积和三类假设需求。三份均为合成案例，有 20 件含角落的设施、三栋建筑和分区路径。运行 `python examples/campus/check_detail.py` 验证尺寸、DXF、操作点和异常输入。实际项目应根据不同用户和底图重新选择，不应照搬样例坐标。

研究园区体验时，可参考设计方关于入口、交流、生态和遮荫的公开项目资料。例如信阳学院南湾校区入口整合户外学习、休闲与雨水景观，SWA 的 The Landing 设置可集会广场和半私密庭院。项目做法需依据本场地的数据与所在地区要求判断。[信阳学院南湾校区](https://www.sasaki.com/projects/xinyang-university-south-bay-campus-gateway-landscape/) · [The Landing](https://www.swagroup.com/projects/the-landing/)
