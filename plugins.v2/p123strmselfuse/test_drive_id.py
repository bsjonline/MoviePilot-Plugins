"""
路径 1 验证测试：不依赖真实 123 账号，mock P123Client.request / user_info。
验证 3 件事：
  1. set_drive_id 正确解析；发出的 mkdir/upload/list 请求携带正确 driveId（非 0）
  2. user_info 无 driveId -> 回退 0（不崩溃）
  3. 5060 冲突 -> 经 list_with_drive 复用已有 FileId
"""
import sys
import types
from unittest import mock

# ---- 构造一个假的 p123client 模块，避免真实依赖缺失 ----
fake_p123 = types.ModuleType("p123client")


class FakeP123Client:
    """记录所有 request 调用，按预设返回。"""

    def __init__(self, passport="", password=""):
        self.calls = []
        self._user_info_resp = {"code": 0, "data": {"UID": 123, "defaultDriveId": 456}}

    def user_info(self):
        return self._user_info_resp

    def request(self, url, method="GET", **kwargs):
        self.calls.append({"url": url, "method": method, "kwargs": kwargs})
        # 默认成功：建目录返回父目录 ID；秒传返回新 FileId；列表返回文件
        if url.endswith("/upload/v1/file/mkdir"):
            return {"code": 0, "data": {"Info": {"FileId": 999}}}
        if url.endswith("file/upload_request"):
            return {"code": 0, "data": {"Info": {"FileId": 888}}}
        if url.endswith("file/list"):
            return {
                "code": 0,
                "data": {
                    "FileList": [
                        {
                            "FileId": 777,
                            "FileName": "movie.mkv",
                            "Etag": "abc",
                            "Size": 1024,
                        }
                    ]
                },
            }
        return {"code": 0, "data": {}}


fake_p123.P123Client = FakeP123Client
sys.modules["p123client"] = fake_p123

# 把本目录加入 path，导入被测模块
sys.path.insert(0, "/opt/data/MoviePilot-Plugins-Custom/plugins.v2/p123strmselfuse")

import tool  # noqa: E402


def test_1_drive_id_injected():
    """set_drive_id 解析后，mkdir/upload/list 请求都带正确 driveId"""
    c = tool.P123AutoClient("p", "w")
    c.set_drive_id(456)
    # mkdir
    c.mkdir_with_drive("我的秒传")
    # upload
    c.upload_fast_with_drive(file_md5="abc", file_name="movie.mkv", file_size=1024, parent_id=999, duplicate=2)
    # list
    c.list_with_drive(999)
    calls = c._client.calls
    assert any(
        call["url"].endswith("/upload/v1/file/mkdir")
        and call["kwargs"]["json"].get("driveId") == 456
        for call in calls
    ), f"mkdir 未带 driveId=456: {calls}"
    assert any(
        call["url"].endswith("file/upload_request")
        and call["kwargs"]["json"].get("driveId") == 456
        for call in calls
    ), f"upload 未带 driveId=456: {calls}"
    assert any(
        call["url"].endswith("file/list")
        and call["kwargs"]["params"].get("driveId") == 456
        for call in calls
    ), f"list 未带 driveId=456: {calls}"
    print("[PASS] test_1_drive_id_injected: mkdir/upload/list 均带上 driveId=456")


def test_2_no_drive_id_fallback_zero():
    """user_info 无 driveId -> set_drive_id 回退 0，不崩溃"""
    c = tool.P123AutoClient("p", "w")
    c.set_drive_id(None)  # 模拟取不到
    c.set_drive_id("")    # 空串
    c.set_drive_id(0)     # 显式 0
    assert c._drive_id == 0, f"应为 0，实际 {c._drive_id}"
    c.mkdir_with_drive("我的秒传")
    drive = c._client.calls[-1]["kwargs"]["json"].get("driveId")
    assert drive == 0, f"回退后 mkdir driveId 应为 0，实际 {drive}"
    print("[PASS] test_2_no_drive_id_fallback_zero: 无 driveId 时回退 0")


def test_3_5060_reuse():
    """5060 冲突 -> list_with_drive 找到已有 FileId 复用"""
    c = tool.P123AutoClient("p", "w")
    c.set_drive_id(456)
    # 先触发 _client 创建
    c.mkdir_with_drive("我的秒传")
    # 让 upload_request 返回 5060，模拟同名冲突
    orig_request = c._client.request

    def patched(url, method="GET", **kwargs):
        if url.endswith("file/upload_request"):
            return {"code": 5060, "message": "同名文件已存在"}
        return orig_request(url, method, **kwargs)

    c._client.request = patched

    # 直接用底层三件套模拟 redirect_url 的 5060 回退逻辑
    mkdir_resp = c.mkdir_with_drive("我的秒传")
    parent_id = mkdir_resp["data"]["Info"]["FileId"]
    up_resp = c.upload_fast_with_drive(
        file_md5="abc", file_name="movie.mkv", file_size=1024, parent_id=parent_id, duplicate=2
    )
    if isinstance(up_resp, dict) and up_resp.get("code") == 5060:
        existing = None
        list_resp = c.list_with_drive(parent_id)
        for item in list_resp.get("data", {}).get("FileList", []):
            if item.get("FileName") == "movie.mkv" and item.get("Etag") == "abc":
                existing = item.get("FileId")
        assert existing == 777, f"应复用 FileId=777，实际 {existing}"
        print("[PASS] test_3_5060_reuse: 冲突后成功复用已有 FileId=777")
    else:
        raise AssertionError(f"未触发 5060: {up_resp}")


if __name__ == "__main__":
    test_1_drive_id_injected()
    test_2_no_drive_id_fallback_zero()
    test_3_5060_reuse()
    print("\n全部测试通过 ✅")
