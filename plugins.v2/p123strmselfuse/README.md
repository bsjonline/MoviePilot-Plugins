# 123STRM
123 云盘 STRM 助手，自定义实现版本。
支持全量同步、分享生成 STRM、自动整理、自动下载 mediainfo、空间清理等。

## 更新记录

- v1.1.7：还原成 `share_iterdir(max_depth=-1)` 并用 `relpath` 保证网盘子目录层级
- v1.1.6：修复分享递归返回路径未转 Path 导致 suffix 报错
- v1.1.5：修复 share 递归生成 STRM 时丢失目录结构的问题
- v1.1.4：统一类名与目录名为 P123StrmSelfuse/p123strmselfuse；补全缺失依赖；package.v2.json 增加 system_version>=2.13.0
