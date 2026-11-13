# 潮汐书房与坡地茶室

同一套合成住宅、同一批家具和同一拍摄角度。两版根据阅读休息、会客和用餐的需求做不同的墙地组合。潮汐书房用铜日、海岸等高线和雾灰矿物地面。坡地茶室用赭日、田埂线条和暖木错缝。两套墙图都覆盖 1,260 × 900 mm 的已校核饰面，地纹按毫米重复并裁切到对应分区。

在 Skill 根目录安装 `requirements.txt` 后，可直接运行以下命令。首次运行用新输出目录。

```sh
python scripts/run.py generate examples/tidal-room/tide-project.json --layouts examples/tidal-room/layouts.json --brief --out demo/tide-options
python scripts/run.py deliver demo/tide-options --brief examples/tidal-room/tide-brief.json --azimuth 45 --out demo/tide
python scripts/run.py generate examples/tidal-room/terrace-project.json --layouts examples/tidal-room/layouts.json --brief --out demo/terrace-options
python scripts/run.py deliver demo/terrace-options --brief examples/tidal-room/terrace-brief.json --azimuth 45 --out demo/terrace
```

打开输出的《先看这里》和《整区总览》。点 D004 看重点墙，点 D001 看地面和铺排尺度。两份《逐件摆放》使用同一布局。样例附 `artwork` 图层，可按客户偏好改色、重绘曲线和编辑图形，无需下载图案、配置模型密钥或使用 Blender。实际设计可同时调整物件和布局，经校核后出图。
