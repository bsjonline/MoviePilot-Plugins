# 123STRM
123 云盘 STRM 助手，自定义实现版本。
支持全量同步、分享生成 STRM、自动整理、自动下载 mediainfo、空间清理等。

## 更新记录
- v1.2.6：手工 STRM 去重：新增清理 tab 下目录填写框、保留文件名最长开关与执行按钮；支持按 STRM 内 size+md5 识别重复。
- v1.2.5：分享批量下载时拆分字幕低并发通道并增加失败最多 2 轮重跑，压缩字幕批量失败量
- v1.2.4：目录拼接时对目录 relpath 补尾部分隔符，避免祖先目录名与子项拼接后串名
- v1.2.3：恢复目录兼容处理，在 share 流程使用 relprefix 完整保留目录层级
- v1.2.2：移除 _share_recursive 中遗留的 is_dir 守卫，避免递归目录导出时崩溃
- v1.2.1：修复 `Generator` 未导入导致加载失败
- v1.2.0：按子目录递归一层一层拉取分享文件避免 1999 条截断；新增 _share_recursive 生成器，以 max_depth=1 逐层枚举并手动拼接 rel_prefix 保持完整相对路径
- v1.1.8：回滚 share_iterdir(max_depth=-1) 翻页递归方案，恢复上游 get_share_list_creata_strm；保持 relpath 目录层级
- v1.1.7：还原 share_iterdir max_depth=-1 单层遍历并使用 relpath 保持网盘子目录层级
- v1.1.6：修复分享递归返回路径未转 Path 导致 suffix 报错
- v1.1.5：修复 share 递归生成 STRM 时丢失目录结构的问题
- v1.1.4：统一类名与目录名为 P123StrmSelfuse/p123strmselfuse；补全缺失依赖；package.v2.json 增加 system_version>=2.13.0
