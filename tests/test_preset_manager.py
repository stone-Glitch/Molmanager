#!/usr/bin/env python3
"""utils/preset_manager —— 预设管理器测试。

目录注入：PresetManager 无注入点，用 monkeypatch 把 _get_preset_dir 指向
tmp_path，避免污染真实 ~/.config/MolManager/presets。
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from utils.preset_manager import PresetManager


@pytest.fixture()
def pm(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> PresetManager:
    preset_dir = tmp_path / "presets"

    def _fake_dir(self: PresetManager) -> Path:
        return preset_dir

    monkeypatch.setattr(PresetManager, "_get_preset_dir", _fake_dir)
    return PresetManager("MolManager")


def test_save_and_get(pm: PresetManager) -> None:
    assert pm.save_preset("动画参数", {"speed": 2.0, "loop": False}) is True
    data = pm.get_preset("动画参数")
    assert data["speed"] == 2.0 and data["loop"] is False
    # 自动注入 _meta
    meta = pm.get_preset_meta("动画参数")
    assert meta["name"] == "动画参数" and "saved_at" in meta
    assert pm.list_presets() == ["动画参数"]


def test_get_missing_returns_empty_copy(pm: PresetManager) -> None:
    assert pm.get_preset("不存在") == {}
    assert pm.get_preset_meta("不存在") == {}


def test_overwrite_semantics(pm: PresetManager) -> None:
    pm.save_preset("p1", {"v": 1})
    # overwrite=False 拒绝覆盖
    assert pm.save_preset("p1", {"v": 2}, overwrite=False) is False
    assert pm.get_preset("p1")["v"] == 1
    # 默认覆盖
    assert pm.save_preset("p1", {"v": 2}) is True
    assert pm.get_preset("p1")["v"] == 2


def test_delete_and_persistence(pm: PresetManager) -> None:
    pm.save_preset("keep", {"a": 1})
    pm.save_preset("gone", {"a": 2})
    assert pm.delete_preset("gone") is True
    assert pm.delete_preset("gone") is True  # 再删也不报错
    assert "gone" not in pm.list_presets()
    # 新实例（模拟重启）→ 磁盘持久化生效
    pm2 = PresetManager("MolManager")
    assert pm2.get_preset("keep")["a"] == 1
    assert pm2.get_preset("gone") == {}


def test_export_import_roundtrip(pm: PresetManager, tmp_path: Path) -> None:
    pm.save_preset("原始", {"x": 42})
    export_path = str(tmp_path / "share.json")
    assert pm.export_preset("原始", export_path) is True
    assert pm.export_preset("不存在", export_path) is False
    # 导入到新 manager（不同目录）→ 名字带 _meta
    import utils.preset_manager as m

    pm2_dir = tmp_path / "other"

    def _fake_dir2(self: PresetManager) -> Path:
        return pm2_dir

    m.PresetManager._get_preset_dir = _fake_dir2  # type: ignore[method-assign]
    pm2 = PresetManager("MolManager")
    name, data = pm2.import_preset(export_path)
    assert name == "原始"
    assert data["x"] == 42
    # new_name 优先
    pm2.save_preset("原始", {"x": 0})  # 占用名字 → 导入自动后缀
    name2, _ = pm2.import_preset(export_path)
    assert name2 != "原始"


def test_import_bad_file_raises(pm: PresetManager, tmp_path: Path) -> None:
    bad = tmp_path / "bad.json"
    bad.write_text("{broken", encoding="utf-8")
    with pytest.raises(ValueError):
        pm.import_preset(str(bad))


def test_import_uses_filename_when_no_meta(pm: PresetManager, tmp_path: Path) -> None:
    f = tmp_path / "my_setting.json"
    f.write_text(json.dumps({"k": "v"}), encoding="utf-8")
    name, _ = pm.import_preset(str(f))
    assert name == "my_setting"


def test_clear_all(pm: PresetManager) -> None:
    for i in range(3):
        pm.save_preset(f"p{i}", {"i": i})
    assert pm.clear_all() == 3
    assert pm.list_presets() == []
