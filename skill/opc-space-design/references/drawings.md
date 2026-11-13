# 图纸读入

先执行 `python scripts/run.py inspect <图纸>`。DXF 输出 modelspace 图层和实体数量，支持毫米、厘米、米、英寸、英尺。单位不明时用 `--unit` 传已确认单位。

用示例项目 JSON 作为初始需求，再执行 `import-dxf <图纸.dxf> --seed <需求.json> --out <待确认项目.json>`。默认图层为 `OPC_OUTLINE`、`OPC_VOID`、`OPC_COLUMN`、`OPC_FIXED_WALL`、`OPC_SERVICE`、`OPC_FIXED`、`OPC_DOOR_SWING`、`OPC_KEEP_CLEAR`、`OPC_NO_WALK`、`OPC_EXIT`、`OPC_WINDOW`。

其他 CAD 图层用 `--layers` 提供 JSON 映射。键为 `outline/void/column/wall/service/existing/door_swing/reserved/no_walk/exit/window`，值为图层名列表。轮廓应形成唯一封闭边界，固定结构、洞口和禁占区使用闭合多段线，出口和窗使用线段。选定图层中的块、弧线或三维实体需由 CAD 整理。对照未映射图层，确认是否遗漏了设施。DXF 导入后开口会重新编号，门头、窗台、窗顶和门洞窄段按原图或实测补录。若约束引用了入口名称，同步核对。DWG 先由有权限的 CAD 软件另存 DXF。

PDF/PNG/JPG 预览用 `inspect <图纸> --preview <新预览.png> --page 1`。有视觉能力的宿主可以按像素描图，也可人工描图。项目 JSON 临时使用 `geometry.unit: px`，坐标原点为预览图左上。使用至少一条已知尺寸校准，建议提供第二条不同方向的尺寸复核。标定格式如下：

```json
{
  "a": [100, 200],
  "b": [800, 200],
  "known_mm": 7000,
  "rectified": true,
  "second": {"a": [100, 200], "b": [100, 600], "known_mm": 4000}
}
```

运行 `calibrate <像素描图.json> --calibration <标定.json> --out <待确认项目.json>`，再用 `preview` 出核对图。标定只适用于正投影或已校正平面。单张斜拍照片需要透视校正和现场尺寸，承重、层高和未知隐蔽结构保留待确认。输出确认前逐项对照原图和现场资料。
