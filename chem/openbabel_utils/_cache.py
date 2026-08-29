from utils.cache import make_file_cache_key

from ._common import *  # noqa: F403  # 取 desc_cache / mol_read_cache 等公共对象

# ======================== 导入与版本兼容 ========================
from ._common import _CONTENT_HASH_MAX_BYTES


def clear_caches() -> tuple[int, int]:
    """
    公开接口：安全地清空所有 OpenBabel 缓存（描述符 + 分子读取）。
    返回: (evicted_desc_count, evicted_mol_read_count)
    """
    d = desc_cache.clear()
    m = mol_read_cache.clear()
    return d, m



def _cache_key(path_str: str) -> tuple[str, int, int, str | None] | None:
    """返回 (解析后路径, mtime_ns, 大小, 内容哈希或None)。

    统一委托 utils.cache.make_file_cache_key（语义、字段顺序完全一致）。
    内容哈希用于抵御「同尺寸/同 mtime 但内容被原地覆盖」导致的陈旧缓存命中（审计 P-2）；
    仅对小文件计算，大文件（P-3 场景）跳过哈希以保性能。
    """
    return make_file_cache_key(path_str, max_hash_bytes=_CONTENT_HASH_MAX_BYTES)



def cache_stats() -> dict[str, int]:
    """公开接口：返回当前缓存状态（只读，内部加锁，对多线程安全）。"""
    ds = desc_cache.stats()
    ms = mol_read_cache.stats()
    return {"descriptors": ds["size"], "mol_read": ms["size"], "desc_max": ds["maxsize"], "mol_read_max": ms["maxsize"]}


# ======================== 问题三：手动 obabel 路径 ========================
