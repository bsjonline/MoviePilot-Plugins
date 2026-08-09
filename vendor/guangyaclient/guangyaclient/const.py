__all__ = ["FILE_TYPE", "FILE_TYPE_NAME"]

FILE_TYPE = {
    "图片": 1,
    "视频": 2,
    "音频": 3,
    "文档": 4,
    "压缩包": 5,
    "BT种子": 9,
}

FILE_TYPE_NAME = {v: k for k, v in FILE_TYPE.items()}
