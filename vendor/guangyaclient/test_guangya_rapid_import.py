#!/usr/bin/env python3
"""
测试光鸭云盘 网页JSON导入接口（userres/v1/）：
get_res_center_token -> check_can_flash_upload -> upload_info

这个接口是网页端批量导入用的，参数和标准上传握手不同。
只传 md5 + size，看看能不能秒传。
"""
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import httpx

TOKEN = "eyJhbGciOiJSUzI1NiIsImtpZCI6IjM3ZjZhMDY1LWY0NTItNDA5OC1iOTJhLTk2YmVmMDA1ZDY1OCJ9.eyJpc3MiOiJodHRwczovL2FjY291bnQuZ3Vhbmd5YXBhbi5jb20iLCJzdWIiOiJhZVczMjV3b2NnelJnRGlWIiwiYXVkIjoiYU1lLThWU2xrcmJRWHBVUiIsImV4cCI6MTc4NjI1ODYwMCwiaWF0IjoxNzg2MjUxNDAwLCJhdF9oYXNoIjoici50VldkNnBPdUVmR2c1V1lzMFd4YjZRIiwic2NvcGUiOiJ1c2VyIHByb2ZpbGUgc3NvIG9mZmxpbmUiLCJwcm9qZWN0X2lkIjoiMzU2amxkNmpqbGJqdnlnaTV2OSIsIm1ldGEiOnsiYSI6Ik9LVmQ5Q2UrYk1DN1AxTXlXaVBWMm1hcGlBMGRqRmpidDVMOEs4eHYvMHM9In19.pbapQ3uKVGzlKLeTJf7ytqWXJEB21ksByvz29OXBCGz-CDfaWtH7aHsrO-CgBo4Pwf-_yTQdv1S1NNueo8EbrT4wqq1wP4yC2jhwXsCAZEOHwLm0giRT76zONcn-MBs27ZWX_S5K4z4u1c70MzGWw2RN8dEJarxmubZDl5gUjD-RLfIaF6lv432j4ytuU3yQPk1fnNXisoJbG9vPNfOxgC4ygtFua95Bp2uYiK7w5qpvpxtLWSQQ-MRB7kKCMbh_ywPcrgpHZPJateHTm2z9ixiraqPGSivgpinPkRRTM4sCD2Jqk2s5QrmBIGzvngLCchIpbaWDUg79-o4mhUUGzg"

BASE = "https://api.guangyapan.com/userres/v1"

HEADERS = {
    "authorization": f"Bearer {TOKEN}",
    "content-type": "application/json;charset=utf-8",
    "dt": "4",
    "origin": "https://www.guangyapan.com",
    "referer": "https://www.guangyapan.com/",
    "user-agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/147.0.0.0 Safari/537.36",
    "x-client-id": "aMe-8VSlkrbQXpUR",
    "x-client-version": "0.0.1",
    "x-device-id": "700bdae5de558dfc3f0616070306c84f",
    "x-device-model": "chrome/147.0.0.0",
    "x-device-name": "PC-Chrome",
    "x-device-sign": "wdi10.700bdae5de558dfc3f0616070306c84f" + "a" * 32,
    "x-net-work-type": "NONE",
    "x-os-version": "MacIntel",
    "x-platform-version": "1",
    "x-protocol-version": "301",
    "x-provider-name": "NONE",
    "x-sdk-version": "9.0.2",
}

client = httpx.Client(headers=HEADERS, timeout=30)

def main():
    md5 = sys.argv[1] if len(sys.argv) > 1 else "4330064811deac1304aacac4f0209701"
    name = sys.argv[2] if len(sys.argv) > 2 else "Royal.Betrothal.2026.S01E01.2160p.WEB-DL.AAC.H.265-HiveWeb.mp4"
    size = int(sys.argv[3]) if len(sys.argv) > 3 else 3234969041

    print("== Step 1: get_res_center_token (userres/v1) ==")
    token_resp = client.post(
        f"{BASE}/get_res_center_token",
        json={
            "capacity": 2,
            "res": {"md5": md5, "fileSize": size},
            "name": name,
            "parentId": "",
        },
    )
    print(f"status: {token_resp.status_code}")
    print("body:", token_resp.text[:500])
    token_data = token_resp.json()
    task_id = token_data.get("data", {}).get("taskId") or token_data.get("data", {}).get("task_id")
    if not task_id:
        print("\n❌ 没有 task_id")
        sys.exit(2)
    print(f"\ntask_id = {task_id}")

    print(f"\n== Step 2: check_can_flash_upload ==")
    check_resp = client.post(
        f"{BASE}/check_can_flash_upload",
        json={"taskId": task_id, "gcid": "", "md5": md5, "fileSize": size},
    )
    print(f"status: {check_resp.status_code}")
    print("body:", check_resp.text[:500])
    check_data = check_resp.json()
    can_flash = check_data.get("data", {}).get("canFlashUpload")
    print(f"\ncanFlashUpload = {can_flash}")

    if can_flash is not True:
        print("❌ 服务端未返回 canFlashUpload=true，纯 MD5 秒传失败")
        sys.exit(3)

    print("\n✅ canFlashUpload=true，轮询 upload_info ...")
    for i in range(6):
        info_resp = client.post(
            f"{BASE}/file/get_info_by_task_id",
            json={"taskId": task_id},
        )
        info = info_resp.json()
        file_info = info.get("data") or info
        file_id = file_info.get("fileId") or file_info.get("file_id") or file_info.get("id")
        status = file_info.get("taskStatus") or file_info.get("task_status") or file_info.get("status")
        print(f"  poll {i+1}: status={status}, file_id={file_id}")
        if file_id:
            print(f"\n✅ 秒传成功，file_id = {file_id}")
            sys.exit(0)
        if status in (3, 4, "3", "4", "FAIL", "ERROR", "CANCEL"):
            print("❌ 任务失败")
            sys.exit(4)
        import time; time.sleep(2)

    print("❌ 超时")
    sys.exit(5)

if __name__ == "__main__":
    main()
