# 123STRM
123 云盘 STRM 助手，自定义实现版本。
支持全量同步、分享递归生成 STRM、自动整理、自动下载 mediainfo、空间清理等。

## 更新记录

- v1.1.5：修复 share 递归生成 STRM 时丢失 123 盘子目录结构的问题
- v1.1.4：统一类名与目录名为 P123StrmSelfuse/p123strmselfuse；share_iterdir 改为分层递归遍历，绕过服务端 1999 条截断限制；补全 pytz、apscheduler、cachetools、requests 等缺失依赖；package.v2.json 增加 system_version >=2.13.0
