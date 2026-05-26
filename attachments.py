"""4 类单据通用附件上传/管理。

存储路径：uploads/{中文类型}/{单号}/{安全文件名}
数据库存储：单据表的 attachments 字段（TEXT），JSON 数组保存相对路径列表。

API:
- save_files(file_list, doc_type, doc_id) -> [str]  保存并返回相对路径列表
- load_attachments(json_str) -> [str]               反序列化
- append_attachments(existing_json, new_paths) -> str  追加并序列化
"""
import json
import os
from pathlib import Path

UPLOAD_ROOT = Path(__file__).parent / "uploads"
MAX_FILE_SIZE = 10 * 1024 * 1024  # 10MB（仅作业务侧硬上限提示；Flask 在 MAX_CONTENT_LENGTH 兜底）

TYPE_LABELS = {
    "inbound": "入库单",
    "outbound": "出库单",
    "damage": "报损单",
    "stocktake": "盘点单",
    "transfer": "调拨单",
    "warehouse": "仓库图",  # v15: 单图存到 uploads/仓库图/<wh_id>/
    "location": "库位",      # v19: 多文件附件存到 uploads/库位/<loc_id>/
    "sku": "物品图",         # v22: 单图存到 uploads/物品图/<sku_id>/
}


def _safe_name(original):
    """文件名安全化：去掉路径分隔符和 ..，保留中文。"""
    return (
        original.replace("/", "_")
        .replace("\\", "_")
        .replace("..", "_")
        .replace("\x00", "")
        .strip()
        or "unnamed"
    )


def save_files(file_list, doc_type, doc_id):
    """保存多文件，返回相对 UPLOAD_ROOT 的路径列表。"""
    label = TYPE_LABELS.get(doc_type, doc_type)
    target_dir = UPLOAD_ROOT / label / str(doc_id)
    target_dir.mkdir(parents=True, exist_ok=True)
    saved = []
    for f in file_list or []:
        if not f or not f.filename:
            continue
        safe = _safe_name(f.filename)
        dest = target_dir / safe
        # 重名加数字后缀
        n = 1
        while dest.exists():
            stem, ext = os.path.splitext(safe)
            dest = target_dir / f"{stem}_{n}{ext}"
            n += 1
        f.save(dest)
        # 检查大小（save 后才能拿到准确字节数）
        if dest.stat().st_size > MAX_FILE_SIZE:
            dest.unlink()
            raise ValueError(f"文件 {safe} 超过 {MAX_FILE_SIZE // 1024 // 1024}MB 上限")
        saved.append(f"{label}/{doc_id}/{dest.name}")
    return saved


def load_attachments(json_str):
    if not json_str:
        return []
    try:
        v = json.loads(json_str)
        return v if isinstance(v, list) else []
    except (json.JSONDecodeError, TypeError):
        return []


def append_attachments(existing_json, new_paths):
    arr = load_attachments(existing_json)
    arr.extend(new_paths)
    return json.dumps(arr, ensure_ascii=False)
