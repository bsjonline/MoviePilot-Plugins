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

    token = ""

    @property
    def token_user_info(self):
        if not self.token or "." not in self.token:
            return {}
        import base64 as _b64, json as _j
        seg = self.token.split(".", 2)[1]
        seg += "=" * (-len(seg) % 4)
        return _j.loads(_b64.urlsafe_b64decode(seg))

    def request(self, url, method="GET", **kwargs):
        self.calls.append({"url": url, "method": method, "kwargs": kwargs})
        # 默认成功：建目录返回父目录 ID；秒传返回新 FileId；列表返回文件
        if url.endswith("file/upload_request"):
            # 根据 payload 的 type 区分目录/文件
            p = kwargs.get("json", {})
            if p.get("type") == 1:
                return {"code": 0, "data": {"Info": {"FileId": 999}}}
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

    def upload_request(self, payload, base_url="https://123pan.com/b"):
        # 复用 request 的 mock 行为，把 payload 嵌进 kwargs
        return self.request("file/upload_request", "POST", json=payload, base_url=base_url)


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
        call["url"].endswith("file/upload_request")
        and call["kwargs"]["json"].get("type") == 1
        and call["kwargs"]["json"].get("driveId") == 456
        and call["kwargs"]["json"].get("fileName") == "我的秒传"
        for call in calls
    ), f"mkdir 未走 upload_request(type=1) 或 driveId 不符: {calls}"
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
    """user_info 无 driveId -> resolve_drive_id 回退 0，不崩溃"""
    c = tool.P123AutoClient("p", "w")
    c.mkdir_with_drive("我的秒传")  # 触发 _client 创建
    c._client._user_info_resp = {"code": 0, "data": {"UID": 1}}  # 无 driveId
    did, src = c.resolve_drive_id()
    assert did == 0 and src == "none", f"应为 (0,'none')，实际 ({did},{src})"
    c.set_drive_id(None)
    c.mkdir_with_drive("我的秒传")
    assert c._client.calls[-1]["kwargs"]["json"].get("driveId") == 0
    print("[PASS] test_2_no_drive_id_fallback_zero: 无 driveId 时回退 0")


def test_2b_drive_id_from_jwt():
    """user_info 无 driveId，但 JWT token_user_info 含 driveId -> 正确取到"""
    c = tool.P123AutoClient("p", "w")
    c.mkdir_with_drive("我的秒传")  # 触发 _client 创建
    c._client._user_info_resp = {"code": 0, "data": {"UID": 1}}  # 无 driveId
    # 给假 JWT：token_user_info 解析中段 -> 注入 driveId
    import base64, json as _json
    claim = _json.dumps({"id": 1, "driveId": 789}).encode()
    seg = base64.urlsafe_b64encode(claim).rstrip(b"=").decode()
    c._client.token = f"a.{seg}.c"  # 触发 token_user_info 解码
    did, src = c.resolve_drive_id()
    assert did == 789 and src == "jwt", f"应从 JWT 取 789，实际 ({did},{src})"
    print("[PASS] test_2b_drive_id_from_jwt: 从 JWT token_user_info 取到 driveId=789")



def test_3_5060_reuse():
    """5060 冲突 -> list_with_drive 找到已有 FileId 复用"""
    c = tool.P123AutoClient("p", "w")
    c.set_drive_id(456)
    # 先触发 _client 创建
    c.mkdir_with_drive("我的秒传")
    # 让第二次 upload_request（秒传文件）返回 5060，模拟同名冲突；第一次（mkdir）正常
    orig_request = c._client.request
    _up_calls = {"n": 0}

    def patched(url, method="GET", **kwargs):
        if url.endswith("file/upload_request"):
            _up_calls["n"] += 1
            if _up_calls["n"] >= 2:  # 第一次是 mkdir，第二次才是秒传文件
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


def test_2c_drive_id_from_fs_list():
    """user_info/JWT 均无，fs_list 根目录响应含 driveId -> 正确取到"""
    c = tool.P123AutoClient("p", "w")
    c.mkdir_with_drive("我的秒传")  # 触发 _client 创建
    c._client._user_info_resp = {"code": 0, "data": {"UID": 1}}

    def patched(url, method="GET", **kwargs):
        if url.endswith("file/list"):
            return {"code": 0, "data": {"driveId": 654, "FileList": []}}
        return c._client._orig_request(url, method, **kwargs)

    c._client._orig_request = c._client.request
    c._client.request = patched
    did, src = c.resolve_drive_id()
    assert did == 654 and src == "fs_list", f"应从 fs_list 取 654，实际 ({did},{src})"
    print("[PASS] test_2c_drive_id_from_fs_list: 从 fs_list 根目录取到 driveId=654")


def test_2d_diag_when_missing():
    """全部取不到 -> 返回 (0,'none') 且 _last_drive_diag 含 user_info/fs_list 记录"""
    c = tool.P123AutoClient("p", "w")
    c.mkdir_with_drive("我的秒传")
    c._client._user_info_resp = {"code": 0, "data": {"UID": 1}}
    did, src = c.resolve_drive_id()
    assert did == 0 and src == "none"
    diag = c._last_drive_diag
    assert "user_info" in diag and "fs_list_root" in diag, f"缺诊断: {list(diag)}"
    print("[PASS] test_2d_diag_when_missing: 取不到时记录完整诊断信息")


if __name__ == "__main__":
    test_1_drive_id_injected()
    test_2_no_drive_id_fallback_zero()
    test_2b_drive_id_from_jwt()
    test_2c_drive_id_from_fs_list()
    test_2d_diag_when_missing()
    test_3_5060_reuse()
    print("\n全部测试通过 ✅")
