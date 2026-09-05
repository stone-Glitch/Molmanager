"""动画生成：反应物 → 产物坐标插值 + 渲染 + MP4。

流程：
1. 反应物/产物 XYZ → 元素 + 坐标
2. 原子配对：按元素排序（保证对齐时同元素对应）
   - 若反应物和产物元素多重集不同 → 不能插值，报错
3. Kabsch 算法做最小二乘对齐（让产物叠在反应物上）
4. 线性插值 N 帧
5. 输出：
   - trajectory.xyz（多帧 XYZ，psi4/3Dmol 都支持）
   - 每帧 matplotlib 3D 渲染 PNG → ffmpeg 合成 MP4
"""

from __future__ import annotations

import os

import matplotlib
import numpy as np

matplotlib.use("Agg")  # headless
import matplotlib.pyplot as plt
from mpl_toolkits.mplot3d import Axes3D  # noqa: F401


def kabsch_align(P: np.ndarray, Q: np.ndarray) -> np.ndarray:
    """把 Q 旋转平移对齐到 P（最小二乘 RMSD）。
    返回对齐后的 Q'。P, Q: (N,3) 同形状。
    """
    # 中心化
    Pc = P - P.mean(axis=0)
    Qc = Q - Q.mean(axis=0)
    # Kabsch（行向量约定：Q' = Qc @ R，最优 R = U·D·Vᵀ，H = Qcᵀ·Pc）
    H = Qc.T @ Pc
    U, S, Vt = np.linalg.svd(H)
    d = np.sign(np.linalg.det(U @ Vt))  # 反射修正：强制 det(R)=+1，禁止镜像解
    D = np.diag([1.0, 1.0, d])
    R = U @ D @ Vt
    Qrot = Qc @ R
    # 平移到 P 的中心
    return Qrot + P.mean(axis=0)


def pair_atoms(r_elements, p_elements):
    """把反应物/产物原子按元素匹配排序。
    要求两边元素 Counter 相同。返回 (r_idx_order, p_idx_order) 使 r[i] 和 p[i] 同元素。
    """
    from collections import defaultdict

    r_groups = defaultdict(list)
    p_groups = defaultdict(list)
    for i, e in enumerate(r_elements):
        r_groups[e].append(i)
    for i, e in enumerate(p_elements):
        p_groups[e].append(i)
    r_order, p_order = [], []
    for e in sorted(r_groups.keys()):
        r_list = r_groups[e]
        p_list = p_groups[e]
        if len(r_list) != len(p_list):
            raise ValueError(f"元素 {e} 在反应物 {len(r_list)} 个但产物 {len(p_list)} 个，无法配对")
        r_order.extend(r_list)
        p_order.extend(p_list)
    return r_order, p_order


def linear_interpolate(r_coords, p_coords, n_frames):
    """线性插值。返回 (n_frames, N, 3)。"""
    if n_frames < 2:
        n_frames = 2
    ts = np.linspace(0.0, 1.0, n_frames)
    out = []
    for t in ts:
        c = (1 - t) * r_coords + t * p_coords
        out.append(c)
    return np.array(out)


def make_trajectory(r_xyz, p_xyz, n_frames=15):
    """主入口：反应物/产物 XYZ 字符串 → 多帧坐标。
    返回 (elements, list of (N,3) arrays)
    """
    from .molbuild import parse_xyz

    r_e, r_c = parse_xyz(r_xyz)
    p_e, p_c = parse_xyz(p_xyz)
    # 检查配平
    from collections import Counter

    if Counter(r_e) != Counter(p_e):
        raise ValueError(
            "原子未配平，无法插值。反应物元素: %s, 产物元素: %s" % (dict(Counter(r_e)), dict(Counter(p_e)))
        )
    # 原子配对
    r_order, p_order = pair_atoms(r_e, p_e)
    r_c = r_c[r_order]
    p_c = p_c[p_order]
    r_e = [r_e[i] for i in r_order]
    # 对齐产物到反应物
    p_aligned = kabsch_align(r_c, p_c)
    # 插值
    frames = linear_interpolate(r_c, p_aligned, n_frames)
    return r_e, [f for f in frames]


def write_multiframe_xyz(elements, frames, path, comment="trajectory"):
    """写多帧 XYZ（每帧一个 N + comment + coords 块）。"""
    lines = []
    for i, coords in enumerate(frames):
        lines.append(str(len(elements)))
        lines.append(f"{comment} frame {i + 1}/{len(frames)}")
        for sym, (x, y, z) in zip(elements, coords):
            lines.append(f"{sym} {x:.6f} {y:.6f} {z:.6f}")
    with open(path, "w") as f:
        f.write("\n".join(lines) + "\n")


