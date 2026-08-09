#!/usr/bin/env python3
"""
测试光鸭云盘 MD5 秒传链路：
upload_token(md5=md5) -> upload_info(task_id) -> 是否返回 file_id

Usage:
  python3 /opt/data/MoviePilot-Plugins-Custom/vendor/guangyaclient/test_guangya_md5.py <md5> <name> <size> --token TOKEN
"""
import sys
import os

# Ensure the package root is importable
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from guangyaclient import GuangyaClient


def main():
    if len(sys.argv) < 5:
        print("Usage: test_guangya_md5.py <md5> <name> <size> --token TOKEN")
        sys.exit(1)

    md5 = sys.argv[1]
    name = sys.argv[2]
    size = int(sys.argv[3])

    token = None
    for i, arg in enumerate(sys.argv):
        if arg == "--token" and i + 1 < len(sys.argv):
            token = sys.argv[i + 1]
            break

    if not token:
        print("❌ 缺少 --token TOKEN")
        sys.exit(1)

    client = GuangyaClient(access_token=token)

    print("== Step 1: upload_token ==")
    token_resp = client.upload_token(
        name=name,
        file_size=size,
        parent_id="",
        md5=md5,
    )
    print("upload_token resp:", token_resp)

    task_id = token_resp.get("data", {}).get("taskId")
    if not task_id:
        print("\n❌ 没有拿到 task_id，秒传不会走")
        sys.exit(2)

    print(f"\n== Step 2: upload_info(task_id={task_id}) ==")
    info_resp = client.upload_info(task_id=task_id)
    print("upload_info resp:", info_resp)

    file_info = info_resp.get("data") or info_resp
    file_id = (
        file_info.get("fileId")
        or file_info.get("file_id")
        or file_info.get("FileId")
    )

    print("\n== Result ==")
    if file_id:
        print(f"✅ 拿到 file_id = {file_id}")
        print("→ 链路可行：可用 MD5 直接获取 file_id，再 download_url 播放")
        try:
            dl = client.download_url(file_id=file_id)
            print("download_url:", dl)
        except Exception as e:
            print("download_url failed:", e)
        sys.exit(0)
    else:
        print("❌ 没有 file_id，服务端未通过 MD5 识别文件")
        print("→ 纯 MD5 秒传不可行，可能需要:")
        print("   1. 本地文件计算 gcid 后再 check_can_flash_upload")
        print("   2. 或放弃无转存，改用已存在的 file_id/分享链接播放")
        sys.exit(3)


if __name__ == "__main__":
    main()
