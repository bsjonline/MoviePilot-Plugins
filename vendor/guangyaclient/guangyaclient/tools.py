__all__ = ["calculate_gcid", "generate_did", "generate_traceparent"]

from hashlib import md5, sha1
from os import urandom
from os.path import getsize
from pathlib import Path
from secrets import token_hex
from typing import Union


def generate_did():
    """
    模拟 did

    :return: did
    """
    random_seed = urandom(16)
    return md5(random_seed).hexdigest()


def generate_traceparent():
    """
    生成遵循 W3C Trace Context 标准的 traceparent 头部信息
    格式: 00-traceId-parentId-flags

    :return: traceparent
    """
    version = "00"
    trace_id = token_hex(16)
    parent_id = token_hex(8)
    flags = "01"
    return f"{version}-{trace_id}-{parent_id}-{flags}"


def calculate_gcid(file_path: Union[str, Path]):
    """
    gcid 计算

    :param file_path: 文件路径

    :return: gcid
    """
    file_size = getsize(file_path)

    if file_size <= 0x8000000:
        chunk_size = 262144
    elif file_size <= 0x10000000:
        chunk_size = 524288
    elif file_size <= 0x20000000:
        chunk_size = 1048576
    else:
        chunk_size = 2097152

    sha1_list = []

    with open(file_path, "rb") as f:
        while True:
            chunk = f.read(chunk_size)
            if not chunk:
                break
            sha1_list.append(sha1(chunk).digest())

    combined_hashes = b"".join(sha1_list)
    final_sha1 = sha1(combined_hashes).hexdigest()

    return final_sha1.upper()
