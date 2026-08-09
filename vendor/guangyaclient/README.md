# 光鸭云盘 Python 客户端

[![PyPI version](https://img.shields.io/pypi/v/guangyaclient)](https://pypi.org/project/guangyaclient/)
[![Python version](https://img.shields.io/pypi/pyversions/guangyaclient)](https://pypi.org/project/guangyaclient/)
[![License](https://img.shields.io/github/license/DDSRem-Dev/guangyaclient)](./LICENSE)

[光鸭云盘](https://www.guangyapan.com) Python 客户端库。

## 安装

```bash
pip install guangyaclient
```

## 快速开始

```python
from guangyaclient import GuangyaClient

# 已有 token 直接初始化
client = GuangyaClient(access_token="your_token")

# 或使用短信登录（会自动更新 client 的 token）
client = GuangyaClient()
client.login_sms("+86 13800138000")
```

## 认证

### 短信登录全流程

```python
client = GuangyaClient()

# 一键完成登录，默认通过 input() 读取验证码
client.login_sms("+86 13800138000")

# 自动化场景（传入回调）
client.login_sms(
    "+86 13800138000",
    get_code=lambda: sms_service.get_code(),
)
```

登录成功后 `client.token`、`client.token_expires_at`、`client.refresh_token_value` 自动更新。

### 手动刷新 Token

```python
client.refresh_token()  # 使用已存储的 refresh_token
```

`request()` 会在 token 过期时自动刷新，也会在收到 401 时自动重试。

## API 参考

### 用户

| 方法 | 说明 |
|------|------|
| `user_info()` | 获取当前登录用户信息 |

### 文件管理

| 方法 | 说明 |
|------|------|
| `fs_files(parent_id, ...)` | 获取文件列表 |
| `fs_image_list(parent_id, ...)` | 获取图片列表 |
| `fs_video_list(parent_id, ...)` | 获取视频列表 |
| `fs_document_list(parent_id, ...)` | 获取文档列表 |
| `fs_recycle_files(...)` | 获取回收站文件列表 |
| `fs_detail(file_id)` | 获取文件详情 |
| `fs_create_dir(dir_name, parent_id)` | 创建文件夹 |
| `fs_copy(file_ids, parent_id)` | 复制文件 |
| `fs_move(file_ids, parent_id)` | 移动文件 |
| `fs_rename(file_id, new_name)` | 重命名文件 |
| `fs_delete(file_ids)` | 删除文件（移入回收站或永久删除） |
| `fs_recycle(file_ids)` | 从回收站还原文件 |
| `fs_clear_recycle_bin()` | 清空回收站 |
| `get_task_status(task_id)` | 获取任务状态 |

### 下载

| 方法 | 说明 |
|------|------|
| `download_url(file_id)` | 获取文件下载链接 |

### 上传

```python
# 自动处理小文件直传、大文件分片上传和秒传
result = client.file_upload("/path/to/file.mp4", parent_id=123)
```

| 方法 | 说明 |
|------|------|
| `file_upload(file_path, parent_id)` | 上传文件（全流程） |
| `upload_token(name, file_size, ...)` | 获取上传 token |
| `check_can_flash_upload(task_id, file_path)` | 检查是否可秒传 |
| `cdn_upload(file_path, token_data, ...)` | CDN 分片上传 |
| `upload_info(task_id)` | 获取上传任务信息 |

### 云下载

| 方法 | 说明 |
|------|------|
| `cloud_task_list(page, page_size, status)` | 获取云下载任务列表 |
| `cloud_resolve_url(url)` | 解析 HTTP/磁力/ed2k 链接 |
| `cloud_resolve_torrent(torrent)` | 解析 BT 种子文件 |
| `cloud_create_task(url, parent_id)` | 创建云下载任务 |

### 分享

| 方法 | 说明 |
|------|------|
| `share_create(file_ids, ...)` | 创建分享 |
| `share_user_list(page, page_size)` | 获取我的分享列表 |
| `share_update(share_id, ...)` | 更新分享设置 |
| `share_delete(ids)` | 删除分享 |
| `share_restore(access_token, file_ids, parent_id)` | 转存分享文件 |
| `share_download_url(file_id, access_token)` | 获取分享文件下载链接 |
| `share_files_size(access_token, file_ids)` | 获取分享文件大小 |
| `share_summary(share_id)` | 获取分享摘要（无需登录） |
| `share_access_token(share_id, code)` | 获取分享访问令牌（无需登录） |
| `share_files_list(access_token, ...)` | 获取分享页文件列表（无需登录） |

### 工具函数

```python
from guangyaclient import calculate_gcid, generate_did, generate_traceparent, FILE_TYPE, FILE_TYPE_NAME

# 文件类型常量
FILE_TYPE["图片"]   # 1
FILE_TYPE["视频"]   # 2
FILE_TYPE_NAME[1]   # "图片"
```

## License

[MIT](./LICENSE)