def _render_frame_png(elements, coords, idx, n_frames, out_dir, dpi=100):
    """matplotlib 3D 渲染一帧为 PNG。"""
    fig = plt.figure(figsize=(6, 5), dpi=dpi)
    ax = fig.add_subplot(111, projection="3d")
    coords = np.array(coords)
    # 元素 → 颜色/大小
    color_map = {
        "H": "#dddddd",
        "C": "#222222",
        "N": "#3050ff",
        "O": "#ff3030",
        "Cl": "#1ff018",
        "S": "#ffff30",
        "F": "#90e050",
        "P": "#ff8000",
    }
    sizes = {"H": 80, "C": 120, "N": 120, "O": 120, "Cl": 150, "S": 150}
    # 画原子
    for sym, (x, y, z) in zip(elements, coords):
        c = color_map.get(sym, "#888888")
        s = sizes.get(sym, 100)
        ax.scatter([x], [y], [z], c=c, s=s, edgecolors="k", depthshade=True)
    # 画键：简单距离判断（≤ 1.8 Å 算键）
    for i in range(len(coords)):
        for j in range(i + 1, len(coords)):
            d = np.linalg.norm(coords[i] - coords[j])
            if 0.5 < d < 1.9:
                ax.plot(*zip(coords[i], coords[j]), color="gray", linewidth=1.5, alpha=0.6)
    # 范围
    pad = 1.5
    cx, cy, cz = coords.mean(axis=0)
    rng = max((coords.max(axis=0) - coords.min(axis=0)).max() / 2 + pad, 1.5)
    ax.set_xlim(cx - rng, cx + rng)
    ax.set_ylim(cy - rng, cy + rng)
    ax.set_zlim(cz - rng, cz + rng)
    ax.set_xlabel("X")
    ax.set_ylabel("Y")
    ax.set_zlabel("Z")
    ax.set_title(f"Frame {idx + 1} / {n_frames}")
    # 隐藏网格背景以更干净
    ax.xaxis.pane.set_alpha(0.2)
    ax.yaxis.pane.set_alpha(0.2)
    ax.zaxis.pane.set_alpha(0.2)
    out_path = os.path.join(out_dir, f"frame_{idx:04d}.png")
    fig.tight_layout()
    fig.savefig(out_path)
    plt.close(fig)
    return out_path


def render_mp4(elements, frames, out_mp4, fps=8, dpi=100, logger=None):
    """matplotlib 渲染帧 + ffmpeg/imageio 合成 MP4。"""

    def log(msg):
        if logger:
            logger(msg)

    os.makedirs(os.path.dirname(out_mp4), exist_ok=True)
    out_dir = os.path.dirname(out_mp4)
    # 先渲染所有 PNG
    pngs = []
    for i, coords in enumerate(frames):
        log(f"  [render] frame {i + 1}/{len(frames)}")
        p = _render_frame_png(elements, coords, i, len(frames), out_dir, dpi=dpi)
        pngs.append(p)
    # 用 imageio 合成
    mp4_ok = False
    try:
        import imageio.v2 as imageio

        log(f"  [mp4] encoding {len(pngs)} frames → {out_mp4} (imageio)")
        with imageio.get_writer(out_mp4, fps=fps, codec="libx264", quality=8, macro_block_size=1) as w:
            for p in pngs:
                w.append_data(imageio.imread(p))
        mp4_ok = True
        log("  [mp4] done (imageio)")
    except Exception as e:
        log(f"  [mp4] imageio failed: {e}; try system ffmpeg")
    # 兜底：直接调系统 ffmpeg（/usr/local/bin/ffmpeg 有 libx264）
    if not mp4_ok:
        import subprocess

        pattern = os.path.join(out_dir, "frame_%04d.png")
        # 优先系统 ffmpeg
        for ff in ["/usr/local/bin/ffmpeg", "/usr/bin/ffmpeg", "ffmpeg"]:
            cmd = [
                ff,
                "-y",
                "-r",
                str(fps),
                "-i",
                pattern,
                "-c:v",
                "libx264",
                "-pix_fmt",
                "yuv420p",
                "-vf",
                "scale=trunc(iw/2)*2:trunc(ih/2)*2",
                out_mp4,
            ]
            try:
                r = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
                if r.returncode == 0 and os.path.exists(out_mp4) and os.path.getsize(out_mp4) > 0:
                    log(f"  [mp4] done (system ffmpeg: {ff})")
                    mp4_ok = True
                    break
                else:
                    log(f"  [mp4] {ff} rc={r.returncode}: {r.stderr[-200:]}")
            except FileNotFoundError:
                continue
            except Exception as e:
                log(f"  [mp4] {ff} failed: {e}")
                continue
    if not mp4_ok:
        log("  [mp4] all methods failed; only XYZ trajectory available")
    # 清理 PNG
    for p in pngs:
        try:
            os.remove(p)
        except OSError:
            pass
    return out_mp4
